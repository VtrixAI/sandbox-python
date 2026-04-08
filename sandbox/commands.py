"""
Process / command execution and PTY support.

Uses the nano-executor envd Connect-RPC endpoints:
  /process.Process/Start
  /process.Process/Connect
  /process.Process/List
  /process.Process/SendSignal
  /process.Process/SendInput
  /process.Process/GetResult
  /process.Process/Update

All stdout/stderr/pty data in SSE frames is base64-encoded.
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
from typing import (
    Any,
    Callable,
    Dict,
    Iterator,
    List,
    Optional,
    Union,
)

import httpx

from .exceptions import _raise_for_status, CommandExitException, SandboxException, TimeoutException
from .types import CommandResult, ConnectionConfig, ProcessInfo, PtySize

_CONNECT_HEADERS = {
    "Connect-Protocol-Version": "1",
    "Content-Type": "application/json",
}

# ---------------------------------------------------------------------------
# SSE parsing helpers (raw httpx streaming, no httpx-sse dependency needed)
# ---------------------------------------------------------------------------


def _parse_sse_chunk(chunk: str) -> Optional[dict]:
    """Extract the JSON payload from a single SSE chunk (``data: {...}`` lines)."""
    for line in chunk.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            raw = line[5:].strip()
            if not raw or raw == "[DONE]":
                return None
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return None
    return None


def _iter_sse_dicts(response: httpx.Response) -> Iterator[dict]:
    """Yield parsed SSE data dicts from a streaming *response*."""
    buffer = ""
    try:
        for chunk in response.iter_text():
            buffer += chunk
            while "\n\n" in buffer:
                chunk_text, buffer = buffer.split("\n\n", 1)
                d = _parse_sse_chunk(chunk_text)
                if d is not None:
                    yield d
    except Exception:
        # Server closed connection without a clean chunked-terminator; treat as EOF.
        pass


async def _aiter_sse_dicts(response: httpx.Response) -> Any:
    """Async generator yielding parsed SSE data dicts."""
    buffer = ""
    try:
        async for chunk in response.aiter_text():
            buffer += chunk
            while "\n\n" in buffer:
                chunk_text, buffer = buffer.split("\n\n", 1)
                d = _parse_sse_chunk(chunk_text)
                if d is not None:
                    yield d
    except Exception:
        # Server closed connection without a clean chunked-terminator; treat as EOF.
        pass


# ---------------------------------------------------------------------------
# CommandHandle (sync)
# ---------------------------------------------------------------------------


class CommandHandle:
    """Handle to a running background process.

    Returned by :meth:`Commands.run` when ``background=True`` and by
    :meth:`Commands.connect`.
    """

    def __init__(
        self,
        pid: int,
        cmd_id: str,
        config: ConnectionConfig,
        client: httpx.Client,
        _tag: Optional[str] = None,
        _drain_thread: Optional[threading.Thread] = None,
        _result_holder: Optional[list] = None,
        _result_event: Optional[threading.Event] = None,
    ) -> None:
        self._pid = pid
        self._cmd_id = cmd_id
        self._config = config
        self._client = client
        self._tag = _tag
        self._done = False
        self._result: Optional[CommandResult] = None
        # Used by RunBackground to drain the Start SSE stream in the background
        self._drain_thread = _drain_thread
        self._result_holder = _result_holder  # list of length 1, holds CommandResult
        self._result_event = _result_event    # set when draining is complete

    @property
    def pid(self) -> int:
        return self._pid

    # ------------------------------------------------------------------
    def _headers(self) -> Dict[str, str]:
        h = dict(_CONNECT_HEADERS)
        if self._config.access_token:
            h["X-Access-Token"] = self._config.access_token
        elif self._config.api_key:
            h["X-API-Key"] = self._config.api_key
        return h

    def _url(self, path: str) -> str:
        return f"{self._config.envd_url.rstrip('/')}/{path.lstrip('/')}"

    # ------------------------------------------------------------------
    def wait(
        self,
        on_stdout: Optional[Callable[[str], None]] = None,
        on_stderr: Optional[Callable[[str], None]] = None,
    ) -> CommandResult:
        """Block until the process exits and return a :class:`CommandResult`."""
        if self._result is not None:
            return self._result

        # If we have a background drain thread (set by run(..., background=True)),
        # wait for it to finish and use its result directly — mirrors Go SDK.
        if self._result_event is not None and self._result_holder is not None:
            self._result_event.wait()
            if self._result_holder:
                self._result = self._result_holder[0]
                self._done = True
                if self._result.exit_code != 0:
                    raise CommandExitException(
                        f"Process exited with code {self._result.exit_code}",
                        exit_code=self._result.exit_code,
                        stdout=self._result.stdout,
                        stderr=self._result.stderr,
                    )
                return self._result

        # Fallback: use Connect endpoint (for handles created by Commands.connect())
        stdout_parts: List[str] = []
        stderr_parts: List[str] = []
        exit_code = -1
        error_msg: Optional[str] = None

        url = self._url("process.Process/Connect")
        if self._tag:
            body: dict = {"process": {"tag": self._tag}}
        else:
            body = {"process": {"pid": self._pid}}

        with self._client.stream(
            "POST",
            url,
            json=body,
            headers=self._headers(),
            timeout=None,
        ) as resp:
            _raise_for_status(resp.status_code, {})
            for d in _iter_sse_dicts(resp):
                event = d.get("event") or {}
                _process_sse_event(
                    d,
                    stdout_parts,
                    stderr_parts,
                    on_stdout=on_stdout,
                    on_stderr=on_stderr,
                )
                if event.get("end") is not None:
                    exit_code = _parse_exit_code(event["end"].get("status", ""))
                    break

        self._result = CommandResult(
            stdout="".join(stdout_parts),
            stderr="".join(stderr_parts),
            exit_code=exit_code,
            error=error_msg,
        )
        self._done = True
        if self._result.exit_code != 0:
            raise CommandExitException(
                f"Process exited with code {self._result.exit_code}",
                exit_code=self._result.exit_code,
                stdout=self._result.stdout,
                stderr=self._result.stderr,
            )
        return self._result

    def _get_result_exit_code(self, fallback: int) -> int:
        try:
            url = self._url("process.Process/GetResult")
            resp = self._client.post(
                url,
                json={"cmdId": self._cmd_id},
                headers=self._headers(),
                timeout=self._config.request_timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                code = data.get("exitCode") or data.get("exit_code")
                if code is not None:
                    return int(code)
        except Exception:
            pass
        return fallback

    def kill(self) -> bool:
        """Send SIGKILL to the process."""
        url = self._url("process.Process/SendSignal")
        resp = self._client.post(
            url,
            json={"process": {"pid": self._pid}, "signal": "SIGKILL"},
            headers=self._headers(),
            timeout=self._config.request_timeout,
        )
        return resp.status_code < 400

    def send_stdin(self, data: str) -> None:
        """Write *data* to this process's stdin."""
        url = self._url("process.Process/SendInput")
        encoded = base64.b64encode(data.encode("utf-8")).decode("ascii")
        self._client.post(
            url,
            json={"process": {"pid": self._pid}, "input": {"stdin": encoded}},
            headers=self._headers(),
            timeout=self._config.request_timeout,
        )

    def disconnect(self) -> None:
        """Detach from the process without killing it."""
        # Nothing to do for the sync client; just mark as done.
        self._done = True

    def __iter__(self) -> Iterator[dict]:
        """Yield raw SSE event dicts from the process output stream."""
        url = self._url("process.Process/Connect")
        if self._tag:
            body: dict = {"process": {"tag": self._tag}}
        else:
            body = {"process": {"pid": self._pid}}
        with self._client.stream(
            "POST", url, json=body, headers=self._headers(), timeout=None
        ) as resp:
            _raise_for_status(resp.status_code, {})
            yield from _iter_sse_dicts(resp)


