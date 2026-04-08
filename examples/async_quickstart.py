"""
async_quickstart.py — AsyncSandbox 异步用法示例。

运行:
    SANDBOX_API_KEY=your-key SANDBOX_BASE_URL=https://api.sandbox.vtrix.ai python examples/async_quickstart.py

环境变量:
    SANDBOX_API_KEY   必填
    SANDBOX_BASE_URL  必填，例如 https://api.sandbox.vtrix.ai
"""

import asyncio
import os
from sandbox.sandbox import AsyncSandbox
from sandbox.exceptions import CommandExitException

async def main() -> None:
    # -------------------------------------------------------------------------
    # 1. 创建沙箱（带 metadata 和自定义环境变量）
    # -------------------------------------------------------------------------
    sb = await AsyncSandbox.create(
        api_key=os.environ["SANDBOX_API_KEY"],
        base_url=os.environ["SANDBOX_BASE_URL"],
        template="base",
        timeout=300,
        metadata={"owner": "async-quickstart"},
        envs={"APP_ENV": "demo"},
    )
    print(f"Sandbox created: {sb.sandbox_id}")

    try:
        # ---------------------------------------------------------------------
        # 2. 运行命令并获取输出
        # ---------------------------------------------------------------------
        result = await sb.commands.run("echo 'hello async'")
        print(f"stdout: {result.stdout.strip()}")

        # ---------------------------------------------------------------------
        # 3. 带环境变量和工作目录运行
        # ---------------------------------------------------------------------
        result = await sb.commands.run(
            "echo $MY_VAR && pwd",
            envs={"MY_VAR": "hello"},
            cwd="/tmp",
            timeout=10,
        )
        print(f"env+cwd:\n{result.stdout.strip()}")

        # ---------------------------------------------------------------------
        # 4. 写入 / 读取文件
        # ---------------------------------------------------------------------
        await sb.files.write("/tmp/async_hello.txt", "async hello!")
        content = await sb.files.read("/tmp/async_hello.txt", format="text")
        print(f"file: {content}")

        # read as bytes
        raw = await sb.files.read("/tmp/async_hello.txt", format="bytes")
        print(f"raw bytes length: {len(raw)}")

        # ---------------------------------------------------------------------
        # 5. 后台运行，流式接收 stdout
        # ---------------------------------------------------------------------
        handle = await sb.commands.run(
            "for i in 1 2 3; do echo line$i; sleep 0.3; done",
            background=True,
        )
        try:
            result = await handle.wait(
                on_stdout=lambda line: print(f"[bg] {line}", end=""),
            )
            print(f"background exit_code={result.exit_code}")
        except CommandExitException as e:
            print(f"background exit_code={e.exit_code}")

        # ---------------------------------------------------------------------
        # 6. 非零退出码
        # ---------------------------------------------------------------------
        try:
            await sb.commands.run("exit 2")
        except CommandExitException as e:
            print(f"exit code: {e.exit_code}")

        print("done.")

    finally:
        await sb.kill()


if __name__ == "__main__":
    asyncio.run(main())
