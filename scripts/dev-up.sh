#!/usr/bin/env bash
# 一键本地启动流水线：依赖校验 → 数据准备 → 启动前检查 → 启动并等待健康 → 冒烟检查。
#
# 设计约定：
# - 任一阶段失败立即停止，打印失败原因和修复建议，不继续往下走；
# - 只在“启动及之后”的阶段失败时回收本脚本拉起的进程，不会误杀用户自己起的服务；
# - 重复执行幂等：数据产物最新则跳过、上一轮本脚本管理的残留进程先清理；
# - 加 --install 时依赖阶段自动安装缺失依赖，其余阶段行为不变。
#
# 用法：scripts/dev-up.sh [--install]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_DIR="$ROOT/.run"
VENV_PY="$ROOT/backend/.venv/bin/python"

INSTALL=0
for arg in "$@"; do
  case "$arg" in
    --install) INSTALL=1 ;;
    *)
      echo "未知参数：$arg（仅支持 --install）" >&2
      exit 2
      ;;
  esac
done

step() { printf '\n========== %s ==========\n' "$1"; }

# ---- 失败回收：只处理本流水线通过 pidfile 管理的进程 ----
STARTED_SOMETHING=0
cleanup_on_failure() {
  local code=$?
  if [ "$code" -ne 0 ] && [ "$STARTED_SOMETHING" -eq 1 ]; then
    echo "" >&2
    echo "启动在健康/冒烟阶段失败，回收本脚本已拉起的服务（日志保留在 .run/ 下）……" >&2
    "$ROOT/scripts/dev-down.sh" || true
  fi
  exit "$code"
}
trap cleanup_on_failure EXIT

step "阶段 1/5 依赖校验"
if [ "$INSTALL" -eq 1 ]; then
  python3 "$ROOT/scripts/check_deps.py" --install
else
  python3 "$ROOT/scripts/check_deps.py"
fi

step "阶段 2/5 数据准备（剧组成员示例数据）"
python3 "$ROOT/scripts/prepare_crew_data.py"

step "阶段 3/5 启动前检查"
# 先清掉上一轮本脚本管理的残留，保证端口检查看到的是真实状态。
"$ROOT/scripts/dev-down.sh" --quiet || true
python3 "$ROOT/scripts/preflight.py"

step "阶段 4/5 启动前后端并等待健康"
mkdir -p "$RUN_DIR"

# setsid 让每个服务独立成新进程组：npm 会再派生 vite 子进程，只杀 npm 会留下
# vite 孤儿继续占端口；按进程组回收才能一次收干净。
setsid bash -c 'cd "$0/backend" && exec "$1" -m uvicorn app.main:app --host 127.0.0.1 --port 8000' \
  "$ROOT" "$VENV_PY" >"$RUN_DIR/backend.log" 2>&1 &
BACKEND_PID=$!
echo "$BACKEND_PID" > "$RUN_DIR/backend.pid"
STARTED_SOMETHING=1
echo "后端已启动（pid $BACKEND_PID），日志 .run/backend.log"

setsid bash -c 'cd "$0/frontend" && exec npm run dev -- --host 127.0.0.1 --port 5173 --strictPort' \
  "$ROOT" >"$RUN_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!
echo "$FRONTEND_PID" > "$RUN_DIR/frontend.pid"
echo "前端已启动（pid $FRONTEND_PID），日志 .run/frontend.log"

# 进程若在等待期间提前退出，直接给出日志尾部，而不是傻等到超时。
for i in $(seq 1 60); do
  kill -0 "$BACKEND_PID" 2>/dev/null || { echo "✗ 后端进程提前退出，.run/backend.log 尾部：" >&2; tail -n 20 "$RUN_DIR/backend.log" >&2; exit 1; }
  kill -0 "$FRONTEND_PID" 2>/dev/null || { echo "✗ 前端进程提前退出，.run/frontend.log 尾部：" >&2; tail -n 20 "$RUN_DIR/frontend.log" >&2; exit 1; }
  sleep 0.5
done

python3 "$ROOT/scripts/wait_for_http.py" http://127.0.0.1:8000/api/health 30 --json ok=true
python3 "$ROOT/scripts/wait_for_http.py" http://127.0.0.1:5173/ 60

step "阶段 5/5 冒烟检查（列表与状态完整性）"
python3 "$ROOT/scripts/smoke_test.py"

echo ""
echo "✓ 全部就绪："
echo "  前端 http://127.0.0.1:5173/ （dev server 不会自动开浏览器，请手动访问）"
echo "  后端 http://127.0.0.1:8000/api/health"
echo "  停止服务：make dev-down"
