# Changelog

The library follows SemVer on its own contract only; OCSF schema versions are data: a new OCSF
version is a minor release, never a major. One line per merged PR, taken from the PR title.

## Unreleased

- Repository skeleton, `ocsf.lock` pinning OCSF 1.9.0, `ocsf-toolkit compile` and `ocsf-toolkit lock --add`.
- Schema IR (`SchemaIR`), compiler-JSON loader with key accounting, canonical IR JSON (`ir_version` 1.0) and `load_ir`; `ocsf-toolkit ir export` / `ir check`; IR for OCSF 1.9.0 shipped in the package; `ocsf.lock` pins OCSF 1.0.0–1.9.0.
- `load()` / `resolve()` → `ResolvedSchema`: profile- and extension-aware attribute sets, class identity, sibling pairs, `type_uid` registry and observable markers, reachable without a model.
