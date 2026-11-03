"""Resolve a SchemaIR under an active profile set into the one schema view every component reads."""

import functools
import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from importlib import resources
from types import MappingProxyType
from typing import final

from ocsf_toolkit.export import digest
from ocsf_toolkit.export import load_ir as load_ir  # re-exported: schema.load_ir is public
from ocsf_toolkit.ir import (
    Attribute,
    Category,
    Constraint,
    EnumMember,
    EnumValue,
    ItemDef,
    Root,
    SchemaIR,
    TypeDef,
)

_VERSION = re.compile(r"\d+\.\d+\.\d+")


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ClassIdentity:
    """The constants a class fixes on every event of that class (R4a tier 1)."""

    class_uid: int
    class_name: str
    category_uid: int
    category_name: str | None  # None for base_event, whose category "other" is not defined


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class SiblingSpec:
    label: str  # the label attribute the id attribute names as its sibling
    is_array: bool  # list-valued: labels map element-wise
    enum: Mapping[EnumValue, EnumMember] | None  # None for *.type_uid: see class_by_type_uid()


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ResolvedAttribute(Attribute):
    gated_by: frozenset[str]  # the active profiles that admitted it; empty for base attributes
    root: Root | None  # resolved primitive root; None for object_t
    owner: str  # key of the item it was resolved on


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ResolvedItem:
    """A class or object holding only the attributes present under the active set."""

    key: str
    name: str
    caption: str
    is_class: bool
    identity: ClassIdentity | None  # classes only
    attributes: Mapping[str, ResolvedAttribute]
    constraints: tuple[Constraint, ...]  # members absent under the active set removed
    _siblings: Mapping[str, SiblingSpec] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        siblings = {
            name: SiblingSpec(label=a.sibling, is_array=a.is_array, enum=a.enum)
            for name, a in self.attributes.items()
            if a.sibling is not None and a.sibling in self.attributes
        }
        object.__setattr__(self, "_siblings", MappingProxyType(siblings))

    def siblings(self) -> Mapping[str, SiblingSpec]:
        """Id attribute -> its label; only pairs whose two attributes are both present."""
        return self._siblings


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class ResolvedSchema:
    """One OCSF version under an active profile and extension set; equal and hashable by those."""

    version: str
    profiles: frozenset[str]
    extensions: frozenset[str]
    digest: str  # sha256 of the canonical IR JSON
    ir: SchemaIR = field(repr=False, compare=False)  # unfiltered, for tooling
    classes: Mapping[str, ResolvedItem] = field(repr=False, compare=False)
    objects: Mapping[str, ResolvedItem] = field(repr=False, compare=False)
    categories: Mapping[str, Category] = field(repr=False, compare=False)
    types: Mapping[str, TypeDef] = field(repr=False, compare=False)
    _by_uid: Mapping[int, ResolvedItem] = field(init=False, repr=False, compare=False)
    _by_type_uid: Mapping[int, ResolvedItem] = field(init=False, repr=False, compare=False)
    _markers: Mapping[tuple[str, str], int] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        by_uid: dict[int, ResolvedItem] = {}
        by_type_uid: dict[int, ResolvedItem] = {}
        for item in self.classes.values():
            assert item.identity is not None
            by_uid[item.identity.class_uid] = item
            type_uid = item.attributes.get("type_uid")
            for value in (type_uid.enum or {}) if type_uid else ():
                by_type_uid[int(value)] = item
        object.__setattr__(self, "_by_uid", MappingProxyType(by_uid))
        object.__setattr__(self, "_by_type_uid", MappingProxyType(by_type_uid))
        object.__setattr__(self, "_markers", MappingProxyType(_markers(self)))

    def class_by_uid(self, uid: int) -> ResolvedItem:
        try:
            return self._by_uid[uid]
        except KeyError:
            raise KeyError(f"no class with uid {uid} in {self._label()}") from None

    def class_by_type_uid(self, type_uid: int) -> tuple[ResolvedItem, int]:
        """(class, activity_id) for a type_uid declared in some class's type_uid enum."""
        try:
            item = self._by_type_uid[type_uid]
        except KeyError:
            raise KeyError(f"no class declares type_uid {type_uid} in {self._label()}") from None
        assert item.identity is not None
        return item, type_uid - item.identity.class_uid * 100

    def observable_markers(self) -> Mapping[tuple[str, str], int]:
        """(owner key, attribute) -> observable type_id, for observable definition kinds 1-5."""
        return self._markers

    def _label(self) -> str:
        return f"OCSF {self.version} with extensions {sorted(self.extensions)}"


def shipped_versions() -> tuple[str, ...]:
    """OCSF versions whose IR ships in this package, oldest first."""
    names = (p.name for p in resources.files("ocsf_toolkit").joinpath("data").iterdir())
    found = [n[3:-5] for n in names if n.startswith("ir-") and n.endswith(".json")]
    return tuple(sorted(found, key=lambda v: tuple(int(p) for p in v.split("."))))


def load(
    version: str, *, profiles: Iterable[str] = (), extensions: Iterable[str] = ()
) -> ResolvedSchema:
    """Resolve a shipped OCSF version; unknown profiles or extensions raise KeyError."""
    return _load(version, _names(profiles, "profiles"), _names(extensions, "extensions"))


def resolve(
    ir: SchemaIR, *, profiles: Iterable[str] = (), extensions: Iterable[str] = ()
) -> ResolvedSchema:
    """Resolve any IR, such as one built with a custom extension."""
    return _resolve(ir, digest(ir), _names(profiles, "profiles"), _names(extensions, "extensions"))


def _names(names: Iterable[str], what: str) -> frozenset[str]:
    if isinstance(names, str):
        raise TypeError(f"{what} must be an iterable of names, not the string {names!r}")
    return frozenset(names)


