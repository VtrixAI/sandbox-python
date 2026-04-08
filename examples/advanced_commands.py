"""
advanced_commands.py — 进程列表、Connect、by-tag 操作、WatchDir 示例。

运行:
    SANDBOX_API_KEY=your-key SANDBOX_BASE_URL=https://api.sandbox.vtrix.ai python examples/advanced_commands.py

环境变量:
    SANDBOX_API_KEY   必填
    SANDBOX_BASE_URL  必填，例如 https://api.sandbox.vtrix.ai

注意: by-tag 操作和进程列表依赖 /proc，仅在 Linux 上可用。
"""

import os
import sys
import time
from sandbox.sandbox import Sandbox
from sandbox.exceptions import CommandExitException

def main() -> None:
    sb = Sandbox.create(
        api_key=os.environ["SANDBOX_API_KEY"],
        base_url=os.environ["SANDBOX_BASE_URL"],
    )

    try:
        # ---------------------------------------------------------------------
        # 1. 列出运行中的进程
        # ---------------------------------------------------------------------
        handle = sb.commands.run("sleep 30", background=True)

        time.sleep(0.2)
        procs = sb.commands.list()
        print(f"running processes: {len(procs)}")
        for p in procs:
            print(f"  pid={p.pid} cmd={p.cmd}")

        # ---------------------------------------------------------------------
        # 2. Connect — 通过 PID 接入运行中的进程
        # ---------------------------------------------------------------------
        connected = sb.commands.connect(handle.pid)
        print(f"connected to pid={connected.pid}")
        connected.disconnect()  # 只是断开流，进程仍在运行

        handle.kill()

        # ---------------------------------------------------------------------
        # 3. By-tag 操作（仅 Linux）
        # ---------------------------------------------------------------------
        if not sys.platform.startswith("linux"):
            print("skipping by-tag operations (non-Linux)")
        else:
            tag = "example-tag"
            # Python SDK 的 run() 不暴露 tag 参数；
            # by-tag 操作通过 connect_by_tag / kill_by_tag / send_stdin_by_tag 使用，
            # 需要在 nano-executor 侧通过其他方式（如 RunBackground+tag 的 Go/Node SDK）
            # 先启动一个带 tag 的进程，这里用 kill_by_tag 演示接口调用。
            result = sb.commands.kill_by_tag("nonexistent-tag")
            print(f"kill_by_tag (nonexistent): {result}")

            sb.commands.send_stdin_by_tag("nonexistent-tag", "data\n")
            print("send_stdin_by_tag sent (no-op for nonexistent tag)")

            conn = sb.commands.connect_by_tag("nonexistent-tag")
            print(f"connect_by_tag handle pid={conn.pid}")
            conn.disconnect()

        # ---------------------------------------------------------------------
        # 4. WatchDir — 监听目录文件系统事件；get_new_events() 轮询式获取
        # ---------------------------------------------------------------------
        watch_path = "/tmp/watch_example"
        sb.files.make_dir(watch_path)

        try:
            watcher = sb.files.watch_dir(watch_path, lambda e: None)  # callback可为空

            time.sleep(0.2)
            sb.files.write(watch_path + "/trigger.txt", "hello")
            time.sleep(0.5)

            # get_new_events() — 非阻塞轮询，取出自上次调用以来的所有事件
            events = watcher.get_new_events()
            print(f"WatchDir events received: {len(events)}")
            for e in events:
                print(f"  {e.type} {e.name}")

            watcher.stop()
        except Exception as e:
            print(f"WatchDir not supported in this environment: {e}")

        print("done.")

    finally:
        sb.kill()


if __name__ == "__main__":
    main()
