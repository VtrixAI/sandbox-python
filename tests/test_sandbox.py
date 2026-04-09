"""
Sandbox management API tests — Python SDK.

Pure computation methods (get_host, sandbox_domain) are tested unconditionally.
Methods requiring Atlas (create/connect/kill/list/get_info/set_timeout/is_running/
download_url/upload_url/get_metrics) are guarded by ATLAS_BASE_URL.
"""
from __future__ import annotations

import os

import pytest

from sandbox.sandbox import Sandbox, AsyncSandbox

# ---------------------------------------------------------------------------
# Atlas guard
# ---------------------------------------------------------------------------

_NEEDS_ATLAS = pytest.mark.skipif(
    not os.environ.get("ATLAS_BASE_URL"),
    reason="ATLAS_BASE_URL not set — skipping Atlas management API tests",
)

_ATLAS_OPTS = dict(
    api_key=os.environ.get("SANDBOX_API_KEY", "test-key"),
    base_url=os.environ.get("ATLAS_BASE_URL", "http://localhost:8080"),
)


# ---------------------------------------------------------------------------
# get_host / sandbox_domain — pure computation, no network
# ---------------------------------------------------------------------------


class TestSandboxGetHost:

    def test_get_host_contains_port_and_sandbox_id(self, sb: Sandbox) -> None:
        host = sb.get_host(3000)
        assert "3000" in host, f"get_host(3000) = {host!r}, expected to contain '3000'"
        assert sb._config.sandbox_id in host, (
            f"get_host(3000) = {host!r}, expected to contain sandbox_id={sb._config.sandbox_id!r}"
        )

    def test_get_host_different_ports(self, sb: Sandbox) -> None:
        h8080 = sb.get_host(8080)
        h5000 = sb.get_host(5000)
        assert "8080" in h8080
        assert "5000" in h5000
        assert h8080 != h5000

    def test_sandbox_domain_is_non_empty(self, sb: Sandbox) -> None:
        domain = sb.sandbox_domain
        assert isinstance(domain, str)
        assert len(domain) > 0, "sandbox_domain should not be empty"


@pytest.mark.asyncio
class TestAsyncSandboxGetHost:

    async def test_get_host_contains_port_and_sandbox_id(self, async_sb: AsyncSandbox) -> None:
        host = async_sb.get_host(3000)
        assert "3000" in host
        assert async_sb._config.sandbox_id in host

    async def test_sandbox_domain_is_non_empty(self, async_sb: AsyncSandbox) -> None:
        domain = async_sb.sandbox_domain
        assert isinstance(domain, str)
        assert len(domain) > 0


# ---------------------------------------------------------------------------
# Atlas management methods — require ATLAS_BASE_URL
# ---------------------------------------------------------------------------


@_NEEDS_ATLAS
class TestSandboxAtlasMethods:

    def test_create_and_kill(self) -> None:
        sb = Sandbox.create(**_ATLAS_OPTS)
        assert sb.sandbox_id
        sb.kill()

    def test_connect(self) -> None:
        sb = Sandbox.create(**_ATLAS_OPTS)
        try:
            sb2 = Sandbox.connect(sb.sandbox_id, **_ATLAS_OPTS)
            result = sb2.commands.run("echo 'connected_ok'")
            assert "connected_ok" in result.stdout
        finally:
            sb.kill()

    def test_list(self) -> None:
        items = Sandbox.list(**_ATLAS_OPTS)
        assert isinstance(items, list)

    def test_get_info(self) -> None:
        sb = Sandbox.create(**_ATLAS_OPTS)
        try:
            info = sb.get_info()
            assert info.state
            assert info.sandbox_id == sb.sandbox_id
        finally:
            sb.kill()

    def test_is_running_true_after_create(self) -> None:
        sb = Sandbox.create(**_ATLAS_OPTS)
        try:
            assert sb.is_running() is True
        finally:
            sb.kill()

    def test_set_timeout(self) -> None:
        sb = Sandbox.create(**_ATLAS_OPTS)
        try:
            sb.set_timeout(120)  # should not raise
        finally:
            sb.kill()

    def test_get_metrics(self) -> None:
        sb = Sandbox.create(**_ATLAS_OPTS)
        try:
            metrics = sb.get_metrics()
            assert isinstance(metrics, dict)
        finally:
            sb.kill()

    def test_sandbox_domain_non_empty_after_create(self) -> None:
        sb = Sandbox.create(**_ATLAS_OPTS)
        try:
            assert isinstance(sb.sandbox_domain, str)
            assert len(sb.sandbox_domain) > 0
        finally:
            sb.kill()

    def test_download_url_returns_signed_url(self) -> None:
        sb = Sandbox.create(**_ATLAS_OPTS)
        try:
            sb.files.write("/tmp/dl_test_py.txt", "download url test")
            url = sb.download_url("/tmp/dl_test_py.txt")
            assert url, "download_url returned empty string"
            assert "signature" in url, f"download_url = {url!r}, expected 'signature' param"
        finally:
            sb.kill()

    def test_upload_url_returns_signed_url(self) -> None:
        sb = Sandbox.create(**_ATLAS_OPTS)
        try:
            url = sb.upload_url("/tmp/up_test_py.txt")
            assert url, "upload_url returned empty string"
            assert "signature" in url, f"upload_url = {url!r}, expected 'signature' param"
        finally:
            sb.kill()

    def test_resize_disk(self) -> None:
        sb = Sandbox.create(**_ATLAS_OPTS)
        try:
            sb.resize_disk(2048)  # expand to 2 GiB — should not raise
        finally:
            sb.kill()

    def test_static_kill_sandbox(self) -> None:
        sb = Sandbox.create(**_ATLAS_OPTS)
        Sandbox.kill_sandbox(sb.sandbox_id, **{k: v for k, v in _ATLAS_OPTS.items()})

    def test_static_get_sandbox_info(self) -> None:
        sb = Sandbox.create(**_ATLAS_OPTS)
        try:
            info = Sandbox.get_sandbox_info(sb.sandbox_id, **{k: v for k, v in _ATLAS_OPTS.items()})
            assert info.sandbox_id == sb.sandbox_id
            assert info.state
        finally:
            sb.kill()

    def test_static_set_sandbox_timeout(self) -> None:
        sb = Sandbox.create(**_ATLAS_OPTS)
        try:
            Sandbox.set_sandbox_timeout(sb.sandbox_id, 120, **{k: v for k, v in _ATLAS_OPTS.items()})
        finally:
            sb.kill()

    def test_static_get_sandbox_metrics(self) -> None:
        sb = Sandbox.create(**_ATLAS_OPTS)
        try:
            metrics = Sandbox.get_sandbox_metrics(sb.sandbox_id, **{k: v for k, v in _ATLAS_OPTS.items()})
            assert isinstance(metrics, dict)
        finally:
            sb.kill()