# A resolved 1.9.0 schema holds up to 7,904 attributes, so the number kept alive is bounded.
@functools.lru_cache(maxsize=32)
def _load(version: str, profiles: frozenset[str], extensions: frozenset[str]) -> ResolvedSchema:
    ir, ir_digest = _shipped(version)
    return _resolve(ir, ir_digest, profiles, extensions)


@functools.cache
def _shipped(version: str) -> tuple[SchemaIR, str]:
    shipped = shipped_versions()
    if not _VERSION.fullmatch(version) or version not in shipped:
        raise ValueError(f"no shipped IR for OCSF version {version!r}; shipped: {list(shipped)}")
    data = resources.files("ocsf_toolkit").joinpath("data", f"ir-{version}.json").read_bytes()
    ir = load_ir(data)
    if ir.version != version:
        raise ValueError(f"data/ir-{version}.json holds OCSF {ir.version!r}")
    return ir, hashlib.sha256(data).hexdigest()  # the file is canonical, so this is digest(ir)


def _resolve(
    ir: SchemaIR, ir_digest: str, profiles: frozenset[str], extensions: frozenset[str]
) -> ResolvedSchema:
    if unknown := extensions - ir.extensions.keys():
        raise KeyError(
            f"unknown extensions {sorted(unknown)} for OCSF {ir.version};"
            f" known: {sorted(ir.extensions)}"
        )
    usable = {key for key, p in ir.profiles.items() if p.extension in (None, *extensions)}
    if unknown := profiles - usable:
        raise KeyError(
            f"unknown profiles {sorted(unknown)} for OCSF {ir.version} with extensions"
            f" {sorted(extensions)}; known: {sorted(usable)}"
        )

    def active(extension: str | None) -> bool:
        return extension is None or extension in extensions

    def items(table: Mapping[str, ItemDef]) -> Mapping[str, ResolvedItem]:
        resolved = {
            key: _item(ir, item, profiles, extensions)
            for key, item in table.items()
            if active(item.extension)
        }
        return MappingProxyType(resolved)

    return ResolvedSchema(
        version=ir.version,
        profiles=profiles,
        extensions=extensions,
        digest=ir_digest,
        ir=ir,
        classes=items(ir.classes),
        objects=items(ir.objects),
        categories=MappingProxyType(
            {k: c for k, c in ir.categories.items() if active(c.extension)}
        ),
        types=MappingProxyType({k: t for k, t in ir.types.items() if active(t.extension)}),
    )


def _item(
    ir: SchemaIR, item: ItemDef, profiles: frozenset[str], extensions: frozenset[str]
) -> ResolvedItem:
    attributes = {
        name: _attribute(ir, item.key, a, profiles)
        for name, a in item.attributes.items()
        if (not a.profiles or a.profiles & profiles)
        and (a.extension is None or a.extension in extensions)
    }
    # A constraint member names an attribute or a dotted path into one; members whose attribute
    # is gated out are dropped, so no rule is ever emitted for an absent attribute (acc. #12a).
    # Members naming no attribute at all are upstream data and kept as declared.
    absent = item.attributes.keys() - attributes.keys()
    constraints: list[Constraint] = []
    for c in item.constraints:
        members = tuple(m for m in c.attributes if m.split(".", 1)[0] not in absent)
        if members:
            constraints.append(Constraint(kind=c.kind, attributes=members))
    return ResolvedItem(
        key=item.key,
        name=item.name,
        caption=item.caption,
        is_class=item.is_class,
        identity=_identity(item) if item.is_class else None,
        attributes=MappingProxyType(attributes),
        constraints=tuple(constraints),
    )


def _identity(item: ItemDef) -> ClassIdentity:
    if item.uid is None or item.category_uid is None:
        raise ValueError(f"class {item.key!r} has no uid or category_uid")
    return ClassIdentity(
        class_uid=item.uid,
        class_name=item.caption,
        category_uid=item.category_uid,
        category_name=item.category_name,
    )


def _attribute(
    ir: SchemaIR, owner: str, a: Attribute, profiles: frozenset[str]
) -> ResolvedAttribute:
    root: Root | None = None
    if a.object_type is None:
        typedef = ir.types.get(a.type)
        if typedef is None:
            raise ValueError(f"{owner}.{a.name}: unknown type {a.type!r}")
        root = typedef.root
    return ResolvedAttribute(
        name=a.name,
        caption=a.caption,
        type=a.type,
        object_type=a.object_type,
        is_array=a.is_array,
        requirement=a.requirement,
        profiles=a.profiles,
        sibling=a.sibling,
        enum=a.enum,
        observable=a.observable,
        deprecated=a.deprecated,
        extension=a.extension,
        group=a.group,
        suppress_checks=a.suppress_checks,
        default=a.default,
        gated_by=a.profiles & profiles,
        root=root,
        owner=owner,
    )


def _markers(schema: ResolvedSchema) -> dict[tuple[str, str], int]:
    # Precedence: attribute-local > dictionary attribute > type > object. The dictionary keys an
    # extension attribute as "<ext>/<name>", so that spelling is tried first.
    ir = schema.ir
    markers: dict[tuple[str, str], int] = {}
    for item in (*schema.classes.values(), *schema.objects.values()):
        for name, a in item.attributes.items():
            scoped = ir.dictionary.get(f"{a.extension}/{name}") if a.extension else None
            entry = scoped or ir.dictionary.get(name)
            typedef = ir.types.get(a.type)
            obj = ir.objects.get(a.object_type) if a.object_type else None
            for candidate in (
                a.observable,
                entry.observable if entry else None,
                typedef.observable if typedef else None,
                obj.observable if obj else None,
            ):
                if candidate is not None:
                    markers[(item.key, name)] = candidate
                    break
    return markers