# ---------------------------------------------------------------------------
# AsyncCommandHandle
# ---------------------------------------------------------------------------


class AsyncCommandHandle:
    """Async handle to a running background process."""

    def __init__(
        self,
        pid: int,
        cmd_id: str,
        config: ConnectionConfig,
        client: httpx.AsyncClient,
        _tag: Optional[str] = None,
        _result_future: Optional[Any] = None,  # asyncio.Future[CommandResult]
        _drain_task: Optional[Any] = None,     # asyncio.Task for draining the SSE stream
    ) -> None:
        self._pid = pid
        self._cmd_id = cmd_id
        self._config = config
        self._client = client
        self._tag = _tag
        self._done = False
        self._result: Optional[CommandResult] = None
        self._result_future = _result_future  # set by AsyncCommands.run background
        self._drain_task = _drain_task        # set by AsyncCommands.run background

    @property
    def pid(self) -> int:
        return self._pid

    def _headers(self) -> Dict[str, str]:
        h = dict(_CONNECT_HEADERS)
        if self._config.access_token:
            h["X-Access-Token"] = self._config.access_token
        elif self._config.api_key:
            h["X-API-Key"] = self._config.api_key
        return h

    def _url(self, path: str) -> str:
        return f"{self._config.envd_url.rstrip('/')}/{path.lstrip('/')}"

    async def wait(
        self,
        on_stdout: Optional[Callable[[str], None]] = None,
        on_stderr: Optional[Callable[[str], None]] = None,
    ) -> CommandResult:
        if self._result is not None:
            return self._result

        # If we have a result future (set by run(..., background=True)),
        # wait for it and return the result — mirrors Go SDK.
        if self._result_future is not None:
            self._result = await self._result_future
            self._done = True
            if self._result.exit_code != 0:
                raise CommandExitException(
                    f"Process exited with code {self._result.exit_code}",
                    exit_code=self._result.exit_code,
                    stdout=self._result.stdout,
                    stderr=self._result.stderr,
                )
            return self._result

        # Fallback: use Connect endpoint (for handles created by AsyncCommands.connect())
        stdout_parts: List[str] = []
        stderr_parts: List[str] = []
        exit_code = -1

        url = self._url("process.Process/Connect")
        if self._tag:
            body: dict = {"process": {"tag": self._tag}}
        else:
            body = {"process": {"pid": self._pid}}
        async with self._client.stream(
            "POST", url, json=body, headers=self._headers(), timeout=None
        ) as resp:
            _raise_for_status(resp.status_code, {})
            async for d in _aiter_sse_dicts(resp):
                event = d.get("event") or {}
                _process_sse_event(d, stdout_parts, stderr_parts, on_stdout, on_stderr)
                if event.get("end") is not None:
                    exit_code = _parse_exit_code(event["end"].get("status", ""))
                    break

        self._result = CommandResult(
            stdout="".join(stdout_parts),
            stderr="".join(stderr_parts),
            exit_code=exit_code,
        )
        self._done = True
        if self._result.exit_code != 0:
            raise CommandExitException(
                f"Process exited with code {self._result.exit_code}",
                exit_code=self._result.exit_code,
                stdout=self._result.stdout,
                stderr=self._result.stderr,
            )
        return self._result

    async def _get_result_exit_code(self, fallback: int) -> int:
        try:
            url = self._url("process.Process/GetResult")
            resp = await self._client.post(
                url,
                json={"cmdId": self._cmd_id},
                headers=self._headers(),
                timeout=self._config.request_timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                code = data.get("exitCode") or data.get("exit_code")
                if code is not None:
                    return int(code)
        except Exception:
            pass
        return fallback

    async def kill(self) -> bool:
        url = self._url("process.Process/SendSignal")
        resp = await self._client.post(
            url,
            json={"process": {"pid": self._pid}, "signal": "SIGKILL"},
            headers=self._headers(),
            timeout=self._config.request_timeout,
        )
        ok = resp.status_code < 400
        # Wait for the drain task to complete so the connection is released cleanly.
        if self._drain_task is not None and not self._drain_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._drain_task), timeout=5.0)
            except Exception:
                pass
        return ok

    async def send_stdin(self, data: str) -> None:
        """Write *data* to this process's stdin."""
        url = self._url("process.Process/SendInput")
        encoded = base64.b64encode(data.encode("utf-8")).decode("ascii")
        await self._client.post(
            url,
            json={"process": {"pid": self._pid}, "input": {"stdin": encoded}},
            headers=self._headers(),
            timeout=self._config.request_timeout,
        )

    def disconnect(self) -> None:
        self._done = True

    async def __aiter__(self):
        url = self._url("process.Process/Connect")
        if self._tag:
            body: dict = {"process": {"tag": self._tag}}
        else:
            body = {"process": {"pid": self._pid}}
        async with self._client.stream(
            "POST", url, json=body, headers=self._headers(), timeout=None
        ) as resp:
            _raise_for_status(resp.status_code, {})
            async for d in _aiter_sse_dicts(resp):
                yield d


