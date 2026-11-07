# Command line

`ocsf-toolkit` is installed with the package. Every command has `--help`. Commands exit 0 on success,
1 on a finding or check that failed, and print errors as `ocsf-toolkit: error: ...`.

## Working with events and schemas

### `check`

Checks every event in one or more JSON Lines files (`-` reads stdin) against an OCSF version, one
finding per line, and exits 1 if any event has an error.

```shell
ocsf-toolkit check --version 1.9.0 events.jsonl
ocsf-toolkit check --version 1.9.0 --profiles cloud,host --json events.jsonl
```

```text
events.jsonl:1: error enum_out_of_range severity_id: severity_id is 9, not one of [0, 1, 2, 3, 4, 5, 6, 99]
events.jsonl:1: warning missing_recommended confidence_id: confidence_id is recommended and absent
```

`--profiles` overrides each event's `metadata.profiles`; `--class` sets the class when the event
doesn't say (by default it comes from `class_uid`, then `type_uid`); `--json` prints one report per
event in the form of `Report.to_dict()`.

### `diff`

What changed between two OCSF versions, or two IR files. It exits 0 when they are equal and 1 when
they differ, like diff(1).

```shell
ocsf-toolkit diff 1.1.0 1.3.0
ocsf-toolkit diff --json 1.7.0 1.9.0 > changes.json
```

```text
added categories.remediation
changed classes.account_change.attributes.duration.caption: "Duration" -> "Duration Milliseconds"
...
renamed classes.win/prefetch_info -> win/prefetch_query
```

`--semver` prints only the release category for replacing A with B, ignoring provenance: `patch` when
nothing changed, `minor` when everything changed is an addition, `major` otherwise.

### `generate`

Writes a typed model package (see [Typed models](models.md)) from a shipped OCSF version or an IR
file.

```shell
ocsf-toolkit generate 1.9.0 --package acme_models --out src/
ocsf-toolkit generate 1.9.0 --package acme_models --out src/ --profiles cloud,host --extensions ""
ocsf-toolkit generate 1.9.0 --package acme_models --out src/ --ext ../my-extension
```

`--profiles` emits exact classes for that profile set; without it the classes are supersets.
`--extensions` limits which extensions are included (all by default; `""` for none). `--ext` compiles
the pinned OCSF commit together with a directory of your own extensions, and can be repeated. It
needs Python 3.14 through `uv`, because the official compiler does, and the generated package bundles
the resulting IR.

## The schema pipeline

These commands maintain `ocsf.lock` and the shipped IRs, and are what CI runs. You need them for
custom extensions or to work on the library itself; using the library needs none of them.

| Command | What it does |
|---|---|
| `compile [VERSION ...]` | clones each pinned commit, compiles it with `ocsf-schema-compiler` on Python 3.14 via `uvx`, and checks the compiled version and canonical sha256 against the lockfile |
| `ir export VERSION ... [--record]` | writes `data/ir-<version>.json` from the compiled schema; `--record` accepts a new IR hash into the lockfile |
| `ir check [VERSION ...]` | rebuilds each IR and checks it against the lockfile and the shipped file |
| `lock --add VERSION [--ref REF]` | pins a new OCSF release by commit, compiles it, and moves the model window to the newest three |
| `models check [VERSION ...]` | renders each model-window package twice and checks it against `models_tree_sha256` |
| `models record [VERSION ...]` | writes the current tree hashes into the lockfile |

`--compiler-cmd` replaces `uvx` with any command that compiles a schema checkout to stdout, for
air-gapped builds.