@_NEEDS_ATLAS
@pytest.mark.asyncio
class TestAsyncSandboxAtlasMethods:

    async def test_create_and_kill(self) -> None:
        sb = await AsyncSandbox.create(**_ATLAS_OPTS)
        assert sb.sandbox_id
        await sb.kill()

    async def test_get_info(self) -> None:
        sb = await AsyncSandbox.create(**_ATLAS_OPTS)
        try:
            info = await sb.get_info()
            assert info.state
        finally:
            await sb.kill()

    async def test_is_running(self) -> None:
        sb = await AsyncSandbox.create(**_ATLAS_OPTS)
        try:
            assert await sb.is_running() is True
        finally:
            await sb.kill()

    async def test_download_url_returns_signed_url(self) -> None:
        sb = await AsyncSandbox.create(**_ATLAS_OPTS)
        try:
            await sb.files.write("/tmp/dl_test_async_py.txt", "async download url test")
            url = await sb.download_url("/tmp/dl_test_async_py.txt")
            assert url, "async download_url returned empty string"
            assert "signature" in url
        finally:
            await sb.kill()

    async def test_upload_url_returns_signed_url(self) -> None:
        sb = await AsyncSandbox.create(**_ATLAS_OPTS)
        try:
            url = await sb.upload_url("/tmp/up_test_async_py.txt")
            assert url, "async upload_url returned empty string"
            assert "signature" in url
        finally:
            await sb.kill()

    async def test_resize_disk(self) -> None:
        sb = await AsyncSandbox.create(**_ATLAS_OPTS)
        try:
            await sb.resize_disk(2048)
        finally:
            await sb.kill()

    async def test_static_kill_sandbox(self) -> None:
        sb = await AsyncSandbox.create(**_ATLAS_OPTS)
        await AsyncSandbox.kill_sandbox(sb.sandbox_id, **{k: v for k, v in _ATLAS_OPTS.items()})

    async def test_static_get_sandbox_info(self) -> None:
        sb = await AsyncSandbox.create(**_ATLAS_OPTS)
        try:
            info = await AsyncSandbox.get_sandbox_info(sb.sandbox_id, **{k: v for k, v in _ATLAS_OPTS.items()})
            assert info.sandbox_id == sb.sandbox_id
            assert info.state
        finally:
            await sb.kill()

    async def test_static_set_sandbox_timeout(self) -> None:
        sb = await AsyncSandbox.create(**_ATLAS_OPTS)
        try:
            await AsyncSandbox.set_sandbox_timeout(sb.sandbox_id, 120, **{k: v for k, v in _ATLAS_OPTS.items()})
        finally:
            await sb.kill()

    async def test_static_get_sandbox_metrics(self) -> None:
        sb = await AsyncSandbox.create(**_ATLAS_OPTS)
        try:
            metrics = await AsyncSandbox.get_sandbox_metrics(sb.sandbox_id, **{k: v for k, v in _ATLAS_OPTS.items()})
            assert isinstance(metrics, dict)
        finally:
            await sb.kill()