# ---------------------------------------------------------------------------
# Shared SSE event processing helper
# ---------------------------------------------------------------------------


def _process_sse_event(
    d: dict,
    stdout_parts: List[str],
    stderr_parts: List[str],
    on_stdout: Optional[Callable[[str], None]] = None,
    on_stderr: Optional[Callable[[str], None]] = None,
) -> None:
    """Decode one SSE payload dict and accumulate stdout/stderr.

    Wire format: {"event": {"data": {"stdout": "<b64>", "stderr": "<b64>", "pty": "<b64>"}}}
    """
    event = d.get("event") or {}
    data = event.get("data") or {}

    # stdout
    raw_out = data.get("stdout") or data.get("pty")
    if raw_out:
        try:
            text = base64.b64decode(raw_out).decode("utf-8", errors="replace")
        except Exception:
            text = str(raw_out)
        stdout_parts.append(text)
        if on_stdout:
            on_stdout(text)

    # stderr
    raw_err = data.get("stderr")
    if raw_err:
        try:
            text = base64.b64decode(raw_err).decode("utf-8", errors="replace")
        except Exception:
            text = str(raw_err)
        stderr_parts.append(text)
        if on_stderr:
            on_stderr(text)


def _parse_exit_code(status: str) -> int:
    """Parse exit code from a status string like 'exit status 1'."""
    if not status or status == "exit status 0":
        return 0
    parts = status.split()
    if parts:
        try:
            return int(parts[-1])
        except ValueError:
            pass
    return 1


def _build_start_body(
    cmd: str,
    envs: Optional[Dict[str, str]],
    user: Optional[str],
    cwd: Optional[str],
    timeout: Optional[float],
    pty_size: Optional[PtySize] = None,
    tag: Optional[str] = None,
) -> dict:
    """Build the JSON body for Process/Start."""
    process: dict = {
        "cmd": "/bin/bash",
        "args": ["-c", cmd],
    }
    if envs:
        process["envs"] = envs
    if cwd:
        process["cwd"] = cwd
    if tag:
        process["tag"] = tag
    body: dict = {"process": process}
    if timeout is not None and timeout != 0:
        body["timeout"] = int(timeout)
    if pty_size is not None:
        body["pty"] = {"size": {"rows": pty_size.rows, "cols": pty_size.cols}}
    return body


# ---------------------------------------------------------------------------
# Commands (synchronous)
# ---------------------------------------------------------------------------


