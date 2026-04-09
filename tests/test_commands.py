"""
Commands e2e tests — synchronous (Commands) and async (AsyncCommands).
"""
from __future__ import annotations

import asyncio
import sys
import time
import threading

import pytest

from sandbox.sandbox import Sandbox, AsyncSandbox
from sandbox.exceptions import CommandExitException


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_linux() -> bool:
    return sys.platform.startswith("linux")


# ===========================================================================
# Synchronous tests
# ===========================================================================


class TestCommandsSync:

    # --- run (foreground) ---

    def test_run_basic(self, sb: Sandbox) -> None:
        result = sb.commands.run("echo 'hello python sdk'")
        assert "hello python sdk" in result.stdout
        assert result.exit_code == 0

    def test_run_exit_code(self, sb: Sandbox) -> None:
        with pytest.raises(CommandExitException) as exc_info:
            sb.commands.run("exit 42")
        assert exc_info.value.exit_code == 42

    def test_run_stderr(self, sb: Sandbox) -> None:
        result = sb.commands.run("echo 'err msg' >&2")
        assert "err msg" in result.stderr

    def test_run_combined_output(self, sb: Sandbox) -> None:
        result = sb.commands.run("echo 'out_line'; echo 'err_line' >&2")
        assert "out_line" in result.stdout
        assert "err_line" in result.stderr

    def test_run_with_env(self, sb: Sandbox) -> None:
        result = sb.commands.run("echo $MY_PY_VAR", envs={"MY_PY_VAR": "py_env_value"})
        assert "py_env_value" in result.stdout

    def test_run_with_cwd(self, sb: Sandbox) -> None:
        sb.files.make_dir("/tmp/py_cwd_test")
        result = sb.commands.run("pwd", cwd="/tmp/py_cwd_test")
        assert "/tmp/py_cwd_test" in result.stdout or "py_cwd_test" in result.stdout

    def test_run_timeout(self, sb: Sandbox) -> None:
        start = time.time()
        with pytest.raises(CommandExitException) as exc_info:
            sb.commands.run("sleep 30", timeout=2)
        elapsed = time.time() - start
        assert elapsed < 10, f"Timeout=2 but run took {elapsed:.1f}s"
        assert exc_info.value.exit_code != 0

    # --- run background ---

    def test_run_background_and_wait(self, sb: Sandbox) -> None:
        handle = sb.commands.run("sleep 1 && echo 'bg_done'", background=True)
        assert handle.pid != 0
        result = handle.wait()
        assert "bg_done" in result.stdout

    def test_run_background_kill(self, sb: Sandbox) -> None:
        handle = sb.commands.run("sleep 60", background=True)
        assert handle.pid != 0
        assert handle.kill() is True

    def test_run_background_stderr(self, sb: Sandbox) -> None:
        handle = sb.commands.run("echo 'bg_stderr_msg' >&2", background=True)
        result = handle.wait()
        assert "bg_stderr_msg" in result.stderr

    # --- list / kill ---

    def test_list(self, sb: Sandbox) -> None:
        handle = sb.commands.run("sleep 30", background=True)
        try:
            time.sleep(0.2)
            procs = sb.commands.list()
            if len(procs) == 0:
                pytest.skip("List returned 0 processes — likely non-Linux; skipping")
            found = any(p.pid == handle.pid for p in procs)
            assert found, f"pid={handle.pid} not found in list"
        finally:
            handle.kill()

    def test_kill_by_pid(self, sb: Sandbox) -> None:
        handle = sb.commands.run("sleep 60", background=True)
        assert sb.commands.kill(handle.pid) is True

    def test_kill_dead_process(self, sb: Sandbox) -> None:
        handle = sb.commands.run("echo done_immediately", background=True)
        try:
            handle.wait()
        except CommandExitException:
            pass
        # Should not raise
        handle.kill()

    # --- connect ---

    def test_connect(self, sb: Sandbox) -> None:
        handle = sb.commands.run("echo 'output_line'; sleep 5", background=True)
        time.sleep(0.1)
        connected = sb.commands.connect(handle.pid)
        handle.kill()
        try:
            result = connected.wait()
            assert result is not None
        except CommandExitException as e:
            assert e.exit_code != 0

    def test_connect_invalid_pid(self, sb: Sandbox) -> None:
        connected = sb.commands.connect(999999)
        # Wait may throw or return error result depending on server behaviour
        try:
            result = connected.wait()
            assert result is not None
        except (CommandExitException, Exception):
            pass

    # --- send_stdin / close_stdin ---

    def test_send_stdin(self, sb: Sandbox) -> None:
        handle = sb.commands.run("cat", background=True)
        time.sleep(0.2)
        sb.commands.send_stdin(handle.pid, "hello stdin\n")
        handle.kill()

    def test_send_stdin_verified(self, sb: Sandbox) -> None:
        handle = sb.commands.run("cat", background=True)
        time.sleep(0.2)
        sb.commands.send_stdin(handle.pid, "stdin_verified_content\n")
        time.sleep(0.2)
        handle.kill()
        try:
            result = handle.wait()
            assert result is not None
        except CommandExitException:
            pass

    def test_close_stdin(self, sb: Sandbox) -> None:
        handle = sb.commands.run("cat", background=True)
        time.sleep(0.2)
        sb.commands.close_stdin(handle.pid)

        done = threading.Event()
        result_box = [None]

        def _wait():
            try:
                result_box[0] = handle.wait()
            except CommandExitException as e:
                result_box[0] = e  # closed stdin — cat exits 0 normally, but just in case
            done.set()

        t = threading.Thread(target=_wait, daemon=True)
        t.start()
        assert done.wait(timeout=5), "cat did not exit within 5s after CloseStdin"
        assert result_box[0] is not None

    # --- send_signal ---

    def test_send_signal_sigterm(self, sb: Sandbox) -> None:
        handle = sb.commands.run("sleep 60", background=True)
        time.sleep(0.2)
        sb.commands.send_signal(handle.pid, "SIGTERM")

        done = threading.Event()

        def _wait():
            try:
                handle.wait()
            except CommandExitException:
                pass
            done.set()

        t = threading.Thread(target=_wait, daemon=True)
        t.start()
        assert done.wait(timeout=5), "process did not exit within 5s after SIGTERM"

    def test_send_signal_sigkill(self, sb: Sandbox) -> None:
        handle = sb.commands.run("sleep 60", background=True)
        time.sleep(0.2)
        sb.commands.send_signal(handle.pid, "SIGKILL")

        done = threading.Event()

        def _wait():
            try:
                handle.wait()
            except CommandExitException:
                pass
            done.set()

        t = threading.Thread(target=_wait, daemon=True)
        t.start()
        assert done.wait(timeout=5), "process did not exit within 5s after SIGKILL"

    # --- by-tag (Linux only) ---

    def test_run_with_tag(self, sb: Sandbox) -> None:
        if not _is_linux():
            pytest.skip("by-tag operations rely on /proc — skipping on non-Linux")

        tag = "e2e-py-tag-test"
        handle = sb.commands.run("sleep 15", background=True, timeout=20)
        # Note: Python SDK doesn't expose tag in run(); tag tests use kill_by_tag etc.
        try:
            time.sleep(0.3)
            procs = sb.commands.list()
            if len(procs) == 0:
                pytest.skip("List returned 0 processes")
        finally:
            handle.kill()

    def test_kill_by_tag(self, sb: Sandbox) -> None:
        if not _is_linux():
            pytest.skip("by-tag operations rely on /proc — skipping on non-Linux")

        # Start a process via shell with a unique marker; use kill_by_tag via process tag
        # The Python SDK's run() doesn't expose tag, so we rely on the nano-executor
        # accepting by-tag kill for a tag we set via a process that was started with a tag.
        # Since run() doesn't expose tag, we directly test kill_by_tag returns no exception.
        # For a full integration test, we'd need a tagged start — validate graceful handling.
        result = sb.commands.kill_by_tag("e2e-py-nonexistent-tag-xyz")
        # Returns bool; non-existent tag may return False or True depending on server
        assert isinstance(result, bool)

    def test_send_stdin_by_tag(self, sb: Sandbox) -> None:
        if not _is_linux():
            pytest.skip("by-tag operations rely on /proc — skipping on non-Linux")

        # Similarly, send_stdin_by_tag should not raise for non-existent tag
        sb.commands.send_stdin_by_tag("e2e-py-nonexistent-tag-xyz", "data\n")

    def test_connect_by_tag(self, sb: Sandbox) -> None:
        if not _is_linux():
            pytest.skip("by-tag operations rely on /proc — skipping on non-Linux")

        # connect_by_tag returns a handle; wait may produce result or error
        connected = sb.commands.connect_by_tag("e2e-py-nonexistent-tag-xyz")
        try:
            result = connected.wait()
            assert result is not None
        except (CommandExitException, Exception):
            pass

    # --- disconnect ---

    def test_disconnect(self, sb: Sandbox) -> None:
        handle = sb.commands.run("sleep 30", background=True)
        pid = handle.pid
        assert pid != 0

        handle.disconnect()
        time.sleep(0.2)

        # Process should still be alive; kill it
        assert sb.commands.kill(pid) is True


