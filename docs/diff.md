# Schema diff

OCSF changes between releases: classes are added and renamed, requirements shift, and enum members
are added and deprecated. `diff` lists every such change between two versions, as data a migration
script or a reviewer can act on.

## Comparing two versions

### `diff`

`diff(old, new)` compares two [IRs](schema.md#the-ir) field by field and returns every difference,
keyed by its path in the IR. Classes are matched by `class_uid`, then by key, so a class renamed at a
stable uid shows up as one rename. Matching is exact.

```pycon
>>> from ocsf_semtools import diff, load
>>> changes = diff(load("1.7.0").ir, load("1.9.0").ir)
>>> changes.old, changes.new, len(changes.changes)
('1.7.0', '1.9.0', 1115)
>>> [c.path[1] for c in changes.changes if c.kind == "added" and c.path[0] == "classes" and len(c.path) == 2]
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

## The result

### `SchemaDiff`

`SchemaDiff(old, new, changes)`: the two versions and the changes, sorted by path. `to_dict()`
renders IR values as plain JSON, which is what `ocsf-semtools diff --json` prints.

```pycon
>>> sorted(changes.to_dict())
['changes', 'new', 'old']

```

### `Change`

`Change(kind, path, before, after)`: one difference. `kind` is `added`, `removed`, `changed` or
`renamed`, and `path` is a tuple of IR keys. An added or removed subtree is one change, with `None`
on the side where it is absent. A rename is one change at the old path, with the old and new keys as
`before` and `after`, after which the class is compared under its new key.
