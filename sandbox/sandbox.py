"""
Sandbox management – create, connect, kill, and query sandbox instances.

Management API is served by the hermes gateway:
  POST   /api/v1/sandboxes
  GET    /api/v1/sandboxes
  GET    /api/v1/sandboxes/:id
  DELETE /api/v1/sandboxes/:id
  POST   /api/v1/sandboxes/:id/timeout
  POST   /api/v1/sandboxes/:id/connect

All sandbox RPC calls (envd) are routed through hermes at:
  /api/v1/sandboxes/:id/<rpc-path>
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import quote, urlparse

import httpx

from .commands import AsyncCommands, AsyncPty, Commands, Pty
from .exceptions import _raise_for_status, AuthenticationException
from .filesystem import AsyncFilesystem, Filesystem
from .types import ConnectionConfig, SandboxInfo

_ENV_API_KEY = "SANDBOX_API_KEY"
_ENV_BASE_URL = "SANDBOX_BASE_URL"
_DEFAULT_BASE_URL = "http://localhost:8080"


def _resolve_api_key(api_key: Optional[str]) -> str:
    key = api_key or os.environ.get(_ENV_API_KEY, "")
    if not key:
        raise AuthenticationException(
            "No API key provided.  Set " + _ENV_API_KEY + " or pass api_key=..."
        )
    return key


def _resolve_base_url(base_url: Optional[str]) -> str:
    return (base_url or os.environ.get(_ENV_BASE_URL, _DEFAULT_BASE_URL)).rstrip("/")


def _mgmt_headers(api_key: str) -> dict:
    return {"X-API-Key": api_key, "Content-Type": "application/json"}


def _sandbox_info_from_dict(d: dict) -> SandboxInfo:
    def _parse_dt(v: Optional[str]) -> Optional[datetime]:
        if not v:
            return None
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None

    return SandboxInfo(
        sandbox_id=d.get("sandboxID") or d.get("sandbox_id") or d.get("id", ""),
        template_id=d.get("templateID") or d.get("template_id"),
        alias=d.get("alias"),
        started_at=_parse_dt(d.get("startedAt") or d.get("started_at")),
        end_at=_parse_dt(d.get("endAt") or d.get("end_at")),
        metadata=d.get("metadata") or {},
        state=d.get("status") or d.get("state", "running"),
    )


def _envd_url(base_url: str, sandbox_id: str) -> str:
    """Build the envd URL routed through hermes.

    The /exec suffix tells hermes to strip it and forward to the nano-executor,
    while leaving /api/v1/sandboxes/:id/* (no /exec) transparently proxied to Atlas.
    """
    return base_url + "/api/v1/sandboxes/" + sandbox_id + "/exec"


def _mb_to_storage_size(size_mb: int) -> str:
    """Convert megabytes to a Kubernetes storage-size string.

    Uses ``Gi`` when *size_mb* is a whole number of gibibytes, ``Mi`` otherwise.
    """
    if size_mb % 1024 == 0:
        return f"{size_mb // 1024}Gi"
    return f"{size_mb}Mi"


def _sandbox_domain(base_url: str) -> str:
    """Extract the bare hostname from a base URL.

    "https://api.example.com" → "api.example.com"
    """
    parsed = urlparse(base_url)
    return parsed.hostname or base_url.lstrip("https://").lstrip("http://").rstrip("/")


class Sandbox:
    """Synchronous sandbox client.

    Mirrors the e2b ``Sandbox`` API and talks to nano-executor through the
    hermes gateway.

    Typical usage::

        sb = Sandbox.create(template="base", timeout=300)
        result = sb.commands.run("echo hello")
        sb.kill()
    """

    def __init__(self, config: ConnectionConfig) -> None:
        self._config = config
        self._mgmt_client = httpx.Client(
            headers=_mgmt_headers(config.api_key or ""),
            timeout=config.request_timeout,
        )
        self._envd_client = httpx.Client(timeout=config.request_timeout)
        self._files = Filesystem(config, self._envd_client)
        self._commands = Commands(config, self._envd_client)
        self._pty = Pty(config, self._envd_client)

    @property
    def sandbox_id(self) -> str:
        return self._config.sandbox_id

    @property
    def files(self) -> Filesystem:
        return self._files

    @property
    def commands(self) -> Commands:
        return self._commands

    @property
    def pty(self) -> Pty:
        return self._pty

    @property
    def sandbox_domain(self) -> str:
        """Bare hostname of the hermes gateway (e.g. 'localhost:8080')."""
        return _sandbox_domain(self._config.base_url)

    @classmethod
    def create(
        cls,
        template: str = "base",
        timeout: int = 300,
        metadata: Optional[Dict[str, str]] = None,
        envs: Optional[Dict[str, str]] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> "Sandbox":
        """Create a new sandbox and return a connected :class:`Sandbox`."""
        key = _resolve_api_key(api_key)
        url_base = _resolve_base_url(base_url)
        client = httpx.Client(headers=_mgmt_headers(key), timeout=30.0)
        try:
            resp = client.post(
                url_base + "/api/v1/sandboxes",
                json={
                    "templateID": template,
                    "timeout": timeout,
                    "metadata": metadata or {},
                    "envs": envs or {},
                },
            )
            _raise_for_status(resp.status_code, resp.json() if resp.content else {})
            data = resp.json()
            sandbox_id = data.get("sandboxID") or data.get("id", "")
            access_token = data.get("envdAccessToken") or data.get("accessToken")
            config = ConnectionConfig(
                sandbox_id=sandbox_id,
                envd_url=_envd_url(url_base, sandbox_id),
                access_token=access_token,
                api_key=key,
                base_url=url_base,
                request_timeout=30.0,
            )
        finally:
            client.close()
        return cls(config)

    @classmethod
    def connect(
        cls,
        sandbox_id: str,
        timeout: float = 30.0,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> "Sandbox":
        """Reconnect to an existing sandbox by ID, resuming it if paused."""
        key = _resolve_api_key(api_key)
        url_base = _resolve_base_url(base_url)
        client = httpx.Client(headers=_mgmt_headers(key), timeout=timeout)
        try:
            resp = client.get(url_base + "/api/v1/sandboxes/" + sandbox_id)
            _raise_for_status(resp.status_code, resp.json() if resp.content else {})
            data = resp.json()
            status = data.get("status") or data.get("state", "")
            if status not in ("running", "active", ""):
                resume_resp = client.post(
                    url_base + "/api/v1/sandboxes/" + sandbox_id + "/connect",
                    json={"timeout": timeout},
                )
                _raise_for_status(resume_resp.status_code, resume_resp.json() if resume_resp.content else {})
                resume_data = resume_resp.json()
                access_token = resume_data.get("envdAccessToken") or resume_data.get("accessToken")
            else:
                access_token = data.get("envdAccessToken") or data.get("accessToken")
            config = ConnectionConfig(
                sandbox_id=sandbox_id,
                envd_url=_envd_url(url_base, sandbox_id),
                access_token=access_token,
                api_key=key,
                base_url=url_base,
                request_timeout=30.0,
            )
        finally:
            client.close()
        return cls(config)

    def kill(self) -> None:
        """Terminate the sandbox immediately."""
        resp = self._mgmt_client.delete(
            self._config.base_url + "/api/v1/sandboxes/" + self._config.sandbox_id,
            timeout=self._config.request_timeout,
        )
        _ = resp.status_code  # ignore errors on kill

    def set_timeout(self, timeout: int) -> None:
        """Update the sandbox timeout to *timeout* seconds."""
        resp = self._mgmt_client.post(
            self._config.base_url + "/api/v1/sandboxes/" + self._config.sandbox_id + "/timeout",
            json={"timeout": timeout},
            timeout=self._config.request_timeout,
        )
        _raise_for_status(resp.status_code, resp.json() if resp.content else {})
        data = resp.json()

    def get_info(self) -> SandboxInfo:
        """Fetch current metadata for this sandbox."""
        resp = self._mgmt_client.get(
            self._config.base_url + "/api/v1/sandboxes/" + self._config.sandbox_id,
            timeout=self._config.request_timeout,
        )
        _raise_for_status(resp.status_code, resp.json() if resp.content else {})
        data = resp.json()
        return _sandbox_info_from_dict(data)

    def is_running(self) -> bool:
        """Return True if the sandbox status is running/active."""
        try:
            info = self.get_info()
            return info.state in ("running", "active", "")
        except Exception:
            return False

    def get_host(self, port: int) -> str:
        """Return the proxy hostname for *port* inside the sandbox.

        Format: ``<port>-<sandbox_id>.<domain>``
        """
        parsed = urlparse(self._config.base_url)
        host = parsed.hostname or self._config.base_url
        return str(port) + "-" + self._config.sandbox_id + "." + host

    def get_metrics(self) -> dict:
        """Fetch current CPU and memory usage for the sandbox.

        Returns a dict with keys ``cpuUsedPct`` and ``memUsedMiB``.
        """
        resp = self._mgmt_client.get(
            self._config.base_url + "/api/v1/sandboxes/" + self._config.sandbox_id + "/exec/metrics",
            timeout=self._config.request_timeout,
        )
        _raise_for_status(resp.status_code, resp.json() if resp.content else {})
        data = resp.json()
        return data

    def resize_disk(self, size_mb: int) -> None:
        """Resize the sandbox disk to *size_mb* megabytes.

        Sends ``PATCH /api/v1/sandboxes/:id`` with
        ``{"spec": {"storage_size": "<n>Gi"}}`` (or ``"<n>Mi"`` when *size_mb*
        is not a whole number of gibibytes).  Atlas performs an in-place PVC
        expansion — the sandbox does not restart for a pure resize.
        """
        if size_mb <= 0:
            raise ValueError("size_mb must be a positive integer")
        storage_size = _mb_to_storage_size(size_mb)
        resp = self._mgmt_client.patch(
            self._config.base_url + "/api/v1/sandboxes/" + self._config.sandbox_id,
            json={"spec": {"storage_size": storage_size}},
            timeout=self._config.request_timeout,
        )
        _raise_for_status(resp.status_code, resp.json() if resp.content else {})

    def download_url(
        self,
        path: str,
        *,
        user: Optional[str] = None,
        expires: int = 300,
    ) -> str:
        """Return a short-lived signed URL for directly downloading *path* from the sandbox."""
        return self._signed_file_url(path, "download-url", user=user, expires=expires)

    def upload_url(
        self,
        path: str,
        *,
        user: Optional[str] = None,
        expires: int = 300,
    ) -> str:
        """Return a short-lived signed URL for directly uploading a file to *path* in the sandbox."""
        return self._signed_file_url(path, "upload-url", user=user, expires=expires)

    def _signed_file_url(self, path: str, endpoint: str, *, user: Optional[str], expires: int) -> str:
        url = (
            self._config.base_url.rstrip("/")
            + "/api/v1/sandboxes/"
            + self._config.sandbox_id
            + "/exec/files/"
            + endpoint
            + "?path="
            + quote(path, safe="")
            + "&expires="
            + str(expires)
        )
        if user:
            url += "&username=" + quote(user, safe="")
        resp = self._mgmt_client.get(url, timeout=self._config.request_timeout)
        _raise_for_status(resp.status_code, resp.json() if resp.content else {})
        data = resp.json()
        return data.get("url", "")

    @staticmethod
    def list(api_key: Optional[str] = None, base_url: Optional[str] = None) -> List[SandboxInfo]:
        """Return all sandboxes visible to the given API key."""
        key = _resolve_api_key(api_key)
        url_base = _resolve_base_url(base_url)
        client = httpx.Client(headers=_mgmt_headers(key), timeout=30.0)
        try:
            resp = client.get(url_base + "/api/v1/sandboxes")
            _raise_for_status(resp.status_code, resp.json() if resp.content else {})
            data = resp.json()
            sandboxes = data if isinstance(data, list) else data.get("sandboxes", [])
            return [_sandbox_info_from_dict(s) for s in sandboxes]
        finally:
            client.close()

    @staticmethod
    def kill_sandbox(sandbox_id: str, api_key: Optional[str] = None, base_url: Optional[str] = None) -> None:
        """Terminate the sandbox identified by *sandbox_id* without creating a Sandbox instance."""
        key = _resolve_api_key(api_key)
        url_base = _resolve_base_url(base_url)
        client = httpx.Client(headers=_mgmt_headers(key), timeout=30.0)
        try:
            resp = client.delete(url_base + "/api/v1/sandboxes/" + sandbox_id)
            _ = resp.status_code
        finally:
            client.close()

    @staticmethod
    def get_sandbox_info(sandbox_id: str, api_key: Optional[str] = None, base_url: Optional[str] = None) -> SandboxInfo:
        """Return metadata for the sandbox identified by *sandbox_id*."""
        key = _resolve_api_key(api_key)
        url_base = _resolve_base_url(base_url)
        client = httpx.Client(headers=_mgmt_headers(key), timeout=30.0)
        try:
            resp = client.get(url_base + "/api/v1/sandboxes/" + sandbox_id)
            _raise_for_status(resp.status_code, resp.json() if resp.content else {})
            data = resp.json()
            return _sandbox_info_from_dict(data)
        finally:
            client.close()

    @staticmethod
    def set_sandbox_timeout(sandbox_id: str, timeout: int, api_key: Optional[str] = None, base_url: Optional[str] = None) -> None:
        """Update the lifetime of the sandbox identified by *sandbox_id* (seconds)."""
        key = _resolve_api_key(api_key)
        url_base = _resolve_base_url(base_url)
        client = httpx.Client(headers=_mgmt_headers(key), timeout=30.0)
        try:
            resp = client.post(
                url_base + "/api/v1/sandboxes/" + sandbox_id + "/timeout",
                json={"timeout": timeout},
            )
            _raise_for_status(resp.status_code, resp.json() if resp.content else {})
            data = resp.json()
        finally:
            client.close()

    @staticmethod
    def get_sandbox_metrics(sandbox_id: str, api_key: Optional[str] = None, base_url: Optional[str] = None) -> dict:
        """Return CPU and memory metrics for the sandbox identified by *sandbox_id*."""
        key = _resolve_api_key(api_key)
        url_base = _resolve_base_url(base_url)
        client = httpx.Client(headers=_mgmt_headers(key), timeout=30.0)
        try:
            resp = client.get(url_base + "/api/v1/sandboxes/" + sandbox_id + "/exec/metrics")
            _raise_for_status(resp.status_code, resp.json() if resp.content else {})
            data = resp.json()
            return data
        finally:
            client.close()

    def __enter__(self) -> "Sandbox":
        return self

    def __exit__(self, *_) -> None:
        self.kill()

    def close(self) -> None:
        """Close underlying HTTP clients without killing the sandbox."""
        self._mgmt_client.close()
        self._envd_client.close()


class AsyncSandbox:
    """Async sandbox client.  Mirrors the e2b ``AsyncSandbox`` API.

    Typical usage::

        sb = await AsyncSandbox.create(template="base")
        result = await sb.commands.run("echo hello")
        await sb.kill()
    """

    def __init__(self, config: ConnectionConfig) -> None:
        self._config = config
        self._mgmt_client = httpx.AsyncClient(
            headers=_mgmt_headers(config.api_key or ""),
            timeout=config.request_timeout,
        )
        self._envd_client = httpx.AsyncClient(timeout=config.request_timeout)
        self._files = AsyncFilesystem(config, self._envd_client)
        self._commands = AsyncCommands(config, self._envd_client)
        self._pty = AsyncPty(config, self._envd_client)

    @property
    def sandbox_id(self) -> str:
        return self._config.sandbox_id

    @property
    def files(self) -> AsyncFilesystem:
        return self._files

    @property
    def commands(self) -> AsyncCommands:
        return self._commands

    @property
    def pty(self) -> AsyncPty:
        return self._pty

    @property
    def sandbox_domain(self) -> str:
        """Bare hostname of the hermes gateway (e.g. 'localhost:8080')."""
        return _sandbox_domain(self._config.base_url)

    @classmethod
    async def create(
        cls,
        template: str = "base",
        timeout: int = 300,
        metadata: Optional[Dict[str, str]] = None,
        envs: Optional[Dict[str, str]] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> "AsyncSandbox":
        key = _resolve_api_key(api_key)
        url_base = _resolve_base_url(base_url)
        client = httpx.AsyncClient(headers=_mgmt_headers(key), timeout=30.0)
        try:
            resp = await client.post(
                url_base + "/api/v1/sandboxes",
                json={
                    "templateID": template,
                    "timeout": timeout,
                    "metadata": metadata or {},
                    "envs": envs or {},
                },
            )
            _raise_for_status(resp.status_code, resp.json() if resp.content else {})
            data = resp.json()
            sandbox_id = data.get("sandboxID") or data.get("id", "")
            access_token = data.get("envdAccessToken") or data.get("accessToken")
            config = ConnectionConfig(
                sandbox_id=sandbox_id,
                envd_url=_envd_url(url_base, sandbox_id),
                access_token=access_token,
                api_key=key,
                base_url=url_base,
                request_timeout=30.0,
            )
        finally:
            await client.aclose()
        return cls(config)

    @classmethod
    async def connect(
        cls,
        sandbox_id: str,
        timeout: float = 30.0,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> "AsyncSandbox":
        """Reconnect to an existing sandbox by ID, resuming it if paused."""
        key = _resolve_api_key(api_key)
        url_base = _resolve_base_url(base_url)
        client = httpx.AsyncClient(headers=_mgmt_headers(key), timeout=timeout)
        try:
            resp = await client.get(url_base + "/api/v1/sandboxes/" + sandbox_id)
            _raise_for_status(resp.status_code, resp.json() if resp.content else {})
            data = resp.json()
            status = data.get("status") or data.get("state", "")
            if status not in ("running", "active", ""):
                resume_resp = await client.post(
                    url_base + "/api/v1/sandboxes/" + sandbox_id + "/connect",
                    json={"timeout": timeout},
                )
                _raise_for_status(resume_resp.status_code, resume_resp.json() if resume_resp.content else {})
                resume_data = resume_resp.json()
                access_token = resume_data.get("envdAccessToken") or resume_data.get("accessToken")
            else:
                access_token = data.get("envdAccessToken") or data.get("accessToken")
            config = ConnectionConfig(
                sandbox_id=sandbox_id,
                envd_url=_envd_url(url_base, sandbox_id),
                access_token=access_token,
                api_key=key,
                base_url=url_base,
                request_timeout=30.0,
            )
        finally:
            await client.aclose()
        return cls(config)

    async def kill(self) -> None:
        """Terminate the sandbox immediately."""
        resp = await self._mgmt_client.delete(
            self._config.base_url + "/api/v1/sandboxes/" + self._config.sandbox_id,
            timeout=self._config.request_timeout,
        )
        _ = resp.status_code

    async def set_timeout(self, timeout: int) -> None:
        """Update the sandbox timeout to *timeout* seconds."""
        resp = await self._mgmt_client.post(
            self._config.base_url + "/api/v1/sandboxes/" + self._config.sandbox_id + "/timeout",
            json={"timeout": timeout},
            timeout=self._config.request_timeout,
        )
        _raise_for_status(resp.status_code, resp.json() if resp.content else {})

    async def get_info(self) -> SandboxInfo:
        """Fetch current metadata for this sandbox."""
        resp = await self._mgmt_client.get(
            self._config.base_url + "/api/v1/sandboxes/" + self._config.sandbox_id,
            timeout=self._config.request_timeout,
        )
        _raise_for_status(resp.status_code, resp.json() if resp.content else {})
        data = resp.json()
        return _sandbox_info_from_dict(data)

    async def is_running(self) -> bool:
        """Return True if the sandbox status is running/active."""
        try:
            info = await self.get_info()
            return info.state in ("running", "active", "")
        except Exception:
            return False

    def get_host(self, port: int) -> str:
        """Return the proxy hostname for *port* inside the sandbox."""
        parsed = urlparse(self._config.base_url)
        host = parsed.hostname or self._config.base_url
        return str(port) + "-" + self._config.sandbox_id + "." + host

    async def get_metrics(self) -> dict:
        """Fetch current CPU and memory usage for the sandbox."""
        resp = await self._mgmt_client.get(
            self._config.base_url + "/api/v1/sandboxes/" + self._config.sandbox_id + "/exec/metrics",
            timeout=self._config.request_timeout,
        )
        _raise_for_status(resp.status_code, resp.json() if resp.content else {})
        return resp.json()

    async def resize_disk(self, size_mb: int) -> None:
        """Resize the sandbox disk to *size_mb* megabytes.

        Sends ``PATCH /api/v1/sandboxes/:id`` with
        ``{"spec": {"storage_size": "<n>Gi"}}`` (or ``"<n>Mi"`` when *size_mb*
        is not a whole number of gibibytes).  Atlas performs an in-place PVC
        expansion — the sandbox does not restart for a pure resize.
        """
        if size_mb <= 0:
            raise ValueError("size_mb must be a positive integer")
        storage_size = _mb_to_storage_size(size_mb)
        resp = await self._mgmt_client.patch(
            self._config.base_url + "/api/v1/sandboxes/" + self._config.sandbox_id,
            json={"spec": {"storage_size": storage_size}},
            timeout=self._config.request_timeout,
        )
        _raise_for_status(resp.status_code, resp.json() if resp.content else {})

    async def download_url(
        self,
        path: str,
        *,
        user: Optional[str] = None,
        expires: int = 300,
    ) -> str:
        """Return a short-lived signed URL for directly downloading *path* from the sandbox."""
        return await self._signed_file_url(path, "download-url", user=user, expires=expires)

    async def upload_url(
        self,
        path: str,
        *,
        user: Optional[str] = None,
        expires: int = 300,
    ) -> str:
        """Return a short-lived signed URL for directly uploading a file to *path* in the sandbox."""
        return await self._signed_file_url(path, "upload-url", user=user, expires=expires)

    async def _signed_file_url(self, path: str, endpoint: str, *, user: Optional[str], expires: int) -> str:
        url = (
            self._config.base_url.rstrip("/")
            + "/api/v1/sandboxes/"
            + self._config.sandbox_id
            + "/exec/files/"
            + endpoint
            + "?path="
            + quote(path, safe="")
            + "&expires="
            + str(expires)
        )
        if user:
            url += "&username=" + quote(user, safe="")
        resp = await self._mgmt_client.get(url, timeout=self._config.request_timeout)
        _raise_for_status(resp.status_code, resp.json() if resp.content else {})
        data = resp.json()
        return data.get("url", "")

    @staticmethod
    async def list(api_key: Optional[str] = None, base_url: Optional[str] = None) -> List[SandboxInfo]:
        """Return all sandboxes visible to the given API key."""
        key = _resolve_api_key(api_key)
        url_base = _resolve_base_url(base_url)
        client = httpx.AsyncClient(headers=_mgmt_headers(key), timeout=30.0)
        try:
            resp = await client.get(url_base + "/api/v1/sandboxes")
            _raise_for_status(resp.status_code, resp.json() if resp.content else {})
            data = resp.json()
            sandboxes = data if isinstance(data, list) else data.get("sandboxes", [])
            return [_sandbox_info_from_dict(s) for s in sandboxes]
        finally:
            await client.aclose()

    @staticmethod
    async def kill_sandbox(sandbox_id: str, api_key: Optional[str] = None, base_url: Optional[str] = None) -> None:
        """Terminate the sandbox identified by *sandbox_id* without an instance."""
        key = _resolve_api_key(api_key)
        url_base = _resolve_base_url(base_url)
        client = httpx.AsyncClient(headers=_mgmt_headers(key), timeout=30.0)
        try:
            resp = await client.delete(url_base + "/api/v1/sandboxes/" + sandbox_id)
            _ = resp.status_code
        finally:
            await client.aclose()

    @staticmethod
    async def get_sandbox_info(sandbox_id: str, api_key: Optional[str] = None, base_url: Optional[str] = None) -> SandboxInfo:
        """Return metadata for the sandbox identified by *sandbox_id*."""
        key = _resolve_api_key(api_key)
        url_base = _resolve_base_url(base_url)
        client = httpx.AsyncClient(headers=_mgmt_headers(key), timeout=30.0)
        try:
            resp = await client.get(url_base + "/api/v1/sandboxes/" + sandbox_id)
            _raise_for_status(resp.status_code, resp.json() if resp.content else {})
            data = resp.json()
            return _sandbox_info_from_dict(data)
        finally:
            await client.aclose()

    @staticmethod
    async def set_sandbox_timeout(sandbox_id: str, timeout: int, api_key: Optional[str] = None, base_url: Optional[str] = None) -> None:
        """Update the lifetime of the sandbox identified by *sandbox_id* (seconds)."""
        key = _resolve_api_key(api_key)
        url_base = _resolve_base_url(base_url)
        client = httpx.AsyncClient(headers=_mgmt_headers(key), timeout=30.0)
        try:
            resp = await client.post(
                url_base + "/api/v1/sandboxes/" + sandbox_id + "/timeout",
                json={"timeout": timeout},
            )
            _raise_for_status(resp.status_code, resp.json() if resp.content else {})
        finally:
            await client.aclose()

    @staticmethod
    async def get_sandbox_metrics(sandbox_id: str, api_key: Optional[str] = None, base_url: Optional[str] = None) -> dict:
        """Return CPU and memory metrics for the sandbox identified by *sandbox_id*."""
        key = _resolve_api_key(api_key)
        url_base = _resolve_base_url(base_url)
        client = httpx.AsyncClient(headers=_mgmt_headers(key), timeout=30.0)
        try:
            resp = await client.get(url_base + "/api/v1/sandboxes/" + sandbox_id + "/exec/metrics")
            _raise_for_status(resp.status_code, resp.json() if resp.content else {})
            return resp.json()
        finally:
            await client.aclose()

    async def __aenter__(self) -> "AsyncSandbox":
        return self

    async def __aexit__(self, *_) -> None:
        await self.kill()

    async def close(self) -> None:
        """Close underlying HTTP clients without killing the sandbox."""
        try:
            await self._mgmt_client.aclose()
        except BaseException:
            pass
        try:
            await self._envd_client.aclose()
        except BaseException:
            pass
