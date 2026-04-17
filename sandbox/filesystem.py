"""
Filesystem access to nano-executor envd API.

All RPC endpoints use the Connect protocol (Content-Type: application/json,
Connect-Protocol-Version: 1).  File upload/download uses plain HTTP.
"""

from __future__ import annotations

import base64
import queue
import threading
from datetime import datetime, timezone
from typing import (
    Any,
    Callable,
    Dict,
    Iterator,
    List,
    Literal,
    Optional,
    Union,
    IO,
)

import httpx

try:
    from httpx_sse import connect_sse, aconnect_sse
except ImportError:  # pragma: no cover
    connect_sse = None  # type: ignore[assignment]
    aconnect_sse = None  # type: ignore[assignment]

from .exceptions import _raise_for_status, NotFoundException, SandboxException
from .types import ConnectionConfig, EntryInfo, FilesystemEvent, WriteEntry, WriteInfo

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONNECT_HEADERS = {
    "Connect-Protocol-Version": "1",
    "Content-Type": "application/json",
}


def _entry_from_dict(d: dict) -> EntryInfo:
    """Convert a raw dict from the envd RPC into an EntryInfo."""
    mtime_raw = d.get("modifiedTime") or d.get("modified_time")
    mtime: Optional[datetime] = None
    if mtime_raw:
        try:
            # RFC-3339 / ISO-8601 – strip trailing 'Z' for Python <3.11
            mtime = datetime.fromisoformat(mtime_raw.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pass

    entry_type = d.get("type", "")
    # Normalise: the RPC may return "TYPE_DIRECTORY" / "TYPE_FILE" etc.
    if "DIR" in entry_type.upper() or entry_type.lower() == "dir":
        entry_type = "dir"
    elif "FILE" in entry_type.upper() or entry_type.lower() == "file":
        entry_type = "file"

    return EntryInfo(
        name=d.get("name", ""),
        type=entry_type,
        path=d.get("path", ""),
        size=int(d.get("size", 0) or 0),
        mode=int(d.get("mode", 0) or 0),
        permissions=d.get("permissions", ""),
        owner=d.get("owner", ""),
        group=d.get("group", ""),
        modified_time=mtime,
        symlink_target=d.get("symlinkTarget") or d.get("symlink_target"),
    )


def _write_info_from_dict(d: dict) -> WriteInfo:
    return WriteInfo(
        name=d.get("name", ""),
        path=d.get("path", ""),
        type=d.get("type"),
    )


# ---------------------------------------------------------------------------
# WatchHandle – wraps a background SSE watcher thread
# ---------------------------------------------------------------------------


class WatchHandle:
    """Handle returned by :meth:`Filesystem.watch_dir`.

    Background thread reads SSE events from the envd ``WatchDir`` RPC and
    pushes them into an internal queue.  Call :meth:`get_new_events` to drain
    the queue, or :meth:`stop` to cancel the watcher.
    """

    def __init__(
        self,
        thread: threading.Thread,
        stop_event: threading.Event,
        event_queue: "queue.Queue[FilesystemEvent]",
    ) -> None:
        self._thread = thread
        self._stop_event = stop_event
        self._queue: "queue.Queue[FilesystemEvent]" = event_queue

    # ------------------------------------------------------------------
    def stop(self) -> None:
        """Signal the background thread to stop and wait for it."""
        self._stop_event.set()
        self._thread.join(timeout=5)

    def get_new_events(self) -> List[FilesystemEvent]:
        """Drain and return all events accumulated since the last call."""
        events: List[FilesystemEvent] = []
        try:
            while True:
                events.append(self._queue.get_nowait())
        except queue.Empty:
            pass
        return events


# ---------------------------------------------------------------------------
# AsyncWatchHandle
# ---------------------------------------------------------------------------


class AsyncWatchHandle:
    """Async variant of :class:`WatchHandle`.

    The background coroutine is driven by :mod:`asyncio` / :mod:`anyio`.
    """

    def __init__(self, stop_event: Any, event_queue: Any) -> None:
        self._stop_event = stop_event
        self._queue = event_queue
        self._task: Optional[Any] = None

    def _set_task(self, task: Any) -> None:
        self._task = task

    async def stop(self) -> None:
        """Cancel the background watcher task."""
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass

    async def get_new_events(self) -> List[FilesystemEvent]:
        """Drain all queued events (non-blocking)."""
        import asyncio

        events: List[FilesystemEvent] = []
        while True:
            try:
                events.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return events


# ---------------------------------------------------------------------------
# Filesystem (synchronous)
# ---------------------------------------------------------------------------


class Filesystem:
    """Synchronous filesystem API backed by nano-executor envd."""

    def __init__(self, config: ConnectionConfig, client: httpx.Client) -> None:
        self._config = config
        self._client = client

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self, user: Optional[str] = None) -> Dict[str, str]:
        h: Dict[str, str] = {}
        if self._config.access_token:
            h["X-Access-Token"] = self._config.access_token
        elif self._config.api_key:
            h["X-API-Key"] = self._config.api_key
        if user:
            h["X-User"] = user
        return h

    def _rpc_headers(self, user: Optional[str] = None) -> Dict[str, str]:
        h = dict(_CONNECT_HEADERS)
        h.update(self._headers(user))
        return h

    def _url(self, path: str) -> str:
        return f"{self._config.envd_url.rstrip('/')}/{path.lstrip('/')}"

    def _rpc(
        self,
        method_path: str,
        body: dict,
        user: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> dict:
        url = self._url(method_path)
        t = timeout if timeout is not None else self._config.request_timeout
        resp = self._client.post(
            url, json=body, headers=self._rpc_headers(user), timeout=t
        )
        try:
            data = resp.json()
        except Exception:
            data = {}

        if resp.status_code == 404:
            raise NotFoundException(
                data.get("message", f"Not found: {method_path}"),
                status_code=404,
            )
        _raise_for_status(resp.status_code, data)
        return data

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read(
        self,
        path: str,
        format: Literal["text", "bytes", "stream"] = "text",
        user: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ) -> Union[str, bytearray, Iterator[bytes]]:
        """Read a file from the sandbox.

        * ``"text"``   – returns the decoded string.
        * ``"bytes"``  – returns a :class:`bytearray`.
        * ``"stream"`` – returns a lazy :class:`Iterator[bytes]`.
        """
        t = request_timeout if request_timeout is not None else self._config.request_timeout

        if format == "text":
            url = self._url("files/content")
            resp = self._client.get(
                url,
                params={"path": path},
                headers=self._headers(user),
                timeout=t,
            )
            _raise_for_status(resp.status_code, resp.json() if resp.content else {})
            payload = resp.json()
            # envd returns {"content": "<base64-or-text>"} or plain text
            if isinstance(payload, dict):
                raw = payload.get("content") or payload.get("text") or ""
                # Try base64 decode; fall back to raw string
                try:
                    return base64.b64decode(raw).decode("utf-8")
                except Exception:
                    return raw
            return str(payload)

        elif format == "bytes":
            url = self._url("files")
            resp = self._client.get(
                url,
                params={"path": path},
                headers=self._headers(user),
                timeout=t,
            )
            _raise_for_status(resp.status_code, {})
            return bytearray(resp.content)

        elif format == "stream":
            url = self._url("files")
            headers = self._headers(user)

            def _iter() -> Iterator[bytes]:
                with self._client.stream(
                    "GET", url, params={"path": path}, headers=headers, timeout=t
                ) as resp_stream:
                    _raise_for_status(resp_stream.status_code, {})
                    yield from resp_stream.iter_bytes()

            return _iter()

        else:
            raise ValueError(f"Unknown format: {format!r}")

    def write(
        self,
        path: str,
        data: Union[str, bytes, IO],
        user: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ) -> WriteInfo:
        """Write a single file into the sandbox."""
        t = request_timeout if request_timeout is not None else self._config.request_timeout
        url = self._url("files")
        headers = self._headers(user)
        headers["Content-Type"] = "application/octet-stream"

        if isinstance(data, str):
            content: bytes = data.encode("utf-8")
        elif isinstance(data, (bytes, bytearray)):
            content = bytes(data)
        else:
            content = data.read()

        resp = self._client.post(
            url,
            content=content,
            params={"path": path},
            headers=headers,
            timeout=t,
        )
        try:
            body = resp.json()
        except Exception:
            body = {}
        _raise_for_status(resp.status_code, body)
        if isinstance(body, dict):
            return _write_info_from_dict(body)
        # Some envd versions return a list with one entry
        if isinstance(body, list) and body:
            return _write_info_from_dict(body[0])
        import os
        return WriteInfo(name=os.path.basename(path), path=path)

    def write_files(
        self,
        files: List[WriteEntry],
        user: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ) -> List[WriteInfo]:
        """Write multiple files atomically via the batch endpoint."""
        t = request_timeout if request_timeout is not None else self._config.request_timeout
        url = self._url("files/batch")
        headers = self._headers(user)
        headers["Content-Type"] = "application/json"

        payload = {
            "files": [
                {
                    "path": f.path,
                    "content": base64.b64encode(
                        f.data.encode("utf-8") if isinstance(f.data, str) else bytes(f.data)
                    ).decode("ascii"),
                }
                for f in files
            ]
        }
        resp = self._client.post(url, json=payload, headers=headers, timeout=t)
        try:
            body = resp.json()
        except Exception:
            body = {}
        _raise_for_status(resp.status_code, body)
        items = body if isinstance(body, list) else body.get("files", [])
        return [_write_info_from_dict(item) for item in items]

    def list(
        self,
        path: str,
        depth: int = 1,
        user: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ) -> List[EntryInfo]:
        """List directory contents."""
        data = self._rpc(
            "filesystem.Filesystem/ListDir",
            {"path": path, "depth": depth},
            user=user,
            timeout=request_timeout,
        )
        entries = data.get("entries") or data.get("entry") or []
        if isinstance(entries, dict):
            entries = [entries]
        return [_entry_from_dict(e) for e in entries]

    def exists(
        self,
        path: str,
        user: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ) -> bool:
        """Return True if *path* exists inside the sandbox."""
        try:
            self._rpc(
                "filesystem.Filesystem/Stat",
                {"path": path},
                user=user,
                timeout=request_timeout,
            )
            return True
        except NotFoundException:
            return False

    def get_info(
        self,
        path: str,
        user: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ) -> EntryInfo:
        """Return metadata for *path*."""
        data = self._rpc(
            "filesystem.Filesystem/Stat",
            {"path": path},
            user=user,
            timeout=request_timeout,
        )
        entry = data.get("entry") or data
        return _entry_from_dict(entry)

    def remove(
        self,
        path: str,
        user: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ) -> None:
        """Delete *path* (file or empty directory) from the sandbox."""
        self._rpc(
            "filesystem.Filesystem/Remove",
            {"path": path},
            user=user,
            timeout=request_timeout,
        )

    def rename(
        self,
        old_path: str,
        new_path: str,
        user: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ) -> EntryInfo:
        """Move / rename a file or directory."""
        data = self._rpc(
            "filesystem.Filesystem/Move",
            {"source": old_path, "destination": new_path},
            user=user,
            timeout=request_timeout,
        )
        entry = data.get("entry") or data
        return _entry_from_dict(entry)

    def make_dir(
        self,
        path: str,
        user: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ) -> bool:
        """Create a directory (and all parents) inside the sandbox."""
        self._rpc(
            "filesystem.Filesystem/MakeDir",
            {"path": path},
            user=user,
            timeout=request_timeout,
        )
        return True

    def edit(
        self,
        path: str,
        old_text: str,
        new_text: str,
        user: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ) -> None:
        """In-place text substitution in the file at *path*.

        *old_text* must appear exactly once; if it is absent or appears more
        than once the server returns HTTP 422.
        """
        self._rpc(
            "filesystem.Filesystem/Edit",
            {"path": path, "oldText": old_text, "newText": new_text},
            user=user,
            timeout=request_timeout,
        )

    def watch_dir(
        self,
        path: str,
        user: Optional[str] = None,
        request_timeout: Optional[float] = None,
        recursive: bool = False,
        on_exit: Optional[Callable[[], None]] = None,
    ) -> WatchHandle:
        """Watch *path* for filesystem events.

        Starts a background daemon thread that consumes the SSE stream from
        the ``WatchDir`` RPC and pushes events onto an internal queue.

        Returns a :class:`WatchHandle` that the caller can use to drain
        events or cancel the watcher.  *on_exit* is called when the watcher
        stream closes (either via :meth:`WatchHandle.stop` or server-side).
        """
        if connect_sse is None:
            raise ImportError("httpx-sse is required for watch_dir")

        event_queue: "queue.Queue[FilesystemEvent]" = queue.Queue()
        stop_event = threading.Event()
        url = self._url("filesystem.Filesystem/WatchDir")
        headers = self._rpc_headers(user)
        body = {"path": path, "recursive": recursive}

        def _worker() -> None:
            try:
                with connect_sse(
                    self._client,
                    "POST",
                    url,
                    json=body,
                    headers=headers,
                    timeout=None,
                ) as event_source:
                    for sse in event_source.iter_sse():
                        if stop_event.is_set():
                            break
                        if sse.data:
                            try:
                                import json as _json
                                d = _json.loads(sse.data)
                                fs = d.get("filesystem")
                                if not fs:
                                    continue
                                ev = FilesystemEvent(
                                    name=fs.get("name", ""),
                                    type=fs.get("type", ""),
                                )
                                event_queue.put(ev)
                            except Exception:
                                pass
            except Exception:
                pass
            finally:
                if on_exit is not None:
                    on_exit()

        t = threading.Thread(target=_worker, daemon=True)
        handle = WatchHandle(thread=t, stop_event=stop_event, event_queue=event_queue)
        t.start()
        return handle


# ---------------------------------------------------------------------------
# AsyncFilesystem
# ---------------------------------------------------------------------------


class AsyncFilesystem:
    """Async filesystem API backed by nano-executor envd."""

    def __init__(self, config: ConnectionConfig, client: httpx.AsyncClient) -> None:
        self._config = config
        self._client = client

    # ------------------------------------------------------------------
    def _headers(self, user: Optional[str] = None) -> Dict[str, str]:
        h: Dict[str, str] = {}
        if self._config.access_token:
            h["X-Access-Token"] = self._config.access_token
        elif self._config.api_key:
            h["X-API-Key"] = self._config.api_key
        if user:
            h["X-User"] = user
        return h

    def _rpc_headers(self, user: Optional[str] = None) -> Dict[str, str]:
        h = dict(_CONNECT_HEADERS)
        h.update(self._headers(user))
        return h

    def _url(self, path: str) -> str:
        return f"{self._config.envd_url.rstrip('/')}/{path.lstrip('/')}"

    async def _rpc(
        self,
        method_path: str,
        body: dict,
        user: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> dict:
        url = self._url(method_path)
        t = timeout if timeout is not None else self._config.request_timeout
        resp = await self._client.post(
            url, json=body, headers=self._rpc_headers(user), timeout=t
        )
        try:
            data = resp.json()
        except Exception:
            data = {}
        if resp.status_code == 404:
            raise NotFoundException(
                data.get("message", f"Not found: {method_path}"),
                status_code=404,
            )
        _raise_for_status(resp.status_code, data)
        return data

    # ------------------------------------------------------------------

    async def read(
        self,
        path: str,
        format: Literal["text", "bytes", "stream"] = "text",
        user: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ) -> Union[str, bytearray]:
        t = request_timeout if request_timeout is not None else self._config.request_timeout

        if format == "text":
            url = self._url("files/content")
            resp = await self._client.get(
                url, params={"path": path}, headers=self._headers(user), timeout=t
            )
            _raise_for_status(resp.status_code, resp.json() if resp.content else {})
            payload = resp.json()
            if isinstance(payload, dict):
                raw = payload.get("content") or payload.get("text") or ""
                try:
                    return base64.b64decode(raw).decode("utf-8")
                except Exception:
                    return raw
            return str(payload)

        elif format == "bytes":
            url = self._url("files")
            resp = await self._client.get(
                url, params={"path": path}, headers=self._headers(user), timeout=t
            )
            _raise_for_status(resp.status_code, {})
            return bytearray(resp.content)

        elif format == "stream":
            # For async, materialise into bytearray (true streaming needs aiter)
            url = self._url("files")
            chunks: List[bytes] = []
            async with self._client.stream(
                "GET", url, params={"path": path}, headers=self._headers(user), timeout=t
            ) as resp_stream:
                _raise_for_status(resp_stream.status_code, {})
                async for chunk in resp_stream.aiter_bytes():
                    chunks.append(chunk)
            return bytearray(b"".join(chunks))

        else:
            raise ValueError(f"Unknown format: {format!r}")

    async def write(
        self,
        path: str,
        data: Union[str, bytes, IO],
        user: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ) -> WriteInfo:
        t = request_timeout if request_timeout is not None else self._config.request_timeout
        url = self._url("files")
        headers = self._headers(user)
        headers["Content-Type"] = "application/octet-stream"

        if isinstance(data, str):
            content: bytes = data.encode("utf-8")
        elif isinstance(data, (bytes, bytearray)):
            content = bytes(data)
        else:
            content = data.read()

        resp = await self._client.post(
            url, content=content, params={"path": path}, headers=headers, timeout=t
        )
        try:
            body = resp.json()
        except Exception:
            body = {}
        _raise_for_status(resp.status_code, body)
        if isinstance(body, dict):
            return _write_info_from_dict(body)
        if isinstance(body, list) and body:
            return _write_info_from_dict(body[0])
        import os
        return WriteInfo(name=os.path.basename(path), path=path)

    async def write_files(
        self,
        files: List[WriteEntry],
        user: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ) -> List[WriteInfo]:
        t = request_timeout if request_timeout is not None else self._config.request_timeout
        url = self._url("files/batch")
        headers = self._headers(user)
        headers["Content-Type"] = "application/json"
        payload = {
            "files": [
                {
                    "path": f.path,
                    "content": base64.b64encode(
                        f.data.encode("utf-8") if isinstance(f.data, str) else bytes(f.data)
                    ).decode("ascii"),
                }
                for f in files
            ]
        }
        resp = await self._client.post(url, json=payload, headers=headers, timeout=t)
        try:
            body = resp.json()
        except Exception:
            body = {}
        _raise_for_status(resp.status_code, body)
        items = body if isinstance(body, list) else body.get("files", [])
        return [_write_info_from_dict(item) for item in items]

    async def list(
        self,
        path: str,
        depth: int = 1,
        user: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ) -> List[EntryInfo]:
        data = await self._rpc(
            "filesystem.Filesystem/ListDir",
            {"path": path, "depth": depth},
            user=user,
            timeout=request_timeout,
        )
        entries = data.get("entries") or data.get("entry") or []
        if isinstance(entries, dict):
            entries = [entries]
        return [_entry_from_dict(e) for e in entries]

    async def exists(
        self,
        path: str,
        user: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ) -> bool:
        try:
            await self._rpc(
                "filesystem.Filesystem/Stat",
                {"path": path},
                user=user,
                timeout=request_timeout,
            )
            return True
        except NotFoundException:
            return False

    async def get_info(
        self,
        path: str,
        user: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ) -> EntryInfo:
        data = await self._rpc(
            "filesystem.Filesystem/Stat",
            {"path": path},
            user=user,
            timeout=request_timeout,
        )
        entry = data.get("entry") or data
        return _entry_from_dict(entry)

    async def remove(
        self,
        path: str,
        user: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ) -> None:
        await self._rpc(
            "filesystem.Filesystem/Remove",
            {"path": path},
            user=user,
            timeout=request_timeout,
        )

    async def rename(
        self,
        old_path: str,
        new_path: str,
        user: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ) -> EntryInfo:
        data = await self._rpc(
            "filesystem.Filesystem/Move",
            {"source": old_path, "destination": new_path},
            user=user,
            timeout=request_timeout,
        )
        entry = data.get("entry") or data
        return _entry_from_dict(entry)

    async def make_dir(
        self,
        path: str,
        user: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ) -> bool:
        await self._rpc(
            "filesystem.Filesystem/MakeDir",
            {"path": path},
            user=user,
            timeout=request_timeout,
        )
        return True

    async def edit(
        self,
        path: str,
        old_text: str,
        new_text: str,
        user: Optional[str] = None,
        request_timeout: Optional[float] = None,
    ) -> None:
        """In-place text substitution in the file at *path*.

        *old_text* must appear exactly once; if it is absent or appears more
        than once the server returns HTTP 422.
        """
        await self._rpc(
            "filesystem.Filesystem/Edit",
            {"path": path, "oldText": old_text, "newText": new_text},
            user=user,
            timeout=request_timeout,
        )

    async def watch_dir(
        self,
        path: str,
        user: Optional[str] = None,
        request_timeout: Optional[float] = None,
        recursive: bool = False,
    ) -> AsyncWatchHandle:
        """Watch *path* asynchronously for filesystem events.

        Requires :mod:`asyncio` and ``httpx-sse``.
        """
        if aconnect_sse is None:
            raise ImportError("httpx-sse is required for watch_dir")

        import asyncio

        event_queue: asyncio.Queue[FilesystemEvent] = asyncio.Queue()
        stop_event = asyncio.Event()
        url = self._url("filesystem.Filesystem/WatchDir")
        headers = self._rpc_headers(user)
        body = {"path": path, "recursive": recursive}
        handle = AsyncWatchHandle(stop_event=stop_event, event_queue=event_queue)

        async def _worker() -> None:
            try:
                async with aconnect_sse(
                    self._client, "POST", url, json=body, headers=headers, timeout=None
                ) as event_source:
                    async for sse in event_source.aiter_sse():
                        if stop_event.is_set():
                            break
                        if sse.data:
                            try:
                                import json as _json
                                d = _json.loads(sse.data)
                                fs = d.get("filesystem")
                                if not fs:
                                    continue
                                ev = FilesystemEvent(
                                    name=fs.get("name", ""),
                                    type=fs.get("type", ""),
                                )
                                await event_queue.put(ev)
                            except Exception:
                                pass
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        task = asyncio.ensure_future(_worker())
        handle._set_task(task)
        return handle
