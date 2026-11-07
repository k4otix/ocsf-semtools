"""Convert a SchemaIR to and from canonical IR JSON, whose sha256 is the IR's identity."""

import hashlib
import json
import os
import warnings
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from ocsf_toolkit.ir import (
    Attribute,
    Category,
    Constraint,
    Deprecation,
    EnumMember,
    EnumValue,
    Extension,
    ItemDef,
    JsonObject,
    Profile,
    Provenance,
    Requirement,
    Root,
    SchemaIR,
    TypeDef,
    as_list,
    constraint_kind,
    enum_key,
    frozen_sorted,
    json_kind,
    parse_requirement,
)

IR_VERSION = "1.0"
_MAJOR, _MINOR = (int(part) for part in IR_VERSION.split("."))

# Encoding rules: object keys sorted, one-space indent (so a git diff shows what changed),
# UTF-8, trailing newline. A field that is None or an empty collection is omitted, except
# provenance.extensions, which is always written as a list; booleans are always written. A name
# that is already the key of its mapping is not repeated inside the entry. Frozensets are written
# as sorted lists.


def export_ir(ir: SchemaIR) -> bytes:
    """Return the canonical IR JSON bytes: identical for equal IRs, on every platform."""
    document = {
        "ir_version": IR_VERSION,
        "provenance": _provenance(ir.provenance),
        "schema": {
            "version": ir.version,
            "compile_version": ir.compile_version,
            "categories": {k: _category(v) for k, v in ir.categories.items()},
            "types": {k: _type(v) for k, v in ir.types.items()},
            "dictionary": {k: _attribute(v) for k, v in ir.dictionary.items()},
            "classes": {k: _item(v) for k, v in ir.classes.items()},
            "objects": {k: _item(v) for k, v in ir.objects.items()},
            "profiles": {k: _profile(v) for k, v in ir.profiles.items()},
            "extensions": {k: _extension(v) for k, v in ir.extensions.items()},
        },
    }
    text = json.dumps(
        document,
        sort_keys=True,
        indent=1,
        separators=(",", ": "),
        ensure_ascii=False,
        allow_nan=False,
    )
    return (text + "\n").encode("utf-8")


def digest(ir: SchemaIR) -> str:
    """Return the sha256 of the canonical IR JSON: the value ``ocsf.lock`` records as ``ir_sha256``."""
    return hashlib.sha256(export_ir(ir)).hexdigest()


def _non_finite(constant: str) -> object:
    raise ValueError(f"IR holds {constant}, which JSON does not allow")


def load_ir(source: str | os.PathLike[str] | bytes) -> SchemaIR:
    """Read IR JSON from a file path or from bytes, such as ``export_ir`` writes.

    A different ``ir_version`` major is refused. A newer minor is read, and fields this reader
    doesn't know are ignored with a warning naming them; at this minor or older they are an error.
    A few display fields may be absent and read as defaults (an empty caption, deprecation since and
    message, or extension version; an item, profile or extension name equal to its key), so a
    hand-edited file can leave them out; the exporter always writes them.
    """
    data = Path(source).read_bytes() if isinstance(source, str | os.PathLike) else source
    try:
        raw: object = json.loads(data, parse_constant=_non_finite)
    except RecursionError:
        raise ValueError("IR is nested too deeply to be an IR JSON document") from None
    except ValueError as exc:
        raise ValueError(f"IR is not JSON: {exc}") from exc
    document = cast(dict[str, object], raw) if isinstance(raw, dict) else {}
    version = document.get("ir_version")
    if not isinstance(version, str):
        raise ValueError("$.ir_version: missing; not an IR JSON document")
    try:
        major, minor = (int(part) for part in version.split("."))
    except ValueError:
        raise ValueError(f"$.ir_version: malformed {version!r}") from None
    if major != _MAJOR:
        raise ValueError(f"$.ir_version: {version} is not supported; this reader reads {_MAJOR}.x")
    ignored: list[str] | None = [] if minor > _MINOR else None
    top = _Fields(document, "$", {"ir_version", "provenance", "schema"}, ignored)
    ir = _Reader(ignored).schema(top)
    if ignored:
        warnings.warn(
            f"IR {version} is newer than this reader ({IR_VERSION}); ignored fields: " + ", ".join(sorted(ignored)),
            stacklevel=2,
        )
    return ir


# --- encoding --------------------------------------------------------------------------------


def _obj(**fields: object) -> dict[str, object]:
    return {k: v for k, v in fields.items() if v is not None and v != [] and v != {}}


