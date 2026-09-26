.PHONY: install backend frontend check-deps check-deps-install prepare-data preflight dev-up dev-down

# 兼容原有目标：直接装依赖、直接起单个服务
install:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
	cd frontend && npm install

backend:
	cd backend && ./run.sh

frontend:
	cd frontend && npm run dev

# ===== 可重复的本地启动流水线：依赖校验 → 数据准备 → 启动前检查 → 启动 → 冒烟 =====
check-deps:
	python3 scripts/check_deps.py

check-deps-install:
	python3 scripts/check_deps.py --install

prepare-data:
	python3 scripts/prepare_crew_data.py

preflight:
	python3 scripts/preflight.py

dev-up:
	./scripts/dev-up.sh

dev-down:
	./scripts/dev-down.sh
