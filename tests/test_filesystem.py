"""
Filesystem e2e tests — synchronous (Filesystem) and async (AsyncFilesystem).
"""
from __future__ import annotations

import platform
import time

import pytest
import pytest_asyncio

from sandbox.sandbox import Sandbox, AsyncSandbox
from sandbox.exceptions import NotFoundException, InvalidArgumentException


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _path(name: str) -> str:
    return f"/tmp/e2e_py_{name}"


# ===========================================================================
# Synchronous tests
# ===========================================================================


class TestFilesystemSync:

    # --- read / write ---

    def test_write_and_read_text(self, sb: Sandbox) -> None:
        path = _path("write_read.txt")
        sb.files.write(path, "hello python sdk")
        got = sb.files.read(path, format="text")
        assert got == "hello python sdk"

    def test_write_bytes_and_read(self, sb: Sandbox) -> None:
        path = _path("bytes.bin")
        data = bytes([0x00, 0x01, 0x7F, 0xFF, 0xFE])
        sb.files.write(path, data)
        got = sb.files.read(path, format="bytes")
        assert bytes(got) == data

    def test_write_overwrite(self, sb: Sandbox) -> None:
        path = _path("overwrite.txt")
        sb.files.write(path, "original")
        sb.files.write(path, "overwritten")
        assert sb.files.read(path, format="text") == "overwritten"

    def test_write_files_batch(self, sb: Sandbox) -> None:
        from sandbox.types import WriteEntry
        files = [
            WriteEntry(path=_path("batch_a.txt"), data="batch file A"),
            WriteEntry(path=_path("batch_b.txt"), data="batch file B"),
        ]
        sb.files.write_files(files)
        assert sb.files.read(_path("batch_a.txt"), format="text") == "batch file A"
        assert sb.files.read(_path("batch_b.txt"), format="text") == "batch file B"

    # --- directory ---

    def test_make_dir(self, sb: Sandbox) -> None:
        path = _path("testdir")
        sb.files.make_dir(path)
        assert sb.files.exists(path)

    def test_make_dir_idempotent(self, sb: Sandbox) -> None:
        path = _path("idempotent_dir")
        sb.files.make_dir(path)
        sb.files.make_dir(path)  # must not raise

    def test_list(self, sb: Sandbox) -> None:
        sb.files.write(_path("list_sentinel.txt"), "sentinel")
        entries = sb.files.list("/tmp")
        assert len(entries) > 0

    def test_list_entry_fields(self, sb: Sandbox) -> None:
        sb.files.write(_path("list_fields.txt"), "list entry fields")
        entries = sb.files.list("/tmp")
        found = next((e for e in entries if e.name == f"e2e_py_list_fields.txt"), None)
        assert found is not None, "written file not found in list"
        assert found.path != ""
        assert found.type != ""
        assert found.size > 0

    # --- exists / stat ---

    def test_exists_true(self, sb: Sandbox) -> None:
        path = _path("exists_true.txt")
        sb.files.write(path, "x")
        assert sb.files.exists(path) is True

    def test_exists_false(self, sb: Sandbox) -> None:
        assert sb.files.exists("/tmp/py_no_such_file_xyz_9999") is False

    def test_get_info(self, sb: Sandbox) -> None:
        path = _path("getinfo.txt")
        content = "getinfo content"
        sb.files.write(path, content)
        info = sb.files.get_info(path)
        assert info.name == "e2e_py_getinfo.txt"
        assert info.size == len(content)
        assert info.type != ""
        assert info.modified_time is not None

    # --- rename / remove ---

    def test_rename(self, sb: Sandbox) -> None:
        src = _path("rename_src.txt")
        dst = _path("rename_dst.txt")
        sb.files.write(src, "rename me")
        try:
            sb.files.remove(dst)
        except Exception:
            pass
        sb.files.rename(src, dst)
        assert sb.files.exists(dst)
        assert not sb.files.exists(src)

    def test_remove(self, sb: Sandbox) -> None:
        path = _path("remove.txt")
        sb.files.write(path, "delete me")
        sb.files.remove(path)
        assert not sb.files.exists(path)

    def test_remove_nonexistent_raises(self, sb: Sandbox) -> None:
        with pytest.raises(Exception):
            sb.files.remove("/tmp/py_remove_nonexistent_xyz_99999.txt")

    def test_remove_directory(self, sb: Sandbox) -> None:
        path = _path("removedir")
        sb.files.make_dir(path)
        sb.files.remove(path)
        assert not sb.files.exists(path)

    def test_read_not_found_raises(self, sb: Sandbox) -> None:
        with pytest.raises(Exception):
            sb.files.read("/tmp/py_definitely_not_exist_xyz.txt", format="text")

    # --- edit ---

    def test_edit_basic(self, sb: Sandbox) -> None:
        path = _path("edit_basic.txt")
        sb.files.write(path, "hello world")
        sb.files.edit(path, "world", "python")
        assert sb.files.read(path, format="text") == "hello python"

    def test_edit_not_found_raises(self, sb: Sandbox) -> None:
        path = _path("edit_notfound.txt")
        sb.files.write(path, "some content")
        with pytest.raises(Exception):
            sb.files.edit(path, "no_such_text_xyz", "replacement")

    def test_edit_not_unique_raises(self, sb: Sandbox) -> None:
        path = _path("edit_notunique.txt")
        sb.files.write(path, "repeat repeat repeat")
        with pytest.raises(Exception):
            sb.files.edit(path, "repeat", "once")

    # --- watch ---

    def test_watch_dir(self, sb: Sandbox) -> None:
        watch_path = _path("watch")
        sb.files.make_dir(watch_path)

        events = []
        handle = sb.files.watch_dir(watch_path)
        time.sleep(0.2)
        sb.files.write(watch_path + "/watched.txt", "trigger")
        time.sleep(0.5)

        events = handle.get_new_events()
        handle.stop()
        assert len(events) > 0, "WatchDir: no events received"

    def test_watch_dir_stop(self, sb: Sandbox) -> None:
        watch_path = _path("watch_stop")
        sb.files.make_dir(watch_path)

        handle = sb.files.watch_dir(watch_path)
        time.sleep(0.2)
        sb.files.write(watch_path + "/before.txt", "before")
        time.sleep(0.4)
        assert len(handle.get_new_events()) > 0, "expected pre-stop event"

        handle.stop()
        # drain
        handle.get_new_events()

        sb.files.write(watch_path + "/after.txt", "after")
        time.sleep(0.4)
        assert len(handle.get_new_events()) == 0, "callback fired after stop"


