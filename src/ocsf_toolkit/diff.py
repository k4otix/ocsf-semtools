"""Schema diff: every difference between two IRs, keyed by IR path, with classes matched by uid."""

import dataclasses
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Literal, cast, final

from ocsf_toolkit.ir import ItemDef, SchemaIR

type IRPath = tuple[str | int, ...]
type ChangeKind = Literal["added", "removed", "changed", "renamed"]


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Change:
    """One difference at an IR path; an added or removed subtree is one change."""

    kind: ChangeKind
    path: IRPath  # ("classes", "user_access", "attributes", "privileges", "requirement")
    before: object  # None when added; for a rename, the old key
    after: object  # None when removed; for a rename, the new key


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class SchemaDiff:
    """What changed from OCSF version a to b; no changes means the IRs are equal."""

    a: str
    b: str
    changes: tuple[Change, ...]

    def to_dict(self) -> dict[str, Any]:
        """A JSON-serializable form: IR values rendered as plain JSON."""
        return {
            "a": self.a,
            "b": self.b,
            "changes": [
                {
                    "kind": c.kind,
                    "path": list(c.path),
                    "before": plain(c.before),
                    "after": plain(c.after),
                }
                for c in self.changes
            ],
        }


def diff(a: SchemaIR, b: SchemaIR) -> SchemaDiff:
    """Compare two IRs field by field; a class key that changes at a stable uid is a rename."""
    renames = _renames(a.classes, b.classes)
    changes = [
        Change(kind="renamed", path=("classes", old), before=old, after=new)
        for old, new in renames.items()
    ]
    matched = {renames.get(key, key): item for key, item in a.classes.items()}
    a = dataclasses.replace(a, classes=MappingProxyType(matched))
    changes += _compare(a, b, ())
    changes.sort(key=lambda c: tuple(str(p) for p in c.path))
    return SchemaDiff(a=a.version, b=b.version, changes=tuple(changes))


def plain(value: object) -> object:
    """An IR value as JSON data: dataclasses as objects, sets sorted, enums by value."""
    match value:
        case Enum():
            return value.value
        case None | bool() | int() | float() | str():
            return value
        case Mapping():
            items = cast(Mapping[object, object], value).items()
            return {str(k): plain(v) for k, v in sorted(items, key=lambda kv: str(kv[0]))}
        case frozenset() | set():
            return sorted((plain(v) for v in cast(set[object], value)), key=str)
        case tuple() | list():
            return [plain(v) for v in cast(tuple[object, ...], value)]
        case _ if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return {f.name: plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
        case _:
            raise TypeError(f"not an IR value: {type(value).__name__}")


def _renames(a: Mapping[str, ItemDef], b: Mapping[str, ItemDef]) -> dict[str, str]:
    # Only a uid that names exactly one class on each side, whose old key is gone from b and
    # whose new key is new in b, is a rename; anything else stays added and removed.
    def by_uid(classes: Mapping[str, ItemDef]) -> dict[int, str]:
        seen: dict[int, list[str]] = {}
        for key, item in classes.items():
            if item.uid is not None:
                seen.setdefault(item.uid, []).append(key)
        return {uid: keys[0] for uid, keys in seen.items() if len(keys) == 1}

    old, new = by_uid(a), by_uid(b)
    return {
        old[uid]: new[uid]
        for uid in old.keys() & new.keys()
        if old[uid] != new[uid] and old[uid] not in b and new[uid] not in a
    }


def _compare(a: object, b: object, path: IRPath) -> Iterator[Change]:
    if _is_record(a) and type(a) is type(b):
        for f in dataclasses.fields(cast(Any, a)):
            yield from _compare(getattr(a, f.name), getattr(b, f.name), (*path, f.name))
    elif (x := _mapping(a)) is not None and (y := _mapping(b)) is not None:
        for key in x.keys() - y.keys():
            yield Change(kind="removed", path=(*path, key), before=x[key], after=None)
        for key in y.keys() - x.keys():
            yield Change(kind="added", path=(*path, key), before=None, after=y[key])
        for key in x.keys() & y.keys():
            yield from _compare(x[key], y[key], (*path, key))
    elif a != b:
        yield Change(kind="changed", path=path, before=a, after=b)


def _mapping(value: object) -> Mapping[str | int, object] | None:
    return cast(Mapping[str | int, object], value) if isinstance(value, Mapping) else None


def _is_record(value: object) -> bool:
    # Not dataclasses.is_dataclass itself: its TypeGuard would narrow the values compared.
    return dataclasses.is_dataclass(value) and not isinstance(value, type)
