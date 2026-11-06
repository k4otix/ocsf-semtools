# Contributing

## Setup

Requires [uv](https://docs.astral.sh/uv/) and git. The library runs on Python ≥ 3.12. Compiling an OCSF
schema needs Python 3.14 (because of `ocsf-schema-compiler`); `uvx` fetches it for that step, so you
do not need to install it yourself.

```shell
make setup        # uv sync: dev environment in .venv
make lint         # ruff format --check, ruff check, lint-imports
make type         # pyright --strict (blocking), mypy --strict (informational)
make test         # pytest
make schema       # ocsf-toolkit compile: rebuild build/compiled/ and check the lockfile hashes
```

`uvx pre-commit install` runs the lint and type checks on every commit (`.pre-commit-config.yaml`).

## Rules that bind code

These architecture rules are binding: one event walker; import direction enforced by
`.importlinter`; one exception type (`ConformanceError`); no `__getattr__` in core; thin entry points;
a 13-module core budget; a query surface of frozen mappings. A PR that needs to break one of
them argues for it in the description.

Conventions:

- No runtime dependencies besides `msgspec`. The CLI uses `argparse`.
- Each module's docstring states its single responsibility in one sentence.
- Lines are at most 120 characters; ruff format sets the layout.
- Every public module, class and function has a docstring whose first line is an imperative
  summary (pydocstyle, pep257 convention). Tests are exempt.

## Pull requests

- `main` is protected; PRs are squash-merged and the PR title becomes the changelog line.
- Plain commit messages; no conventional-commits prefixes required.
- Changing `ocsf.lock` triggers the schema workflow.
- A compiler bump (`[toolchain]` in `ocsf.lock`) is expected to change every `compiled_sha256`; run
  `make schema` and update them in the same PR.

## Adding an OCSF version

```shell
make lock VERSION=x.y.z      # ocsf-toolkit lock --add x.y.z
```

This resolves the upstream tag (`x.y.z`, then `vx.y.z`), pins its commit, compiles it, and records
`compiled_sha256`. The compile fails if the compiled schema's `version` does not equal `x.y.z`. Tag
`1.5.0` upstream is one such case; pass `--ref` to pin a branch instead.
