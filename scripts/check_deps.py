#!/usr/bin/env python3
"""阶段一：本地开发依赖校验。

检查项：
1. 工具链：python3 >= 3.10、node >= 18、npm；
2. 后端：backend/.venv 存在且其中的解释器能导入 fastapi / uvicorn / pydantic；
3. 前端：frontend/node_modules 存在且 vue / vite 关键依赖可解析。

默认只报告不修改，缺什么列什么并给出修复命令，退出码非零让流水线停下。
加 --install 时自动执行修复（创建 venv、pip install、npm install），
安装输出直接透传到终端，失败同样停在这一阶段、不会继续启动。
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
FRONTEND_DIR = REPO_ROOT / "frontend"
VENV_PY = BACKEND_DIR / ".venv" / "bin" / "python"


def run(cmd: list[str], cwd: Path | None = None) -> int:
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None).returncode


def version_key(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for token in text.strip().split("."):
        digits = "".join(ch for ch in token if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def tool_version(cmd: str, args: list[str]) -> str | None:
    try:
        proc = subprocess.run([cmd, *args], capture_output=True, text=True, check=False)
    except OSError:
        return None
    text = (proc.stdout or proc.stderr).strip()
    # 各家版本输出格式不一，取第一个 x.y 形式的数字段。
    for token in text.replace("v", " ").split():
        if token.count(".") >= 1 and all(part.isdigit() for part in token.split(".")[:2]):
            return token
    return text.splitlines()[0] if text else None


def main() -> int:
    parser = argparse.ArgumentParser(description="校验本地开发依赖")
    parser.add_argument("--install", action="store_true", help="缺失时自动安装修复")
    args = parser.parse_args()

    problems: list[str] = []

    print("== 1/3 工具链 ==")
    py_path = shutil.which("python3")
    py_version = tool_version("python3", ["--version"]) if py_path else None
    if py_version is None:
        problems.append("未找到 python3（需要 3.10+），请先安装 Python")
    elif version_key(py_version) < (3, 10):
        problems.append(f"python3 版本过低：{py_version}，需要 3.10+")
    else:
        print(f"  ✓ python3 {py_version}")

    node_path = shutil.which("node")
    node_version = tool_version("node", ["--version"]) if node_path else None
    if node_version is None:
        problems.append("未找到 node（需要 18+），请先安装 Node.js")
    elif version_key(node_version) < (18, 0):
        problems.append(f"node 版本过低：{node_version}，需要 18+")
    else:
        print(f"  ✓ node {node_version}")

    if shutil.which("npm") is None:
        problems.append("未找到 npm，请随 Node.js 一起安装")
    else:
        npm_version = tool_version("npm", ["--version"])
        print(f"  ✓ npm {npm_version}")

    print("== 2/3 后端虚拟环境与 Python 依赖 ==")
    backend_ok = False
    if VENV_PY.exists():
        check = subprocess.run(
            [str(VENV_PY), "-c", "import fastapi, uvicorn, pydantic"],
            capture_output=True, text=True,
        )
        if check.returncode == 0:
            backend_ok = True
            print("  ✓ backend/.venv 可用，fastapi / uvicorn / pydantic 均可导入")
        else:
            detail = (check.stderr or "").strip().splitlines()
            tail = detail[-1] if detail else "导入失败"
            problems.append(f"backend/.venv 依赖不完整：{tail}")
    else:
        problems.append("backend/.venv 不存在")

    if not backend_ok:
        if args.install:
            if not VENV_PY.exists():
                if run(["python3", "-m", "venv", ".venv"], cwd=BACKEND_DIR) != 0:
                    print("✗ 创建后端虚拟环境失败", file=sys.stderr)
                    return 1
            if run([str(VENV_PY), "-m", "pip", "install", "-r", "requirements.txt"],
                   cwd=BACKEND_DIR) != 0:
                print("✗ 后端依赖安装失败，请查看上方 pip 输出", file=sys.stderr)
                return 1
        else:
            print("  → 修复命令：make check-deps-install 或 make install")

    print("== 3/3 前端 node_modules ==")
    frontend_ok = (FRONTEND_DIR / "node_modules" / "vue" / "package.json").exists() and (
        FRONTEND_DIR / "node_modules" / "vite" / "package.json"
    ).exists()
    if frontend_ok:
        print("  ✓ frontend/node_modules 可用，vue / vite 均已安装")
    else:
        problems.append("frontend/node_modules 不完整（缺少 vue 或 vite）")
        if args.install:
            if run(["npm", "install"], cwd=FRONTEND_DIR) != 0:
                print("✗ 前端依赖安装失败，请查看上方 npm 输出", file=sys.stderr)
                return 1
        else:
            print("  → 修复命令：make check-deps-install 或 (cd frontend && npm install)")

    if problems:
        print("\n依赖校验未通过：", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print("\n修复后重新执行 make dev-up，流水线会从失败的阶段继续，"
              "已完成的步骤（如数据产物）不会被重做。", file=sys.stderr)
        return 1

    print("\n✓ 依赖校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
