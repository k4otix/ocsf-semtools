# Changelog

The library follows SemVer on its own API; before 1.0, a minor release may change it. OCSF schema
versions are data, so support for a new OCSF version ships as a minor release. Each change gets a
line here, written for users, in the commit that makes it.

## Unreleased

### Changed

- The command line exits 2 when a command can't do its job: a usage error, a missing file, an unknown version, profile or class, or a failed pipeline gate. Exit 1 now means only that `check` found an error or `diff` a difference.
- `parse` and `repair` read extensions from `metadata.extensions`, so an event of an extension class
  such as `win/prefetch_query` is checked against that class; before, it was checked as `base_event`.
  Both also take `schema=`, a schema from `load()` or `resolve()`, for custom extensions.
- Conformance enforces numbers as OCSF defines them: NaN and the infinities are a `type_mismatch` for
  `float_t`, and `integer_t` and `long_t` values must fit in a signed 64-bit integer. An event that
  passed before may now report these errors.
- An object an event holds twice, through a cycle or under two paths, is a `repeated_node` error and
  is walked once. Every run stops after `max_nodes` attribute visits, 100,000 by default; `parse`,
  `repair` and `extract` take `max_nodes=`, and `None` walks everything.
- An event that is not a dict raises `TypeError`; a malformed `(schema, class key)` reference is a
  `TypeError`, and an unknown `class_key` is a `KeyError` naming the schema.
- `load_ir` reads a `str` as a path, and refuses NaN, infinity, an enum on a non-scalar type and a
  table of the wrong kind. `resolve` refuses an IR with no `base_event`, an item in the wrong table,
  an attribute naming a missing object, or non-integer `type_uid` keys. Each is a `ValueError` naming
  the key path.
- Enum values in `enum_out_of_range` messages and in `expected` are listed in numeric order.
- `Report.to_dict()` always returns valid JSON, writing non-finite values as strings.
- A diff lists a renamed class once, without a separate change to its `key`, and sorts integer keys
  numerically.
- Every package carries a NOTICE with the OCSF schema's attribution. Package metadata says Alpha and
  links the documentation and issue tracker.
- Releases build with a pinned backend (`packaging/build-requirements.txt`), so a tag rebuilds to its
  published hashes, and a release requires a passing CI run on the tagged commit.

### Added

- `RepairAction` (the type of `Finding.repair`), `export_ir`, and `ConformanceError.fatal`, the
  findings that stopped a build.
- `ResolvedItem.inactive`: the attributes that only inactive profiles add.
- `ResolvedSchema`, `Report` and `ConformanceError` can be pickled, so they cross process boundaries.

### Removed

- `Policy`, `STRICT`, `LENIENT` and `REPAIR` from the public API. No function accepted them;
  [Building and checking events](docs/events.md) says what each entry point changes.

### Fixed

- `generate()` accepted a class or enum named `Literal`, `Struct`, `IntEnum`, `StrEnum` or `Any`, which shadowed the module's import and broke it at import time; such names are refused.
- `ocsf-toolkit generate --ext` needed `ocsf.lock` in the current directory, which only a checkout of this repository has; it takes the pin from the shipped IR when there is none. An extension's hash leaves out hidden files such as `.git`, so a git operation no longer changes it.
- Generated code was not ruff-format stable for a required field whose line passed the width, which only long custom names reach; `generate()` now also refuses class, enum and field names over 100 characters, so every line can wrap.
- `generate()` lost the package it was replacing when both the swap and the restore failed; it now keeps it and names where it is.
- `build()` and `parse(as_=...)` on a superset model class raised `msgspec.ValidationError` for a
  wrong-typed attribute of an inactive profile. Such values are checked now, and objects under them
  walked.
- Events nested deeper than Python's recursion limit raised `RecursionError` from `build` and
  `repair`, and an event holding a cycle made `parse` and `repair` hang.
- `type_uid` was derived from an `activity_id` the class doesn't define, so `activity_id=-1` produced
  a plausible `type_uid` such as 400099.
- Observables were read before coercion, so `repair` reported a caller entry for a coerced value as
  unfounded.
- A deprecated value moved into a successor present as null was never checked.
- `repair` reported unknown attributes instead of relocating them when the event had `unmapped: null`.
- `Report.by_kind("filled")` returned nothing; it matches a `Kind` or its name.
- A timezone-aware `datetime` with no UTC instant, such as year 1 at +01:00, raised `OverflowError`,
  and an integer too long to print raised `ValueError` from a message.

## 0.1.0a2 (2026-11-06)

- Publishes the model package `ocsf-toolkit-v1-9-0`.

## 0.1.0a1 (2026-11-06)

The first alpha, with the model packages `ocsf-toolkit-v1-7-0` and `ocsf-toolkit-v1-8-0`.

- Every OCSF release from 1.0.0 to 1.9.0 ships as data. `load()` resolves a version under a set of
  profiles and extensions into a `ResolvedSchema`: classes, objects, attribute requirements, enums,
  sibling pairs, class identity and observable markers, with no model needed.
- `build()` constructs a conformant event and derives what the schema computes: class identity,
  `type_uid`, sibling labels, `*_dt` timestamps and `metadata.version`. It raises one exception,
  `ConformanceError`, carrying the full report.
- `parse()` checks an event and reports every deviation as a `Finding` in a `Report`; `repair()`
  returns a normalized copy, with every change recorded as a finding.
- `extract()` and `merge()` derive an event's observables from the schema and check the ones it
  already lists.
- `diff()` compares two OCSF versions or IRs, matching classes by uid so a renamed class shows as a
  rename.
- `generate()` writes a typed package of msgspec Structs for any version or IR, including one with
  your own extensions; `build()` and `parse()` take and return its classes.
- The `ocsf-toolkit` command line: `check`, `diff` (with `--json` and `--semver`), `generate`, and the
  schema pipeline commands that maintain `ocsf.lock` and the shipped IRs.
- Releases are reproducible, publish to PyPI through trusted publishing, and attach `ocsf.lock` and
  `SHA256SUMS` to a GitHub release.