def _provenance(p: Provenance) -> dict[str, object]:
    return {
        "schema_repo": p.schema_repo,
        "ref": p.ref,
        "commit": p.commit,
        "compiler_version": p.compiler_version,
        "compiler_output_sha256": p.compiler_output_sha256,
        "extensions": [list(e) for e in p.extensions],
    }


def _deprecation(d: Deprecation | None) -> dict[str, object] | None:
    if d is None:
        return None
    return _obj(message=d.message, since=d.since, superseded_by=list(d.superseded_by))


def _category(c: Category) -> dict[str, object]:
    return _obj(uid=c.uid, caption=c.caption, extension=c.extension)


def _type(t: TypeDef) -> dict[str, object]:
    return _obj(
        caption=t.caption,
        root=t.root.value,
        parent=t.parent,
        max_len=t.max_len,
        regex=t.regex,
        range=list(t.range) if t.range is not None else None,
        values=list(t.values) if t.values is not None else None,
        observable=t.observable,
        extension=t.extension,
    )


def _attribute(a: Attribute) -> dict[str, object]:
    enum = None
    if a.enum is not None:
        enum = {
            str(value): _obj(caption=m.caption, deprecated=_deprecation(m.deprecated)) for value, m in a.enum.items()
        }
    return {
        "is_array": a.is_array,
        **_obj(
            caption=a.caption,
            type=a.type,
            object_type=a.object_type,
            requirement=a.requirement.value,
            profiles=sorted(a.profiles),
            sibling=a.sibling,
            enum=enum,
            observable=a.observable,
            deprecated=_deprecation(a.deprecated),
            extension=a.extension,
            group=a.group,
            suppress_checks=sorted(a.suppress_checks),
            default=a.default,
        ),
    }


def _item(i: ItemDef) -> dict[str, object]:
    return {
        "is_class": i.is_class,
        **_obj(
            name=i.name,
            caption=i.caption,
            extends=i.extends,
            extension=i.extension,
            deprecated=_deprecation(i.deprecated),
            attributes={k: _attribute(v) for k, v in i.attributes.items()},
            profiles=sorted(i.profiles),
            constraints=[{"kind": c.kind, "attributes": list(c.attributes)} for c in i.constraints],
            observable=i.observable,
            uid=i.uid,
            category=i.category,
            category_uid=i.category_uid,
            category_name=i.category_name,
            associations={k: list(v) for k, v in i.associations.items()},
            class_observables=dict(i.class_observables),
        ),
    }


def _profile(p: Profile) -> dict[str, object]:
    return _obj(name=p.name, caption=p.caption, extension=p.extension, annotations=dict(p.annotations))


def _extension(e: Extension) -> dict[str, object]:
    return {
        "platform": e.platform,
        **_obj(name=e.name, uid=e.uid, caption=e.caption, version=e.version),
    }


# --- decoding --------------------------------------------------------------------------------

_DEPRECATION = {"message", "since", "superseded_by"}
_PROVENANCE = {
    "schema_repo", "ref", "commit", "compiler_version", "compiler_output_sha256", "extensions",
}  # fmt: skip
_SCHEMA = {
    "version", "compile_version", "categories", "types", "dictionary", "classes", "objects",
    "profiles", "extensions",
}  # fmt: skip
_TYPE = {
    "caption", "root", "parent", "max_len", "regex", "range", "values", "observable", "extension",
}  # fmt: skip
_ATTRIBUTE = {
    "caption", "type", "object_type", "is_array", "requirement", "profiles", "sibling", "enum",
    "observable", "deprecated", "extension", "group", "suppress_checks", "default",
}  # fmt: skip
_ITEM = {
    "name", "caption", "is_class", "extends", "extension", "deprecated", "attributes", "profiles",
    "constraints", "observable", "uid", "category", "category_uid", "category_name",
    "associations", "class_observables",
}  # fmt: skip


