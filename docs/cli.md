# Command line

`ocsf-semtools` is installed with the package. Every command has `--help`.

| Exit status | Meaning |
|---|---|
| 0 | done: the events are clean, the IRs are equal, the gates pass |
| 1 | `check` found an error in an event, or `diff` found a difference |
| 2 | the command couldn't do its job: a usage error, a missing file, an unknown version, profile or class, or a schema pipeline gate that failed |

A failure prints one line, `ocsf-semtools: error: ...`, to stderr. So a script can tell bad data
(1) from a broken run (2).

## Working with events and schemas

### `check`

Checks every event in one or more JSON Lines files (`-` reads stdin) against an OCSF version, one
finding per line, and exits 1 if any event has an error.

```shell
ocsf-semtools check --version 1.9.0 events.jsonl
ocsf-semtools check --version 1.9.0 --profiles cloud,host --json events.jsonl
```

```text
events.jsonl:1: error enum_out_of_range severity_id: severity_id is 9, not one of [0, 1, 2, 3, 4, 5, 6, 99]
events.jsonl:1: warning missing_recommended confidence_id: confidence_id is recommended and absent
```

`--profiles` overrides each event's `metadata.profiles`; `--class` sets the class when the event
doesn't say (by default it comes from `class_uid`, then `type_uid`); `--json` prints one report per
event in the form of `Report.to_dict()`.

### `diff`

What changed between two OCSF versions, or two IR files. It exits 0 when they are equal, 1 when they
differ and 2 when one can't be read, like diff(1).

```shell
ocsf-semtools diff 1.1.0 1.3.0
ocsf-semtools diff --json 1.7.0 1.9.0 > changes.json
```

```text
added categories.remediation
changed classes.account_change.attributes.duration.caption: "Duration" -> "Duration Milliseconds"
...
renamed classes.win/prefetch_info -> win/prefetch_query
```

`--semver` prints only the release category for moving from OLD to NEW, ignoring provenance: `patch` when
nothing changed, `minor` when everything changed is an addition, `major` otherwise.

### `generate`

Writes a typed model package (see [Typed models](models.md)) from a shipped OCSF version or an IR
file.

```shell
ocsf-semtools generate 1.9.0 --package acme_models --out src/
ocsf-semtools generate 1.9.0 --package acme_models --out src/ --profiles cloud,host --extensions ""
ocsf-semtools generate 1.9.0 --package acme_models --out src/ --ext ../my-extension
```

`--profiles` emits exact classes for that profile set; without it the classes are supersets.
`--extensions` limits which extensions are included (all by default; `""` for none). `--ext` compiles
the pinned OCSF commit together with a directory of your own extensions, and can be repeated. The
commit and compiler version come from the shipped IR's provenance, so it works wherever the library
is installed; in a checkout of this repository, `ocsf.lock` (or `--lock`) supplies them. It needs git,
network access to the upstream repository, and `uv` to run the official compiler on Python 3.14;
`--compiler-cmd` replaces `uv` with your own command. The generated package bundles the resulting IR,
whose provenance records each extension directory's hash (hidden files such as `.git` left out).

## The schema pipeline

These commands maintain `ocsf.lock` and the shipped IRs, and CI runs them. They matter for custom
extensions and for work on the library itself; using the library needs none of them.

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