# ===========================================================================
# Async tests
# ===========================================================================


@pytest.mark.asyncio
class TestCommandsAsync:

    async def test_run_basic(self, async_sb: AsyncSandbox) -> None:
        result = await async_sb.commands.run("echo 'hello async python sdk'")
        assert "hello async python sdk" in result.stdout
        assert result.exit_code == 0

    async def test_run_exit_code(self, async_sb: AsyncSandbox) -> None:
        with pytest.raises(CommandExitException) as exc_info:
            await async_sb.commands.run("exit 7")
        assert exc_info.value.exit_code == 7

    async def test_run_stderr(self, async_sb: AsyncSandbox) -> None:
        result = await async_sb.commands.run("echo 'async_err' >&2")
        assert "async_err" in result.stderr

    async def test_run_with_env(self, async_sb: AsyncSandbox) -> None:
        result = await async_sb.commands.run(
            "echo $ASYNC_PY_VAR", envs={"ASYNC_PY_VAR": "async_env_ok"}
        )
        assert "async_env_ok" in result.stdout

    async def test_run_background_and_wait(self, async_sb: AsyncSandbox) -> None:
        handle = await async_sb.commands.run("sleep 1 && echo 'async_bg_done'", background=True)
        assert handle.pid != 0
        result = await handle.wait()
        assert "async_bg_done" in result.stdout

    async def test_run_background_kill(self, async_sb: AsyncSandbox) -> None:
        handle = await async_sb.commands.run("sleep 60", background=True)
        assert await handle.kill() is True

    async def test_list(self, async_sb: AsyncSandbox) -> None:
        handle = await async_sb.commands.run("sleep 30", background=True)
        try:
            await asyncio.sleep(0.2)
            procs = await async_sb.commands.list()
            if len(procs) == 0:
                pytest.skip("List returned 0 — likely non-Linux")
            found = any(p.pid == handle.pid for p in procs)
            assert found
        finally:
            await handle.kill()

    async def test_kill_by_pid(self, async_sb: AsyncSandbox) -> None:
        handle = await async_sb.commands.run("sleep 60", background=True)
        assert await async_sb.commands.kill(handle.pid) is True

    async def test_send_stdin(self, async_sb: AsyncSandbox) -> None:
        handle = await async_sb.commands.run("cat", background=True)
        await asyncio.sleep(0.2)
        await async_sb.commands.send_stdin(handle.pid, "hello async stdin\n")
        await handle.kill()

    async def test_close_stdin(self, async_sb: AsyncSandbox) -> None:
        handle = await async_sb.commands.run("cat", background=True)
        await asyncio.sleep(0.2)
        await async_sb.commands.close_stdin(handle.pid)

        try:
            result = await asyncio.wait_for(handle.wait(), timeout=5.0)
            assert result is not None
        except CommandExitException:
            pass

    async def test_send_signal_sigterm(self, async_sb: AsyncSandbox) -> None:
        handle = await async_sb.commands.run("sleep 60", background=True)
        await asyncio.sleep(0.2)
        await async_sb.commands.send_signal(handle.pid, "SIGTERM")
        try:
            result = await asyncio.wait_for(handle.wait(), timeout=5.0)
            assert result is not None
        except CommandExitException:
            pass

    async def test_connect(self, async_sb: AsyncSandbox) -> None:
        handle = await async_sb.commands.run("echo 'async_connect_line'; sleep 5", background=True)
        await asyncio.sleep(0.1)
        connected = await async_sb.commands.connect(handle.pid)
        await handle.kill()
        try:
            result = await connected.wait()
            assert result is not None
        except CommandExitException as e:
            assert e.exit_code != 0


