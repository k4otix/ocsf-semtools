# Building and checking events

Three entry points share one engine and one traversal of the event, so they find the same problems
in it. They differ in what they change: `build` fills in what the schema derives, `parse` changes
nothing, and `repair` also corrects what it safely can. Each returns or raises a `Report` that holds
every finding.

```pycon
>>> import json
>>> from ocsf_toolkit import build, parse, repair, load
>>> schema = load("1.9.0")
>>> fields = {
...     "activity_id": 1,
...     "severity_id": 4,
...     "time": 1_700_000_000_000,
...     "metadata": {"product": {"name": "Sensor", "vendor_name": "Acme"}},
...     "finding_info": {"uid": "f-1", "title": "Suspicious login"},
... }

```

## Entry points

### `build`

`build((schema, class_key), **fields)` constructs a conformant event dict. It fills everything the
schema derives: class and category identity, `type_uid`, sibling labels such as `severity` beside
`severity_id`, and `metadata.version`. If the result has errors it raises `ConformanceError`, so
every event it returns conforms. Given a [generated model class](models.md) in place of
`(schema, class_key)`, it takes and returns typed instances.

```pycon
>>> event = build((schema, "detection_finding"), **fields)
>>> event["type_uid"], event["severity"], event["metadata"]["version"]
(200401, 'High', '1.9.0')

```

### `parse`

`parse(event, *, version=None, profiles=None, class_key=None, as_=None)` checks an event and returns
`(event, report)`. It leaves the event as it is and puts every problem in the report. The OCSF version, profiles
and class default to what the event says about itself: `metadata.version`, `metadata.profiles` and
`class_uid`. With `as_=` a generated class, it returns an instance of that class, or `None` if the
event has errors.

```pycon
>>> _, report = parse(dict(event, severity="Low"))
>>> [finding.message for finding in report.errors()]
["severity is 'Low'; the schema derives 'High'"]
>>> with_cloud = dict(event, metadata=dict(event["metadata"], profiles=["cloud"]))
>>> [finding.message for finding in parse(with_cloud)[1].errors()]
["cloud is required under profiles ['cloud'] and absent"]

```

### `repair`

`repair(event, ...)` returns a normalized copy and a report in which every change is a finding
with its `repair` action (before and after). It fills derived values, coerces values whose meaning
is unambiguous, and moves attributes the schema doesn't know into `unmapped`.

```pycon
>>> received = {
...     "class_uid": 2004,
...     "activity_id": "1",
...     "severity_id": 4,
...     "time": 1_700_000_000_000,
...     "metadata": {"version": "1.9.0", "product": {"name": "Sensor", "vendor_name": "Acme"}},
...     "finding_info": {"uid": "f-1", "title": "Suspicious login"},
...     "vendor_field": "x",
... }
>>> fixed, report = repair(received)
>>> for finding in report.findings[:3]:
...     print(finding.kind.value, finding.path, finding.repair.before, "->", finding.repair.after)
coerced ('activity_id',) 1 -> 1
relocated ('vendor_field',) ('vendor_field',) -> ('unmapped', 'vendor_field')
filled ('class_name',) None -> Detection Finding
>>> fixed["activity_id"], fixed["unmapped"], report.ok()
(1, {'vendor_field': 'x'}, True)

```

The first line shows the string `"1"` coerced to the integer `1`; both print as `1`.

### `ConformanceError`

Raised by `build` only, and the only exception the library raises for an event. `error.report` is the
full `Report`; `str(error)` names the errors.

```pycon
>>> from ocsf_toolkit import ConformanceError
>>> try:
...     build((schema, "detection_finding"), severity_id=4, time=1)
... except ConformanceError as error:
...     print(error)
3 conformance error(s) in detection_finding: activity_id is required and absent; finding_info is required and absent; metadata is required and absent

```

## What each entry point changes

| | `build` | `parse` | `parse(as_=...)` | `repair` |
|---|---|---|---|---|
| fills derived values (identity, labels, `*_dt`, `metadata.version`) | yes | no | no | yes |
| replaces a derived value that conflicts with the schema | no | no | no | yes |
| coerces `"4"` to `4`, `"true"` to `True`, `4.0` to `4` | no | no | no | yes |
| moves a deprecated attribute to its one successor | no | no | no | yes |
| moves unknown attributes into `unmapped` | no | no | yes | yes |
| raises `ConformanceError` on errors | yes | no | no | no |

## Reports

### `Report`

Every finding from one run. `ok()` is true when nothing is an error; `errors()` returns the findings
of severity `error`; `by_kind(kind)` filters by `Kind`; `to_dict()` gives a JSON-ready form, which
is also what `ocsf-toolkit check --json` prints.

### `Finding`

One deviation, located by an indexed path such as `("resources", 0, "uid")`: `kind`, `path`,
`owner` (the class or object it belongs to), `message`, `tier` (the attribute's requirement),
`profiles`, `expected`, `actual`, `repair` (set when `repair` changed something) and `severity`
(`error`, `warning` or `info`, determined by the kind).

```pycon
>>> from ocsf_toolkit import Kind
>>> _, report = parse(dict(event, vendor_field=1))
>>> (unknown,) = report.by_kind(Kind.UNKNOWN_ATTRIBUTE)
>>> unknown.severity, unknown.path, unknown.message
('error', ('vendor_field',), 'vendor_field is not an attribute of detection_finding')
>>> sorted(report.to_dict())
['class', 'extensions', 'findings', 'ok', 'profiles', 'version']

```

### `Kind`

What a finding is about, which also fixes its severity:

| Severity | Kinds |
|---|---|
| error | `missing_required`, `unknown_attribute`, `type_mismatch`, `enum_out_of_range`, `sibling_conflict`, `identity_conflict`, `constraint_violated`, `repeated_node` |
| warning | `missing_recommended`, `profile_mismatch`, `deprecated`, `observable_mismatch` |
| info | `semantically_empty_enum`, `max_nodes_exceeded`, and the repairs `filled`, `coerced`, `relocated` |

Any finding that `repair` resolved is `info`, whatever its kind.

`repeated_node` marks an object the event holds twice, through a cycle or under two paths; it is
walked once. `parse`, `repair` and `extract` stop after `max_nodes` attribute visits (100,000 by
default, `None` for no limit) with a `max_nodes_exceeded` finding, and `build` stops at the same
default, so no event can make a run hang.

```pycon
>>> len(Kind), Kind("missing_required") is Kind.MISSING_REQUIRED
(17, True)

```
