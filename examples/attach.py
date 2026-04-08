"""
attach.py  — 复用已有沙箱（不重新创建）
"""
import asyncio
import sys
from src import Client, ClientOptions


async def main(sandbox_id: str):
    client = Client(ClientOptions(
        base_url="http://localhost:8080",
        token="your-token",
        project_id="seaclaw",
    ))

    print(f"Attaching to existing sandbox: {sandbox_id}")
    sb = await client.attach(sandbox_id)
    print(f"Attached: {sb.info.id} (status={sb.info.status})")

    result = await sb.run_command("hostname && date")
    print(result.output)

    await sb.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python attach.py <sandbox-id>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
