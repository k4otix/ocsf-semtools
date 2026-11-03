.PHONY: setup format lint type test schema ir lock generate test-generated clean

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

# gates 1 and 2: compile every lockfile schema, then check every committed IR against it
schema:
	$(UV) run ocsf-toolkit compile
	$(UV) run ocsf-toolkit ir check

# rewrite data/ir-<ver>.json for VERSION (default: every entry with an ir_sha256); RECORD=1 accepts a new IR
ir:
	$(UV) run ocsf-toolkit ir export $(VERSION) $(if $(RECORD),--record)

lock:
	@test -n "$(VERSION)" || { echo "usage: make lock VERSION=x.y.z"; exit 2; }
	$(UV) run ocsf-toolkit lock --add $(VERSION)

# The model dist for one OCSF version as an installable project in build/dists/<package>/.
# Nothing here deletes: `generate` replaces <package>/ only if every file in it is generated,
# and the two rendered files are overwritten by name. The dist pins the core's build version.
OCSF ?= 1.9.0
PACKAGE = ocsf_toolkit_v$(subst .,_,$(OCSF))
DIST = build/dists/$(PACKAGE)

generate:
	@echo "$(OCSF)" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$$' || { echo "OCSF must be x.y.z"; exit 2; }
	mkdir -p $(DIST)
	$(UV) run ocsf-toolkit generate $(OCSF) --package $(PACKAGE) --out $(DIST)
	version=$$($(UV)x --quiet --with hatch-vcs hatchling version) && \
	for f in pyproject.toml README.md; do \
		sed -e "s/@DIST@/$(subst _,-,$(PACKAGE))/g" -e "s/@PACKAGE@/$(PACKAGE)/g" \
			-e "s/@OCSF@/$(OCSF)/g" -e "s/@VERSION@/$$version/g" \
			packaging/version_dist/$$f.tmpl > $(DIST)/$$f; \
	done

# static analysis, snapshot, laziness and a clean-environment install of the generated dist
test-generated:
	$(UV) run pytest tests/generated

clean:
	rm -rf build dist .pytest_cache .ruff_cache .mypy_cache .import_linter_cache
