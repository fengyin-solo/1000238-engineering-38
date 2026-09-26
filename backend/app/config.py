"""运行配置：端口、跨域、运行环境。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    app_name: str = "影视剧组拍摄制作管理平台"
    env: str = "local"
    port: int = 8000
    # 启动流水线 prepare-data 阶段的产物；存在时剧组成员数据以它为准，
    # 缺失或损坏时 store 会回落到内置示例数据，保证直接跑 uvicorn 也能起。
    crew_data_file: Path = field(
        default_factory=lambda: Path(os.environ.get("CREW_DATA_FILE", BACKEND_ROOT / "data" / "crew.prepared.json"))
    )
    allowed_origins: list[str] = field(
        default_factory=lambda: [
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ]
    )
    page_size_default: int = 20
    page_size_max: int = 200


settings = Settings()
