"""
Pytest configuration for the sandbox-sdk e2e test suite.

Architecture:
    Test → SDK → http://localhost:8080/api/v1/sandboxes/<id>/exec/<rpc>
                 ↓ (hermes strips /exec prefix, proxies to 127.0.0.1:9000)
                 http://127.0.0.1:9000/<rpc>  (nano-executor)

Environment variables:
    NANO_EXECUTOR_BIN  – path to nano-executor binary
                         (default: <repo>/../nano-executor/target/release/nano-executor)
    HERMES_DIR         – path to hermes repo root (default: auto-detected)
    SKIP_START         – set to "1" to skip starting subprocesses
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Generator, AsyncGenerator

import httpx
import pytest
import pytest_asyncio

from sandbox import ConnectionConfig
from sandbox.sandbox import Sandbox, AsyncSandbox

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HERMES_ADDR = "http://localhost:8080"
NANO_ADDR = "http://localhost:9000"
TEST_SANDBOX_ID = "e2e-test-sandbox"
STARTUP_TIMEOUT = 45  # seconds
POLL_INTERVAL = 0.3


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    hermes_dir = os.environ.get("HERMES_DIR")
    if hermes_dir:
        return Path(hermes_dir)
    # This file is at sdk/python/tests/conftest.py; repo root is 3 levels up.
    return Path(__file__).resolve().parents[3]


def _start_nano_executor() -> subprocess.Popen:
    bin_path = os.environ.get("NANO_EXECUTOR_BIN")
    if not bin_path:
        bin_path = str(_repo_root().parent / "nano-executor" / "target" / "release" / "nano-executor")
    bin_path = str(Path(bin_path).resolve())

    if not Path(bin_path).exists():
        print(f"[e2e] SKIP: nano-executor binary not found at {bin_path}", file=sys.stderr)
        print("[e2e] Build it with: cd ../nano-executor && cargo build --release", file=sys.stderr)
        sys.exit(1)

    proc = subprocess.Popen([bin_path, "serve"], stdout=sys.stdout, stderr=sys.stderr)
    print(f"[e2e] started nano-executor pid={proc.pid}")
    return proc


def _start_hermes() -> subprocess.Popen:
    root = _repo_root()
    bin_path = str(Path(os.environ.get("TMPDIR", "/tmp")) / "hermes-e2e-test")

    result = subprocess.run(
        ["go", "build", "-o", bin_path, "."],
        cwd=str(root),
        stdout=sys.stderr,
        stderr=sys.stderr,
    )
    if result.returncode != 0:
        print("[e2e] failed to build hermes", file=sys.stderr)
        sys.exit(1)

    env = {**os.environ, "APP_ENV": "local"}
    proc = subprocess.Popen(
        [bin_path],
        cwd=str(root),
        env=env,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    print(f"[e2e] started hermes pid={proc.pid}")
    return proc


def _wait_ready(url: str, name: str, timeout: int = STARTUP_TIMEOUT) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = httpx.get(url, timeout=2.0)
            if resp.status_code < 500:
                print(f"[e2e] {name} ready (HTTP {resp.status_code})")
                return
        except Exception:
            pass
        time.sleep(POLL_INTERVAL)
    print(f"[e2e] FATAL: {name} not ready within {timeout}s", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Session-scoped subprocess management
# ---------------------------------------------------------------------------

_procs: list[subprocess.Popen] = []


def pytest_configure(config: pytest.Config) -> None:
    if os.environ.get("SKIP_START") == "1":
        return
    _procs.append(_start_nano_executor())
    _procs.append(_start_hermes())


def pytest_sessionstart(session: pytest.Session) -> None:
    _wait_ready(NANO_ADDR + "/health", "nano-executor")
    _wait_ready(HERMES_ADDR + "/health", "hermes")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if os.environ.get("SKIP_START") == "1":
        return
    for proc in _procs:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# SDK client fixtures
# ---------------------------------------------------------------------------

def _make_config() -> ConnectionConfig:
    envd_url = f"{HERMES_ADDR}/api/v1/sandboxes/{TEST_SANDBOX_ID}/exec"
    return ConnectionConfig(
        sandbox_id=TEST_SANDBOX_ID,
        envd_url=envd_url,
        access_token=None,
        api_key="test-key",
        base_url=HERMES_ADDR,
        request_timeout=30.0,
    )


@pytest.fixture(scope="session")
def sb() -> Generator[Sandbox, None, None]:
    """Session-scoped synchronous Sandbox client."""
    sandbox = Sandbox(_make_config())
    yield sandbox
    sandbox.close()


@pytest_asyncio.fixture(scope="session")
async def async_sb() -> AsyncGenerator[AsyncSandbox, None]:
    """Session-scoped async Sandbox client."""
    sandbox = AsyncSandbox(_make_config())
    yield sandbox
    await sandbox.close()
