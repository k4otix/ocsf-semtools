"""Observables visitor: which visited values the schema marks as observables, and their entries."""

import copy
import functools
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, cast, final

from ocsf_semtools.ir import EnumMember, EnumValue
from ocsf_semtools.schema import SCHEMAS_KEPT, ResolvedItem, ResolvedSchema
from ocsf_semtools.walk import Finding, Kind, Path, Visit, canonical, dotted, is_int, is_int64

OBSERVABLES = "observables"  # the class attribute holding an event's observable entries
_OBSERVABLE = "observable"  # the object type of each entry


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Observable:
    """One observable found in an event: where (indexed path), what kind, and its value."""

    name: str  # indexed notation: "evidences[0].process.file.name"
    type_id: int
    type: str | None  # the type_id's caption; None for an id the schema doesn't define
    value: str | None  # None when the marked attribute is an object

    def to_dict(self) -> dict[str, Any]:
        """Return the entry as an OCSF observable object."""
        entry: dict[str, Any] = {"name": self.name, "type_id": self.type_id}
        if self.type is not None:
            entry["type"] = self.type
        if self.value is not None:
            entry["value"] = self.value
        return entry


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Markers:
    """What makes a visited attribute an observable, for one schema and root class; built once per pair.

    Upstream defines six kinds of observable marker. Kinds 1-5 (on a dictionary attribute, a type, an
    object, or an attribute local to one item) resolve into one (owner key, attribute) table; kind 6
    is a path map a class declares, matched only at the root class.
    """

    by_attr: Mapping[tuple[str, str], int]  # (owner key, attribute) -> type_id: kinds 1-5
    by_path: Mapping[str, int]  # canonical path -> type_id: kind 6, the root class's own map
    tails: frozenset[str]  # the last segment of each by_path key, to skip canonical() for the rest
    captions: Mapping[int, str]  # type_id -> caption, for the ids the schema defines


def markers(schema: ResolvedSchema, root: ResolvedItem) -> Markers:
    """Return the observable markers for events of root under schema."""
    built = _markers(schema)
    found = built.get(root.key)
    if found is None:
        item = schema.ir.classes.get(root.key)
        paths: Mapping[str, int] = item.class_observables if item is not None else {}
        found = built[root.key] = Markers(
            by_attr=schema.observable_markers,
            by_path=paths,
            tails=frozenset(p.rpartition(".")[2] for p in paths),
            captions=_captions(schema),
        )
    return found


def observe(visit: Visit, marks: Markers) -> list[Observable]:
    """Return the observables one attribute visit yields; none for a close or an undeclared attribute."""
    attr = visit.attr
    if attr is None or visit.value is None:
        return []
    type_id = marks.by_attr.get((visit.owner.key, attr))
    if type_id is None and attr in marks.tails:  # kind 6: the root class's own path map
        type_id = marks.by_path.get(canonical(visit.path))
    if type_id is None:
        return []
    ra = visit.owner.attributes.get(attr)
    if ra is None:
        return []
    caption = marks.captions.get(type_id)
    found: list[Observable] = []
    for path, value in _elements(visit.path, visit.value, ra.is_array):
        if ra.object_type is not None:
            if isinstance(value, dict):
                found.append(Observable(name=dotted(path), type_id=type_id, type=caption, value=None))
        elif (text := stringify(value)) is not None:
            found.append(Observable(name=dotted(path), type_id=type_id, type=caption, value=text))
    return found


def stringify(value: object) -> str | None:
    """Return an observable value as upstream spells it; None for anything that isn't a scalar."""
    match value:
        case bool():
            return "true" if value else "false"
        case int():
            return str(value) if is_int64(value) else None  # past 64 bits it is a type_mismatch
        case str():
            return value
        case float():
            return repr(value)  # nan, inf and the shortest round-tripping digits
        case _:
            return None


def unfounded(event: Mapping[str, Any], found: Iterable[Observable]) -> list[Finding]:
    """Return an observable_mismatch for each caller entry that no schema-derived observable backs."""
    entries = event.get(OBSERVABLES)
    if not isinstance(entries, list):
        return []
    by_name: dict[str, list[Observable]] = {}
    for o in found:
        by_name.setdefault(canonical(o.name), []).append(o)
    findings: list[Finding] = []
    for i, entry in enumerate(cast(list[object], entries)):
        if not isinstance(entry, dict):
            continue  # conform reports the shape
        e = cast(dict[str, Any], entry)
        name, type_id, value = e.get("name"), e.get("type_id"), e.get("value")
        if not isinstance(name, str) or not is_int(type_id):
            continue  # conform reports the types
        problem = _problem(by_name.get(canonical(name), []), type_id, value)
        if problem is not None:
            findings.append(
                Finding(
                    kind=Kind.OBSERVABLE_MISMATCH,
                    path=(OBSERVABLES, i),
                    owner=_OBSERVABLE,
                    message=f"observables[{i}] {name!r}: {problem}",
                    expected=None,
                    actual={"name": name, "type_id": type_id, "value": value},
                )
            )
    return findings


def merge(event: Mapping[str, Any], found: Iterable[Observable]) -> dict[str, Any]:
    """Return a copy of the event with found observables appended to the caller's, which stay as given.

    An entry already present, matched on (canonical name, type_id, value), is not added twice. An
    ``observables`` attribute that is not an array is a ValueError.
    """
    out = copy.deepcopy(dict(event))
    entries = out.setdefault(OBSERVABLES, [])
    if not isinstance(entries, list):
        raise ValueError(f"{OBSERVABLES} is {type(entries).__name__}, not an array")
    existing = cast(list[object], entries)
    seen = {_key(cast(dict[str, Any], e)) for e in existing if isinstance(e, dict)}
    for o in found:
        if (key := _key(o.to_dict())) not in seen:
            seen.add(key)
            existing.append(o.to_dict())
    return out


def _problem(candidates: list[Observable], type_id: int, value: object) -> str | None:
    if not candidates:
        return "names no observable attribute present in this event"
    typed = [o for o in candidates if o.type_id == type_id]
    if not typed:
        expected = sorted({o.type_id for o in candidates})
        return f"the schema marks this attribute as type_id {expected}"
    if value is None or any(o.value == value for o in typed):
        return None
    return "value does not occur at that name"


def _key(entry: Mapping[str, Any]) -> tuple[str, object, object]:
    name = entry.get("name")
    return (
        canonical(name) if isinstance(name, str) else "",
        entry.get("type_id"),
        entry.get("value"),
    )


def _elements(path: Path, value: object, is_array: bool) -> Iterator[tuple[Path, object]]:
    if not is_array:
        yield path, value
    elif isinstance(value, list):
        for i, element in enumerate(cast(list[object], value)):
            if element is not None:
                yield (*path, i), element


@functools.lru_cache(maxsize=SCHEMAS_KEPT)
def _markers(schema: ResolvedSchema) -> dict[str, Markers]:
    """Return the schema's markers by root class key, filled as classes are met."""
    return {}


def _captions(schema: ResolvedSchema) -> Mapping[int, str]:
    item = schema.objects.get(_OBSERVABLE)
    ra = item.attributes.get("type_id") if item else None
    enum: Mapping[EnumValue, EnumMember] = ra.enum if ra and ra.enum else {}
    return {int(value): member.caption for value, member in enum.items()}
