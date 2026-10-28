# FORMA — developer entrypoints (Linux/macOS/WSL/Git-Bash). Windows PowerShell: see scripts/dev.ps1
SHELL := /bin/bash
BACKEND := backend
PY := $(BACKEND)/.venv/bin/python
FORMA := $(BACKEND)/.venv/bin/forma

.PHONY: install install-backend install-frontend dev api web worker check lint typecheck test synth seed eval bench bench-short reset clean

install: install-backend install-frontend

install-backend:
	cd $(BACKEND) && uv venv --python 3.12 && uv pip install -e ".[dev]"

install-frontend:
	cd frontend && npm install

api:                 ## run the API on :8000 (reload)
	cd $(BACKEND) && .venv/bin/forma serve --reload

web:                 ## run the web app on :5173
	cd frontend && npm run dev

worker:              ## run a job worker (FORMA_JOB_BACKEND=arq needs Redis; thread/process drain the DB queue)
	cd $(BACKEND) && .venv/bin/forma worker

dev:                 ## API + web together
	$(MAKE) -j2 api web

lint:
	cd $(BACKEND) && .venv/bin/ruff check . && .venv/bin/ruff format --check .

format:
	cd $(BACKEND) && .venv/bin/ruff format . && .venv/bin/ruff check --fix .

typecheck:
	cd $(BACKEND) && .venv/bin/mypy

test:
	cd $(BACKEND) && .venv/bin/pytest --cov=forma --cov-report=term-missing:skip-covered

check: lint typecheck test   ## everything CI runs

synth:               ## regenerate the synthetic ERP corpus (30 variants, ground truth)
	cd $(BACKEND) && .venv/bin/forma synth --out ../data/synthetic --seed 42 --variants 30

eval:                ## heuristic mapping + engine accuracy vs ground truth
	cd $(BACKEND) && .venv/bin/forma eval --corpus ../data/synthetic --report ../data/eval_report.json

seed:                ## load the corpus into the DB as demo customers/rounds and run the pipeline
	cd $(BACKEND) && .venv/bin/forma seed --corpus ../data/synthetic

bench:               ## 300 files x 100K rows through the configured job backend (docs/PERFORMANCE.md)
	cd $(BACKEND) && .venv/bin/forma bench --files 300 --rows 100000 --out ../data/bench

bench-short:         ## CI-sized benchmark (6 files x 2K rows)
	cd $(BACKEND) && .venv/bin/forma bench --files 6 --rows 2000 --distinct 3 --out ../data/bench_short

reset:               ## wipe runtime data (DB, stored files, datasets, exports)
	rm -rf data/forma.db* data/files data/datasets data/exports

clean: reset
	rm -rf $(BACKEND)/.venv frontend/node_modules frontend/dist
