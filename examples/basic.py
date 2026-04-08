"""
basic.py  — 创建沙箱 → 执行命令 → detached 命令 → 文件操作 → 关闭
"""
import asyncio
from src import Client, ClientOptions, CreateOptions, Spec, RunOptions


async def main():
    client = Client(ClientOptions(
        base_url="http://localhost:8080",
        token="your-token",
        project_id="seaclaw",
    ))

    print("Creating sandbox...")
    sb = await client.create(CreateOptions(
        user_id="user-123",
        spec=Spec(cpu="2", memory="4Gi"),
    ))
    print(f"Sandbox ready: {sb.info.id} (status={sb.info.status})")

    # 一次性执行（阻塞直到命令结束）
    result = await sb.run_command("echo hello && uname -a")
    print(f"exit_code={result.exit_code}")
    print(f"output:\n{result.output}")

    # 带 args 和选项
    result2 = await sb.run_command("ls", ["-la", "/tmp"], RunOptions(working_dir="/tmp", timeout_sec=10))
    print(f"ls -la /tmp:\n{result2.output.strip()}")

    # detached 命令：立即返回 Command，稍后 wait()
    cmd = await sb.run_command_detached(
        "for i in $(seq 1 3); do echo bg_$i; sleep 0.3; done",
    )
    print(f"Detached cmdId={cmd.cmd_id}  pid={cmd.pid}")

    # 用 wait() 等待结束，获取 CommandFinished
    finished = await cmd.wait()
    print(f"Detached done: exit_code={finished.exit_code}")
    print(f"Detached output:\n{finished.output.strip()}")

    # get_command：通过 cmd_id 重新拿到 Command 对象
    cmd2 = sb.get_command(cmd.cmd_id)
    stdout_text = await cmd2.stdout()
    print(f"Re-fetched stdout: {stdout_text.strip()}")

    # kill 示例（先启动一个 sleep，再 kill 它）
    sleeper = await sb.run_command_detached("sleep 60")
    await sb.kill(sleeper.cmd_id, "SIGKILL")
    print(f"Killed sleep, cmdId={sleeper.cmd_id}")

    # 文件操作
    wr = await sb.write("/tmp/hello.txt", "Hello, Sandbox!\nLine 2\n")
    print(f"Written {wr.bytes_written} bytes")

    rr = await sb.read("/tmp/hello.txt")
    print(f"Content:\n{rr.content}")

    er = await sb.edit("/tmp/hello.txt", "Hello, Sandbox!", "Hello, World!")
    print(f"Edit: {er.message}")

    await sb.close()


if __name__ == "__main__":
    asyncio.run(main())
