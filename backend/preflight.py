"""启动前流水线：依赖校验 → 数据准备 → 启动前检查。

把原本靠人工确认的三件事串成一条可重复执行的流程：

1. 依赖校验：后端虚拟环境、requirements.txt 里的包、前端 node/npm；
2. 数据准备：校验 app/seed.py 示例数据完整且自洽（只读，绝不改动已有数据），
   并把期望的数据清单写入临时目录，供启动前检查逐项核对；
3. 启动前检查：用虚拟环境真实导入 app.main、核对内存仓库与数据清单一致、
   确认端口与运行脚本就绪。

约定：
- 任一环节失败会打印具体原因并以环节序号（1/2/3）作为退出码；
- 每次运行先清空自己的临时目录 backend/.preflight/，失败时也会删掉，
  修复问题后直接重跑即可，不会残留上一次的结果；
- 前端相关的缺失只算“提醒”（不阻塞后端启动），后端相关的缺失才算失败。

用法：python3 preflight.py（在 backend/ 目录下，或直接用 make preflight）。
"""
from __future__ import annotations

import ast
import importlib
import json
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

MIN_PYTHON = (3, 10)
FRONTEND_PORT = 5173  # 与 frontend/vite.config.ts 的 server.port 保持一致
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class Paths:
    """流水线涉及的所有路径，集中在一处方便测试时替换。"""

    backend: Path
    frontend: Path
    workdir: Path
    venv_python: Path
    seed_file: Path
    requirements: Path
    run_sh: Path


def build_paths() -> Paths:
    backend = Path(__file__).resolve().parent
    return Paths(
        backend=backend,
        frontend=backend.parent / "frontend",
        workdir=backend / ".preflight",
        venv_python=backend / ".venv" / "bin" / "python",
        seed_file=backend / "app" / "seed.py",
        requirements=backend / "requirements.txt",
        run_sh=backend / "run.sh",
    )


@dataclass
class StageOutcome:
    problems: list[str] = field(default_factory=list)
    summary: str = ""
    warnings: list[str] = field(default_factory=list)


def reset_workdir(paths: Paths) -> None:
    """每次运行先清空临时目录，保证不残留上一次的结果。"""
    shutil.rmtree(paths.workdir, ignore_errors=True)
    paths.workdir.mkdir(parents=True)


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 60) -> tuple[int, str, str]:
    proc = subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _last_lines(text: str, count: int = 3) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return " / ".join(lines[-count:]) if lines else "（无输出）"


# ---------------------------------------------------------------- 环节一：依赖校验

def _requirement_modules(requirements: Path) -> list[str]:
    modules = []
    for raw in requirements.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name = re.split(r"[<>=!~;\[]", line, maxsplit=1)[0].strip()
        if name:
            modules.append(name)
    return modules


