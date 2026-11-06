# Changelog

The library follows SemVer on its own contract only; OCSF schema versions are data: a new OCSF
version is a minor release, never a major. One line per merged PR, taken from the PR title.

## Unreleased

- Repository skeleton, `ocsf.lock` pinning OCSF 1.9.0, `ocsf-toolkit compile` and `ocsf-toolkit lock --add`.
- Schema IR (`SchemaIR`), compiler-JSON loader with key accounting, canonical IR JSON (`ir_version` 1.0) and `load_ir`; `ocsf-toolkit ir export` / `ir check`; IR for OCSF 1.9.0 shipped in the package; `ocsf.lock` pins OCSF 1.0.0–1.9.0.
- `load()` / `resolve()` → `ResolvedSchema`: profile- and extension-aware attribute sets, class identity, sibling pairs, `type_uid` registry and observable markers, reachable without a model.
- `build()`, `parse()` and `repair()` on event dicts under the `STRICT`, `LENIENT` and `REPAIR` policies: one traversal (`walk()`) that derives what the schema computes and reports every deviation as a `Finding` of a `Kind` in a `Report`; only `build()` raises, and only `ConformanceError`. `ocsf-toolkit check` reports on JSON Lines files.
- Observables: `extract()` and `merge()` → `Observable`, with value validation.
- `diff()` → `SchemaDiff`: a structural diff of two IRs with classes matched by uid, so a renamed class shows as a rename; `ocsf-toolkit diff`, with `--json` and `--semver` (the release category for a re-pinned IR). IRs for OCSF 1.1.0, 1.3.0 and 1.7.0 shipped.
- `generate()` and `ocsf-toolkit generate`: an OCSF version or IR projected into a typed package of msgspec Structs, superset by default or exact for a `--profiles` set, with `--ext` for custom extensions; `build()` and `parse()` accept and return the generated classes.
- Model dists `ocsf-toolkit-v1-7-0`, `-v1-8-0` and `-v1-9-0`, each bundling its IR and requiring a range of core versions, so a core release republishes a dist only when its generated tree changes. `ocsf-toolkit models check` / `record` pin each tree in `ocsf.lock`; `lock --add` keeps models for the newest three versions. IRs for every OCSF release from 1.0.0 to 1.9.0 ship in core.
- Release workflow: reproducible sdists and wheels, PyPI trusted publishing, and a GitHub release with `ocsf.lock` and `SHA256SUMS`.
- Property tests (Hypothesis, strategies drawn from the IR): dict and typed round trips for every core class of the model-window versions, and repair undoing unambiguous damage idempotently. Fixes they found: a typed `build()` reads profiles from a `Metadata` Struct, and `repair()` finds the class from a `class_uid`/`type_uid` it coerces. `make bench` measures engine throughput; the engine walks at about 2 µs per attribute (1.55× faster than its first cut).
- README with installation and a worked example, and API docs in `docs/` (schema, events, observables, diff, typed models, CLI); every example runs as a doctest.
