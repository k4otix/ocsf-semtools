"""The schema IR: frozen dataclasses describing one compiled OCSF version."""

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import ClassVar, Literal, Self, cast

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


# --- reading JSON into the IR ----------------------------------------------------------------
# Shared by the compiler-output loader and the IR JSON reader in export; not part of the public API.


def frozen_sorted[K: int | str, V](items: Mapping[K, V]) -> Mapping[K, V]:
    """Freeze a mapping in key order, so iteration never depends on input order."""
    return MappingProxyType(dict(sorted(items.items(), key=lambda kv: kv[0])))


def json_kind(value: object) -> str:
    """Return the JSON-facing name of a value's type, for error messages: null, str, list..."""
    return "null" if value is None else type(value).__name__


def as_list(value: object) -> list[object] | None:
    """Return the value as a list of objects, or None if it is not a list."""
    return cast(list[object], value) if isinstance(value, list) else None


def parse_requirement(value: str | None, path: str) -> Requirement:
    """Return the requirement a string names; absent is optional, anything unknown a ValueError at path."""
    try:
        return Requirement(value) if value is not None else Requirement.OPTIONAL
    except ValueError:
        raise ValueError(f"{path}.requirement: unknown value {value!r}") from None


def constraint_kind(name: str, path: str) -> ConstraintKind:
    """Return the constraint kind a string names; anything unknown is a ValueError at path."""
    kind = CONSTRAINT_KINDS.get(name)
    if kind is None:
        raise ValueError(f"{path}: unknown constraint kind {name!r}")
    return kind


def enum_key(key: str, root: Root | None, path: str) -> EnumValue:
    """Return an enum member's value from its JSON key: an integer unless the enum is on a string type."""
    if root is Root.STRING:
        return key
    try:
        return int(key)
    except ValueError:
        raise ValueError(f"{path}: enum key is not an integer") from None


class JsonObject:
    """One JSON object read into the IR: typed reads whose errors name the key path.

    Null reads as absent. ``_get`` is the one read, so a subclass can record what it read, and
    ``_child`` returns a nested object of the subclass's own kind, for ``entries``.
    """

    __slots__ = ("_data", "path")
    null_flag_is_false: ClassVar[bool] = True  # a null flag reads as false; the IR reader refuses it

    def __init__(self, data: object, path: str) -> None:
        """Wrap data, which must be a JSON object, read at path."""
        if not isinstance(data, dict):
            raise ValueError(f"{path}: expected an object, got {json_kind(data)}")
        self._data = cast(dict[str, object], data)
        self.path = path

    def _get(self, key: str) -> object:
        return self._data.get(key)

    def _child(self, key: str) -> Self | None:
        raise NotImplementedError

    def _fail(self, key: str, expected: str, value: object) -> ValueError:
        return ValueError(f"{self.path}.{key}: expected {expected}, got {json_kind(value)}")

    def names(self) -> Iterator[str]:
        """Return the object's keys, in input order."""
        return iter(self._data)

    def text(self, key: str, default: str | None = None) -> str:
        """Return a string; ``default`` stands in for an absent key (a null is still an error)."""
        value = self._get(key)
        if value is None and default is not None and key not in self._data:
            return default
        if not isinstance(value, str):
            raise self._fail(key, "a string", value)
        return value

    def opt_text(self, key: str) -> str | None:
        """Return a string, or None when absent."""
        value = self._get(key)
        if value is not None and not isinstance(value, str):
            raise self._fail(key, "a string", value)
        return value

    def integer(self, key: str) -> int:
        """Return an integer; a boolean is not one."""
        value = self._get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            raise self._fail(key, "an integer", value)
        return value

    def opt_integer(self, key: str) -> int | None:
        """Return an integer, or None when absent."""
        return None if self._get(key) is None else self.integer(key)

    def flag(self, key: str) -> bool:
        """Return a boolean; absent is false."""
        value = self._get(key)
        if value is None and (key not in self._data or self.null_flag_is_false):
            return False
        if not isinstance(value, bool):
            raise self._fail(key, "a boolean", value)
        return value

    def texts(self, key: str) -> tuple[str, ...]:
        """Return a list of strings; absent is empty."""
        value = self._get(key)
        if value is None:
            return ()
        items = as_list(value)
        if items is None or not all(isinstance(v, str) for v in items):
            raise self._fail(key, "a list of strings", value)
        return tuple(cast(list[str], items))

    def scalar(self, key: str) -> ScalarValue | None:
        """Return a JSON scalar, or None when absent."""
        value = self._get(key)
        if value is not None and not isinstance(value, bool | int | float | str):
            raise self._fail(key, "a scalar", value)
        return value

    def scalars(self, key: str) -> tuple[ScalarValue, ...] | None:
        """Return a list of JSON scalars, or None when absent."""
        value = self._get(key)
        if value is None:
            return None
        items = as_list(value)
        if items is None or not all(isinstance(v, bool | int | float | str) for v in items):
            raise self._fail(key, "a list of scalars", value)
        return tuple(cast(list[ScalarValue], items))

    def range(self, key: str) -> tuple[int | float, int | float] | None:
        """Return a [min, max] pair of numbers, or None when absent."""
        value = self._get(key)
        if value is None:
            return None
        items = as_list(value) or []
        if len(items) != 2 or not all(isinstance(v, int | float) and not isinstance(v, bool) for v in items):
            raise self._fail(key, "a [min, max] pair", value)
        low, high = cast(list[int | float], items)
        return (low, high)

    def entries[V](self, key: str, read: Callable[[Self, str], V]) -> Mapping[str, V]:
        """Return a nested object's entries, each read by ``read(nested, name)``, frozen in key order."""
        child = self._child(key)
        return frozen_sorted({} if child is None else {name: read(child, name) for name in list(child.names())})
