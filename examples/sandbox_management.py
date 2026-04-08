"""
sandbox_management.py — 沙箱生命周期管理示例：
Connect、List、SetTimeout、GetInfo、IsRunning、GetHost、GetMetrics。

运行:
    SANDBOX_API_KEY=your-key SANDBOX_BASE_URL=https://api.sandbox.vtrix.ai python examples/sandbox_management.py

环境变量:
    SANDBOX_API_KEY   必填
    SANDBOX_BASE_URL  必填，例如 https://api.sandbox.vtrix.ai
"""

import os
from sandbox.sandbox import Sandbox

def main() -> None:
    api_key = os.environ["SANDBOX_API_KEY"]
    base_url = os.environ["SANDBOX_BASE_URL"]

    # -------------------------------------------------------------------------
    # 1. 列出已有沙箱
    # -------------------------------------------------------------------------
    infos = Sandbox.list(api_key=api_key, base_url=base_url)
    print(f"existing sandboxes: {len(infos)}")

    # -------------------------------------------------------------------------
    # 2. 创建沙箱
    # -------------------------------------------------------------------------
    sb = Sandbox.create(api_key=api_key, base_url=base_url, timeout=120)
    print(f"created: {sb.sandbox_id}")

    try:
        # ---------------------------------------------------------------------
        # 3. GetInfo / IsRunning
        # ---------------------------------------------------------------------
        info = sb.get_info()
        print(f"state: {info.state}  running: {sb.is_running()}")

        # ---------------------------------------------------------------------
        # 4. SetTimeout — 延长生命周期
        # ---------------------------------------------------------------------
        try:
            sb.set_timeout(300)
            print("timeout extended to 300s")
        except Exception as e:
            print(f"SetTimeout not supported: {e}")

        # ---------------------------------------------------------------------
        # 5. GetHost — 沙箱内端口的代理地址
        # ---------------------------------------------------------------------
        host = sb.get_host(8080)
        print(f"proxy host for port 8080: {host}")

        # ---------------------------------------------------------------------
        # 6. GetMetrics — 当前 CPU / 内存占用
        # ---------------------------------------------------------------------
        try:
            metrics = sb.get_metrics()
            print(f"cpu={metrics.get('cpuUsedPct', 0):.2f}%  mem={metrics.get('memUsedMiB', 0):.2f}MiB")
        except Exception as e:
            print(f"GetMetrics not supported: {e}")

        # ---------------------------------------------------------------------
        # 7. Connect — 通过 ID 重新连接到已有沙箱
        # ---------------------------------------------------------------------
        sb2 = Sandbox.connect(sb.sandbox_id, api_key=api_key, base_url=base_url)
        result = sb2.commands.run("echo 'reconnected'")
        print(f"reconnected stdout: {result.stdout.strip()}")
        sb2.close()

        print("done.")

    finally:
        try:
            sb.kill()
        except Exception:
            pass


if __name__ == "__main__":
    main()
