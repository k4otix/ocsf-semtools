# Schema as data

Everything OCSF says about a class is available as plain data for a given version, without building an
event or importing a model: required attributes, types, enums and their captions, sibling pairs,
profile gating and class identity.

## Loading a version

### `load`

`load(version, *, profiles=(), extensions=())` returns the `ResolvedSchema` for an OCSF version that
ships with the library. Every release from 1.0.0 to 1.9.0 ships.

```pycon
>>> from ocsf_toolkit import load
>>> schema = load("1.9.0")
>>> schema.version
'1.9.0'
>>> len(schema.classes), len(schema.objects)
(80, 190)

```

A schema is resolved under an active set of profiles and extensions. Attributes a profile adds exist
only when that profile is active, and their requirement is the one that profile gives them:

```pycon
>>> "cloud" in schema.classes["detection_finding"].attributes
False
>>> cloud = load("1.9.0", profiles=["cloud"])
>>> attribute = cloud.classes["detection_finding"].attributes["cloud"]
>>> attribute.requirement.value, sorted(attribute.gated_by)
('required', ['cloud'])
>>> sorted(schema.ir.profiles)[:5]
['ai_operation', 'cloud', 'container', 'data_classification', 'datetime']

```

An unknown profile or extension name raises `KeyError`. Schemas are cached, equal and hashable by
version, profiles and extensions.

### `ResolvedSchema`

| Member | What it is |
|---|---|
| `version`, `profiles`, `extensions` | what was resolved |
| `digest` | sha256 of the IR it came from |
| `ir` | the underlying `SchemaIR` |
| `classes`, `objects` | read-only mappings from key to `ResolvedItem` |
| `categories`, `types` | read-only mappings of the schema's categories and data types |
| `class_by_uid(uid)` | the class with that `class_uid`; `KeyError` if none |
| `class_by_type_uid(type_uid)` | `(class, activity_id)` for a `type_uid`; `KeyError` if none |
| `observable_markers` | read-only mapping from `(owner key, attribute)` to observable `type_id` |

```pycon
>>> schema.class_by_uid(2004).key
'detection_finding'
>>> item, activity_id = schema.class_by_type_uid(200401)
>>> item.key, activity_id
('detection_finding', 1)

```

### `ResolvedItem`

A class or an object, holding only the attributes present under the active profiles and extensions.

```pycon
>>> finding = schema.classes["detection_finding"]
>>> finding.caption, finding.is_class
('Detection Finding', True)
>>> finding.identity
ClassIdentity(class_uid=2004, class_name='Detection Finding', category_uid=2, category_name='Findings')
>>> sorted(name for name, a in finding.attributes.items() if a.requirement.value == "required")
['activity_id', 'category_uid', 'class_uid', 'finding_info', 'metadata', 'severity_id', 'time', 'type_uid']
>>> finding.siblings["severity_id"].label
'severity'

```

`identity` is `None` for objects. `siblings` maps each `_id` attribute to its label attribute, with
the enum that derives one from the other. `inactive` holds the attributes that only inactive profiles
add: an event that uses one gets a `profile_mismatch` warning, and its value is still checked.

### `ResolvedAttribute`

One attribute as it applies under the active set: `name`, `caption`, `type`, `object_type`,
`is_array`, `requirement`, `enum`, `sibling`, `observable`, `deprecated`, `profiles` (the profiles that
mention it), `gated_by` (the active profiles it depends on), `extension` and `owner`.

```pycon
>>> severity = finding.attributes["severity_id"]
>>> severity.type, severity.sibling
('integer_t', 'severity')
>>> {value: member.caption for value, member in severity.enum.items()}
{0: 'Unknown', 1: 'Informational', 2: 'Low', 3: 'Medium', 4: 'High', 5: 'Critical', 6: 'Fatal', 99: 'Other'}
>>> schema.objects["metadata"].attributes["version"].requirement.value
'required'

```

## The IR

### `SchemaIR`

The schema intermediate representation: one OCSF version as frozen dataclasses, before any profile or
extension is chosen. Every shipped version is an IR file inside the package, and its provenance
records exactly where it came from:

```pycon
>>> provenance = schema.ir.provenance
>>> provenance.schema_repo, provenance.ref, provenance.commit
('https://github.com/ocsf/ocsf-schema', '1.9.0', '856d462bd20dc46cc1ffed2dfffe3b91ef0fbeba')
>>> provenance.compiler_version
'1.1.1'

```

### `load_ir`

`load_ir(source)` reads IR JSON from a path (a `str` or `Path`) or from bytes, such as a file written
by `ocsf-toolkit ir export` or bundled in a generated model package. Malformed JSON, NaN or infinity,
and fields of the wrong type are a `ValueError` naming the key path; `resolve` also refuses an IR
whose tables disagree, such as one with no `base_event` or an attribute naming a missing object.

### `export_ir`

`export_ir(ir)` returns the canonical IR JSON bytes: sorted keys, compact separators, UTF-8, so equal
IRs give identical bytes on every platform. `load_ir(export_ir(ir)) == ir`.

### `resolve`

`resolve(ir, *, profiles=(), extensions=())` resolves any IR, including one compiled with your own
extensions, the way `load` resolves a shipped one:

```pycon
>>> from ocsf_toolkit import resolve
>>> resolve(schema.ir, profiles=["cloud"]) == cloud
True

```
