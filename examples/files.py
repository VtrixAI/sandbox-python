"""
files.py  — 文件读写、编辑、stat、列目录、upload/download、read_stream
"""
import asyncio
import os
from src import Client, ClientOptions, CreateOptions, WriteFileEntry


async def main():
    client = Client(ClientOptions(
        base_url="http://localhost:8080",
        token="your-token",
        project_id="seaclaw",
    ))

    sb = await client.create(CreateOptions(user_id="user-123"))
    print(f"Sandbox: {sb.info.id}")

    # ── 写 / 读 / 编辑 ────────────────────────────────────
    wr = await sb.write("/tmp/hello.txt", "Hello, Sandbox!\nLine 2\n")
    print(f"Written {wr.bytes_written} bytes")

    rr = await sb.read("/tmp/hello.txt")
    print(f"Read (truncated={rr.truncated}):\n{rr.content}")

    er = await sb.edit("/tmp/hello.txt", "Hello, Sandbox!", "Hello, World!")
    print(f"Edit: {er.message}")

    # ── write_files（批量 / 二进制 / 设权限）──────────────
    await sb.write_files([
        WriteFileEntry(path="/tmp/data.bin", content=bytes(range(64))),
        WriteFileEntry(
            path="/tmp/run.sh",
            content=b"#!/bin/bash\necho 'hello from script'\n",
            mode=0o755,
        ),
    ])
    print("WriteFiles done")

    # 验证可执行脚本
    result = await sb.run_command("/tmp/run.sh")
    print(f"Script output: {result.output.strip()}")

    # ── read_to_buffer（读取原始字节）────────────────────
    buf = await sb.read_to_buffer("/tmp/data.bin")
    print(f"ReadToBuffer: {len(buf)} bytes, first 4 = {list(buf[:4])}")

    # 不存在的文件 → None
    missing = await sb.read_to_buffer("/tmp/no_such_file")
    print(f"Missing file → {missing}")

    # ── mkdir / stat / exists / list_files ───────────────
    await sb.mkdir("/tmp/mydir/sub")
    print("MkDir done")

    info = await sb.stat("/tmp/mydir")
    print(f"Stat /tmp/mydir: exists={info.exists}, is_dir={info.is_dir}")

    exists = await sb.exists("/tmp/hello.txt")
    print(f"Exists /tmp/hello.txt: {exists}")

    # 写几个文件到目录
    for name in ("a.txt", "b.txt"):
        await sb.write(f"/tmp/mydir/{name}", f"content of {name}")
    entries = await sb.list_files("/tmp/mydir")
    print(f"ListFiles /tmp/mydir: {[e.name for e in entries]}")

    # ── read_stream（大文件分块读取）─────────────────────
    # 写 100 KB 的文件
    await sb.write("/tmp/big.txt", "X" * 100_000)
    total = 0
    chunks = 0
    async for chunk in sb.read_stream("/tmp/big.txt", chunk_size=32768):
        total += len(chunk)
        chunks += 1
    print(f"ReadStream: {total} bytes in {chunks} chunk(s)")

    # ── upload / download ─────────────────────────────────
    local_src = "/tmp/sdk_upload_test.txt"
    with open(local_src, "w") as f:
        f.write("uploaded content\n")

    await sb.upload_file(local_src, "/tmp/uploaded.txt")
    print("UploadFile done")

    local_dst = "/tmp/sdk_download_test.txt"
    dst = await sb.download_file("/tmp/uploaded.txt", local_dst)
    print(f"DownloadFile → {dst}")
    with open(dst) as f:
        print(f"Downloaded content: {f.read().strip()}")

    # 下载不存在的文件 → None
    missing_dst = await sb.download_file("/tmp/nonexistent.txt", "/tmp/never.txt")
    print(f"Download missing → {missing_dst}")

    # 综合：写代码文件再执行
    code = "#!/usr/bin/env python3\nprint('Hello from Python inside sandbox!')\n"
    await sb.write("/tmp/script.py", code)
    run_result = await sb.run_command("python3 /tmp/script.py")
    print(f"Script output: {run_result.output.strip()}")

    await sb.close()


if __name__ == "__main__":
    asyncio.run(main())
