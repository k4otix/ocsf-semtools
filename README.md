# ocsf-toolkit

Version-pinned [OCSF](https://schema.ocsf.io/) semantics for Python. Each OCSF release is compiled
from upstream `ocsf/ocsf-schema` at a pinned commit into a schema you can query as data. On top of
that schema, the library builds events and derives everything the schema computes, checks events and
reports every deviation it finds, extracts observables, and diffs two versions. Typed
[msgspec](https://github.com/msgspec/msgspec) models are generated from the same schema.

**Status: alpha.** Minor releases before 1.0 may change the API, and the
[changelog](https://github.com/k4otix/ocsf-toolkit/blob/main/CHANGELOG.md) lists every change.

## Install

```shell
pip install --pre ocsf-toolkit          # core: every OCSF release from 1.0.0 to 1.9.0, as data
pip install --pre ocsf-toolkit-v1-9-0   # optional: typed models for OCSF 1.9.0 (also -v1-8-0, -v1-7-0)
```

`ocsf-toolkit generate` builds a model package for any other version or extension set (see
[Command line](https://github.com/k4otix/ocsf-toolkit/blob/main/docs/cli.md)).

Python 3.12 or newer. The only runtime dependency is msgspec.

## Build and emit one event

Pick an OCSF version and a class, give the fields you know, and `build` fills in what the schema
derives: class and category identity, `type_uid`, the label beside every `_id` enum, and
`metadata.version`.

```pycon
>>> import json
>>> from ocsf_toolkit import build, load
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
>>> from ocsf_toolkit import ConformanceError
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
severity. You decide which findings are fatal.

```pycon
>>> from ocsf_toolkit import parse
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
`ocsf-toolkit check --version 1.9.0 events.jsonl`.

## Typed models

With `ocsf-toolkit-v1-9-0` installed, every class and object is a msgspec Struct, every enum is
referenceable by its caption, and `build` and `parse` accept and return the typed classes:

```python
import msgspec

from ocsf_toolkit import build, parse
from ocsf_toolkit_v1_9_0 import DetectionFinding
from ocsf_toolkit_v1_9_0.classes.detection_finding import DetectionFindingActivityId
from ocsf_toolkit_v1_9_0.enums import SeverityId
from ocsf_toolkit_v1_9_0.objects import FindingInfo, Metadata, Product

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

The models pass pyright in strict mode, and importing one class loads only that class's module.

## More

- [Schema as data](https://github.com/k4otix/ocsf-toolkit/blob/main/docs/schema.md): required attributes, enums and captions, siblings, profiles,
  class identity, all without building an event.
- [Building and checking events](https://github.com/k4otix/ocsf-toolkit/blob/main/docs/events.md): `build`, `parse`, `repair`, what each one changes,
  and reports.
- [Observables](https://github.com/k4otix/ocsf-toolkit/blob/main/docs/observables.md): extracting and merging the observables an event carries.
- [Schema diff](https://github.com/k4otix/ocsf-toolkit/blob/main/docs/diff.md): what changed between two OCSF versions, renames included.
- [Typed models](https://github.com/k4otix/ocsf-toolkit/blob/main/docs/models.md): the model packages, and generating your own (custom extensions
  and exact profile sets).
- [Command line](https://github.com/k4otix/ocsf-toolkit/blob/main/docs/cli.md): `check`, `diff`, `generate` and the schema pipeline.

## How versions work

Every OCSF release is pinned by upstream commit in [`ocsf.lock`](https://github.com/k4otix/ocsf-toolkit/blob/main/ocsf.lock) and compiled with the
official `ocsf-schema-compiler`. CI recompiles each one and checks its canonical sha256 against the
lockfile. Core ships the schema of every release, so you can check, repair and diff events of any of
them and extract their observables. Typed models ship for the newest three releases, one package
each, so you install only the versions you produce.

The library's own version follows SemVer on its API; before 1.0, a minor release may change it.
Support for a new OCSF release ships as a minor release of the library. A model package requires the
core minor it was generated with, so patch releases of the core install under it unchanged.

## Why not an existing library?

We installed and ran each Python OCSF package we could find. These results are from 2026-11-02,
against the versions named:

- **pydantic-ocsf** (3.0.0) is the closest relative and the one we learned the most from: multiple
  OCSF versions side by side, sibling-label checks and computed `type_uid`. It builds its models
  from the schema's *source* files, which leave profiles unresolved, so profile attributes such as
  `actor` or `action_id` are missing or untyped. Against the official compiler's output for
  OCSF 1.7.0, about a third of its integer enums differ; for example, `device.type_id=2`
  ("Desktop") is rejected.
- **py-ocsf-models** (0.10.0, Prowler) is a hand-written model set for three finding classes at
  OCSF 1.5.0. It silently drops attributes it doesn't model.
- **ocsf-lib** (0.10.4) is useful schema tooling (compile, diff, compatibility checks), but it has
  no event models or validation. It can't read the official `ocsf-schema-compiler` output, and its
  own compiler disagrees with upstream on profile gating.

This library exists for four things those packages lack: OCSF knowledge as data you can query
without building a model, a conformance report that lists every problem in an event, observables
derived from the schema, and profiles treated as part of an event's identity.

## License

Apache-2.0. The bundled schema data is compiled from the [OCSF schema](https://github.com/ocsf/ocsf-schema),
© OCSF a Series of LF Projects, LLC, also under Apache-2.0; [NOTICE](https://github.com/k4otix/ocsf-toolkit/blob/main/NOTICE) carries its notice.
ocsf-toolkit is an independent project with no affiliation to OCSF.