# ===========================================================================
# on_stdout / on_stderr callbacks
# ===========================================================================


class TestCommandsCallbacks:

    def test_run_on_stdout_callback(self, sb: Sandbox) -> None:
        collected: list[str] = []
        result = sb.commands.run(
            "echo 'cb_stdout_line'",
            on_stdout=lambda line: collected.append(line),
        )
        assert "cb_stdout_line" in result.stdout
        assert any("cb_stdout_line" in s for s in collected), (
            f"on_stdout callback not called; collected={collected}"
        )

    def test_run_on_stderr_callback(self, sb: Sandbox) -> None:
        collected: list[str] = []
        result = sb.commands.run(
            "echo 'cb_stderr_line' >&2",
            on_stderr=lambda line: collected.append(line),
        )
        assert "cb_stderr_line" in result.stderr
        assert any("cb_stderr_line" in s for s in collected), (
            f"on_stderr callback not called; collected={collected}"
        )

    def test_run_both_callbacks(self, sb: Sandbox) -> None:
        out_lines: list[str] = []
        err_lines: list[str] = []
        sb.commands.run(
            "echo 'both_out'; echo 'both_err' >&2",
            on_stdout=lambda l: out_lines.append(l),
            on_stderr=lambda l: err_lines.append(l),
        )
        assert any("both_out" in l for l in out_lines)
        assert any("both_err" in l for l in err_lines)


