PYTHON ?= python3

.PHONY: check check-python check-js policy test lint typecheck format frontend

check: check-python check-js

check-python: policy test lint typecheck

policy:
	$(PYTHON) scripts/check_repository.py

test:
	$(PYTHON) -m unittest discover -v

lint:
	ruff check .
	ruff format --check .

typecheck:
	mypy

check-js:
	npm --prefix backend/module run check
	npm --prefix frontend run check

format:
	ruff check --fix .
	ruff format .
	npm --prefix backend/module run format
	npm --prefix frontend run format

frontend:
	npm --prefix frontend run dev

.PHONY: check-spacetime
check-spacetime:
	$(PYTHON) -m tests.integration.spacetimedb_live
