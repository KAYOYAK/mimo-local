"""
对比测试：U 盘版的参数 vs 现在用的参数，显存差多少。

这决定「参数移植」值得不值得。
两套都跑 64K 上下文，只差 flash-attn 和 KV 量化。
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config


def gpu_mem() -> int:
    """当前显存占用（MiB）。"""
    r = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=10,
    )
    try:
        return int(r.stdout.strip().splitlines()[0])
    except Exception:
        return -1


def wait_ready(port: int, timeout=120) -> bool:
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def run_case(name: str, extra: list[str]) -> int:
    """起一个引擎，量显存，关掉。返回显存占用。"""
    base = gpu_mem()
    print(f"\n  [{name}]  启动前基线 {base} MiB")
    cmd = [
        str(config.ENGINE),
        "-m", str(config.current_model()),
        "--mmproj", str(config.current_mmproj()),
        "--host", "127.0.0.1", "--port", "8080",
        "--ctx-size", "65536",
        "--n-gpu-layers", "99",
        "--jinja",
        *extra,
    ]
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_ready(8080):
            print("    ✗ 没就绪")
            return -1
        time.sleep(2)
        used = gpu_mem()
        print(f"    ✓ 就绪，占用 {used} MiB  （比基线多 {used - base} MiB）")
        return used - base
    finally:
        p.terminate()
        try:
            p.wait(timeout=10)
        except Exception:
            p.kill()
        time.sleep(3)


def main():
    print("=" * 62)
    print("  显存对比：现在的参数 vs U 盘版参数")
    print("=" * 62)

    # 清场
    subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"], capture_output=True)
    time.sleep(2)

    cases = [
        ("现在用的", ["--temp", "0.6"]),
        ("U盘版参数", [
            "--temp", "0.6", "--top-p", "0.95", "--top-k", "20",
            "--min-p", "0", "--repeat-penalty", "1.1",
            "--reasoning-format", "deepseek",
            "--flash-attn", "on",
            "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
        ]),
    ]

    results = {}
    for name, extra in cases:
        n = run_case(name, extra)
        results[name] = n
        time.sleep(2)

    print("\n" + "=" * 62)
    a = results.get("现在用的")
    b = results.get("U盘版参数")
    if a and b and a > 0 and b > 0:
        diff = a - b
        print(f"  现在：{a} MiB")
        print(f"  U盘：{b} MiB")
        print(f"  差  ：{diff} MiB  {'（U盘版更省）' if diff > 0 else '（没省，甚至更多）'}")
    print("=" * 62)


if __name__ == "__main__":
    main()
