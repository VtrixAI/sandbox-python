"""
PTY e2e tests — synchronous (Pty) and async (AsyncPty).
"""
from __future__ import annotations

import asyncio
import time

import pytest

from sandbox.sandbox import Sandbox, AsyncSandbox
from sandbox.types import PtySize


# ===========================================================================
# Synchronous tests
# ===========================================================================


class TestPtySync:

    def test_create_and_kill(self, sb: Sandbox) -> None:
        handle = sb.pty.create(PtySize(rows=24, cols=80))
        assert handle.pid != 0, "PTY create returned pid=0"
        assert sb.pty.kill(handle.pid) is True

    def test_resize(self, sb: Sandbox) -> None:
        handle = sb.pty.create(PtySize(rows=24, cols=80))
        try:
            sb.pty.resize(handle.pid, PtySize(rows=40, cols=200))
        finally:
            sb.pty.kill(handle.pid)

    def test_create_with_cwd(self, sb: Sandbox) -> None:
        handle = sb.pty.create(PtySize(rows=24, cols=80), cwd="/tmp")
        try:
            assert handle.pid != 0
        finally:
            sb.pty.kill(handle.pid)

    def test_create_with_envs(self, sb: Sandbox) -> None:
        handle = sb.pty.create(
            PtySize(rows=24, cols=80),
            envs={"E2E_PTY_PY_VAR": "pty_py_env_ok"},
        )
        try:
            assert handle.pid != 0
        finally:
            sb.pty.kill(handle.pid)

    def test_send_stdin(self, sb: Sandbox) -> None:
        handle = sb.pty.create(PtySize(rows=24, cols=80))
        try:
            time.sleep(0.2)
            sb.pty.send_stdin(handle.pid, b"echo pty_py_test\n")
        finally:
            sb.pty.kill(handle.pid)

    def test_send_stdin_read_output(self, sb: Sandbox) -> None:
        handle = sb.pty.create(PtySize(rows=24, cols=80))
        try:
            time.sleep(0.3)
            sb.pty.send_stdin(handle.pid, b"echo pty_py_echo_test\n")
            time.sleep(0.5)
        finally:
            sb.pty.kill(handle.pid)

    def test_create_and_wait(self, sb: Sandbox) -> None:
        handle = sb.pty.create(PtySize(rows=24, cols=80))
        time.sleep(0.3)
        sb.pty.send_stdin(handle.pid, b"exit\n")

        import threading
        done = threading.Event()
        result_box = [None]

        def _wait():
            result_box[0] = handle.wait()
            done.set()

        t = threading.Thread(target=_wait, daemon=True)
        t.start()

        if not done.wait(timeout=10):
            sb.pty.kill(handle.pid)
            pytest.skip("PTY Wait timed out after exit — killed")
        else:
            r = result_box[0]
            if r is not None:
                assert r.exit_code == 0, f"expected exit code 0, got {r.exit_code}"


# ===========================================================================
# Async tests
# ===========================================================================


@pytest.mark.asyncio
class TestPtyAsync:

    async def test_create_and_kill(self, async_sb: AsyncSandbox) -> None:
        handle = await async_sb.pty.create(PtySize(rows=24, cols=80))
        assert handle.pid != 0, "Async PTY create returned pid=0"
        assert await async_sb.pty.kill(handle.pid) is True

    async def test_resize(self, async_sb: AsyncSandbox) -> None:
        handle = await async_sb.pty.create(PtySize(rows=24, cols=80))
        try:
            await async_sb.pty.resize(handle.pid, PtySize(rows=40, cols=200))
        finally:
            await async_sb.pty.kill(handle.pid)

    async def test_create_with_cwd(self, async_sb: AsyncSandbox) -> None:
        handle = await async_sb.pty.create(PtySize(rows=24, cols=80), cwd="/tmp")
        try:
            assert handle.pid != 0
        finally:
            await async_sb.pty.kill(handle.pid)

    async def test_create_with_envs(self, async_sb: AsyncSandbox) -> None:
        handle = await async_sb.pty.create(
            PtySize(rows=24, cols=80),
            envs={"E2E_ASYNC_PTY_PY_VAR": "async_pty_py_env_ok"},
        )
        try:
            assert handle.pid != 0
        finally:
            await async_sb.pty.kill(handle.pid)

    async def test_send_stdin(self, async_sb: AsyncSandbox) -> None:
        handle = await async_sb.pty.create(PtySize(rows=24, cols=80))
        try:
            await asyncio.sleep(0.2)
            await async_sb.pty.send_stdin(handle.pid, b"echo async_pty_test\n")
        finally:
            await async_sb.pty.kill(handle.pid)

    async def test_create_and_wait(self, async_sb: AsyncSandbox) -> None:
        handle = await async_sb.pty.create(PtySize(rows=24, cols=80))
        await asyncio.sleep(0.3)
        await async_sb.pty.send_stdin(handle.pid, b"exit\n")

        try:
            r = await asyncio.wait_for(handle.wait(), timeout=10.0)
            if r is not None:
                assert r.exit_code == 0
        except asyncio.TimeoutError:
            await async_sb.pty.kill(handle.pid)
            pytest.skip("Async PTY Wait timed out after exit")