class Commands:
    """Synchronous command execution API."""

    def __init__(self, config: ConnectionConfig, client: httpx.Client) -> None:
        self._config = config
        self._client = client

    def _headers(self) -> Dict[str, str]:
        h = dict(_CONNECT_HEADERS)
        if self._config.access_token:
            h["X-Access-Token"] = self._config.access_token
        elif self._config.api_key:
            h["X-API-Key"] = self._config.api_key
        return h

    def _url(self, path: str) -> str:
        return f"{self._config.envd_url.rstrip('/')}/{path.lstrip('/')}"

    # ------------------------------------------------------------------

    def run(
        self,
        cmd: str,
        background: bool = False,
        envs: Optional[Dict[str, str]] = None,
        user: Optional[str] = None,
        cwd: Optional[str] = None,
        on_stdout: Optional[Callable[[str], None]] = None,
        on_stderr: Optional[Callable[[str], None]] = None,
        stdin: bool = False,
        timeout: float = 60,
        request_timeout: Optional[float] = None,
        tag: Optional[str] = None,
    ) -> Union[CommandResult, CommandHandle]:
        """Run *cmd* inside the sandbox.

        If ``background=False`` (default), block until completion and return a
        :class:`CommandResult`.  If ``background=True``, return a
        :class:`CommandHandle` after the process has started.
        """
        url = self._url("process.Process/Start")
        body = _build_start_body(cmd, envs, user, cwd, timeout, tag=tag)

        stdout_parts: List[str] = []
        stderr_parts: List[str] = []
        pid: Optional[int] = None
        cmd_id: Optional[str] = None
        exit_code = -1

        # For background mode we must keep the stream open after returning the handle.
        # Use stream() without a context manager by calling enter/exit manually.
        stream_ctx = self._client.stream(
            "POST",
            url,
            json=body,
            headers=self._headers(),
            timeout=None,
        )
        resp = stream_ctx.__enter__()
        try:
            _raise_for_status(resp.status_code, {})

            if background:
                # For background mode: manually read chunks until we see the start event,
                # then hand off the SAME iterator to a drain thread.
                # httpx marks the stream as consumed after the first iter_text() call,
                # so we must reuse the same iterator object throughout.
                text_iter = resp.iter_text()
                buffer = ""
                for chunk in text_iter:
                    buffer += chunk
                    while "\n\n" in buffer:
                        chunk_text, buffer = buffer.split("\n\n", 1)
                        d = _parse_sse_chunk(chunk_text)
                        if d is None:
                            continue
                        event = d.get("event") or {}
                        if event.get("start"):
                            start = event["start"]
                            pid = start.get("pid")
                            cmd_id = start.get("cmdId") or start.get("cmd_id") or ""
                            if pid is not None:
                                break
                    if pid is not None:
                        break

                if pid is None:
                    stream_ctx.__exit__(None, None, None)
                    raise SandboxException("Process start did not return a PID")

                result_holder: List[CommandResult] = []
                result_event = threading.Event()

                handle = CommandHandle(
                    pid=int(pid),
                    cmd_id=str(cmd_id or ""),
                    config=self._config,
                    client=self._client,
                    _result_holder=result_holder,
                    _result_event=result_event,
                )

                # Pass leftover buffer + the SAME iterator to drain thread
                bg_stdout: List[str] = []
                bg_stderr: List[str] = []
                leftover = buffer

                def _drain(text_it: Any, ctx: Any, initial_buf: str, holder: List, evt: threading.Event, outh: List[str], errh: List[str]) -> None:
                    try:
                        buf = initial_buf
                        # Process any complete events already in the leftover buffer
                        while "\n\n" in buf:
                            chunk_text, buf = buf.split("\n\n", 1)
                            dd = _parse_sse_chunk(chunk_text)
                            if dd is None:
                                continue
                            ev = dd.get("event") or {}
                            _process_sse_event(dd, outh, errh)
                            if ev.get("end") is not None:
                                ec = _parse_exit_code(ev["end"].get("status", ""))
                                holder.append(CommandResult(
                                    stdout="".join(outh),
                                    stderr="".join(errh),
                                    exit_code=ec,
                                ))
                                return
                        # Continue reading from the SAME iterator (not a new one)
                        for new_chunk in text_it:
                            buf += new_chunk
                            while "\n\n" in buf:
                                chunk_text, buf = buf.split("\n\n", 1)
                                dd = _parse_sse_chunk(chunk_text)
                                if dd is None:
                                    continue
                                ev = dd.get("event") or {}
                                _process_sse_event(dd, outh, errh)
                                if ev.get("end") is not None:
                                    ec = _parse_exit_code(ev["end"].get("status", ""))
                                    holder.append(CommandResult(
                                        stdout="".join(outh),
                                        stderr="".join(errh),
                                        exit_code=ec,
                                    ))
                                    return
                    except Exception:
                        pass
                    finally:
                        try:
                            ctx.__exit__(None, None, None)
                        except Exception:
                            pass
                        evt.set()
                    if not holder:
                        holder.append(CommandResult(stdout="".join(outh), stderr="".join(errh), exit_code=-1))

                t = threading.Thread(
                    target=_drain,
                    args=(text_iter, stream_ctx, leftover, result_holder, result_event, bg_stdout, bg_stderr),
                    daemon=True,
                )
                t.start()
                handle._drain_thread = t
                return handle

            # Foreground mode: use generator-based iteration
            for d in _iter_sse_dicts(resp):
                # All events: {"event": {"start": {...}, "data": {...}, "end": {...}, "keepalive": {}}}
                event = d.get("event") or {}

                if pid is None and event.get("start"):
                    start = event["start"]
                    pid = start.get("pid")
                    cmd_id = start.get("cmdId") or start.get("cmd_id") or ""
                    continue

                _process_sse_event(d, stdout_parts, stderr_parts, on_stdout, on_stderr)

                if event.get("end") is not None:
                    end = event["end"]
                    exit_code = _parse_exit_code(end.get("status", ""))
                    break
        finally:
            # For foreground mode only — background mode lets the drain thread close it.
            if not (background and pid is not None):
                stream_ctx.__exit__(None, None, None)

        result = CommandResult(
            stdout="".join(stdout_parts),
            stderr="".join(stderr_parts),
            exit_code=exit_code,
        )
        if result.exit_code != 0:
            raise CommandExitException(
                f"Process exited with code {result.exit_code}",
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        return result

    def connect(
        self,
        pid: int,
        timeout: float = 60,
        request_timeout: Optional[float] = None,
    ) -> CommandHandle:
        """Attach to an already-running process by PID."""
        return CommandHandle(
            pid=pid,
            cmd_id="",
            config=self._config,
            client=self._client,
        )

    def list(self, request_timeout: Optional[float] = None) -> List[ProcessInfo]:
        """Return a list of running processes."""
        url = self._url("process.Process/List")
        t = request_timeout if request_timeout is not None else self._config.request_timeout
        resp = self._client.post(url, json={}, headers=self._headers(), timeout=t)
        try:
            data = resp.json()
        except Exception:
            data = {}
        _raise_for_status(resp.status_code, data)
        processes = data.get("processes") or data.get("process") or []
        if isinstance(processes, dict):
            processes = [processes]
        return [
            ProcessInfo(
                pid=int(p.get("pid", 0)),
                cmd=(p.get("config") or {}).get("cmd", ""),
                args=(p.get("config") or {}).get("args", []),
                cwd=(p.get("config") or {}).get("cwd"),
                envs=(p.get("config") or {}).get("envs") or {},
                tag=p.get("tag"),
            )
            for p in processes
        ]

    def kill(self, pid: int, request_timeout: Optional[float] = None) -> bool:
        """Send SIGKILL to process *pid*."""
        url = self._url("process.Process/SendSignal")
        t = request_timeout if request_timeout is not None else self._config.request_timeout
        resp = self._client.post(
            url,
            json={"process": {"pid": pid}, "signal": "SIGKILL"},
            headers=self._headers(),
            timeout=t,
        )
        return resp.status_code < 400

    def send_stdin(
        self, pid: int, data: str, request_timeout: Optional[float] = None
    ) -> None:
        """Write *data* to the stdin of process *pid*."""
        url = self._url("process.Process/SendInput")
        t = request_timeout if request_timeout is not None else self._config.request_timeout
        encoded = base64.b64encode(data.encode("utf-8")).decode("ascii")
        self._client.post(
            url,
            json={"process": {"pid": pid}, "input": {"stdin": encoded}},
            headers=self._headers(),
            timeout=t,
        )

    def close_stdin(self, pid: int, request_timeout: Optional[float] = None) -> None:
        """Close stdin of process *pid* (triggers EOF)."""
        url = self._url("process.Process/CloseStdin")
        t = request_timeout if request_timeout is not None else self._config.request_timeout
        self._client.post(
            url,
            json={"process": {"pid": pid}},
            headers=self._headers(),
            timeout=t,
        )

    def send_signal(
        self, pid: int, signal: str, request_timeout: Optional[float] = None
    ) -> None:
        """Send *signal* (e.g. ``"SIGTERM"``) to process *pid*."""
        url = self._url("process.Process/SendSignal")
        t = request_timeout if request_timeout is not None else self._config.request_timeout
        self._client.post(
            url,
            json={"process": {"pid": pid}, "signal": signal},
            headers=self._headers(),
            timeout=t,
        )

    def connect_by_tag(
        self,
        tag: str,
        timeout: float = 60,
        request_timeout: Optional[float] = None,
    ) -> "CommandHandle":
        """Attach to an already-running process by tag name."""
        return CommandHandle(
            pid=0,
            cmd_id="",
            config=self._config,
            client=self._client,
            _tag=tag,
        )

    def kill_by_tag(self, tag: str, request_timeout: Optional[float] = None) -> bool:
        """Send SIGKILL to the process matching *tag*."""
        url = self._url("process.Process/SendSignal")
        t = request_timeout if request_timeout is not None else self._config.request_timeout
        resp = self._client.post(
            url,
            json={"process": {"tag": tag}, "signal": "SIGKILL"},
            headers=self._headers(),
            timeout=t,
        )
        return resp.status_code < 400

    def send_stdin_by_tag(
        self, tag: str, data: str, request_timeout: Optional[float] = None
    ) -> None:
        """Write *data* to the stdin of the process matching *tag*."""
        url = self._url("process.Process/SendInput")
        t = request_timeout if request_timeout is not None else self._config.request_timeout
        encoded = base64.b64encode(data.encode("utf-8")).decode("ascii")
        self._client.post(
            url,
            json={"process": {"tag": tag}, "input": {"stdin": encoded}},
            headers=self._headers(),
            timeout=t,
        )


# ---------------------------------------------------------------------------
# AsyncCommands
# ---------------------------------------------------------------------------


class AsyncCommands:
    """Async command execution API."""

    def __init__(self, config: ConnectionConfig, client: httpx.AsyncClient) -> None:
        self._config = config
        self._client = client

    def _headers(self) -> Dict[str, str]:
        h = dict(_CONNECT_HEADERS)
        if self._config.access_token:
            h["X-Access-Token"] = self._config.access_token
        elif self._config.api_key:
            h["X-API-Key"] = self._config.api_key
        return h

    def _url(self, path: str) -> str:
        return f"{self._config.envd_url.rstrip('/')}/{path.lstrip('/')}"

    async def run(
        self,
        cmd: str,
        background: bool = False,
        envs: Optional[Dict[str, str]] = None,
        user: Optional[str] = None,
        cwd: Optional[str] = None,
        on_stdout: Optional[Callable[[str], None]] = None,
        on_stderr: Optional[Callable[[str], None]] = None,
        stdin: bool = False,
        timeout: float = 60,
        request_timeout: Optional[float] = None,
        tag: Optional[str] = None,
    ) -> Union[CommandResult, AsyncCommandHandle]:
        url = self._url("process.Process/Start")
        body = _build_start_body(cmd, envs, user, cwd, timeout, tag=tag)

        stdout_parts: List[str] = []
        stderr_parts: List[str] = []
        pid: Optional[int] = None
        cmd_id: Optional[str] = None
        exit_code = -1

        # For background mode we must keep the stream open after returning the handle.
        # Use the stream context manager manually so we can hand off to a task.
        stream_ctx = self._client.stream(
            "POST", url, json=body, headers=self._headers(), timeout=None
        )
        resp = await stream_ctx.__aenter__()
        try:
            _raise_for_status(resp.status_code, {})

            if background:
                # Manually read chunks until we see the start event, then hand off
                # the SAME async iterator to a drain task.
                # httpx marks the stream as consumed after the first aiter_text() call,
                # so we must reuse the same iterator object throughout.
                atext_iter = resp.aiter_text()
                buffer = ""
                async for chunk in atext_iter:
                    buffer += chunk
                    while "\n\n" in buffer:
                        chunk_text, buffer = buffer.split("\n\n", 1)
                        d = _parse_sse_chunk(chunk_text)
                        if d is None:
                            continue
                        event = d.get("event") or {}
                        if event.get("start"):
                            start = event["start"]
                            pid = start.get("pid")
                            cmd_id = start.get("cmdId") or start.get("cmd_id") or ""
                            if pid is not None:
                                break
                    if pid is not None:
                        break

                if pid is None:
                    await stream_ctx.__aexit__(None, None, None)
                    raise SandboxException("Process start did not return a PID")

                loop = asyncio.get_event_loop()
                future: asyncio.Future = loop.create_future()

                handle = AsyncCommandHandle(
                    pid=int(pid),
                    cmd_id=str(cmd_id or ""),
                    config=self._config,
                    client=self._client,
                    _result_future=future,
                )

                bg_stdout: List[str] = []
                bg_stderr: List[str] = []
                leftover = buffer

                async def _drain(atext_it: Any, ctx: Any, initial_buf: str, fut: asyncio.Future, outh: List[str], errh: List[str]) -> None:
                    try:
                        buf = initial_buf
                        # Process any complete events in the leftover buffer
                        while "\n\n" in buf:
                            chunk_text, buf = buf.split("\n\n", 1)
                            dd = _parse_sse_chunk(chunk_text)
                            if dd is None:
                                continue
                            ev = dd.get("event") or {}
                            _process_sse_event(dd, outh, errh)
                            if ev.get("end") is not None:
                                ec = _parse_exit_code(ev["end"].get("status", ""))
                                if not fut.done():
                                    fut.set_result(CommandResult(stdout="".join(outh), stderr="".join(errh), exit_code=ec))
                                return
                        # Continue reading from the SAME async iterator
                        async for new_chunk in atext_it:
                            buf += new_chunk
                            while "\n\n" in buf:
                                chunk_text, buf = buf.split("\n\n", 1)
                                dd = _parse_sse_chunk(chunk_text)
                                if dd is None:
                                    continue
                                ev = dd.get("event") or {}
                                _process_sse_event(dd, outh, errh)
                                if ev.get("end") is not None:
                                    ec = _parse_exit_code(ev["end"].get("status", ""))
                                    if not fut.done():
                                        fut.set_result(CommandResult(stdout="".join(outh), stderr="".join(errh), exit_code=ec))
                                    return
                    except Exception as e:
                        if not fut.done():
                            fut.set_exception(e)
                    finally:
                        try:
                            await ctx.__aexit__(None, None, None)
                        except Exception:
                            pass
                    if not fut.done():
                        fut.set_result(CommandResult(stdout="".join(outh), stderr="".join(errh), exit_code=-1))

                drain_task = asyncio.create_task(_drain(atext_iter, stream_ctx, leftover, future, bg_stdout, bg_stderr))
                handle._drain_task = drain_task
                return handle

            # Foreground mode: use generator-based iteration
            async for d in _aiter_sse_dicts(resp):
                event = d.get("event") or {}

                if pid is None and event.get("start"):
                    start = event["start"]
                    pid = start.get("pid")
                    cmd_id = start.get("cmdId") or start.get("cmd_id") or ""
                    continue

                _process_sse_event(d, stdout_parts, stderr_parts, on_stdout, on_stderr)

                if event.get("end") is not None:
                    end = event["end"]
                    exit_code = _parse_exit_code(end.get("status", ""))
                    break
        finally:
            if not (background and pid is not None):
                await stream_ctx.__aexit__(None, None, None)

        result = CommandResult(
            stdout="".join(stdout_parts),
            stderr="".join(stderr_parts),
            exit_code=exit_code,
        )
        if result.exit_code != 0:
            raise CommandExitException(
                f"Process exited with code {result.exit_code}",
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
            )
        return result

    async def connect(
        self,
        pid: int,
        timeout: float = 60,
        request_timeout: Optional[float] = None,
    ) -> AsyncCommandHandle:
        return AsyncCommandHandle(
            pid=pid,
            cmd_id="",
            config=self._config,
            client=self._client,
        )

    async def list(self, request_timeout: Optional[float] = None) -> List[ProcessInfo]:
        url = self._url("process.Process/List")
        t = request_timeout if request_timeout is not None else self._config.request_timeout
        resp = await self._client.post(url, json={}, headers=self._headers(), timeout=t)
        try:
            data = resp.json()
        except Exception:
            data = {}
        _raise_for_status(resp.status_code, data)
        processes = data.get("processes") or data.get("process") or []
        if isinstance(processes, dict):
            processes = [processes]
        return [
            ProcessInfo(
                pid=int(p.get("pid", 0)),
                cmd=(p.get("config") or {}).get("cmd", ""),
                args=(p.get("config") or {}).get("args", []),
                cwd=(p.get("config") or {}).get("cwd"),
                envs=(p.get("config") or {}).get("envs") or {},
                tag=p.get("tag"),
            )
            for p in processes
        ]

    async def kill(self, pid: int, request_timeout: Optional[float] = None) -> bool:
        url = self._url("process.Process/SendSignal")
        t = request_timeout if request_timeout is not None else self._config.request_timeout
        resp = await self._client.post(
            url,
            json={"process": {"pid": pid}, "signal": "SIGKILL"},
            headers=self._headers(),
            timeout=t,
        )
        return resp.status_code < 400

    async def send_stdin(
        self, pid: int, data: str, request_timeout: Optional[float] = None
    ) -> None:
        url = self._url("process.Process/SendInput")
        t = request_timeout if request_timeout is not None else self._config.request_timeout
        encoded = base64.b64encode(data.encode("utf-8")).decode("ascii")
        await self._client.post(
            url,
            json={"process": {"pid": pid}, "input": {"stdin": encoded}},
            headers=self._headers(),
            timeout=t,
        )

    async def close_stdin(self, pid: int, request_timeout: Optional[float] = None) -> None:
        """Close stdin of process *pid* (triggers EOF)."""
        url = self._url("process.Process/CloseStdin")
        t = request_timeout if request_timeout is not None else self._config.request_timeout
        await self._client.post(
            url,
            json={"process": {"pid": pid}},
            headers=self._headers(),
            timeout=t,
        )

    async def send_signal(
        self, pid: int, signal: str, request_timeout: Optional[float] = None
    ) -> None:
        """Send *signal* (e.g. ``"SIGTERM"``) to process *pid*."""
        url = self._url("process.Process/SendSignal")
        t = request_timeout if request_timeout is not None else self._config.request_timeout
        await self._client.post(
            url,
            json={"process": {"pid": pid}, "signal": signal},
            headers=self._headers(),
            timeout=t,
        )

    async def connect_by_tag(
        self,
        tag: str,
        timeout: float = 60,
        request_timeout: Optional[float] = None,
    ) -> "AsyncCommandHandle":
        """Attach to an already-running process by tag name."""
        return AsyncCommandHandle(
            pid=0,
            cmd_id="",
            config=self._config,
            client=self._client,
            _tag=tag,
        )

    async def kill_by_tag(self, tag: str, request_timeout: Optional[float] = None) -> bool:
        """Send SIGKILL to the process matching *tag*."""
        url = self._url("process.Process/SendSignal")
        t = request_timeout if request_timeout is not None else self._config.request_timeout
        resp = await self._client.post(
            url,
            json={"process": {"tag": tag}, "signal": "SIGKILL"},
            headers=self._headers(),
            timeout=t,
        )
        return resp.status_code < 400

    async def send_stdin_by_tag(
        self, tag: str, data: str, request_timeout: Optional[float] = None
    ) -> None:
        """Write *data* to the stdin of the process matching *tag*."""
        url = self._url("process.Process/SendInput")
        t = request_timeout if request_timeout is not None else self._config.request_timeout
        encoded = base64.b64encode(data.encode("utf-8")).decode("ascii")
        await self._client.post(
            url,
            json={"process": {"tag": tag}, "input": {"stdin": encoded}},
            headers=self._headers(),
            timeout=t,
        )


class Pty:
    """Synchronous PTY (pseudo-terminal) API."""

    def __init__(self, config: ConnectionConfig, client: httpx.Client) -> None:
        self._config = config
        self._client = client

    def _headers(self) -> Dict[str, str]:
        h = dict(_CONNECT_HEADERS)
        if self._config.access_token:
            h["X-Access-Token"] = self._config.access_token
        elif self._config.api_key:
            h["X-API-Key"] = self._config.api_key
        return h

    def _url(self, path: str) -> str:
        return f"{self._config.envd_url.rstrip('/')}/{path.lstrip('/')}"

    def create(
        self,
        size: PtySize,
        user: Optional[str] = None,
        cwd: Optional[str] = None,
        envs: Optional[Dict[str, str]] = None,
        timeout: float = 60,
        request_timeout: Optional[float] = None,
    ) -> CommandHandle:
        """Start a new PTY session and return a :class:`CommandHandle`."""
        url = self._url("process.Process/Start")
        process: dict = {"cmd": "/bin/bash"}
        if envs:
            process["envs"] = envs
        if cwd:
            process["cwd"] = cwd
        body: dict = {
            "process": process,
            "pty": {"size": {"rows": size.rows, "cols": size.cols}},
        }
        if timeout is not None and timeout != 0:
            body["timeout"] = int(timeout)

        pid: Optional[int] = None
        cmd_id = ""

        # We need to open the stream but only read until we get the start event.
        # Use a regular (non-streaming) request and collect the first SSE chunk.
        with self._client.stream(
            "POST", url, json=body, headers=self._headers(), timeout=None
        ) as resp:
            _raise_for_status(resp.status_code, {})
            for d in _iter_sse_dicts(resp):
                event = d.get("event") or {}
                if event.get("start"):
                    start = event["start"]
                    pid = start.get("pid")
                    cmd_id = start.get("cmdId") or start.get("cmd_id") or ""
                if pid is not None:
                    break

        if pid is None:
            raise SandboxException("PTY start did not return a PID")

        return CommandHandle(
            pid=int(pid),
            cmd_id=str(cmd_id),
            config=self._config,
            client=self._client,
        )

    def kill(self, pid: int, request_timeout: Optional[float] = None) -> bool:
        url = self._url("process.Process/SendSignal")
        t = request_timeout if request_timeout is not None else self._config.request_timeout
        resp = self._client.post(
            url,
            json={"process": {"pid": pid}, "signal": "SIGKILL"},
            headers=self._headers(),
            timeout=t,
        )
        return resp.status_code < 400

    def send_stdin(
        self, pid: int, data: bytes, request_timeout: Optional[float] = None
    ) -> None:
        """Write raw bytes to the PTY input of *pid*."""
        url = self._url("process.Process/SendInput")
        t = request_timeout if request_timeout is not None else self._config.request_timeout
        encoded = base64.b64encode(data).decode("ascii")
        self._client.post(
            url,
            json={"process": {"pid": pid}, "input": {"pty": encoded}},
            headers=self._headers(),
            timeout=t,
        )

    def resize(
        self,
        pid: int,
        size: PtySize,
        request_timeout: Optional[float] = None,
    ) -> None:
        """Resize the PTY window for *pid*."""
        url = self._url("process.Process/Update")
        t = request_timeout if request_timeout is not None else self._config.request_timeout
        self._client.post(
            url,
            json={"process": {"pid": pid}, "pty": {"size": {"rows": size.rows, "cols": size.cols}}},
            headers=self._headers(),
            timeout=t,
        )


# ---------------------------------------------------------------------------
# AsyncPty
# ---------------------------------------------------------------------------


class AsyncPty:
    """Async PTY API."""

    def __init__(self, config: ConnectionConfig, client: httpx.AsyncClient) -> None:
        self._config = config
        self._client = client

    def _headers(self) -> Dict[str, str]:
        h = dict(_CONNECT_HEADERS)
        if self._config.access_token:
            h["X-Access-Token"] = self._config.access_token
        elif self._config.api_key:
            h["X-API-Key"] = self._config.api_key
        return h

    def _url(self, path: str) -> str:
        return f"{self._config.envd_url.rstrip('/')}/{path.lstrip('/')}"

    async def create(
        self,
        size: PtySize,
        user: Optional[str] = None,
        cwd: Optional[str] = None,
        envs: Optional[Dict[str, str]] = None,
        timeout: float = 60,
        request_timeout: Optional[float] = None,
    ) -> AsyncCommandHandle:
        url = self._url("process.Process/Start")
        process: dict = {"cmd": "/bin/bash"}
        if envs:
            process["envs"] = envs
        if cwd:
            process["cwd"] = cwd
        body: dict = {
            "process": process,
            "pty": {"size": {"rows": size.rows, "cols": size.cols}},
        }
        if timeout is not None and timeout != 0:
            body["timeout"] = int(timeout)
        pid: Optional[int] = None
        cmd_id = ""

        async with self._client.stream(
            "POST", url, json=body, headers=self._headers(), timeout=None
        ) as resp:
            _raise_for_status(resp.status_code, {})
            async for d in _aiter_sse_dicts(resp):
                event = d.get("event") or {}
                if event.get("start"):
                    start = event["start"]
                    pid = start.get("pid")
                    cmd_id = start.get("cmdId") or start.get("cmd_id") or ""
                if pid is not None:
                    break

        if pid is None:
            raise SandboxException("PTY start did not return a PID")

        return AsyncCommandHandle(
            pid=int(pid),
            cmd_id=str(cmd_id),
            config=self._config,
            client=self._client,
        )

    async def kill(self, pid: int, request_timeout: Optional[float] = None) -> bool:
        url = self._url("process.Process/SendSignal")
        t = request_timeout if request_timeout is not None else self._config.request_timeout
        resp = await self._client.post(
            url,
            json={"process": {"pid": pid}, "signal": "SIGKILL"},
            headers=self._headers(),
            timeout=t,
        )
        return resp.status_code < 400

    async def send_stdin(
        self, pid: int, data: bytes, request_timeout: Optional[float] = None
    ) -> None:
        url = self._url("process.Process/SendInput")
        t = request_timeout if request_timeout is not None else self._config.request_timeout
        encoded = base64.b64encode(data).decode("ascii")
        await self._client.post(
            url,
            json={"process": {"pid": pid}, "input": {"pty": encoded}},
            headers=self._headers(),
            timeout=t,
        )

    async def resize(
        self,
        pid: int,
        size: PtySize,
        request_timeout: Optional[float] = None,
    ) -> None:
        url = self._url("process.Process/Update")
        t = request_timeout if request_timeout is not None else self._config.request_timeout
        await self._client.post(
            url,
            json={"process": {"pid": pid}, "pty": {"size": {"rows": size.rows, "cols": size.cols}}},
            headers=self._headers(),
            timeout=t,
        )
