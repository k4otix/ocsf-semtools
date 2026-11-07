.PHONY: setup format lint type test bench schema ir lock version generate dists wheels compat test-generated clean

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

# engine throughput at 2-20 KB per event, on this machine; slow, so not part of `test` or CI
bench:
	$(UV) run python -m tests.bench.engine

# gates 1 and 2: compile every lockfile schema, then check every committed IR against it
schema:
	$(UV) run ocsf-toolkit compile
	$(UV) run ocsf-toolkit ir check

# rewrite data/ir-<ver>.json for VERSION (default: every entry with an ir_sha256); RECORD=1 accepts a new IR
ir:
	$(UV) run ocsf-toolkit ir export $(VERSION) $(if $(RECORD),--record)

# pin a new OCSF version (REF= for a branch or tag of another name), ship its IR, move the model
# window and record its trees; safe to run again after a step fails
lock:
	@test -n "$(VERSION)" || { echo "usage: make lock VERSION=x.y.z"; exit 2; }
	$(UV) run ocsf-toolkit lock --add $(VERSION) $(if $(REF),--ref $(REF))
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
# Sdists and wheels carry this timestamp and the version of the backend that built them, so a commit
# rebuilt with the pinned backend gives identical bytes.
export SOURCE_DATE_EPOCH ?= $(shell git log -1 --format=%ct)
BUILD_CONSTRAINTS = packaging/build-requirements.txt
export UV_BUILD_CONSTRAINT := $(CURDIR)/$(BUILD_CONSTRAINTS)
VERSION_CMD = $(UV) tool run --quiet --constraints $(BUILD_CONSTRAINTS) --with hatch-vcs hatchling version

# the version hatch-vcs derives from git, with the pinned backend
version:
	@$(VERSION_CMD)

generate:
	@echo "$(OCSF)" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$$' || { echo "OCSF must be x.y.z"; exit 2; }
	mkdir -p $(DIST)
	$(UV) run ocsf-toolkit generate $(OCSF) --package $(PACKAGE) --out $(DIST)
	version=$$($(VERSION_CMD)) && \
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

# the released model dists (LOCAL=1: the wheels in build/wheels) run on the core in this checkout
compat:
	version=$$($(VERSION_CMD)) && \
	$(UV) run python packaging/version_dist/dist.py compat --version "$$version" \
		$(if $(LOCAL),--wheels $(WHEELS))

# static analysis, snapshot, laziness and a clean-environment install of the generated dist
test-generated:
	$(UV) run pytest tests/generated

clean:
	rm -rf build dist .pytest_cache .ruff_cache .mypy_cache .import_linter_cache