def check_dependencies(paths: Paths) -> StageOutcome:
    outcome = StageOutcome()
    details: list[str] = []

    if not paths.venv_python.exists():
        outcome.problems.append(
            "未发现后端虚拟环境 backend/.venv；请先执行 make install"
            "（或 cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt）"
        )
        return outcome

    rc, out, err = _run(
        [str(paths.venv_python), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"]
    )
    if rc != 0:
        outcome.problems.append(f"无法执行 backend/.venv/bin/python：{_last_lines(err)}；建议删除 .venv 后重新 make install")
        return outcome
    version = tuple(int(part) for part in out.split("."))
    if version < MIN_PYTHON:
        outcome.problems.append(
            f"虚拟环境 Python 版本为 {out}，要求 >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}；"
            "请用新版本解释器重建：cd backend && rm -rf .venv && make install"
        )
        return outcome
    details.append(f"Python {out}")

    modules = _requirement_modules(paths.requirements)
    missing: list[str] = []
    for name in modules:
        rc, _, err = _run([str(paths.venv_python), "-c", f"import {name}"])
        if rc != 0:
            missing.append(f"{name}（{_last_lines(err, 1)}）")
    if missing:
        outcome.problems.append(
            f"requirements.txt 中的依赖未装齐：{'、'.join(missing)}；"
            "修复：cd backend && .venv/bin/pip install -r requirements.txt"
        )
    else:
        details.append(f"依赖 {'/'.join(modules)} 已安装")

    node = shutil.which("node")
    npm = shutil.which("npm")
    if node and npm:
        _, node_version, _ = _run(["node", "--version"])
        details.append(f"node {node_version}")
    else:
        outcome.warnings.append("未找到 node/npm，前端无法启动；如需前端请先安装 Node.js")

    if not (paths.frontend / "node_modules").is_dir():
        outcome.warnings.append("前端依赖未安装（缺 frontend/node_modules）；如需前端请执行 cd frontend && npm install")

    outcome.summary = "；".join(details)
    return outcome


# ---------------------------------------------------------------- 环节二：数据准备

def _reset_app_imports(backend: Path) -> None:
    """清掉已缓存的 app.* 模块再从指定目录导入，保证多次运行/测试之间互不影响。"""
    for name in [name for name in sys.modules if name == "app" or name.startswith("app.")]:
        del sys.modules[name]
    sys.path.insert(0, str(backend))


def _router_constants(router_file: Path) -> dict[str, list[str]]:
    """用 AST 读出路由里的 LIST_FIELDS / STATUSES，避免为两个常量去 import fastapi。"""
    tree = ast.parse(router_file.read_text(encoding="utf-8"), filename=str(router_file))
    found: dict[str, list[str]] = {}
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if not (isinstance(target, ast.Name) and target.id in ("LIST_FIELDS", "STATUSES")):
            continue
        if isinstance(node.value, (ast.List, ast.Tuple)):
            found[target.id] = [
                elt.value
                for elt in node.value.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            ]
    return found


def _row_label(module: str, index: int, row: dict) -> str:
    return f"{module} 第 {index} 行（id={row.get('id', '?')}）"


def _validate_row(module: str, index: int, row: dict, list_fields: list[str], required: list[str], statuses: list[str]) -> list[str]:
    problems: list[str] = []
    label = _row_label(module, index, row)

    missing_fields = [name for name in list_fields if name not in row]
    if missing_fields:
        problems.append(f"{label}：缺少列表字段 {'、'.join(missing_fields)}，启动后列表会缺项")

    blank_required = [name for name in required if not str(row.get(name) or "").strip()]
    if blank_required:
        problems.append(f"{label}：必填字段 {'、'.join(blank_required)} 为空")

    status = row.get("status")
    if statuses and status not in statuses:
        problems.append(f"{label}：状态「{status}」不在允许序列 {'/'.join(statuses)} 里")

    for field_name, value in row.items():
        looks_like_date = "日期" in field_name or (isinstance(value, str) and DATE_RE.match(value))
        if not looks_like_date:
            continue
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{label}：日期字段「{field_name}」为空或不是字符串")
            continue
        try:
            date.fromisoformat(value)
        except ValueError:
            problems.append(f"{label}：{field_name}「{value}」不是合法的 YYYY-MM-DD 日期")

    if module == "crew":
        start, end = row.get("进场日期"), row.get("离场日期")
        try:
            if start and end and date.fromisoformat(str(start)) > date.fromisoformat(str(end)):
                problems.append(f"{label}：进场日期 {start} 晚于离场日期 {end}")
        except ValueError:
            pass  # 非法日期已在上面逐字段报告

    return problems


def prepare_data(paths: Paths) -> StageOutcome:
    outcome = StageOutcome()

    if not paths.seed_file.exists():
        outcome.problems.append(
            "缺少 app/seed.py 示例数据文件；本流程不会自动生成以免覆盖本地修改，"
            "请从版本库恢复（git checkout -- backend/app/seed.py）后重跑"
        )
        return outcome

    _reset_app_imports(paths.backend)
    try:
        seed_module = importlib.import_module("app.seed")
        store_module = importlib.import_module("app.store")
    except Exception as exc:  # seed.py 语法错误等，启动时才会炸，这里提前拦下
        outcome.problems.append(f"示例数据加载失败：{exc}；请检查 app/seed.py 是否被改坏")
        return outcome
    seed_rows: dict = getattr(seed_module, "SEED_ROWS", {})
    if not seed_rows:
        outcome.problems.append("app/seed.py 里的 SEED_ROWS 为空，启动后所有列表都会缺项")
        return outcome

    routers_dir = paths.backend / "app" / "routers"
    modules = sorted(p.stem for p in routers_dir.glob("*.py") if p.stem != "__init__")
    manifest: dict[str, dict] = {}
    total_rows = 0

    for module in modules:
        rows = seed_rows.get(module)
        if not rows:
            outcome.problems.append(f"模块 {module} 在 app/seed.py 中没有示例数据，启动后列表会缺项")
            continue

        constants = _router_constants(routers_dir / f"{module}.py")
        list_fields = constants.get("LIST_FIELDS", [])
        router_statuses = constants.get("STATUSES", [])

        try:
            service_module = importlib.import_module(f"app.services.{module}")
        except Exception as exc:
            outcome.problems.append(f"业务模块 app.services.{module} 导入失败：{exc}")
            continue
        required = list(getattr(service_module, "REQUIRED_FIELDS", []))
        statuses = list(getattr(service_module, "STATUS_ORDER", []))
        action_rules = dict(getattr(service_module, "ACTION_RULES", {}))

        if router_statuses and statuses and router_statuses != statuses:
            outcome.problems.append(
                f"模块 {module} 路由状态 {router_statuses} 与服务层状态序列 {statuses} 不一致"
            )
        bad_targets = sorted({target for target in action_rules.values() if target not in statuses})
        if bad_targets:
            outcome.problems.append(f"模块 {module} 的动作目标状态 {'、'.join(bad_targets)} 不在状态序列里")

        ids: list = []
        serials: dict[str, set] = {}
        for index, row in enumerate(rows, start=1):
            outcome.problems.extend(_validate_row(module, index, row, list_fields, required, statuses))
            row_id = row.get("id")
            if row_id in ids:
                outcome.problems.append(f"{_row_label(module, index, row)}：id {row_id} 重复")
            ids.append(row_id)
            for field_name, value in row.items():
                if field_name.endswith("编号"):
                    seen = serials.setdefault(field_name, set())
                    if value in seen:
                        outcome.problems.append(f"{_row_label(module, index, row)}：{field_name}「{value}」重复")
                    seen.add(value)

        manifest[module] = {"rows": len(rows), "ids": ids}
        total_rows += len(rows)

    extra = sorted(set(seed_rows) - set(modules))
    if extra:
        outcome.warnings.append(f"示例数据里的 {'、'.join(extra)} 没有对应路由，启动后不会被使用")

    if outcome.problems:
        return outcome

    # 只写临时目录里的清单，绝不回写 app/seed.py，已有数据不被覆盖。
    manifest_file = paths.workdir / "manifest.json"
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    outcome.summary = f"{len(manifest)} 个模块共 {total_rows} 条示例记录，数据清单已写入 {manifest_file.relative_to(paths.backend.parent)}"
    return outcome


# ---------------------------------------------------------------- 环节三：启动前检查

def _port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def check_before_start(paths: Paths) -> StageOutcome:
    outcome = StageOutcome()
    details: list[str] = []

    rc, _, err = _run([str(paths.venv_python), "-c", "import app.main"], cwd=paths.backend)
    if rc != 0:
        outcome.problems.append(f"app.main 导入失败，直接启动会报错：{_last_lines(err)}")
        return outcome
    details.append("app.main 可正常导入")

    manifest_file = paths.workdir / "manifest.json"
    if not manifest_file.exists():
        outcome.problems.append("缺少数据清单（环节二未产出），请完整重跑本流程")
        return outcome
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))

    _reset_app_imports(paths.backend)
    store_module = importlib.import_module("app.store")
    fresh_store = store_module.Store()
    for module, expected in manifest.items():
        rows = fresh_store.rows(module)
        if len(rows) != expected["rows"]:
            outcome.problems.append(
                f"模块 {module} 内存仓库 {len(rows)} 条与数据清单 {expected['rows']} 条不一致"
            )
            continue
        try:
            service_module = importlib.import_module(f"app.services.{module}")
            service_class = next(
                value for value in vars(service_module).values() if isinstance(value, type) and value.__name__.endswith("Service")
            )
            _, total = service_class().list_entries(page=1, size=1)
            if total != expected["rows"]:
                outcome.problems.append(f"模块 {module} 列表接口返回 {total} 条，与数据清单 {expected['rows']} 条不一致")
        except Exception as exc:
            outcome.problems.append(f"模块 {module} 列表查询冒烟失败：{exc}")
    if not outcome.problems:
        details.append("内存仓库与数据清单一致")

    if not paths.run_sh.exists():
        outcome.problems.append("缺少启动脚本 backend/run.sh")
    elif not paths.run_sh.stat().st_mode & 0o111:
        outcome.problems.append("backend/run.sh 没有执行权限；修复：chmod +x backend/run.sh")

    config_module = importlib.import_module("app.config")
    backend_port = int(config_module.settings.port)
    if _port_in_use(backend_port):
        outcome.problems.append(
            f"端口 {backend_port} 已被占用，可能是上次启动的服务未退出；"
            f"请先结束占用进程（如 lsof -i :{backend_port} 查看）再启动"
        )
    else:
        details.append(f"端口 {backend_port} 空闲")

    if _port_in_use(FRONTEND_PORT):
        outcome.warnings.append(f"前端端口 {FRONTEND_PORT} 已被占用，vite 会自动换端口")
    if not (paths.frontend / ".env.development").exists():
        outcome.warnings.append("缺少 frontend/.env.development，前端将使用默认配置")

    outcome.summary = "；".join(details)
    return outcome


# ---------------------------------------------------------------- 流程编排

STAGES = [
    ("依赖校验", check_dependencies),
    ("数据准备", prepare_data),
    ("启动前检查", check_before_start),
]


def main() -> int:
    paths = build_paths()
    reset_workdir(paths)
    print("启动前流水线：依赖校验 → 数据准备 → 启动前检查")

    for index, (name, stage) in enumerate(STAGES, start=1):
        outcome = stage(paths)
        print(f"[{index}/{len(STAGES)}] {name}", end="")
        if outcome.problems:
            print(" ... 失败")
            for problem in outcome.problems:
                print(f"  - {problem}")
            shutil.rmtree(paths.workdir, ignore_errors=True)
            print("未改动任何数据文件；修复后重新执行 make preflight 即可。")
            return index
        print(f" ... 通过（{outcome.summary}）" if outcome.summary else " ... 通过")
        for warning in outcome.warnings:
            print(f"  提醒：{warning}")

    print("全部通过，可以启动服务：make backend（后端）、make frontend（前端）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
