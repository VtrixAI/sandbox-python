"""
Template and build management – wraps the sandbox-builder API.

All endpoints live under ``/api/v1/templates`` and are proxied through hermes.
Auth is ``X-API-Key`` (same as sandbox management).

Typical usage::

    client = TemplateClient(api_key="...", base_url="http://localhost:8080")

    # Create a template with a Dockerfile
    template = client.create(name="my-env", dockerfile="FROM ubuntu:22.04")

    # Poll build status
    import time
    while True:
        status = client.get_build_status(template["templateId"], template["buildId"])
        print(status["status"], status.get("logEntries", []))
        if status["status"] in ("ready", "error"):
            break
        time.sleep(2)
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx

from .exceptions import _raise_for_status

_ENV_API_KEY = "SANDBOX_API_KEY"
_ENV_BASE_URL = "SANDBOX_BASE_URL"
_DEFAULT_BASE_URL = "http://localhost:8080"


def _resolve_api_key(api_key: Optional[str]) -> str:
    key = api_key or os.environ.get(_ENV_API_KEY, "")
    if not key:
        raise ValueError("No API key provided. Set " + _ENV_API_KEY + " or pass api_key=...")
    return key


def _resolve_base_url(base_url: Optional[str]) -> str:
    return (base_url or os.environ.get(_ENV_BASE_URL, _DEFAULT_BASE_URL)).rstrip("/")


def _headers(api_key: str, namespace_id: str = "", user_id: str = "") -> dict:
    h: dict = {"X-API-Key": api_key, "Content-Type": "application/json"}
    if namespace_id:
        h["X-Namespace-ID"] = namespace_id
    if user_id:
        h["X-User-ID"] = user_id
    return h


class TemplateClient:
    """Synchronous client for the sandbox-builder template API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        namespace_id: str = "",
        user_id: str = "",
        timeout: float = 30.0,
    ) -> None:
        self._api_key = _resolve_api_key(api_key)
        self._base_url = _resolve_base_url(base_url)
        self._namespace_id = namespace_id
        self._user_id = user_id
        self._client = httpx.Client(
            headers=_headers(self._api_key, namespace_id, user_id),
            timeout=timeout,
        )

    def _url(self, path: str) -> str:
        return self._base_url + path

    def _check(self, resp: httpx.Response) -> dict:
        _raise_for_status(resp.status_code, resp.json() if resp.content else {})
        if not resp.content:
            return {}
        data = resp.json()
        return data.get("data", data)

    # ── Template CRUD ──────────────────────────────────────────────────────

    def create(
        self,
        name: str,
        visibility: str = "personal",
        dockerfile: Optional[str] = None,
        image: Optional[str] = None,
        cpu_count: int = 1,
        memory_mb: int = 512,
        envs: Optional[Dict[str, str]] = None,
        ttl_seconds: int = 300,
        storage_type: Optional[str] = None,
        storage_size_gb: Optional[int] = None,
        daemon_image: Optional[str] = None,
        cloudsink_url: Optional[str] = None,
        **kwargs: Any,
    ) -> dict:
        """Create a new template. Returns TemplateResponse dict."""
        body: dict = {
            "name": name,
            "visibility": visibility,
            "cpuCount": cpu_count,
            "memoryMB": memory_mb,
            "ttlSeconds": ttl_seconds,
        }
        if dockerfile:
            body["dockerfile"] = dockerfile
        if image:
            body["image"] = image
        if envs:
            body["envs"] = envs
        if storage_type:
            body["storageType"] = storage_type
        if storage_size_gb is not None:
            body["storageSizeGB"] = storage_size_gb
        if daemon_image:
            body["daemonImage"] = daemon_image
        if cloudsink_url:
            body["cloudsinkURL"] = cloudsink_url
        body.update(kwargs)
        resp = self._client.post(self._url("/api/v1/templates"), json=body)
        return self._check(resp)

    def list(
        self,
        visibility: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """List templates. Returns ``{"templates": [...], "total": N}``."""
        params: dict = {"limit": limit, "offset": offset}
        if visibility:
            params["visibility"] = visibility
        resp = self._client.get(self._url("/api/v1/templates"), params=params)
        return self._check(resp)

    def get(self, template_id: str) -> dict:
        """Get a template by ID (includes last 10 builds)."""
        resp = self._client.get(self._url(f"/api/v1/templates/{template_id}"))
        return self._check(resp)

    def get_by_alias(self, alias: str) -> dict:
        """Find a template by name (alias). Returns first match across official→team→personal."""
        resp = self._client.get(self._url(f"/api/v1/templates/aliases/{alias}"))
        return self._check(resp)

    def update(self, template_id: str, **fields: Any) -> dict:
        """Update template metadata. Pass any TemplateResponse fields as kwargs."""
        resp = self._client.patch(self._url(f"/api/v1/templates/{template_id}"), json=fields)
        return self._check(resp)

    def delete(self, template_id: str) -> None:
        """Soft-delete a template."""
        resp = self._client.delete(self._url(f"/api/v1/templates/{template_id}"))
        _raise_for_status(resp.status_code, resp.json() if resp.content else {})

    # ── Build operations ───────────────────────────────────────────────────

    def build(
        self,
        template_id: str,
        from_image: Optional[str] = None,
        files_hash: Optional[str] = None,
    ) -> dict:
        """Trigger a rebuild. Returns BuildResponse dict."""
        body: dict = {}
        if from_image:
            body["fromImage"] = from_image
        if files_hash is not None:
            body["filesHash"] = files_hash
        resp = self._client.post(
            self._url(f"/api/v1/templates/{template_id}/builds"), json=body
        )
        return self._check(resp)

    def rollback(self, template_id: str, build_id: str) -> dict:
        """Rollback to a previous build version (must be in ``ready`` status)."""
        resp = self._client.post(
            self._url(f"/api/v1/templates/{template_id}/rollback"),
            json={"buildId": build_id},
        )
        return self._check(resp)

    def list_builds(
        self, template_id: str, limit: int = 50, offset: int = 0
    ) -> dict:
        """List build history for a template."""
        resp = self._client.get(
            self._url(f"/api/v1/templates/{template_id}/builds"),
            params={"limit": limit, "offset": offset},
        )
        return self._check(resp)

    def get_build(self, template_id: str, build_id: str) -> dict:
        """Get a single build record."""
        resp = self._client.get(
            self._url(f"/api/v1/templates/{template_id}/builds/{build_id}")
        )
        return self._check(resp)

    def get_build_status(
        self,
        template_id: str,
        build_id: str,
        logs_offset: int = 0,
        limit: int = 100,
        level: Optional[str] = None,
    ) -> dict:
        """Poll build status and incremental logs.

        Returns dict with ``buildId``, ``status``, ``logEntries``, ``logs``, and optional ``reason``.
        Pass the previous ``len(logEntries)`` as *logs_offset* for incremental fetching.
        """
        params: dict = {"logsOffset": logs_offset, "limit": limit}
        if level:
            params["level"] = level
        resp = self._client.get(
            self._url(f"/api/v1/templates/{template_id}/builds/{build_id}/status"),
            params=params,
        )
        return self._check(resp)

    def get_build_logs(
        self,
        template_id: str,
        build_id: str,
        cursor: int = 0,
        limit: int = 100,
        direction: str = "forward",
        level: Optional[str] = None,
        source: str = "temporary",
    ) -> dict:
        """Get structured build logs with cursor-based pagination."""
        params: dict = {
            "cursor": cursor,
            "limit": limit,
            "direction": direction,
            "source": source,
        }
        if level:
            params["level"] = level
        resp = self._client.get(
            self._url(f"/api/v1/templates/{template_id}/builds/{build_id}/logs"),
            params=params,
        )
        return self._check(resp)

    def get_files_upload_url(self, template_id: str, files_hash: str) -> dict:
        """Check if a build context already exists in GCS; if not, return a presigned PUT URL.

        Returns ``{"present": True}`` or ``{"present": False, "url": "https://..."}``
        """
        resp = self._client.get(
            self._url(f"/api/v1/templates/{template_id}/files/{files_hash}")
        )
        return self._check(resp)

    @staticmethod
    def quick_build(
        project: str,
        image: str,
        tag: str,
        dockerfile: str,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
    ) -> dict:
        """Directly build an image without pre-creating a template.

        Does not require authentication. Returns ``{"templateID", "buildID", "imageFullName"}``.
        """
        url = _resolve_base_url(base_url) + "/build"
        resp = httpx.post(
            url,
            json={"project": project, "image": image, "tag": tag, "dockerfile": dockerfile},
            timeout=timeout,
        )
        _raise_for_status(resp.status_code, resp.json() if resp.content else {})
        return resp.json()

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self) -> "TemplateClient":
        return self

    def __exit__(self, *_) -> None:
        self.close()


