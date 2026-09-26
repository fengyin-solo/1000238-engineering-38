#!/usr/bin/env python3
"""阶段二：剧组成员示例数据准备。

职责：
1. 读取数据源——优先本地覆盖文件 backend/data/crew.local.json（gitignore，人工维护），
   不存在时回落到随仓库分发的 backend/data/crew.sample.json；源文件永远不会被本脚本写入。
2. 全量校验：结构、必填、唯一、枚举、日期格式与状态-日期一致性，并强制四种在组状态
   （含“已请假”）各至少一条，避免起服务后才发现列表缺项。
3. 校验通过后用“临时文件 + os.replace”原子产出 backend/data/crew.prepared.json；
   只有完全成功才落盘，失败时旧产物原样保留（不会残留写了一半的结果）。

退出码：0 成功（产物已就绪/本就最新）；1 数据源或数据内容错误；2 运行环境错误。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "backend" / "data"
SAMPLE_FILE = DATA_DIR / "crew.sample.json"
LOCAL_FILE = DATA_DIR / "crew.local.json"
PREPARED_FILE = DATA_DIR / "crew.prepared.json"

REQUIRED_FIELDS = ["成员编号", "姓名", "岗位职务", "所属组别", "联系电话", "进场日期", "离场日期", "在组状态"]
STATUSES = ["待进场", "在组", "已请假", "已离场"]
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PHONE_RE = re.compile(r"^1\d{10}$")
MEMBER_NO_RE = re.compile(r"^CREW-\d{4}$")

# 岗位/组别白名单：本地开发环境之间口径不一致（岗位名称随手写）是这次要消灭的问题之一，
# 所以枚举固化在这里，新增岗位需要显式改白名单。
ALLOWED_GROUPS = {
    "导演组", "摄影组", "灯光组", "录音组", "美术组", "置景组", "道具组",
    "服装组", "化妆组", "制片组", "场务组", "司机组", "特效组", "外联组",
}
ALLOWED_POSITIONS = {
    "导演", "执行导演", "副导演", "场记",
    "摄影指导", "摄影师", "副摄影", "跟机员",
    "灯光师", "灯光助理",
    "录音师", "录音助理",
    "美术指导", "美术助理", "置景师",
    "道具师", "道具助理",
    "造型指导", "服装师", "服装助理",
    "化妆师", "化妆助理",
    "制片人", "执行制片人", "制片主任", "现场制片", "生活制片", "外联制片",
    "场务", "场务组长",
    "司机", "特效指导", "特效助理",
}

# status -> pending 口径，和 backend/app/services/crew.py 的状态流转保持一致：
# 终态“已离场”不再待处理；其余状态（含已请假）都还需要跟进。
PENDING_BY_STATUS = {"待进场": True, "在组": True, "已请假": True, "已离场": False}


class ValidationError(Exception):
    """数据校验失败：消息面向使用者，直接说明哪一行哪里不对。"""


def fail(message: str, code: int = 1) -> None:
    print(f"✗ {message}", file=sys.stderr)
    sys.exit(code)


def load_source(path: Path) -> tuple[list[dict[str, Any]], str, str]:
    """读取数据源，返回 (行列表, 源文件标签, 源文件 sha256)。源文件只读。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"无法读取剧组成员数据源 {path}：{exc}")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"{path} 不是合法 JSON：第 {exc.lineno} 行第 {exc.colno} 列附近 {exc.msg}")
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        fail(f"{path} 结构错误：顶层必须是对象，且包含数组字段 rows")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    label = "crew.local.json（本地覆盖）" if path == LOCAL_FILE else "crew.sample.json（仓库样例）"
    return payload["rows"], label, digest


