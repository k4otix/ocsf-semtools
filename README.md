# ocsf-toolkit

Version-pinned [OCSF](https://schema.ocsf.io/) semantics for Python: a schema substrate compiled from
upstream `ocsf/ocsf-schema` at a pinned commit, with derivation, conformance reporting, observables,
schema diff, and generated [msgspec](https://github.com/msgspec/msgspec) models as a projection of that
substrate.

**Status: pre-alpha (milestone M0).** Nothing here is usable as a library yet. What exists today is the
reproducible schema pipeline: `ocsf.lock` pins each OCSF release by upstream commit, and
`ocsf-toolkit compile` rebuilds the compiled schema and checks it against the lockfile hash.

```shell
uv sync
uv run ocsf-toolkit compile          # needs network to github.com and PyPI, and uv
```

License: Apache-2.0.