class AsyncTemplateClient:
    """Async client for the sandbox-builder template API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        namespace_id: str = "",
        user_id: str = "",
        timeout: float = 30.0,
    ) -> None:
        self._api_key = _resolve_api_key(api_key)
        self._base_url = _resolve_base_url(base_url)
        self._client = httpx.AsyncClient(
            headers=_headers(self._api_key, namespace_id, user_id),
            timeout=timeout,
        )

    def _url(self, path: str) -> str:
        return self._base_url + path

    def _check(self, resp: httpx.Response) -> dict:
        _raise_for_status(resp.status_code, resp.json() if resp.content else {})
        if not resp.content:
            return {}
        data = resp.json()
        return data.get("data", data)

    async def create(
        self,
        name: str,
        visibility: str = "personal",
        dockerfile: Optional[str] = None,
        image: Optional[str] = None,
        cpu_count: int = 1,
        memory_mb: int = 512,
        envs: Optional[Dict[str, str]] = None,
        ttl_seconds: int = 300,
        storage_type: Optional[str] = None,
        storage_size_gb: Optional[int] = None,
        daemon_image: Optional[str] = None,
        cloudsink_url: Optional[str] = None,
        **kwargs: Any,
    ) -> dict:
        body: dict = {
            "name": name,
            "visibility": visibility,
            "cpuCount": cpu_count,
            "memoryMB": memory_mb,
            "ttlSeconds": ttl_seconds,
        }
        if dockerfile:
            body["dockerfile"] = dockerfile
        if image:
            body["image"] = image
        if envs:
            body["envs"] = envs
        if storage_type:
            body["storageType"] = storage_type
        if storage_size_gb is not None:
            body["storageSizeGB"] = storage_size_gb
        if daemon_image:
            body["daemonImage"] = daemon_image
        if cloudsink_url:
            body["cloudsinkURL"] = cloudsink_url
        body.update(kwargs)
        resp = await self._client.post(self._url("/api/v1/templates"), json=body)
        return self._check(resp)

    async def list(
        self,
        visibility: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        params: dict = {"limit": limit, "offset": offset}
        if visibility:
            params["visibility"] = visibility
        resp = await self._client.get(self._url("/api/v1/templates"), params=params)
        return self._check(resp)

    async def get(self, template_id: str) -> dict:
        resp = await self._client.get(self._url(f"/api/v1/templates/{template_id}"))
        return self._check(resp)

    async def get_by_alias(self, alias: str) -> dict:
        resp = await self._client.get(self._url(f"/api/v1/templates/aliases/{alias}"))
        return self._check(resp)

    async def update(self, template_id: str, **fields: Any) -> dict:
        resp = await self._client.patch(self._url(f"/api/v1/templates/{template_id}"), json=fields)
        return self._check(resp)

    async def delete(self, template_id: str) -> None:
        resp = await self._client.delete(self._url(f"/api/v1/templates/{template_id}"))
        _raise_for_status(resp.status_code, resp.json() if resp.content else {})

    async def build(
        self,
        template_id: str,
        from_image: Optional[str] = None,
        files_hash: Optional[str] = None,
    ) -> dict:
        body: dict = {}
        if from_image:
            body["fromImage"] = from_image
        if files_hash is not None:
            body["filesHash"] = files_hash
        resp = await self._client.post(
            self._url(f"/api/v1/templates/{template_id}/builds"), json=body
        )
        return self._check(resp)

    async def rollback(self, template_id: str, build_id: str) -> dict:
        resp = await self._client.post(
            self._url(f"/api/v1/templates/{template_id}/rollback"),
            json={"buildId": build_id},
        )
        return self._check(resp)

    async def list_builds(
        self, template_id: str, limit: int = 50, offset: int = 0
    ) -> dict:
        resp = await self._client.get(
            self._url(f"/api/v1/templates/{template_id}/builds"),
            params={"limit": limit, "offset": offset},
        )
        return self._check(resp)

    async def get_build(self, template_id: str, build_id: str) -> dict:
        resp = await self._client.get(
            self._url(f"/api/v1/templates/{template_id}/builds/{build_id}")
        )
        return self._check(resp)

    async def get_build_status(
        self,
        template_id: str,
        build_id: str,
        logs_offset: int = 0,
        limit: int = 100,
        level: Optional[str] = None,
    ) -> dict:
        params: dict = {"logsOffset": logs_offset, "limit": limit}
        if level:
            params["level"] = level
        resp = await self._client.get(
            self._url(f"/api/v1/templates/{template_id}/builds/{build_id}/status"),
            params=params,
        )
        return self._check(resp)

    async def get_build_logs(
        self,
        template_id: str,
        build_id: str,
        cursor: int = 0,
        limit: int = 100,
        direction: str = "forward",
        level: Optional[str] = None,
        source: str = "temporary",
    ) -> dict:
        params: dict = {
            "cursor": cursor,
            "limit": limit,
            "direction": direction,
            "source": source,
        }
        if level:
            params["level"] = level
        resp = await self._client.get(
            self._url(f"/api/v1/templates/{template_id}/builds/{build_id}/logs"),
            params=params,
        )
        return self._check(resp)

    async def get_files_upload_url(self, template_id: str, files_hash: str) -> dict:
        resp = await self._client.get(
            self._url(f"/api/v1/templates/{template_id}/files/{files_hash}")
        )
        return self._check(resp)

    @staticmethod
    async def quick_build(
        project: str,
        image: str,
        tag: str,
        dockerfile: str,
        base_url: Optional[str] = None,
        timeout: float = 30.0,
    ) -> dict:
        """Directly build an image without pre-creating a template.

        Does not require authentication. Returns ``{"templateID", "buildID", "imageFullName"}``.
        """
        url = _resolve_base_url(base_url) + "/build"
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                json={"project": project, "image": image, "tag": tag, "dockerfile": dockerfile},
                timeout=timeout,
            )
        _raise_for_status(resp.status_code, resp.json() if resp.content else {})
        return resp.json()

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "AsyncTemplateClient":
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()
