# Typed models

The models are a projection of the schema: every class and object as a
[msgspec](https://github.com/msgspec/msgspec) Struct, every enum as an `IntEnum` or `StrEnum` whose
members are the OCSF captions. They are generated from the same IR the rest of the library uses, so
the dict API and the typed API never disagree.

## Model packages

One package per OCSF version, for the newest three releases. Install the ones you produce:

| Install | Import |
|---|---|
| `pip install ocsf-toolkit-v1-9-0` | `ocsf_toolkit_v1_9_0` |
| `pip install ocsf-toolkit-v1-8-0` | `ocsf_toolkit_v1_8_0` |
| `pip install ocsf-toolkit-v1-7-0` | `ocsf_toolkit_v1_7_0` |

Packages for different versions install side by side. Each one bundles the IR it was generated from
and requires a range of `ocsf-toolkit` versions, so upgrading the core doesn't force a new model
package.

```python
from ocsf_toolkit import build, parse
from ocsf_toolkit_v1_9_0 import DetectionFinding  # classes from the package root
from ocsf_toolkit_v1_9_0.enums import SeverityId  # enums shared through the dictionary
from ocsf_toolkit_v1_9_0.objects import Metadata, Product  # objects
from ocsf_toolkit_v1_9_0.classes.detection_finding import DetectionFindingActivityId  # class enums
```

Layout of a package:

| Module | Holds |
|---|---|
| package root | every class, imported on first use (subpackages are imported as usual); `__ocsf_version__`, `__schema_digest__`, `__ocsf_profiles__`, `__ocsf_extensions__` |
| `classes.<key>` | one class and the enums only it uses |
| `objects.<key>` | one object and its own enums |
| `ext.<extension>.<key>` | classes and objects an extension adds |
| `enums` | enums defined once in the OCSF dictionary, such as `SeverityId` |

Importing one class loads only that class's module. Every package passes ruff and pyright in strict
mode with no exclusions.

**Superset classes.** The published packages are generated for no particular profile set. Every
attribute any profile adds is present and optional, and `build` enforces what the event's own
`metadata.profiles` require at runtime. Attributes that `build` derives, such as `type_uid`,
`class_name` and sibling labels, are always optional on the class.

**Enums by caption.** `SeverityId.HIGH is SeverityId(4)`; a caption that starts with a digit gets an
`N_` prefix (`N_5VIEWS`).

`build(Class, **fields)` returns an instance of `Class` with everything derived, or raises
`ConformanceError`. `parse(event, as_=Class)` returns `(instance or None, report)`; unknown attributes
are moved into `unmapped`, since a Struct has no field for them.

## Generating your own

### `generate`

`generate(ir, *, package, out, profiles=None, extensions=None)` writes the package `out/<package>`
and returns its path. Use it for an exact profile set, a subset of extensions, or an IR compiled with
your own extensions (see `ocsf-toolkit generate --ext` in the [CLI docs](cli.md)).

With `profiles`, the classes are exact for that set: an attribute a profile requires is a required
field.

```pycon
>>> import sys, tempfile
>>> from pathlib import Path
>>> from ocsf_toolkit import ConformanceError, build, generate, load
>>> tmp = tempfile.TemporaryDirectory()
>>> path = generate(load("1.9.0").ir, package="acme_models", out=Path(tmp.name), profiles=["cloud"])
>>> path.name
'acme_models'
>>> sys.path.insert(0, tmp.name)
>>> import acme_models
>>> acme_models.__ocsf_version__, acme_models.__ocsf_profiles__
('1.9.0', ('cloud',))
>>> from acme_models import enums, objects as o
>>> finding = build(
...     acme_models.DetectionFinding,
...     activity_id=1,
...     severity_id=enums.SeverityId.HIGH,
...     time=1_700_000_000_000,
...     metadata=o.Metadata(product=o.Product(name="Sensor", vendor_name="Acme")),
...     finding_info=o.FindingInfo(uid="f-1", title="Suspicious login"),
...     cloud=o.Cloud(provider="AWS"),
... )
>>> finding.type_uid, finding.severity, finding.metadata.profiles
(200401, 'High', ['cloud'])
>>> enums.SeverityId.HIGH is enums.SeverityId(4)
True
>>> sys.path.remove(tmp.name)
>>> tmp.cleanup()

```

`generate` replaces an existing `out/<package>` only if every file in it is one the generator writes;
anything else there stops it with an error and nothing changes. The generated code imports nothing
from `ocsf_toolkit`; `build` and `parse` find the schema through the package's bundled IR.
