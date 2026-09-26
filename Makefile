.PHONY: install preflight backend frontend test

install:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
	cd frontend && npm install

preflight:
	cd backend && python3 preflight.py

backend: preflight
	cd backend && ./run.sh

frontend:
	cd frontend && npm run dev

test:
	cd backend && python3 -m unittest discover -s tests -v