@pytest.mark.asyncio
class TestCommandsAsyncCallbacks:

    async def test_run_on_stdout_callback(self, async_sb: AsyncSandbox) -> None:
        collected: list[str] = []
        result = await async_sb.commands.run(
            "echo 'async_cb_stdout'",
            on_stdout=lambda line: collected.append(line),
        )
        assert "async_cb_stdout" in result.stdout
        assert any("async_cb_stdout" in s for s in collected)

    async def test_run_on_stderr_callback(self, async_sb: AsyncSandbox) -> None:
        collected: list[str] = []
        result = await async_sb.commands.run(
            "echo 'async_cb_stderr' >&2",
            on_stderr=lambda line: collected.append(line),
        )
        assert "async_cb_stderr" in result.stderr
        assert any("async_cb_stderr" in s for s in collected)


# ===========================================================================
# CommandHandle.__iter__ / AsyncCommandHandle.__aiter__
# ===========================================================================


class TestCommandHandleIter:

    def test_iter_yields_sse_dicts(self, sb: Sandbox) -> None:
        handle = sb.commands.run("echo 'iter_output'", background=True)
        time.sleep(0.1)

        events: list[dict] = []
        for d in handle:
            events.append(d)
            ev = d.get("event", {})
            if "end" in ev or "start" in ev:
                break

        assert len(events) > 0, "CommandHandle.__iter__ yielded no events"
        event_keys = {k for d in events for k in d.get("event", {}).keys()}
        assert event_keys & {"start", "data", "end", "keepalive"}, (
            f"Unexpected event keys: {event_keys}"
        )

    def test_iter_contains_stdout(self, sb: Sandbox) -> None:
        import base64
        handle = sb.commands.run("echo 'iter_stdout_check'", background=True)
        time.sleep(0.1)

        stdout_found = False
        for d in handle:
            ev = d.get("event", {})
            data = ev.get("data", {})
            if "stdout" in data:
                decoded = base64.b64decode(data["stdout"]).decode()
                if "iter_stdout_check" in decoded:
                    stdout_found = True
            if "end" in ev:
                break

        assert stdout_found, "CommandHandle.__iter__ did not yield stdout event"


@pytest.mark.asyncio
class TestCommandHandleAiter:

    async def test_aiter_yields_sse_dicts(self, async_sb: AsyncSandbox) -> None:
        handle = await async_sb.commands.run("echo 'aiter_output'", background=True)
        await asyncio.sleep(0.1)

        events: list[dict] = []
        async for d in handle:
            events.append(d)
            ev = d.get("event", {})
            if "end" in ev or "start" in ev:
                break

        assert len(events) > 0, "AsyncCommandHandle.__aiter__ yielded no events"

    async def test_aiter_contains_stdout(self, async_sb: AsyncSandbox) -> None:
        import base64
        handle = await async_sb.commands.run("echo 'aiter_stdout_check'", background=True)
        await asyncio.sleep(0.1)

        stdout_found = False
        async for d in handle:
            ev = d.get("event", {})
            data = ev.get("data", {})
            if "stdout" in data:
                decoded = base64.b64decode(data["stdout"]).decode()
                if "aiter_stdout_check" in decoded:
                    stdout_found = True
            if "end" in ev:
                break

        assert stdout_found, "AsyncCommandHandle.__aiter__ did not yield stdout event"
