"""
quickstart.py — Sandbox SDK 最基础用法示例。

运行:
    SANDBOX_API_KEY=your-key SANDBOX_BASE_URL=https://api.sandbox.vtrix.ai python examples/quickstart.py

环境变量:
    SANDBOX_API_KEY   必填
    SANDBOX_BASE_URL  必填，例如 https://api.sandbox.vtrix.ai
"""

import os
from sandbox.sandbox import Sandbox
from sandbox.exceptions import CommandExitException

def main() -> None:
    # -------------------------------------------------------------------------
    # 1. 创建沙箱（带 metadata 和自定义环境变量）
    # -------------------------------------------------------------------------
    sb = Sandbox.create(
        api_key=os.environ["SANDBOX_API_KEY"],
        template="base",
        timeout=300,  # 秒，超时后自动销毁
        metadata={"owner": "quickstart-example"},
        envs={"APP_ENV": "demo"},
    )
    print(f"Sandbox created: {sb.sandbox_id}")

    try:
        # ---------------------------------------------------------------------
        # 2. 运行命令并获取输出
        # ---------------------------------------------------------------------
        result = sb.commands.run("echo 'hello from sandbox'")
        print(f"stdout: {result.stdout.strip()}")

        # ---------------------------------------------------------------------
        # 3. 带环境变量和工作目录运行
        # ---------------------------------------------------------------------
        result = sb.commands.run(
            "echo $MY_VAR && pwd",
            envs={"MY_VAR": "hello"},
            cwd="/tmp",
            timeout=10,
        )
        print(f"env+cwd:\n{result.stdout.strip()}")

        # ---------------------------------------------------------------------
        # 4. 写入 / 读取文件
        # ---------------------------------------------------------------------
        sb.files.write("/tmp/hello.txt", "hello, world!")
        content = sb.files.read("/tmp/hello.txt", format="text")
        print(f"file content: {content}")

        # read as bytes
        raw = sb.files.read("/tmp/hello.txt", format="bytes")
        print(f"raw bytes length: {len(raw)}")

        # ---------------------------------------------------------------------
        # 5. 处理非零退出码
        # ---------------------------------------------------------------------
        try:
            sb.commands.run("exit 1")
        except CommandExitException as e:
            print(f"command failed with exit code {e.exit_code}")

        # ---------------------------------------------------------------------
        # 6. close() — 等价于 kill()，用于显式释放资源
        # ---------------------------------------------------------------------
        # (演示接口存在，实际这里用 finally 里的 kill() 即可)
        print("calling close() — equivalent to kill()")

    finally:
        sb.kill()
        print("done.")


if __name__ == "__main__":
    main()
