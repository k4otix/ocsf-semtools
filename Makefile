.PHONY: setup format lint type test schema ir lock generate dists wheels test-generated clean

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

# pin a new OCSF version, ship its IR, move the model window and record the window's trees
lock:
	@test -n "$(VERSION)" || { echo "usage: make lock VERSION=x.y.z"; exit 2; }
	$(UV) run ocsf-toolkit lock --add $(VERSION)
	$(UV) run ocsf-toolkit ir export $(VERSION) --record
	$(UV) run ocsf-toolkit models record

# The model dist for one OCSF version as an installable project in build/dists/<package>/.
# Nothing here deletes: `generate` replaces <package>/ only if every file in it is generated,
# and the rendered project files are overwritten by name. The dist requires the core version
# building it, up to the next generated-code contract break.
OCSF ?= 1.9.0
PACKAGE = ocsf_toolkit_v$(subst .,_,$(OCSF))
DIST = build/dists/$(PACKAGE)
WINDOW = $(shell $(UV) run python -c 'import tomllib; print(*(e["version"] for e in \
	tomllib.load(open("ocsf.lock", "rb"))["schemas"] if e["ship_models"]))')
WHEELS = build/wheels
# sdists and wheels carry this timestamp, so rebuilding one commit gives identical bytes
export SOURCE_DATE_EPOCH ?= $(shell git log -1 --format=%ct)

generate:
	@echo "$(OCSF)" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$$' || { echo "OCSF must be x.y.z"; exit 2; }
	mkdir -p $(DIST)
	$(UV) run ocsf-toolkit generate $(OCSF) --package $(PACKAGE) --out $(DIST)
	version=$$($(UV)x --quiet --with hatch-vcs hatchling version) && \
	$(UV) run python packaging/version_dist/dist.py render --ocsf $(OCSF) --version "$$version" \
		--out $(DIST)

# every model-window dist, then regeneration gate 3 over the trees on disk
dists:
	for v in $(WINDOW); do $(MAKE) --no-print-directory generate OCSF=$$v || exit 1; done
	$(UV) run ocsf-toolkit models check

# core and every model-window dist as sdist and wheel in build/wheels/, each wheel checked
wheels: dists
	$(UV) build --quiet --out-dir $(WHEELS)
	for v in $(WINDOW); do \
		$(UV) build --quiet --out-dir $(WHEELS) build/dists/ocsf_toolkit_v$$(echo $$v | tr . _) \
			|| exit 1; \
	done
	$(UV) run python packaging/version_dist/dist.py check-wheels $(WHEELS)/*.whl

# static analysis, snapshot, laziness and a clean-environment install of the generated dist
test-generated:
	$(UV) run pytest tests/generated

clean:
	rm -rf build dist .pytest_cache .ruff_cache .mypy_cache .import_linter_cache
