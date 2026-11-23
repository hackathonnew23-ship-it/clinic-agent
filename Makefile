# Mac/Linux. On Windows without make, use the equivalent commands from the README / Phase 1 notes.
PY ?= python3
VENV := backend/.venv
BIN := $(VENV)/bin

.PHONY: setup seed dev test race clean

setup:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -r backend/requirements.txt
	@test -f .env || cp .env.example .env
	@echo "Setup done. Next: make seed && make dev"

seed:
	cd backend && .venv/bin/python -m seed.seed --reset

dev:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

test:
	cd backend && .venv/bin/python -m pytest -q

# The demo moment: 10 parallel requests at one slot, exactly 1 wins.
race:
	cd backend && .venv/bin/python -m pytest tests/test_concurrency.py -v -s

clean:
	rm -f backend/clinic.db backend/clinic.db-wal backend/clinic.db-shm
