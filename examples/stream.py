"""
stream.py  — 流式执行 + exec_logs 日志回放
"""
import asyncio
import sys
from src import Client, ClientOptions, CreateOptions, RunOptions


async def main():
    client = Client(ClientOptions(
        base_url="http://localhost:8080",
        token="your-token",
        project_id="seaclaw",
    ))

    sb = await client.create(CreateOptions(user_id="user-123"))
    print(f"Sandbox: {sb.info.id}")

    cmd = r"""
        for i in $(seq 1 5); do
            echo "stdout line $i"
            echo "stderr line $i" >&2
            sleep 0.2
        done
    """

    # ── 实时流式输出 ────────────────────────────────────────
    print("Streaming:")
    async for ev in sb.run_command_stream(cmd):
        match ev.type:
            case "start":
                print("[start]")
            case "stdout":
                print(f"[stdout] {ev.data}")
            case "stderr":
                print(f"[stderr] {ev.data}")
            case "done":
                print("[done]")

    # ── detached 命令 ────────────────────────────────────────
    detached = await sb.run_command_detached(cmd)
    print(f"\nDetached cmdId={detached.cmd_id}")
    finished = await detached.wait()
    print(f"Wait done: exit_code={finished.exit_code}")

    # ── exec_logs 日志回放（已完成命令）───────────────────
    print("\nReplaying logs via exec_logs:")
    async for ev in sb.exec_logs(detached.cmd_id):
        match ev.type:
            case "stdout":
                print(f"  [replay stdout] {ev.data}")
            case "stderr":
                print(f"  [replay stderr] {ev.data}")

    # ── 流式输出到 writer ────────────────────────────────────
    print("\nStreaming to writers:")
    await sb.run_command(
        'echo "out_line" && echo "err_line" >&2',
        opts=RunOptions(stdout=sys.stdout, stderr=sys.stderr),
    )

    # ── Command.logs() / .stdout() / .stderr() ─────────────
    cmd2 = await sb.run_command_detached('echo "out_line" && echo "err_line" >&2')
    print("\nCommand.logs():")
    async for log in cmd2.logs():
        print(f"  [{log.stream}] {log.data}")

    cmd3 = await sb.run_command_detached('printf "line1\\nline2\\n"')
    out = await cmd3.stdout()
    print(f"\nCommand.stdout(): {out.strip()!r}")

    await sb.close()


if __name__ == "__main__":
    asyncio.run(main())
