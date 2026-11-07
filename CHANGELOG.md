# Changelog

The library follows SemVer on its own API. OCSF schema versions are data, so support for a new OCSF
version ships as a minor release. One line per merged PR, taken from the PR title.

## Unreleased

- Removed from the public API: `Policy`, `STRICT`, `LENIENT` and `REPAIR`. No function accepted them; [Building and checking events](docs/events.md) says what each entry point changes.
- Fixed: `build()` and `parse(as_=...)` on a superset model class raised `msgspec.ValidationError` for a wrong-typed attribute of an inactive profile. Such values are now checked, and objects under them walked; `ResolvedItem.inactive` holds their declarations.
- Fixed: events nested deeper than Python's recursion limit raised `RecursionError` from `build` and `repair`, and an event holding a cycle made `parse` and `repair` hang. An object reached twice is now a `repeated_node` error and walked once, and every run stops after `max_nodes` attribute visits (100,000 by default; `parse`, `repair` and `extract` take `max_nodes=`).
- An event that is not a dict raises `TypeError`.
- Conformance now enforces numbers as OCSF defines them: NaN and the infinities are a `type_mismatch` for `float_t`, and `integer_t` and `long_t` values must fit in a signed 64-bit integer. An event that passed before may now report these errors. `Report.to_dict()` always returns valid JSON, writing non-finite values as strings.
- Enum values in `enum_out_of_range` messages and `expected` are listed in numeric order.
- `parse` and `repair` read extensions from `metadata.extensions`, so an event of an extension class such as `win/prefetch_query` is checked against it; before, it was checked as `base_event`. Both also take `schema=`, a schema from `load()` or `resolve()`, for custom extensions.
- New in the public API: `RepairAction` (the type of `Finding.repair`), `export_ir`, and `ConformanceError.fatal`, the findings that stopped a build. `ResolvedSchema`, `Report` and `ConformanceError` can be pickled.
- Fixed: `Report.by_kind("filled")` returned nothing; it now matches a `Kind` or its name.
- Fixed: observables were read before coercion, so `repair` reported a caller entry for a coerced value as unfounded; a deprecated value moved into a successor present as null was never checked.
- `load_ir` reads a `str` path as a path, and refuses NaN, infinity, an enum on a non-scalar type and a table of the wrong kind; `resolve` refuses an IR with no `base_event`, an item in the wrong table, an attribute naming a missing object, or non-integer `type_uid` keys. Each is a `ValueError` with the key path, where some crashed later before.
- Fixed: `type_uid` was derived from an `activity_id` the class doesn't define, so `activity_id=-1` produced a plausible `type_uid` such as 400099.
- Fixed: a timezone-aware `datetime` with no UTC instant (such as year 1 at +01:00) raised `OverflowError`; an integer too long to print raised `ValueError` from a message.

## 0.1.0a2

- Publishes model package `ocsf-toolkit-v1-9-0`.

## 0.1.0a1

The first alpha. Model packages `ocsf-toolkit-v1-7-0` and `ocsf-toolkit-v1-8-0` publish with it;
`ocsf-toolkit-v1-9-0` follows with the next release.

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
