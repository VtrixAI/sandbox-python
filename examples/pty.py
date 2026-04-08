"""
pty.py — PTY（伪终端）操作示例：创建、调整大小、发送输入、等待退出。

运行:
    SANDBOX_API_KEY=your-key SANDBOX_BASE_URL=https://api.sandbox.vtrix.ai python examples/pty.py

环境变量:
    SANDBOX_API_KEY   必填
    SANDBOX_BASE_URL  必填，例如 https://api.sandbox.vtrix.ai
"""

import os
import sys
import time
import threading
from sandbox.sandbox import Sandbox
from sandbox.types import PtySize

def main() -> None:
    sb = Sandbox.create(
        api_key=os.environ["SANDBOX_API_KEY"],
        base_url=os.environ["SANDBOX_BASE_URL"],
    )

    try:
        # ---------------------------------------------------------------------
        # 1. 创建 PTY（bash shell）
        # ---------------------------------------------------------------------
        handle = sb.pty.create(PtySize(rows=24, cols=80))
        print(f"PTY pid={handle.pid}")

        # ---------------------------------------------------------------------
        # 1b. 自定义环境变量和工作目录
        # ---------------------------------------------------------------------
        handle2 = sb.pty.create(
            PtySize(rows=24, cols=80),
            envs={"TERM": "xterm-256color"},
            cwd="/tmp",
        )
        print(f"custom PTY pid={handle2.pid}")
        time.sleep(0.1)
        sb.pty.kill(handle2.pid)

        # ---------------------------------------------------------------------
        # 2. 调整终端大小
        # ---------------------------------------------------------------------
        sb.pty.resize(handle.pid, PtySize(rows=40, cols=200))
        print("resized to 40x200")

        # ---------------------------------------------------------------------
        # 3. 发送输入（模拟在终端中打字）
        # ---------------------------------------------------------------------
        time.sleep(0.3)
        sb.pty.send_stdin(handle.pid, b"echo 'hello from pty'\n")
        time.sleep(0.3)

        # ---------------------------------------------------------------------
        # 4. 退出 shell，等待 PTY 关闭
        # ---------------------------------------------------------------------
        sb.pty.send_stdin(handle.pid, b"exit\n")

        done = threading.Event()
        result_box = [None]

        def _wait():
            result_box[0] = handle.wait()
            done.set()

        t = threading.Thread(target=_wait, daemon=True)
        t.start()

        if done.wait(timeout=10):
            r = result_box[0]
            if r is not None:
                print(f"PTY exited with code {r.exit_code}")
        else:
            sb.pty.kill(handle.pid)
            print("PTY killed after timeout")

        print("done.")

    finally:
        sb.kill()


if __name__ == "__main__":
    main()
