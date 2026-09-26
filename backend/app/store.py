"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
剧组成员（crew）的数据来自启动流水线 prepare-data 阶段产出的
backend/data/crew.prepared.json（受校验、可被 crew.local.json 覆盖）；
产物缺失或损坏时回落到 app/seed.py 里的内置示例，保证后端单独启动也不会空。
"""
from __future__ import annotations

import json
from typing import Any

from app.config import settings
from app.seed import SEED_ROWS


def load_crew_rows() -> list[dict[str, Any]]:
    """读取数据准备阶段的剧组成员产物；任何异常都回落到内置 seed。"""
    path = settings.crew_data_file
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload["rows"]
        if not isinstance(rows, list) or not rows:
            raise ValueError("rows 为空或不是数组")
        return rows
    except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError):
        return [dict(row) for row in SEED_ROWS["crew"]]


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        self._tables["crew"] = [dict(row) for row in load_crew_rows()]

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
