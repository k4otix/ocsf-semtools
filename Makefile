.PHONY: setup format lint type test schema lock clean

UV ?= uv

setup:
	$(UV) sync

format:
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

lint:
	$(UV) run ruff format --check .
	$(UV) run ruff check .
	$(UV) run lint-imports

# pyright strict is the blocking gate; mypy strict is informational.
type:
	$(UV) run pyright
	-$(UV) run mypy

test:
	$(UV) run pytest

schema:
	$(UV) run ocsf-toolkit compile

lock:
	@test -n "$(VERSION)" || { echo "usage: make lock VERSION=x.y.z"; exit 2; }
	$(UV) run ocsf-toolkit lock --add $(VERSION)

clean:
	rm -rf build dist .pytest_cache .ruff_cache .mypy_cache .import_linter_cache
