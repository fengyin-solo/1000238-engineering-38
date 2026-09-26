#!/usr/bin/env python3
"""阶段五：启动后冒烟检查——防止“服务起来了但列表缺项”。

检查三件事：
1. 后端健康检查通过且模块数完整；
2. /api/crew 列表总数与准备产物一致，待进场/在组/已请假/已离场四种状态都能取到，
   且按状态筛选接口逐一返回非空（前端列表缺项会在这里被发现）；
3. 前端 dev server 可达，且 /api 代理确实转发到后端（而不是 vite 自己 404）。

只做只读请求，不产生任何业务数据；失败返回非零，由 dev-up.sh 负责回收已启动的服务。
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BACKEND = "http://127.0.0.1:8000"
FRONTEND = "http://127.0.0.1:5173"
EXPECTED_MODULES = 20
REQUIRED_STATUSES = ["待进场", "在组", "已请假", "已离场"]
PREPARED_FILE = Path(__file__).resolve().parent.parent / "backend" / "data" / "crew.prepared.json"


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as response:
        if not (200 <= response.status < 300):
            raise RuntimeError(f"HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    failures: list[str] = []

    try:
        health = get_json(f"{BACKEND}/api/health")
        if health.get("ok") is not True:
            failures.append(f"/api/health 返回 ok != true：{health}")
        if int(health.get("modules", 0)) < EXPECTED_MODULES:
            failures.append(f"/api/health 模块数为 {health.get('modules')}，期望至少 {EXPECTED_MODULES}（路由漏注册？）")
        print(f"✓ 后端健康：modules={health.get('modules')}")
    except (urllib.error.URLError, OSError, ValueError, RuntimeError) as exc:
        failures.append(f"后端健康检查失败：{exc}")

    expected_total = 0
    try:
        prepared = json.loads(PREPARED_FILE.read_text(encoding="utf-8"))
        expected_total = int(prepared["_meta"]["count"])
    except (OSError, KeyError, ValueError) as exc:
        failures.append(f"无法读取数据产物 {PREPARED_FILE}：{exc}")

    if expected_total:
        try:
            listing = get_json(f"{BACKEND}/api/crew?page=1&size=200")
            total = int(listing.get("total", 0))
            if total != expected_total:
                failures.append(f"/api/crew 总数 {total} 与准备产物 {expected_total} 不一致（后端没加载准备好的数据？）")
            present = {row.get("status") for row in listing.get("items", [])}
            missing = [status for status in REQUIRED_STATUSES if status not in present]
            if missing:
                failures.append(f"/api/crew 列表缺项，看不到状态：{'、'.join(missing)}")

            for status in REQUIRED_STATUSES:
                filtered = get_json(f"{BACKEND}/api/crew?status={urllib.parse.quote(status)}&size=200")
                count = int(filtered.get("total", 0))
                if count < 1:
                    failures.append(f"/api/crew?status={status} 筛选结果为空，前端该状态页会显示缺项")
                else:
                    print(f"  - 状态「{status}」{count} 人")
            print(f"✓ 剧组人员列表：共 {total} 人，四种状态均可筛选")
        except (urllib.error.URLError, OSError, ValueError, RuntimeError) as exc:
            failures.append(f"/api/crew 冒烟失败：{exc}")

    try:
        with urllib.request.urlopen(f"{FRONTEND}/", timeout=5) as response:
            if response.status != 200:
                failures.append(f"前端首页返回 HTTP {response.status}")
        proxied = get_json(f"{FRONTEND}/api/health")
        if proxied.get("ok") is not True:
            failures.append("前端 /api 代理返回异常，未转发到后端")
        print("✓ 前端 dev server 与 /api 代理正常")
    except (urllib.error.URLError, OSError, ValueError, RuntimeError) as exc:
        failures.append(f"前端冒烟失败：{exc}（详见 .run/frontend.log）")

    if failures:
        print("\n冒烟检查未通过：", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print("\n✓ 冒烟检查全部通过，开发环境可用")
    return 0


if __name__ == "__main__":
    sys.exit(main())
