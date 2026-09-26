#!/usr/bin/env python3
"""阶段四辅助：轮询 HTTP 地址直到返回 2xx，或超时退出。

用法：wait_for_http.py <url> <超时秒> [--json 字段=值 ...]
带 --json 时额外要求响应体是 JSON 且对应字段等于期望值（用于确认服务不只是端口活着）。
不依赖 curl（本机可能没有），只用标准库。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request


def parse_expectations(pairs: list[str]) -> dict[str, str]:
    expected: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            print(f"✗ --json 参数必须是 字段=值 形式：{pair}", file=sys.stderr)
            sys.exit(2)
        key, value = pair.split("=", 1)
        expected[key] = value
    return expected


def main() -> int:
    if len(sys.argv) < 3:
        print("用法：wait_for_http.py <url> <超时秒> [--json key=value ...]", file=sys.stderr)
        return 2
    url = sys.argv[1]
    try:
        timeout = float(sys.argv[2])
    except ValueError:
        print("✗ 超时秒数必须是数字", file=sys.stderr)
        return 2

    expected: dict[str, str] = {}
    if "--json" in sys.argv:
        expected = parse_expectations(sys.argv[sys.argv.index("--json") + 1:])

    deadline = time.monotonic() + timeout
    last_error = "尚未发起请求"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                if 200 <= response.status < 300:
                    body = response.read().decode("utf-8")
                    if not expected:
                        print(f"✓ {url} 已就绪（HTTP {response.status}）")
                        return 0
                    data = json.loads(body)
                    mismatches = [
                        f"{key} 期望 {value!r}，实际 {data.get(key)!r}"
                        for key, value in expected.items()
                        if str(data.get(key)).lower() != value.lower()
                    ]
                    if not mismatches:
                        print(f"✓ {url} 已就绪且响应字段符合预期：{', '.join(expected)}")
                        return 0
                    last_error = "；".join(mismatches)
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        time.sleep(0.5)

    print(f"✗ 等待 {url} 超时（{timeout:g}s），最后一次错误：{last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
