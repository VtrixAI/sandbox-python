"""
lifecycle.py — 停止 / 启动 / 延期 / 更新 / 列表 / 删除
"""
import asyncio
from src import (
    Client, ClientOptions, CreateOptions, Payload,
    ListOptions, Spec,
)


async def main():
    client = Client(ClientOptions(
        base_url="http://localhost:8080",
        token="your-token",
        project_id="seaclaw",
    ))

    # ── 列表查询 ────────────────────────────────────────
    result = await client.list(ListOptions(status="active", limit=10))
    print(f"Active sandboxes: {len(result.items)} (total={result.pagination.total})")
    for info in result.items:
        print(f"  {info.id}  status={info.status:<10}  expires={info.expire_at}")

    # ── 创建 ────────────────────────────────────────────
    sb = await client.create(CreateOptions(
        user_id="user-123",
        ttl_hours=1,
        payloads=[Payload(api="/api/v1/env", body={"LOG_LEVEL": "info"})],
    ))
    print(f"\nCreated: {sb.info.id}")

    # ── 查询单个 ────────────────────────────────────────
    info = await client.get(sb.info.id)
    print(f"Get: status={info.status}, ip={info.ip}")

    # ── 延期 12h（Atlas 使用秒）──────────────────────────
    await sb.extend(12 * 3600)
    print("Extended TTL by 12h")

    # ── 资源/镜像用 update()；env 等 payload 用 configure() ──
    await sb.configure([Payload(api="/api/v1/env", body={"LOG_LEVEL": "debug"})])
    print("Applied env via configure")

    # ── 立即应用配置 ────────────────────────────────────
    await sb.configure()
    print("Configured")

    # ── 刷新本地 info ───────────────────────────────────
    await sb.refresh()
    print(f"Refreshed: status={sb.info.status}")

    # ── 停止 / 启动 ─────────────────────────────────────
    await sb.stop()
    print("Stopped")
    await asyncio.sleep(2)

    await sb.start()
    print("Started")

    # ── 重启 ───────────────────────────────────────────
    await sb.restart()
    print("Restarted")

    # ── 删除 ────────────────────────────────────────────
    await sb.close()
    await sb.delete()
    print(f"Deleted {sb.info.id}")


if __name__ == "__main__":
    asyncio.run(main())