class _Reader:
    def __init__(self, ignored: list[str] | None) -> None:
        self._ignored = ignored

    def _fields(self, raw: object, path: str, allowed: set[str]) -> "_Fields":
        return _Fields(raw, path, allowed, self._ignored)

    @staticmethod
    def _table(raw: object, path: str) -> dict[str, object]:
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: expected an object, got {json_kind(raw)}")
        return cast(dict[str, object], raw)

    def schema(self, top: "_Fields") -> SchemaIR:
        prov = self._fields(top.value("provenance"), "$.provenance", _PROVENANCE)
        s = self._fields(top.value("schema"), "$.schema", _SCHEMA)
        types = {
            name: self._type(name, raw, f"$.schema.types.{name}")
            for name, raw in self._table(s.value("types"), "$.schema.types").items()
        }
        roots = {name: t.root for name, t in types.items()}
        return SchemaIR(
            version=s.text("version"),
            compile_version=s.integer("compile_version"),
            provenance=Provenance(
                schema_repo=prov.text("schema_repo"),
                ref=prov.text("ref"),
                commit=prov.text("commit"),
                compiler_version=prov.text("compiler_version"),
                compiler_output_sha256=prov.text("compiler_output_sha256"),
                extensions=prov.triples("extensions"),
            ),
            categories=self._map(s, "categories", self._category),
            types=frozen_sorted(types),
            dictionary=self._map(s, "dictionary", lambda k, r, p: self._attribute(k, r, p, roots)),
            classes=self._map(s, "classes", lambda k, r, p: self._item(k, r, p, roots)),
            objects=self._map(s, "objects", lambda k, r, p: self._item(k, r, p, roots)),
            profiles=self._map(s, "profiles", self._profile),
            extensions=self._map(s, "extensions", self._extension),
        )

    def _map[V](
        self, owner: "_Fields", key: str, decode: "_Decoder[V]"
    ) -> Mapping[str, V]:  # fmt: skip
        path = f"{owner.path}.{key}"
        raw = owner.value(key)
        table = self._table({} if raw is None else raw, path)
        return frozen_sorted({name: decode(name, table[name], f"{path}.{name}") for name in sorted(table)})

    def _deprecation(self, raw: object, path: str) -> Deprecation | None:
        if raw is None:
            return None
        d = self._fields(raw, path, _DEPRECATION)
        return Deprecation(
            message=d.text("message", ""),
            since=d.text("since", ""),
            superseded_by=d.texts("superseded_by"),
        )

    def _category(self, name: str, raw: object, path: str) -> Category:
        c = self._fields(raw, path, {"uid", "caption", "extension"})
        return Category(
            name=name,
            uid=c.integer("uid"),
            caption=c.text("caption", ""),
            extension=c.opt_text("extension"),
        )

    def _type(self, name: str, raw: object, path: str) -> TypeDef:
        t = self._fields(raw, path, _TYPE)
        root = t.text("root")
        try:
            root_enum = Root(root)
        except ValueError:
            raise ValueError(f"{path}.root: unknown root {root!r}") from None
        return TypeDef(
            name=name,
            caption=t.text("caption", ""),
            root=root_enum,
            parent=t.opt_text("parent"),
            max_len=t.opt_integer("max_len"),
            regex=t.opt_text("regex"),
            range=t.range("range"),
            values=t.scalars("values"),
            observable=t.opt_integer("observable"),
            extension=t.opt_text("extension"),
        )

    def _attribute(self, name: str, raw: object, path: str, roots: Mapping[str, Root]) -> Attribute:
        a = self._fields(raw, path, _ATTRIBUTE)
        type_key = a.text("type")
        return Attribute(
            name=name,
            caption=a.text("caption", ""),
            type=type_key,
            object_type=a.opt_text("object_type"),
            is_array=a.flag("is_array"),
            requirement=parse_requirement(a.text("requirement", Requirement.OPTIONAL.value), path),
            profiles=frozenset(a.texts("profiles")),
            sibling=a.opt_text("sibling"),
            enum=self._enum(a.value("enum"), f"{path}.enum", roots.get(type_key)),
            observable=a.opt_integer("observable"),
            deprecated=self._deprecation(a.value("deprecated"), f"{path}.deprecated"),
            extension=a.opt_text("extension"),
            group=a.opt_text("group"),
            suppress_checks=frozenset(a.texts("suppress_checks")),
            default=a.scalar("default"),
        )

    def _enum(self, raw: object, path: str, root: Root | None) -> Mapping[EnumValue, EnumMember] | None:
        if raw is None:
            return None
        if root not in (Root.INTEGER, Root.LONG, Root.STRING):  # the compiler loader's rule, for hand-made IRs
            raise ValueError(f"{path}: enum on a {root.value if root else 'non-primitive'} type")
        members: dict[EnumValue, EnumMember] = {}
        for key, member_raw in self._table(raw, path).items():
            value = enum_key(key, root, f"{path}.{key}")
            m = self._fields(member_raw, f"{path}.{key}", {"caption", "deprecated"})
            members[value] = EnumMember(
                value=value,
                caption=m.text("caption", ""),
                deprecated=self._deprecation(m.value("deprecated"), f"{path}.{key}.deprecated"),
            )
        return frozen_sorted(members)

    def _item(self, key: str, raw: object, path: str, roots: Mapping[str, Root]) -> ItemDef:
        i = self._fields(raw, path, _ITEM)
        constraints: list[Constraint] = []
        for n, c_raw in enumerate(i.list("constraints")):
            c = self._fields(c_raw, f"{path}.constraints[{n}]", {"kind", "attributes"})
            kind = constraint_kind(c.text("kind"), f"{c.path}.kind")
            constraints.append(Constraint(kind=kind, attributes=c.texts("attributes")))
        return ItemDef(
            key=key,
            name=i.text("name", key),
            caption=i.text("caption", ""),
            is_class=i.flag("is_class"),
            extends=i.opt_text("extends"),
            extension=i.opt_text("extension"),
            deprecated=self._deprecation(i.value("deprecated"), f"{path}.deprecated"),
            attributes=self._map(i, "attributes", lambda k, r, p: self._attribute(k, r, p, roots)),
            profiles=frozenset(i.texts("profiles")),
            constraints=tuple(constraints),
            observable=i.opt_integer("observable"),
            uid=i.opt_integer("uid"),
            category=i.opt_text("category"),
            category_uid=i.opt_integer("category_uid"),
            category_name=i.opt_text("category_name"),
            associations=i.entries("associations", _Fields.texts),
            class_observables=i.entries("class_observables", _Fields.integer),
        )

    def _profile(self, key: str, raw: object, path: str) -> Profile:
        p = self._fields(raw, path, {"name", "caption", "extension", "annotations"})
        return Profile(
            key=key,
            name=p.text("name", key),
            caption=p.text("caption", ""),
            extension=p.opt_text("extension"),
            annotations=p.entries("annotations", _Fields.text),
        )

    def _extension(self, key: str, raw: object, path: str) -> Extension:
        e = self._fields(raw, path, {"name", "uid", "caption", "version", "platform"})
        return Extension(
            name=e.text("name", key),
            uid=e.integer("uid"),
            caption=e.text("caption", ""),
            version=e.text("version", ""),
            platform=e.flag("platform"),
        )


