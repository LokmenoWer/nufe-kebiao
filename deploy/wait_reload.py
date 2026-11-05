#!/usr/bin/env python3
"""等服务写出热重载结果：systemctl reload 只是发信号，成没成功要看 reload-status.json。

用法：wait_reload.py <状态文件> [超时秒数]。调用前要先删掉旧的状态文件。
退出码 0 表示新代码已经在运行；其余情况（失败、超时、代码没对上）调用方应改走重启。
"""
import json
import sys
import time
from pathlib import Path


def check(data: dict) -> str | None:
    """返回失败原因；None 表示成功。"""
    if data.get("last_reload_error"):
        return str(data["last_reload_error"])
    if data.get("needs_restart"):
        return "模块处于新旧混杂状态"
    if not data.get("in_sync"):
        return "运行中的代码和磁盘不一致"
    return None


def main(argv: list[str]) -> int:
    path = Path(argv[1])
    deadline = time.monotonic() + (float(argv[2]) if len(argv) > 2 else 45.0)
    while time.monotonic() < deadline:
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            time.sleep(0.2)
            continue
        why = check(data)
        if why:
            print(f"hot reload failed: {why}", file=sys.stderr)
            return 1
        return 0
    print("hot reload timed out: no status written", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