def validate_date(value: Any, field: str, index: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not DATE_RE.match(value):
        raise ValidationError(f"第 {index} 行「{field}」必须是 YYYY-MM-DD 格式，实际为 {value!r}")
    import datetime

    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        raise ValidationError(f"第 {index} 行「{field}」不是真实日期：{value!r}") from None
    return value


def validate_rows(rows: list[Any]) -> list[dict[str, Any]]:
    if not rows:
        raise ValidationError("rows 为空：剧组成员示例数据至少要有四行（四种在组状态各一条）")

    seen_numbers: set[str] = set()
    seen_phones: set[str] = set()
    status_counts = {status: 0 for status in STATUSES}

    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValidationError(f"第 {index} 行必须是对象，实际为 {type(row).__name__}")

        missing = [field for field in REQUIRED_FIELDS if field not in row]
        if missing:
            raise ValidationError(f"第 {index} 行缺少字段：{'、'.join(missing)}")

        member_no = row["成员编号"]
        name = row["姓名"]
        position = row["岗位职务"]
        group = row["所属组别"]
        phone = row["联系电话"]
        status = row["在组状态"]
        enter = row["进场日期"]
        leave = row["离场日期"]

        if not isinstance(member_no, str) or not MEMBER_NO_RE.match(member_no):
            raise ValidationError(f"第 {index} 行「成员编号」必须形如 CREW-0001，实际为 {member_no!r}")
        if member_no in seen_numbers:
            raise ValidationError(f"第 {index} 行成员编号重复：{member_no}")
        seen_numbers.add(member_no)

        if not isinstance(name, str) or not name.strip():
            raise ValidationError(f"第 {index} 行「姓名」不能为空")
        if position not in ALLOWED_POSITIONS:
            raise ValidationError(
                f"第 {index} 行岗位职务 {position!r} 不在岗位白名单内；"
                "若是新岗位，请在 scripts/prepare_crew_data.py 的 ALLOWED_POSITIONS 中登记"
            )
        if group not in ALLOWED_GROUPS:
            raise ValidationError(
                f"第 {index} 行所属组别 {group!r} 不在组别白名单内；"
                "若是新组别，请在 scripts/prepare_crew_data.py 的 ALLOWED_GROUPS 中登记"
            )
        if not isinstance(phone, str) or not PHONE_RE.match(phone):
            raise ValidationError(f"第 {index} 行「联系电话」必须是 1 开头的 11 位手机号，实际为 {phone!r}")
        if phone in seen_phones:
            raise ValidationError(f"第 {index} 行联系电话重复：{phone}")
        seen_phones.add(phone)

        if status not in STATUSES:
            raise ValidationError(f"第 {index} 行在组状态 {status!r} 非法，允许值：{'、'.join(STATUSES)}")
        status_counts[status] += 1

        enter_date = validate_date(enter, "进场日期", index)
        leave_date = validate_date(leave, "离场日期", index)
        if enter_date is None:
            raise ValidationError(f"第 {index} 行「进场日期」不能为空")

        # 状态与日期的一致性规则——这是跨机器最容易出现的“请假状态/进场日期对不上”：
        if status == "已离场":
            if leave_date is None:
                raise ValidationError(f"第 {index} 行状态为「已离场」，但离场日期为空")
            if leave_date < enter_date:
                raise ValidationError(f"第 {index} 行离场日期 {leave_date} 早于进场日期 {enter_date}")
        else:
            if leave_date is not None:
                raise ValidationError(
                    f"第 {index} 行状态为「{status}」，离场日期必须留空（null），实际为 {leave_date}"
                )

    absent_statuses = [status for status, count in status_counts.items() if count == 0]
    if absent_statuses:
        raise ValidationError(
            "列表缺项：以下在组状态在示例数据里一条都没有，列表页会看不到对应筛选项——"
            f"{'、'.join(absent_statuses)}；请在数据源中补齐（每种状态至少一条）"
        )

    return rows


def build_prepared(rows: list[dict[str, Any]], source_label: str, source_sha: str) -> dict[str, Any]:
    """补出与 app/store 口径一致的 id/status/pending/abnormal 运行期字段。"""
    prepared_rows: list[dict[str, Any]] = []
    for entry_id, row in enumerate(rows, start=1):
        status = row["在组状态"]
        prepared = {"id": entry_id}
        prepared.update({field: row[field] for field in REQUIRED_FIELDS})
        prepared["status"] = status
        prepared["pending"] = PENDING_BY_STATUS[status]
        prepared["abnormal"] = False
        prepared_rows.append(prepared)
    return {
        "_meta": {
            "module": "crew",
            "source": source_label,
            "source_sha256": source_sha,
            "count": len(prepared_rows),
        },
        "rows": prepared_rows,
    }


def write_atomic(payload: dict[str, Any]) -> None:
    """先写同目录临时文件再 os.replace：要么完整出现新产物，要么旧产物纹丝不动。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".crew.prepared.", suffix=".tmp", dir=DATA_DIR)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_path, PREPARED_FILE)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="准备并校验剧组成员示例数据")
    parser.add_argument("--source", choices=["auto", "sample", "local"], default="auto",
                        help="数据源选择：auto=本地覆盖优先（默认），sample=强制仓库样例，local=强制本地覆盖")
    args = parser.parse_args()

    if args.source == "local" and not LOCAL_FILE.exists():
        fail(f"指定了 --source local，但本地覆盖文件不存在：{LOCAL_FILE}；"
             f"可先复制样例：cp {SAMPLE_FILE} {LOCAL_FILE}")
    if args.source == "sample":
        source_path = SAMPLE_FILE
    else:
        source_path = LOCAL_FILE if LOCAL_FILE.exists() else SAMPLE_FILE

    if not source_path.exists():
        fail(f"剧组成员数据源缺失：{source_path}")

    rows, source_label, source_sha = load_source(source_path)
    try:
        validate_rows(rows)
    except ValidationError as exc:
        fail(f"剧组成员数据校验失败（数据源 {source_label}）：{exc}\n"
             "修复数据源后重新执行 make prepare-data 即可，不会影响任何已有文件")

    payload = build_prepared(rows, source_label, source_sha)

    # 产物已与源一致则跳过写入：重复执行幂等，不制造无意义变更。
    if PREPARED_FILE.exists():
        try:
            current = json.loads(PREPARED_FILE.read_text(encoding="utf-8"))
            if current.get("_meta", {}).get("source_sha256") == source_sha:
                print(f"✓ 数据准备已就绪：{PREPARED_FILE.relative_to(REPO_ROOT)} "
                      f"（{len(rows)} 人，来源 {source_label}，无变更）")
                return 0
        except (OSError, json.JSONDecodeError):
            pass  # 旧产物损坏，下面直接原子替换

    write_atomic(payload)
    print(f"✓ 数据准备完成：{PREPARED_FILE.relative_to(REPO_ROOT)} "
          f"（{len(rows)} 人，来源 {source_label}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
