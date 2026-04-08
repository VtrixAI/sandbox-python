"""
filesystem.py — 文件系统操作示例：读写、目录、编辑、监听等。

运行:
    SANDBOX_API_KEY=your-key SANDBOX_BASE_URL=https://api.sandbox.vtrix.ai python examples/filesystem.py

环境变量:
    SANDBOX_API_KEY   必填
    SANDBOX_BASE_URL  必填，例如 https://api.sandbox.vtrix.ai
"""

import os
from sandbox.sandbox import Sandbox
from sandbox.types import WriteEntry

def main() -> None:
    sb = Sandbox.create(api_key=os.environ["SANDBOX_API_KEY"])

    try:
        # --- 写入文本 / 字节 ---
        sb.files.write("/tmp/notes.txt", "line1\nline2\nline3")
        sb.files.write("/tmp/data.bin", bytes([0x00, 0x01, 0x02]))

        # --- 读取 ---
        text = sb.files.read("/tmp/notes.txt", format="text")
        print(f"notes.txt:\n{text}")

        # --- 查找替换 (edit) ---
        sb.files.edit("/tmp/notes.txt", "line2", "LINE_TWO")
        updated = sb.files.read("/tmp/notes.txt", format="text")
        print(f"after edit:\n{updated}")

        # --- 批量写入 ---
        sb.files.write_files([
            WriteEntry(path="/tmp/a.txt", data="file A"),
            WriteEntry(path="/tmp/b.txt", data="file B"),
        ])

        # --- 目录操作 ---
        sb.files.make_dir("/tmp/mydir")

        entries = sb.files.list("/tmp")
        print(f"/tmp entries ({len(entries)}):")
        for e in entries:
            print(f"  {e.name} ({e.type}, {e.size} bytes)")

        # --- exists / get_info / rename / remove ---
        print(f"notes.txt exists: {sb.files.exists('/tmp/notes.txt')}")

        info = sb.files.get_info("/tmp/notes.txt")
        print(f"notes.txt size: {info.size}")

        sb.files.rename("/tmp/notes.txt", "/tmp/notes_renamed.txt")
        sb.files.remove("/tmp/notes_renamed.txt")

        print("done.")

    finally:
        sb.kill()


if __name__ == "__main__":
    main()
