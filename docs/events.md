# Building and checking events

Three functions handle events. `build` creates one from known fields and fills in what the schema
derives, `parse` checks one as received and changes nothing, and `repair` returns a corrected copy.
All three run the same checks, so an event gets the same findings from each, and each returns or
raises a `Report` holding every finding.

```pycon
>>> import json
>>> from ocsf_semtools import build, parse, repair, load
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
every event it returns conforms. For each deprecated attribute or enum value it uses, it emits a
`DeprecationWarning` naming the replacement. Given a [generated model class](models.md) in place of
`(schema, class_key)`, it takes and returns typed instances.

```pycon
>>> event = build((schema, "detection_finding"), **fields)
>>> event["type_uid"], event["severity"], event["metadata"]["version"]
(200401, 'High', '1.9.0')

```

### `parse`

`parse(event, *, version=None, profiles=None, class_key=None, schema=None, as_=None, max_nodes=100_000)`
checks an event and returns `(event, report)`: the same dict it was given, unchanged, and a report of
every problem in it. The OCSF version, profiles, extensions and class default
to what the event says about itself: `metadata.version`, `metadata.profiles`, the names in
`metadata.extensions`, and `class_uid` (then `type_uid`). An event that names no class this schema
knows is checked as `base_event`. `schema=` reads the event against a schema the caller resolved,
such as one with a custom extension, in place of `version` and `profiles`. With `as_=` a generated
class, it returns an instance of that class, or `None` if the event has errors; the class then fixes
the schema, so `version`, `class_key` and `schema` are a `TypeError`.

```pycon
>>> _, report = parse(dict(event, severity="Low"))
>>> [finding.message for finding in report.errors()]
["severity is 'Low'; the schema derives 'High'"]
>>> with_cloud = dict(event, metadata=dict(event["metadata"], profiles=["cloud"]))
>>> [finding.message for finding in parse(with_cloud)[1].errors()]
["cloud is required under profiles ['cloud'] and absent"]

```

### `repair`

`repair(event, *, version=None, profiles=None, class_key=None, schema=None, max_nodes=100_000)` picks
the schema the way `parse` does, and returns a normalized copy and a report in which every change is
a finding with its `repair` action (before and after). It fills derived values, coerces values whose
meaning is unambiguous, and moves attributes the schema doesn't know into `unmapped`, each under its
dotted path: an unknown `zzz` inside `process` becomes `unmapped["process.zzz"]`. An event that also
has a top-level attribute literally named `"process.zzz"` gives two attributes one key; the first
one reached moves, and the other stays in place, reported as `unknown_attribute`.

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
...     print(finding.kind.value, finding.path, f"{finding.repair.before!r} -> {finding.repair.after!r}")
coerced ('activity_id',) '1' -> 1
relocated ('vendor_field',) ('vendor_field',) -> ('unmapped', 'vendor_field')
filled ('class_name',) None -> 'Detection Finding'
>>> fixed["activity_id"], fixed["unmapped"], report.ok()
(1, {'vendor_field': 'x'}, True)

```

### `ConformanceError`

`build` raises it when the event has errors. `error.report` is the full `Report`, and `error.fatal`
holds the findings that stopped the build: the errors, and for an exact model class also any attribute
outside its profile set. `str(error)` names the first five of them. The exception, like a `Report`
and a `ResolvedSchema`, can be pickled, so it crosses process boundaries.

```pycon
>>> from ocsf_semtools import ConformanceError
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

Every finding from one run: `findings` in walk order, the `schema` the event was read against, and
the `class_key` it was checked as. `ok()` is true when nothing is an error; `errors()` returns the
findings of severity `error`; `by_kind(kind)` filters by a `Kind` or its name; `to_dict()` gives a
form that is always valid JSON, which is also what `ocsf-semtools check --json` prints.

### `Finding`

One deviation, located by an indexed path such as `("resources", 0, "uid")`: `kind`, `path`,
`owner` (the class or object it belongs to), `message`, `tier` (the attribute's requirement),
`profiles`, `expected`, `actual`, `repair` (set when `repair` changed something) and `severity`
(`error`, `warning` or `info`, determined by the kind).

```pycon
>>> from ocsf_semtools import Kind
>>> _, report = parse(dict(event, vendor_field=1))
>>> (unknown,) = report.by_kind(Kind.UNKNOWN_ATTRIBUTE)
>>> unknown.severity, unknown.path, unknown.message
('error', ('vendor_field',), 'vendor_field is not an attribute of detection_finding')
>>> sorted(report.to_dict())
['class', 'extensions', 'findings', 'ok', 'profiles', 'version']

```

### `RepairAction`

What `repair` changed at a finding's path: `action` (`fill`, `coerce`, `overwrite`, `relocate` or
`migrate`), `before` and `after`. A finding with a `repair` has severity `info`.

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

An OCSF data type can carry a pattern (`regex`), a maximum length (`max_len`), a numeric `range` or
a list of allowed `values`. The library loads them with the schema, in `ResolvedSchema.types`, and
conformance doesn't check them yet: in every OCSF release so far, some of these patterns accept
invalid IP and email addresses and some limits reject valid values. A later release adds the check
as a finding kind once an OCSF release ships corrected patterns.

```pycon
>>> len(Kind), Kind("missing_required") is Kind.MISSING_REQUIRED
(17, True)

```