type _Decoder[V] = Callable[[str, object, str], V]


class _Fields(JsonObject):
    """One IR JSON object: the shared typed reads, plus the unknown-field policy."""

    __slots__ = ("_ignored",)
    null_flag_is_false = False  # the exporter writes every flag, so a null one is damage

    def __init__(self, raw: object, path: str, allowed: set[str], ignored: list[str] | None) -> None:
        """Wrap one object; fields outside allowed are an error, or recorded in ignored when it is given."""
        super().__init__(raw, path)
        self._ignored = ignored
        unknown = sorted(set(self._data) - allowed)
        if unknown and ignored is None:
            raise ValueError(f"{path}: unknown fields {unknown}")
        if ignored is not None:
            ignored.extend(f"{path}.{key}" for key in unknown)

    def _child(self, key: str) -> "_Fields | None":
        raw: object = self._data.get(key)
        if not raw:
            return None  # absent, or an empty table
        path = f"{self.path}.{key}"
        if not isinstance(raw, dict):
            return _Fields(raw, path, set(), self._ignored)  # raises: expected an object
        table = cast(dict[str, object], raw)
        return _Fields(table, path, set(table), self._ignored)

    def value(self, key: str) -> object:
        """Return a field's raw value, or None when absent."""
        return self._data.get(key)

    def list(self, key: str) -> list[object]:
        """Return a list field; absent is empty."""
        value = self._data.get(key)
        if value is None:
            return []
        items = as_list(value)
        if items is None:
            raise self._fail(key, "a list", value)
        return items

    def triples(self, key: str) -> tuple[tuple[str, str, str], ...]:
        """Return a list of [name, source, sha256] string triples; absent is empty."""
        out: list[tuple[str, str, str]] = []
        for item in self.list(key):
            parts = as_list(item)
            if parts is None or len(parts) != 3 or not all(isinstance(v, str) for v in parts):
                raise self._fail(key, "a list of [name, source, sha256] triples", self._data[key])
            name, source, sha = cast(list[str], parts)
            out.append((name, source, sha))
        return tuple(out)
