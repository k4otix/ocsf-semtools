# ocsf-semtools

ocsf-semtools brings the complete rules of [OCSF](https://schema.ocsf.io/) to Python, for every OCSF
release since 1.0.0. The library builds an event from the fields a producer knows and fills in what
the schema defines: class and category IDs, `type_uid`, and the label beside every enum. It checks
events from any source against the same rules and returns every problem in one report, each with a
path and a severity. It also extracts observables, shows what changed between two OCSF versions, and
generates typed [msgspec](https://github.com/msgspec/msgspec) models from the same schema.

**Status: alpha.** Minor releases before 1.0 may change the API, and the
[changelog](https://github.com/k4otix/ocsf-semtools/blob/main/CHANGELOG.md) lists every change.

## Install

```shell
pip install --pre ocsf-semtools          # core: every OCSF release since 1.0.0, as data
pip install --pre ocsf-semtools-v1-9-0   # optional: typed models for one OCSF version
```

[Typed models](https://github.com/k4otix/ocsf-semtools/blob/main/docs/models.md) lists the packages available.

`ocsf-semtools generate` builds a model package for any other version or extension set (see
[Command line](https://github.com/k4otix/ocsf-semtools/blob/main/docs/cli.md)).

Python 3.12 or newer. The only runtime dependency is msgspec.

## Build and emit one event

Pick an OCSF version and a class, give the fields you know, and `build` fills in what the schema
derives: class and category identity, `type_uid`, the label beside every `_id` enum, and
`metadata.version`.

```pycon
>>> import json
>>> from ocsf_semtools import build, load
>>> schema = load("1.9.0")
>>> event = build(
...     (schema, "detection_finding"),
...     activity_id=1,
...     severity_id=4,
...     time=1_700_000_000_000,
...     metadata={"product": {"name": "Sensor", "vendor_name": "Acme"}},
...     finding_info={"uid": "f-1", "title": "Suspicious login"},
... )
>>> print(json.dumps(event, indent=2, sort_keys=True))
{
  "activity_id": 1,
  "activity_name": "Create",
  "category_name": "Findings",
  "category_uid": 2,
  "class_name": "Detection Finding",
  "class_uid": 2004,
  "finding_info": {
    "title": "Suspicious login",
    "uid": "f-1"
  },
  "metadata": {
    "product": {
      "name": "Sensor",
      "vendor_name": "Acme"
    },
    "version": "1.9.0"
  },
  "severity": "High",
  "severity_id": 4,
  "time": 1700000000000,
  "type_name": "Detection Finding: Create",
  "type_uid": 200401
}

```

`build` raises `ConformanceError` if the event can't conform, and the error carries every problem
it found:

```pycon
>>> from ocsf_semtools import ConformanceError
>>> try:
...     build((schema, "detection_finding"), severity_id=4, time=1)
... except ConformanceError as error:
...     for finding in error.report.errors():
...         print(finding.message)
activity_id is required and absent
finding_info is required and absent
metadata is required and absent

```

## Check an event you received

`parse` returns the event with a report of findings, each with a kind, an indexed path and a
severity. The caller decides which findings are fatal.

```pycon
>>> from ocsf_semtools import parse
>>> received = dict(event, severity_id=9, status_id="x")
>>> _, report = parse(received)
>>> report.ok()
False
>>> for finding in report.errors():
...     print(finding.kind.value, finding.path, finding.message)
enum_out_of_range ('severity_id',) severity_id is 9, not one of [0, 1, 2, 3, 4, 5, 6, 99]
type_mismatch ('status_id',) status_id is str 'x'; expected integer_t

```

The version and profiles come from the event's own `metadata`. `repair` returns a normalized copy
and records every change it made. The same check runs from the shell over JSON Lines files:
`ocsf-semtools check --version 1.9.0 events.jsonl`.

## Typed models

With `ocsf-semtools-v1-9-0` installed, every class and object is a msgspec Struct, every enum is
referenceable by its caption, and `build` and `parse` accept and return the typed classes:

```python
import msgspec

from ocsf_semtools import build, parse
from ocsf_semtools_v1_9_0 import DetectionFinding
from ocsf_semtools_v1_9_0.classes.detection_finding import DetectionFindingActivityId
from ocsf_semtools_v1_9_0.enums import SeverityId
from ocsf_semtools_v1_9_0.objects import FindingInfo, Metadata, Product

finding = build(
    DetectionFinding,
    activity_id=DetectionFindingActivityId.CREATE,
    severity_id=SeverityId.HIGH,
    time=1_700_000_000_000,
    metadata=Metadata(product=Product(name="Sensor", vendor_name="Acme")),
    finding_info=FindingInfo(uid="f-1", title="Suspicious login"),
)
assert finding.type_uid == 200401
wire = msgspec.json.encode(finding)  # the event as JSON bytes

same, report = parse(msgspec.json.decode(wire), as_=DetectionFinding)
assert same == finding and report.ok()
```

The models type-check under strict pyright, and importing one class loads only that class's module.

## More

- [Schema as data](https://github.com/k4otix/ocsf-semtools/blob/main/docs/schema.md): required attributes, enums and captions, siblings, profiles,
  class identity, all without building an event.
- [Building and checking events](https://github.com/k4otix/ocsf-semtools/blob/main/docs/events.md): `build`, `parse`, `repair`, what each one changes,
  and reports.
- [Observables](https://github.com/k4otix/ocsf-semtools/blob/main/docs/observables.md): extracting and merging the observables an event carries.
- [Schema diff](https://github.com/k4otix/ocsf-semtools/blob/main/docs/diff.md): what changed between two OCSF versions, renames included.
- [Typed models](https://github.com/k4otix/ocsf-semtools/blob/main/docs/models.md): the model packages, and generating your own (custom extensions
  and exact profile sets).
- [Command line](https://github.com/k4otix/ocsf-semtools/blob/main/docs/cli.md): `check`, `diff`, `generate` and the schema pipeline.

## How versions work

Each OCSF release is compiled with the official `ocsf-schema-compiler` from an exact upstream commit,
recorded in [`ocsf.lock`](https://github.com/k4otix/ocsf-semtools/blob/main/ocsf.lock). CI recompiles every release and confirms
that the shipped schema still matches the upstream compiler's output. The core package carries every
release, so events of any version can be checked, repaired, diffed and mined for observables. Typed
models come as one package per release for the newest three, so a project installs only the versions
it produces.

The library follows SemVer on its own API, and before 1.0 a minor release may change it. Support for
a new OCSF release arrives in a minor release. A model package accepts every core release up to the
next minor, so core bugfixes install under it unchanged.

## Other Python OCSF libraries

Several Python packages cover parts of OCSF. [pydantic-ocsf](https://pypi.org/project/pydantic-ocsf/)
offers Pydantic models across OCSF versions, and its computed `type_uid` and sibling-label checks
shaped ideas here. [py-ocsf-models](https://pypi.org/project/py-ocsf-models/) provides models for
finding classes. [ocsf-lib](https://pypi.org/project/ocsf-lib/) provides schema tooling for
compiling, diffing and compatibility checks.

ocsf-semtools fills the gaps between them:

- The schema comes from the official compiler with profiles resolved, so every attribute and enum
  matches upstream.
- The schema is data a program can query without building a model.
- One report lists every problem in an event.
- Observables are derived from the schema.
- Profiles are part of an event's identity.

The table compares major features as of 2026-11-08, one release per column, and includes the Go
library [ocsf/ocsf-toolkit](https://github.com/ocsf/ocsf-toolkit).

● full · ◕ mostly · ◑ partial · ◔ minimal · ○ none · ? not tested

| | ocsf-semtools 0.1.0a4 | ocsf/ocsf-toolkit v0.9.0 (Go) | pydantic-ocsf 3.0.0 | py-ocsf-models 0.10.0 | ocsf-lib 0.10.4 |
|---|:-:|:-:|:-:|:-:|:-:|
| Schema matches the official compiler | ● | ● | ◔ | ◔ | ◑ |
| Several OCSF versions side by side | ● | ◑ | ◕ | ○ | ● |
| Profiles | ● | ● | ○ | ○ | ◔ |
| Schema queryable as data | ● | ○ | ◔ | ○ | ● |
| Complete validation report | ● | ● | ◔ | ◔ | ○ |
| Per-check severity control | ◑ | ● | ○ | ○ | ○ |
| Type constraints (pattern, length, range) | ○ | ● | ? | ? | ○ |
| Fills derived fields (`type_uid`, labels) | ● | ◑ | ◑ | ○ | ○ |
| Observables from the schema | ● | ● | ○ | ○ | ○ |
| Removes enrichment | ○ | ● | ○ | ○ | ○ |
| Diff between OCSF versions | ● | ○ | ○ | ○ | ● |
| Typed models | ● | ○ | ◑ | ◔ | ○ |
| Custom extensions | ● | ● | ○ | ○ | ● |

## License

Apache-2.0. The bundled schema data is compiled from the [OCSF schema](https://github.com/ocsf/ocsf-schema),
© OCSF a Series of LF Projects, LLC, also under Apache-2.0; [NOTICE](https://github.com/k4otix/ocsf-semtools/blob/main/NOTICE) carries its notice.
ocsf-semtools is an independent project with no affiliation to OCSF, and is separate from the Go
library [ocsf/ocsf-toolkit](https://github.com/ocsf/ocsf-toolkit).