# ===========================================================================
# Async tests
# ===========================================================================


@pytest.mark.asyncio
class TestFilesystemAsync:

    async def test_write_and_read_text(self, async_sb: AsyncSandbox) -> None:
        path = _path("async_write_read.txt")
        await async_sb.files.write(path, "hello async python sdk")
        got = await async_sb.files.read(path, format="text")
        assert got == "hello async python sdk"

    async def test_write_bytes_and_read(self, async_sb: AsyncSandbox) -> None:
        path = _path("async_bytes.bin")
        data = bytes([0xDE, 0xAD, 0xBE, 0xEF])
        await async_sb.files.write(path, data)
        got = await async_sb.files.read(path, format="bytes")
        assert bytes(got) == data

    async def test_make_dir_and_exists(self, async_sb: AsyncSandbox) -> None:
        path = _path("async_testdir")
        await async_sb.files.make_dir(path)
        assert await async_sb.files.exists(path)

    async def test_list(self, async_sb: AsyncSandbox) -> None:
        await async_sb.files.write(_path("async_list_sentinel.txt"), "sentinel")
        entries = await async_sb.files.list("/tmp")
        assert len(entries) > 0

    async def test_get_info(self, async_sb: AsyncSandbox) -> None:
        path = _path("async_getinfo.txt")
        await async_sb.files.write(path, "async getinfo")
        info = await async_sb.files.get_info(path)
        assert info.name != ""
        assert info.size > 0

    async def test_rename(self, async_sb: AsyncSandbox) -> None:
        src = _path("async_rename_src.txt")
        dst = _path("async_rename_dst.txt")
        await async_sb.files.write(src, "rename async")
        try:
            await async_sb.files.remove(dst)
        except Exception:
            pass
        await async_sb.files.rename(src, dst)
        assert await async_sb.files.exists(dst)
        assert not await async_sb.files.exists(src)

    async def test_remove(self, async_sb: AsyncSandbox) -> None:
        path = _path("async_remove.txt")
        await async_sb.files.write(path, "delete me async")
        await async_sb.files.remove(path)
        assert not await async_sb.files.exists(path)

    async def test_edit_basic(self, async_sb: AsyncSandbox) -> None:
        path = _path("async_edit.txt")
        await async_sb.files.write(path, "hello async world")
        await async_sb.files.edit(path, "world", "sdk")
        assert await async_sb.files.read(path, format="text") == "hello async sdk"

    async def test_edit_not_found_raises(self, async_sb: AsyncSandbox) -> None:
        path = _path("async_edit_notfound.txt")
        await async_sb.files.write(path, "content")
        with pytest.raises(Exception):
            await async_sb.files.edit(path, "no_such_xyz", "x")

    async def test_write_files_batch(self, async_sb: AsyncSandbox) -> None:
        from sandbox.types import WriteEntry
        files = [
            WriteEntry(path=_path("async_batch_a.txt"), data="async A"),
            WriteEntry(path=_path("async_batch_b.txt"), data="async B"),
        ]
        await async_sb.files.write_files(files)
        assert await async_sb.files.read(_path("async_batch_a.txt"), format="text") == "async A"
