# vtrix-sandbox

Python SDK for [Vtrix](https://github.com/VtrixAI) sandbox — run commands, manage files, and operate sandbox instances in isolated Linux environments via the hermes gateway.

Provides both synchronous (`Sandbox`) and asynchronous (`AsyncSandbox`) clients.

## Installation

```bash
pip install vtrix-sandbox
```

**Requires Python 3.10+**

## Quick Start

```python
from sandbox import Sandbox

sb = Sandbox.create(template="base", timeout=300)
result = sb.commands.run("echo hello")
print(result.stdout)
sb.kill()
```

Async version:

```python
import asyncio
from sandbox import AsyncSandbox

async def main():
    sb = await AsyncSandbox.create(template="base", timeout=300)
    result = await sb.commands.run("echo hello")
    print(result.stdout)
    await sb.kill()

asyncio.run(main())
```

## Configuration

API key and base URL are resolved in order:

1. Explicit argument (`api_key=`, `base_url=`)
2. Environment variables: `SANDBOX_API_KEY`, `SANDBOX_BASE_URL`
3. Default base URL: `http://localhost:8080`

---

## Sandbox lifecycle

### `Sandbox.create(...) → Sandbox`

Create a new sandbox.

```python
sb = Sandbox.create(
    template="base",
    timeout=300,
    metadata={"env": "dev"},
    envs={"NODE_ENV": "production"},
    api_key="your-api-key",
    base_url="http://your-hermes-host:8080",
)
```

### `Sandbox.connect(sandbox_id, ...) → Sandbox`

Connect to an existing sandbox, resuming it if paused.

```python
sb = Sandbox.connect("sandbox-id", api_key="your-api-key")
```

### `sb.kill()`

Terminate the sandbox immediately.

### `sb.set_timeout(timeout: int)`

Update the sandbox lifetime in seconds.

### `sb.get_info() → SandboxInfo`

Fetch current metadata.

```python
info = sb.get_info()
print(info.state)  # "running", "paused", ...
```

### `sb.is_running() → bool`

Return `True` if the sandbox state is `"running"` or `"active"`.

### `sb.get_host(port: int) → str`

Return the proxy hostname for a port inside the sandbox.

```python
host = sb.get_host(3000)  # "3000-<sandbox_id>.<domain>"
```

### `sb.get_metrics() → dict`

Fetch current CPU and memory utilization. Returns `{"cpuUsedPct": float, "memUsedMiB": float}`.

```python
m = sb.get_metrics()
print(f"CPU {m['cpuUsedPct']:.1f}%  Mem {m['memUsedMiB']:.0f} MiB")
```

### `sb.resize_disk(size_mb: int)`

Expand the sandbox disk. Atlas performs an in-place PVC resize — the sandbox does not restart.

```python
sb.resize_disk(20 * 1024)  # 20 GiB
```

### `sb.download_url(path, *, user=None, expires=300) → str`

Return a short-lived signed URL for downloading a file directly from the sandbox.

### `sb.upload_url(path, *, user=None, expires=300) → str`

Return a short-lived signed URL for uploading a file directly into the sandbox.

### Static helpers

```python
Sandbox.list(api_key=..., base_url=...)          # → List[SandboxInfo]
Sandbox.kill_sandbox(sandbox_id, ...)
Sandbox.get_sandbox_info(sandbox_id, ...)        # → SandboxInfo
Sandbox.set_sandbox_timeout(sandbox_id, timeout, ...)
Sandbox.get_sandbox_metrics(sandbox_id, ...)     # → dict
```

### Context manager

```python
with Sandbox.create() as sb:
    result = sb.commands.run("echo hello")
# sandbox is killed on exit
```

---

## Commands

`sb.commands` exposes all process-management operations.

### `commands.run(cmd, *, on_stdout=None, on_stderr=None, ...) → CommandResult`

Run a command and block until it finishes. Raises `CommandExitException` if the exit code is non-zero.

```python
result = sb.commands.run(
    "npm install",
    working_dir="/app",
    timeout_ms=60_000,
    envs={"NODE_ENV": "production"},
    on_stdout=lambda s: print(s, end=""),
)
print(result.stdout)
```

Options (`RunOpts` / keyword arguments):

| Parameter | Type | Description |
|---|---|---|
| `working_dir` | `str` | Working directory inside the sandbox. |
| `timeout_ms` | `int` | Kill the process after this many milliseconds. `0` = no timeout. |
| `envs` | `dict` | Additional environment variables. |
| `on_stdout` | `callable` | Called for each stdout chunk. |
| `on_stderr` | `callable` | Called for each stderr chunk. |

### `commands.run(..., background=True) → CommandHandle`

Start a command in the background and return a handle immediately. The SSE stream is drained in a background thread so the nano-executor registry stays alive.

```python
handle = sb.commands.run("node server.js", working_dir="/app", background=True)
# ... do other work ...
result = handle.wait()
```

### `CommandHandle`

| Method | Description |
|---|---|
| `wait(on_stdout=None, on_stderr=None) → CommandResult` | Block until the process finishes. Raises `CommandExitException` on non-zero exit. |
| `kill() → bool` | Send SIGKILL. |
| `send_stdin(data: str)` | Write data to stdin. |

### `commands.connect(pid, ...) → CommandHandle`

Attach to a running process by PID.

### `commands.list() → List[ProcessInfo]`

List all running processes.

### `commands.kill(pid) → bool`

Send SIGKILL to a process by PID.

### `commands.kill_by_tag(tag) → bool`

Send SIGKILL to a process by tag.

### `commands.send_signal(pid, signal: str)`

Send an arbitrary signal (`"SIGTERM"`, `"SIGINT"`, etc.).

### `commands.send_stdin(pid, data: str)`

Write to a process's stdin.

### `commands.close_stdin(pid)`

Close stdin (EOF).

`CommandResult` fields: `stdout: str`, `stderr: str`, `exit_code: int`.

---

## Filesystem

`sb.files` exposes all filesystem operations.

### Read

```python
data   = sb.files.read("/app/config.json")         # → bytes
text   = sb.files.read_text("/app/config.json")    # → str
stream = sb.files.read_stream("/app/large.csv")    # → BinaryIO
```

### Write

```python
info = sb.files.write("/app/out.bin", data)
info = sb.files.write_text("/app/config.json", '{"port":8080}')
infos = sb.files.write_files([
    WriteEntry(path="/app/run.sh", content=script_bytes, mode=0o755),
])
```

### Directory operations

```python
entries = sb.files.list("/app")                    # → List[EntryInfo]
ok      = sb.files.make_dir("/app/logs")           # → bool
exists  = sb.files.exists("/app/config.json")      # → bool
info    = sb.files.get_info("/app/config.json")    # → EntryInfo
```

`EntryInfo` fields: `name`, `path`, `type` (`"file"` / `"dir"` / `"symlink"`), `size`, `modified_at`, `symlink_target`.

### Mutation

```python
sb.files.edit("/app/config.json", '"port": 3000', '"port": 8080')
sb.files.remove("/app/old.log")
info = sb.files.rename("/app/old.txt", "/app/new.txt")
```

### Watch

```python
def on_event(ev: FilesystemEvent):
    print(ev.operation, ev.path)

handle = sb.files.watch_dir("/app", on_event)
# ... do work ...
handle.stop()
```

---

## PTY

`sb.pty` provides interactive terminal sessions.

```python
handle = sb.pty.create(rows=24, cols=80)
sb.pty.resize(handle.pid, rows=40, cols=200)
sb.pty.send_input(handle.pid, "ls -la\n")
result = handle.wait()
print(result.stdout)
sb.pty.kill(handle.pid)
```

---

## Async API

Every class has an `Async` counterpart: `AsyncSandbox`, `AsyncCommands`, `AsyncFilesystem`, `AsyncPty`, `AsyncCommandHandle`.

All methods are identical but `async def` and must be awaited:

```python
async with await AsyncSandbox.create() as sb:
    result = await sb.commands.run("echo hello")
    entries = await sb.files.list("/")
```

Background commands in async context:

```python
handle = await sb.commands.run("node server.js", background=True)
result = await handle.wait()
```

---

## Examples

| File | Description |
|---|---|
| [`examples/quickstart.py`](examples/quickstart.py) | Create sandbox, run commands |
| [`examples/async_quickstart.py`](examples/async_quickstart.py) | Async version of quickstart |
| [`examples/background_commands.py`](examples/background_commands.py) | Background processes |
| [`examples/filesystem.py`](examples/filesystem.py) | Read, write, list, watch files |
| [`examples/pty.py`](examples/pty.py) | PTY create, resize, input |
| [`examples/sandbox_management.py`](examples/sandbox_management.py) | Lifecycle, metrics, disk resize |

```bash
python examples/quickstart.py
```

## License

MIT — see [LICENSE](LICENSE).
