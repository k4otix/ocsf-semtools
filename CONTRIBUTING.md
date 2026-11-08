# Contributing

## Reporting a problem

Open an [issue](https://github.com/k4otix/ocsf-semtools/issues/new/choose). For a wrong finding or a
missed one, the bug template asks for what reproduces it: the OCSF version, the event (trimmed to
what matters) and `ocsf-semtools --version`. Report a security problem privately, as
[SECURITY.md](SECURITY.md) describes.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and git. The library runs on Python 3.12 or newer.
Compiling an OCSF schema needs Python 3.14, because `ocsf-schema-compiler` does; `uv` fetches it for
that step, so you don't need to install it yourself.

```shell
make setup           # uv sync: the dev environment in .venv
make format          # ruff format and ruff check --fix
make lint            # ruff format --check, ruff check, lint-imports
make type            # pyright in strict mode (blocking), mypy --strict (informational)
make test            # pytest, including the doctests in README.md and docs/
```

`uvx pre-commit install` runs the blocking lint and type checks on every commit
(`.pre-commit-config.yaml`).

The schema pipeline and the model packages have their own targets:

```shell
make schema          # compile every lockfile schema and check every shipped IR against it
make ir VERSION=...  # rewrite data/ir-<version>.json; RECORD=1 accepts a new IR hash
make lock VERSION=...  # pin a new OCSF version (see below)
make generate OCSF=x.y.z  # one model package, in build/dists/
make dists           # every model-window package, then the regeneration check
make wheels          # core and model sdists and wheels in build/wheels/ (a new directory), each wheel checked
make test-generated  # pyright, the snapshot, laziness and a clean install over the built packages
make compat          # the model packages on PyPI run on this core (LOCAL=1: the ones just built)
make bench           # engine throughput, on this machine; not part of CI
make version         # the version hatch-vcs derives, with the pinned build backend
make clean           # remove build/ and the tool caches
make help            # every target, with what it does
```

## Rules that bind code

These architecture rules bind the code, and a PR that needs to break one argues for it in its
description:

- One traversal of an event, `walk()`; derivation, conformance and observables are visitors on it.
- The import direction that `.importlinter` enforces: `ir` ← `loader`/`export` ← `schema` ← `walk` ←
  `derive`/`conform`/`observables` ← `engine` ← `models`, with `codegen` and `diff` on `schema` alone.
- One exception type, `ConformanceError`; everything else is a builtin exception.
- No `__getattr__` in the library itself; lazy loading lives only in generated packages.
- Thin entry points: `build`, `parse` and `repair` each choose a policy, run the engine and convert.
- Thirteen core modules: `ir`, `loader`, `export`, `schema`, `walk`, `derive`, `conform`,
  `observables`, `engine`, `diff`, `models`, `codegen` and `cli`.
- A query surface of read-only mappings, plus the few methods that compute something.

Conventions:

- No runtime dependencies besides `msgspec`. The CLI uses `argparse`.
- Each module's docstring states its single responsibility in one sentence.
- Lines are at most 120 characters; ruff format sets the layout.
- Every public module, class and function has a docstring whose first line is an imperative
  summary (pydocstyle, pep257 convention). Tests are exempt.
- Docs and comments say what something is and does.

## Pull requests

- `main` is protected: a PR needs the CI checks to pass and is squash-merged.
- Plain commit messages; no conventional-commits prefixes.
- A change users would notice gets a line in `CHANGELOG.md` under Unreleased, in the same PR.
- Changing `ocsf.lock`, the loader or the shipped IRs runs the schema workflow too.
- A compiler bump records its new hashes in the same PR, as [Bumping the compiler](#bumping-the-compiler)
  describes.

## Adding an OCSF version

A new OCSF release ships as a minor release of the library, with a model package for it.

1. Pin it, ship its IR and move the model window:

   ```shell
   make lock VERSION=x.y.z      # REF=<branch or tag> if the tag isn't x.y.z or vx.y.z
   ```

   This resolves the upstream tag (`x.y.z`, then `vx.y.z`), pins its commit, compiles it, records
   `compiled_sha256`, exports the IR with its `ir_sha256`, and records `models_tree_sha256` for the
   newest three versions. The compile fails if the compiled schema's `version` isn't `x.y.z`: tag
   `1.5.0` upstream is one such case, so pin the branch with `REF=v1.5.0`. If a step fails, fix the
   cause and run `make lock` again; a version already pinned at the same commit is kept.
2. Add the new package, `ocsf_semtools_vX_Y_Z`, to `forbidden_modules` in `.importlinter`; a test
   fails until you do.
3. Update the package table in `docs/models.md`.
4. Set up publishing for the new package, once:
   - on PyPI, a pending trusted publisher for project `ocsf-semtools-vX-Y-Z`, repository
     `k4otix/ocsf-semtools`, workflow `release.yaml`, environment `pypi-vX-Y-Z`;
   - on GitHub, the environment `pypi-vX-Y-Z`, limited to `v*` tags like the others.

   PyPI allows three pending publishers per account. While none is free, add `X.Y.Z` to the
   repository variable `RELEASE_HOLD_DISTS`: the release builds and checks that package without
   publishing it, and the next release after you clear the variable publishes it.
5. The package that leaves the window gets no further releases and stays on the core minor it last
   required; [Typed models](docs/models.md) tells its users their options. Note it in the
   changelog.

## Bumping the compiler

The compiler version in `[toolchain]` in `ocsf.lock` is part of every IR's provenance, so a bump
changes every hash:

```shell
uv run ocsf-semtools compile --record    # every compiled_sha256
make ir RECORD=1                         # every ir_sha256 and data/ir-*.json
uv run ocsf-semtools models record        # every models_tree_sha256
```

Review the IR diffs (`ocsf-semtools diff OLD.json NEW.json`), since a compiler change can change the
schema itself. The next release republishes every model package in the window, as their trees
changed.

## Releasing

1. Run the release workflow by hand (Actions, release, Run workflow) on `main` as a dry run: it runs
   every gate and builds every package twice, and publishes nothing.
2. Tag the commit and push the tag: `git tag -s vX.Y.Z -m "ocsf-semtools X.Y.Z" && git push origin vX.Y.Z`.
   The release needs a passing CI run on that commit.
3. Approve the `release` environment when the workflow asks. It publishes the core and each model
   package PyPI lacks, and attaches `ocsf.lock` and `SHA256SUMS` to a GitHub release.
