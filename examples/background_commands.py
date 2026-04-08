"""
background_commands.py — 后台进程、流式输出、stdin、信号示例。

运行:
    SANDBOX_API_KEY=your-key SANDBOX_BASE_URL=https://api.sandbox.vtrix.ai python examples/background_commands.py

环境变量:
    SANDBOX_API_KEY   必填
    SANDBOX_BASE_URL  必填，例如 https://api.sandbox.vtrix.ai
"""

import os
import time
import threading
from sandbox.sandbox import Sandbox
from sandbox.exceptions import CommandExitException

def main() -> None:
    sb = Sandbox.create(api_key=os.environ["SANDBOX_API_KEY"])

    try:
        # ---------------------------------------------------------------------
        # 1. 后台运行并等待结果
        # ---------------------------------------------------------------------
        handle = sb.commands.run(
            "for i in 1 2 3; do echo line$i; sleep 0.3; done",
            background=True,
        )
        print(f"started pid={handle.pid}")

        try:
            result = handle.wait(
                on_stdout=lambda line: print(f"[stdout] {line}", end=""),
                on_stderr=lambda line: print(f"[stderr] {line}", end=""),
            )
            print(f"finished. exit_code={result.exit_code}")
        except CommandExitException as e:
            print(f"exited with code {e.exit_code}")

        # ---------------------------------------------------------------------
        # 2. 向运行中的进程发送 stdin
        # ---------------------------------------------------------------------
        cat = sb.commands.run("cat", background=True)
        time.sleep(0.2)
        sb.commands.send_stdin(cat.pid, "hello stdin\n")
        sb.commands.close_stdin(cat.pid)

        done = threading.Event()
        result_box: list = [None]

        def _wait_cat():
            try:
                result_box[0] = cat.wait()
            except CommandExitException as e:
                result_box[0] = e
            done.set()

        threading.Thread(target=_wait_cat, daemon=True).start()
        if done.wait(timeout=5) and result_box[0] is not None:
            r = result_box[0]
            stdout = r.stdout if hasattr(r, "stdout") else r.stdout
            print(f"cat echoed: {stdout!r}")

        # ---------------------------------------------------------------------
        # 3. 发送信号（SIGTERM / SIGINT / SIGHUP）
        # ---------------------------------------------------------------------
        sleeper = sb.commands.run("sleep 60", background=True)
        time.sleep(0.2)
        sb.commands.send_signal(sleeper.pid, "SIGTERM")

        terminated = threading.Event()

        def _wait_sleep():
            try:
                sleeper.wait()
            except CommandExitException:
                pass
            terminated.set()

        threading.Thread(target=_wait_sleep, daemon=True).start()
        if terminated.wait(timeout=5):
            print("sleep process terminated via SIGTERM")

        # SIGINT — interrupt (Ctrl-C 等价)
        sleeper2 = sb.commands.run("sleep 60", background=True)
        time.sleep(0.1)
        sb.commands.send_signal(sleeper2.pid, "SIGINT")
        done2 = threading.Event()

        def _wait_sleep2():
            try:
                sleeper2.wait()
            except CommandExitException:
                pass
            done2.set()

        threading.Thread(target=_wait_sleep2, daemon=True).start()
        if done2.wait(timeout=5):
            print("sleep process interrupted via SIGINT")

        # SIGHUP — hangup / 配置重载
        sleeper3 = sb.commands.run("sleep 60", background=True)
        time.sleep(0.1)
        sb.commands.send_signal(sleeper3.pid, "SIGHUP")
        done3 = threading.Event()

        def _wait_sleep3():
            try:
                sleeper3.wait()
            except CommandExitException:
                pass
            done3.set()

        threading.Thread(target=_wait_sleep3, daemon=True).start()
        if done3.wait(timeout=5):
            print("sleep process received SIGHUP")

        # ---------------------------------------------------------------------
        # 4. Disconnect（后台进程保持运行）
        # ---------------------------------------------------------------------
        bg = sb.commands.run("sleep 30", background=True)
        pid = bg.pid
        bg.disconnect()

        time.sleep(0.2)
        ok = sb.commands.kill(pid)
        print(f"kill after disconnect: {ok}")

        # ---------------------------------------------------------------------
        # 5. RunBackground 带 Tag（用于 by-tag 操作，仅 Linux 生效）
        # ---------------------------------------------------------------------
        tagged = sb.commands.run("sleep 5", background=True, tag="bg-example")
        print(f"tagged process pid={tagged.pid} tag=bg-example")
        tagged.kill()

        print("done.")

    finally:
        sb.kill()


if __name__ == "__main__":
    main()
