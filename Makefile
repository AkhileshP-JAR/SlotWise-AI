PYTHON ?= .venv/Scripts/python.exe

.PHONY: lint format test migrate generate-small generate-medium generate-full validate-small validate-medium validate-full load-small load-medium load-full reproduce-small simulate-small simulate-medium train-ml benchmark audit run-api run-ui

lint:
	$(PYTHON) -m ruff check backend frontend scripts

format:
	$(PYTHON) -m ruff format backend frontend scripts

migrate:
	$(PYTHON) -m alembic upgrade head

generate-small:
	$(PYTHON) scripts/generate_data.py --profile small

generate-medium:
	$(PYTHON) scripts/generate_data.py --profile medium

generate-full:
	$(PYTHON) scripts/generate_data.py --profile full

validate-small:
	$(PYTHON) scripts/validate_data.py --profile small

validate-medium:
	$(PYTHON) scripts/validate_data.py --profile medium

validate-full:
	$(PYTHON) scripts/validate_data.py --profile full

load-small:
	$(PYTHON) scripts/load_data.py --profile small

load-medium:
	$(PYTHON) scripts/load_data.py --profile medium

load-full:
	$(PYTHON) scripts/load_data.py --profile full

reproduce-small:
	$(PYTHON) scripts/reproduce_dataset.py --profile small

simulate-small:
	$(PYTHON) scripts/run_simulation.py --profile small

simulate-medium:
	$(PYTHON) scripts/run_simulation.py --profile medium

train-ml:
	$(PYTHON) scripts/train_forecaster.py --profile small

benchmark:
	$(PYTHON) scripts/benchmark.py

audit:
	$(PYTHON) scripts/audit_integrity.py --db data/slotwise.db

test:
	$(PYTHON) -m pytest -v

run-api:
	$(PYTHON) -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload

run-ui:
	$(PYTHON) -m streamlit run frontend/app.py
