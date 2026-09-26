#!/usr/bin/env bash
# 停止 dev-up.sh 拉起的前后端进程，并清理 .run/ 下的 pid 文件。
# dev-up 用 setsid 把每个服务放进独立进程组（npm 还会派生 vite 子进程），
# 因此这里向“负 pid”（整个进程组）发信号，避免只杀掉 npm 而 vite 继续占端口。
# 用法：scripts/dev-down.sh [--quiet]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_DIR="$ROOT/.run"
QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1

say() { [ "$QUIET" -eq 0 ] && echo "$@" || true; }

stop_one() {
  local name="$1" pidfile="$RUN_DIR/$2"
  if [ ! -f "$pidfile" ]; then
    say "· $name：无 pid 文件，跳过"
    return 0
  fi
  local pid
  pid="$(cat "$pidfile")"
  # 进程组首进程存活，就按进程组回收；否则只做记录清理。
  if kill -0 "$pid" 2>/dev/null; then
    kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.5
    done
    if kill -0 "$pid" 2>/dev/null; then
      say "! $name（进程组 $pid）TERM 后未退出，发送 KILL"
      kill -9 -- "-$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null || true
    fi
    say "✓ $name 已停止（进程组 $pid，含其子进程）"
  else
    say "· $name：进程组首进程 $pid 已不在运行"
  fi
  rm -f "$pidfile"
}

if [ -d "$RUN_DIR" ]; then
  stop_one "后端" backend.pid
  stop_one "前端" frontend.pid
  rmdir "$RUN_DIR" 2>/dev/null || true
fi
say "✓ 已清理 dev-up 管理的进程与 pid 文件（日志保留在 .run/ 内，未被删除）"
