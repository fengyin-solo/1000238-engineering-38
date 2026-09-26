#!/usr/bin/env python3
"""阶段三：启动前检查。

在依赖校验、数据准备之后、真正拉起服务之前执行，全部通过才允许启动：
1. 数据产物存在且与当前数据源同一次准备的结果（sha 对得上、四种状态都在）；
2. 后端应用可被 venv 解释器正常导入（语法/配置错误在这里暴露，而不是起了个黑盒）；
3. 8000 / 5173 端口空闲，避免“启动看似成功，实际连到了别人占的端口”。

退出码非 0 时打印每条失败原因与对应修复方式。
"""
from __future__ import annotations

import hashlib
import json
import socket
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
DATA_DIR = BACKEND_DIR / "data"
SAMPLE_FILE = DATA_DIR / "crew.sample.json"
LOCAL_FILE = DATA_DIR / "crew.local.json"
PREPARED_FILE = DATA_DIR / "crew.prepared.json"
VENV_PY = BACKEND_DIR / ".venv" / "bin" / "python"

PORTS = [8000, 5173]
REQUIRED_STATUSES = ["待进场", "在组", "已请假", "已离场"]


def current_source_sha() -> tuple[str | None, str | None]:
    """按数据准备时同样的优先级找到数据源并算 sha。"""
    source_path = LOCAL_FILE if LOCAL_FILE.exists() else SAMPLE_FILE
    if not source_path.exists():
        return None, f"数据源缺失：{source_path}"
    try:
        raw = source_path.read_text(encoding="utf-8")
        json.loads(raw)  # 顺便确认仍然是合法 JSON
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"数据源 {source_path.name} 无法读取或不是合法 JSON：{exc}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest(), None


def check_prepared_data() -> list[str]:
    errors: list[str] = []
    if not PREPARED_FILE.exists():
        return ["剧组成员数据产物不存在：backend/data/crew.prepared.json → 执行 make prepare-data"]

    try:
        payload = json.loads(PREPARED_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"数据产物损坏：{exc} → 重新执行 make prepare-data 原子生成"]

    meta = payload.get("_meta") if isinstance(payload, dict) else None
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(meta, dict) or not isinstance(rows, list) or not rows:
        errors.append("数据产物结构异常（缺少 _meta 或 rows 为空）→ 重新执行 make prepare-data")
        return errors

    source_sha, source_error = current_source_sha()
    if source_error:
        errors.append(source_error)
    elif meta.get("source_sha256") != source_sha:
        errors.append(
            "数据产物已过期：数据源（crew.local.json 或 crew.sample.json）在准备之后被改过，"
            "但 backend/data/crew.prepared.json 还是旧结果 → 重新执行 make prepare-data"
        )

    statuses = {row.get("status") for row in rows}
    missing = [status for status in REQUIRED_STATUSES if status not in statuses]
    if missing:
        errors.append(f"产物中缺少在组状态：{'、'.join(missing)} → 在数据源补齐后重新 make prepare-data")
    if len(rows) != len({row.get("成员编号") for row in rows}):
        errors.append("产物中成员编号不唯一 → 修复数据源后重新 make prepare-data")

    return errors


def check_backend_import() -> list[str]:
    if not VENV_PY.exists():
        return ["backend/.venv 不存在 → make check-deps-install"]
    proc = subprocess.run(
        [str(VENV_PY), "-c", "from app.main import app; print(app.title)"],
        cwd=str(BACKEND_DIR), capture_output=True, text=True,
    )
    if proc.returncode != 0:
        tail = " ".join((proc.stderr or "").strip().splitlines()[-3:])
        return [f"后端应用导入失败：{tail}"]
    return []


def check_ports() -> list[str]:
    errors: list[str] = []
    for port in PORTS:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                errors.append(
                    f"端口 {port} 已被占用（本流水线没有在管它）→ "
                    f"先停掉占用进程；若为上次 dev-up 残留，执行 make dev-down"
                )
    return errors


def main() -> int:
    checks = [
        ("数据产物", check_prepared_data),
        ("后端可导入", check_backend_import),
        ("端口占用", check_ports),
    ]
    failed: list[str] = []
    for name, check in checks:
        errors = check()
        if errors:
            print(f"✗ {name}：")
            for error in errors:
                print(f"    - {error}")
            failed.append(name)
        else:
            print(f"✓ {name}")

    if failed:
        print(f"\n启动前检查未通过（{ '、'.join(failed) }），未启动任何服务。", file=sys.stderr)
        print("按上面给出的方式修复后重新 make dev-up 即可，没有需要清理的残留。", file=sys.stderr)
        return 1

    print("\n✓ 启动前检查通过，可以启动")
    return 0


if __name__ == "__main__":
    sys.exit(main())
