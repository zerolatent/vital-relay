PYTHON_CANDIDATE := $(firstword $(wildcard /usr/local/bin/python3.14 /opt/homebrew/bin/python3.14))
PYTHON ?= $(if $(PYTHON_CANDIDATE),$(PYTHON_CANDIDATE),python3.14)

.PHONY: install test test-postgres dev migrate

install:
	$(PYTHON) -m venv .venv
	.venv/bin/python -m pip install -e '.[dev]'

test:
	env -u VITAL_RELAY_DATABASE_URL -u VITAL_RELAY_DEMO_SCOPE_ID \
		.venv/bin/python -m pytest

test-postgres:
	env -u VITAL_RELAY_DATABASE_URL -u VITAL_RELAY_DEMO_SCOPE_ID \
		.venv/bin/python -m pytest -m postgres backend/tests/postgres

dev:
	.venv/bin/python -m uvicorn vital_relay.main:create_app --factory --reload

migrate:
	@test -n "$$VITAL_RELAY_DATABASE_URL" || \
		(echo "VITAL_RELAY_DATABASE_URL is required"; exit 2)
	.venv/bin/python -m alembic upgrade head
