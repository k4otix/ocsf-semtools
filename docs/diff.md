# Schema diff

### `diff`

`diff(a, b)` compares two IRs field by field and returns every difference, keyed by its path in the IR.
Classes are matched by `class_uid` before key, so a class renamed at a stable uid shows up as a rename
instead of a removal plus an addition. There are no similarity heuristics and no event migration:
the diff is data for you to read or act on.

```pycon
>>> from ocsf_toolkit import diff, load
>>> changes = diff(load("1.7.0").ir, load("1.9.0").ir)
>>> changes.a, changes.b, len(changes.changes)
('1.7.0', '1.9.0', 1115)
>>> [
...     c.path[1]
...     for c in changes.changes
...     if c.kind == "added" and c.path[0] == "classes" and len(c.path) == 2
... ]
['clipboard_activity', 'device_power_state_activity', 'role_management', 'user_management']

```

Requirement drift, profile gating, enum members, deprecations and type changes all fall out of the
same comparison:

```pycon
>>> for c in changes.changes:
...     if c.path[:2] == ("classes", "detection_finding") and c.path[-1] == "superseded_by":
...         print(".".join(c.path), c.before, "->", c.after)
classes.detection_finding.attributes.ticket.deprecated.superseded_by () -> ('tickets',)

```

The upstream rename of the `*_info` Windows classes to `*_query` between 1.1.0 and 1.3.0:

```pycon
>>> for c in diff(load("1.1.0").ir, load("1.3.0").ir).changes:
...     if c.kind == "renamed":
...         print(c.before, "->", c.after)
win/prefetch_info -> win/prefetch_query
win/registry_key_info -> win/registry_key_query
win/registry_value_info -> win/registry_value_query

```

### `SchemaDiff`

`SchemaDiff(a, b, changes)`: the two versions and the changes, sorted by path. Each change has a
`kind` (`added`, `removed`, `changed` or `renamed`), a `path`, and `before` and `after` values. An
added or removed subtree is one change. A rename is one change at the old path, after which the
class is compared under its new key. `to_dict()` renders IR values as plain JSON, which is what
`ocsf-toolkit diff --json` prints.

```pycon
>>> sorted(changes.to_dict())
['a', 'b', 'changes']

```
