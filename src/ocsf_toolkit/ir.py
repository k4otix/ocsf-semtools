"""The schema IR: frozen dataclasses describing one compiled OCSF version."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Literal

type EnumValue = int | str
type ScalarValue = bool | int | float | str
type ConstraintKind = Literal["at_least_one", "just_one"]
# Each kind keyed by its own name: a lookup that narrows a str read from JSON to ConstraintKind.
CONSTRAINT_KINDS: Mapping[str, ConstraintKind] = MappingProxyType(
    {"at_least_one": "at_least_one", "just_one": "just_one"}
)


class Requirement(StrEnum):
    """How strongly a class or object asks for an attribute."""

    REQUIRED = "required"
    RECOMMENDED = "recommended"
    OPTIONAL = "optional"


class Root(StrEnum):
    """The six primitive types every named OCSF type resolves to."""

    STRING = "string_t"
    INTEGER = "integer_t"
    LONG = "long_t"
    FLOAT = "float_t"
    BOOLEAN = "boolean_t"
    JSON = "json_t"


@dataclass(frozen=True, slots=True, kw_only=True)
class Deprecation:
    """Upstream's deprecation notice for a class, object, attribute or enum member."""

    message: str
    since: str
    superseded_by: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class EnumMember:
    """One value of an attribute's enum."""

    value: EnumValue
    caption: str
    deprecated: Deprecation | None


@dataclass(frozen=True, slots=True, kw_only=True)
class TypeDef:
    """A data type of the dictionary, and the primitive it is rooted in."""

    name: str
    caption: str
    root: Root
    parent: str | None  # None for the six roots
    max_len: int | None
    regex: str | None
    range: tuple[int | float, int | float] | None
    values: tuple[ScalarValue, ...] | None
    observable: int | None
    extension: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class Attribute:
    """An attribute as its owner declares it, before any profile or extension is resolved."""

    name: str  # key within its owner; unscoped even when contributed by an extension
    caption: str
    type: str  # type key; "object_t" when object_type is set
    object_type: str | None  # scoped object key, e.g. "win/win_service"
    is_array: bool
    requirement: Requirement
    profiles: frozenset[str]  # empty means not profile-dependent (null, absent and [] collapse)
    sibling: str | None
    enum: Mapping[EnumValue, EnumMember] | None  # int keys on integer/long roots, str on string
    observable: int | None  # attribute-local marker
    deprecated: Deprecation | None
    extension: str | None
    group: str | None
    suppress_checks: frozenset[str]
    default: ScalarValue | None  # OCSF 1.0.0 only; data, never applied


@dataclass(frozen=True, slots=True, kw_only=True)
class Constraint:
    """An at_least_one or just_one rule over a group of an item's attributes."""

    kind: ConstraintKind
    attributes: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ItemDef:
    """A class or an object; the two are structurally identical."""

    key: str  # scoped lookup key, e.g. "win/reg_key"
    name: str  # unscoped
    caption: str
    is_class: bool
    extends: str | None
    extension: str | None
    deprecated: Deprecation | None
    attributes: Mapping[str, Attribute]
    profiles: frozenset[str]  # profiles affecting this item or anything reachable from it
    constraints: tuple[Constraint, ...]
    observable: int | None  # object-level marker; objects only
    uid: int | None  # class only, as are the next three fields
    category: str | None
    category_uid: int | None
    category_name: str | None  # None for base_event, whose category "other" is not defined
    associations: Mapping[str, tuple[str, ...]]  # data only
    class_observables: Mapping[str, int]  # class-relative path -> observable type id


@dataclass(frozen=True, slots=True, kw_only=True)
class Profile:
    """A profile: a named set of attributes classes can take on."""

    key: str  # "<extension>/<name>" for extension profiles
    name: str
    caption: str
    extension: str | None
    annotations: Mapping[str, str]


@dataclass(frozen=True, slots=True, kw_only=True)
class Extension:
    """An extension that contributes classes, objects, attributes or profiles."""

    name: str
    uid: int
    caption: str
    version: str
    platform: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class Category:
    """A category of classes."""

    name: str
    uid: int
    caption: str
    extension: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class Provenance:
    """Where an IR was compiled from, and with what."""

    schema_repo: str
    ref: str  # upstream tag or branch as spelled upstream; commit is the pin
    commit: str
    compiler_version: str
    compiler_output_sha256: str  # sha256 of the compiler output as canonical JSON
    extensions: tuple[tuple[str, str, str], ...]  # custom extensions: (name, source, sha256)


@dataclass(frozen=True, slots=True, kw_only=True)
class SchemaIR:
    """One OCSF version as the library's own intermediate representation."""

    version: str
    compile_version: int
    provenance: Provenance
    categories: Mapping[str, Category]
    types: Mapping[str, TypeDef]
    dictionary: Mapping[str, Attribute]  # scoped keys for extension attributes
    classes: Mapping[str, ItemDef]  # scoped keys for extension classes
    objects: Mapping[str, ItemDef]
    profiles: Mapping[str, Profile]
    extensions: Mapping[str, Extension]
