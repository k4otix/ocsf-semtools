"""Conformance visitor: report how each visited value deviates from its attribute's declaration."""

import math
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, TypeGuard, cast, final

from ocsf_toolkit.ir import ItemDef, Requirement, Root
from ocsf_toolkit.schema import ResolvedAttribute, ResolvedItem, ResolvedSchema
from ocsf_toolkit.walk import OPEN_OBJECT, Finding, Kind, Path, RepairAction, Visit, dotted

UNMAPPED = "unmapped"  # the class attribute that holds what the schema has no place for
# long_t is 64-bit: 19 digits covers it, and caps int() well below its 4300-digit limit.
_INT = re.compile(r"[+-]?[0-9]{1,19}")
_BOOLS = {"true": True, "false": False}
_MISSING = {
    Requirement.REQUIRED: Kind.MISSING_REQUIRED,
    Requirement.RECOMMENDED: Kind.MISSING_RECOMMENDED,
}
# Derivation owns these at the class root and reports them as identity_conflict.
_IDENTITY = frozenset({"class_uid", "category_uid", "type_uid"})


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Checks:
    """What the conformance visitor may change while it checks; it reports in every mode."""

    schema: ResolvedSchema
    root: dict[str, Any]  # the event, whose unmapped attribute receives relocations
    root_item: ResolvedItem  # the class the event is checked against
    coerce: bool  # "4"->4, "true"->True, integral float->int
    datetimes: bool  # tz-aware datetime -> epoch ms on timestamp_t (D#17)
    relocate: bool  # unknown attributes move into the root's unmapped
    migrate: bool  # a deprecated attribute's value moves to its one superseding attribute


def check(visit: Visit, checks: Checks) -> list[Finding]:
    """Check one attribute visit, or at a node's close its absent attributes and constraints."""
    if visit.attr is None:
        return [*_absent(visit), *_constraints(visit), *_item_deprecated(visit, checks.schema)]
    ra = visit.owner.attributes.get(visit.attr)
    if ra is None:
        return _unknown(visit, visit.attr, checks)
    if visit.value is None:
        return []  # JSON null reads as absent
    findings = list(_deprecated(visit, ra, checks))
    if visit.attr not in visit.node:
        return findings  # migrated to its successor, which is not this attribute's concern
    return findings + list(_value(visit, ra, checks))


# --- attribute visits ----------------------------------------------------------------------


def _unknown(visit: Visit, attr: str, checks: Checks) -> list[Finding]:
    declared = _item_def(checks.schema, visit.owner).attributes.get(attr)
    if (
        declared is not None
        and declared.profiles
        and (declared.extension is None or declared.extension in checks.schema.extensions)
    ):
        return [
            Finding(
                kind=Kind.PROFILE_MISMATCH,
                path=visit.path,
                owner=visit.owner.key,
                message=f"{dotted(visit.path)} belongs to profiles {sorted(declared.profiles)},"
                f" none of which is active",
                tier=declared.requirement,
                expected=sorted(declared.profiles),
                actual=sorted(checks.schema.profiles),
            )
        ]
    key = dotted(visit.path)
    unmapped = checks.root.get(UNMAPPED, {})
    can_move = (
        checks.relocate
        and UNMAPPED in checks.root_item.attributes
        and isinstance(unmapped, dict)
        and key not in unmapped
    )
    if not can_move:
        return [
            Finding(
                kind=Kind.UNKNOWN_ATTRIBUTE,
                path=visit.path,
                owner=visit.owner.key,
                message=f"{key} is not an attribute of {visit.owner.key}",
                actual=visit.value,
            )
        ]
    moved = cast(dict[str, Any], unmapped)
    moved[key] = visit.node.pop(attr)
    checks.root[UNMAPPED] = moved
    return [
        Finding(
            kind=Kind.RELOCATED,
            path=visit.path,
            owner=visit.owner.key,
            message=f"{key} is not an attribute of {visit.owner.key}; moved into {UNMAPPED}",
            actual=visit.value,
            repair=RepairAction(action="relocate", before=visit.path, after=(UNMAPPED, key)),
        )
    ]


def _deprecated(visit: Visit, ra: ResolvedAttribute, checks: Checks) -> Iterator[Finding]:
    dep = ra.deprecated
    if dep is None:
        return
    targets = dep.superseded_by
    target = targets[0] if len(targets) == 1 else None
    successor = visit.owner.attributes.get(target) if target and "." not in target else None
    movable = (
        checks.migrate
        and successor is not None
        and (successor.type, successor.object_type, successor.is_array)
        == (ra.type, ra.object_type, ra.is_array)  # e.g. comment (string) -> notes (note[]) isn't
        and visit.node.get(successor.name) is None
    )
    repair = None
    if movable and target is not None:
        visit.node[target] = visit.node.pop(ra.name)
        repair = RepairAction(action="migrate", before=visit.path, after=(*visit.path[:-1], target))
    yield Finding(
        kind=Kind.DEPRECATED,
        path=visit.path,
        owner=visit.owner.key,
        message=f"{dotted(visit.path)} is deprecated since {dep.since}: {dep.message}",
        tier=ra.requirement,
        profiles=ra.gated_by,
        expected=targets,
        repair=repair,
    )


def _value(visit: Visit, ra: ResolvedAttribute, checks: Checks) -> Iterator[Finding]:
    value = visit.value
    if not ra.is_array:
        yield from _element(visit, ra, checks, value, visit.path, None)
        return
    if not isinstance(value, list):
        yield _mismatch(visit, ra, visit.path, value, f"an array of {ra.type}")
        return
    for i, element in enumerate(cast(list[object], value)):
        yield from _element(visit, ra, checks, element, (*visit.path, i), i)


def _element(
    visit: Visit,
    ra: ResolvedAttribute,
    checks: Checks,
    value: object,
    path: Path,
    index: int | None,
) -> Iterator[Finding]:
    if ra.object_type is not None:
        if not isinstance(value, dict):
            what = "a JSON object" if ra.object_type == OPEN_OBJECT else f"a {ra.object_type}"
            yield _mismatch(visit, ra, path, value, what)
        return
    assert ra.root is not None
    if not _conforms(value, ra.root):
        new = _coerced(value, ra, checks)
        if new is None:
            yield _mismatch(visit, ra, path, value, ra.type)
            return
        _replace(visit, index, new)
        yield Finding(
            kind=Kind.COERCED,
            path=path,
            owner=visit.owner.key,
            message=f"{dotted(path)} coerced from {value!r} to {new!r}",
            tier=ra.requirement,
            profiles=ra.gated_by,
            expected=ra.type,
            actual=value,
            repair=RepairAction(action="coerce", before=value, after=new),
        )
        value = new
    if ra.enum is None or (visit.owner.identity is not None and visit.path in _ROOT_IDENTITY):
        return
    member = ra.enum.get(cast(int | str, value))
    if member is None:
        yield Finding(
            kind=Kind.ENUM_OUT_OF_RANGE,
            path=path,
            owner=visit.owner.key,
            message=f"{dotted(path)} is {value!r}, not one of {sorted(map(str, ra.enum))}",
            tier=ra.requirement,
            profiles=ra.gated_by,
            expected=sorted(ra.enum, key=str),
            actual=value,
        )
    elif member.deprecated is not None:
        yield Finding(
            kind=Kind.DEPRECATED,
            path=path,
            owner=visit.owner.key,
            message=f"{dotted(path)} value {value!r} ({member.caption}) is deprecated"
            f" since {member.deprecated.since}: {member.deprecated.message}",
            tier=ra.requirement,
            profiles=ra.gated_by,
            expected=member.deprecated.superseded_by,
            actual=value,
        )


_ROOT_IDENTITY = frozenset((name,) for name in _IDENTITY)


def _conforms(value: object, root: Root) -> bool:
    match root:
        case Root.STRING:
            return isinstance(value, str)
        case Root.INTEGER | Root.LONG:
            return _is_int(value)
        case Root.FLOAT:
            return _is_int(value) or isinstance(value, float)  # a JSON 1 is a valid float
        case Root.BOOLEAN:
            return isinstance(value, bool)
        case Root.JSON:
            return True


def _coerced(value: object, ra: ResolvedAttribute, checks: Checks) -> object | None:
    """The coercion this policy allows for value, or None."""
    if isinstance(value, datetime):
        if checks.datetimes and ra.type == "timestamp_t" and value.utcoffset() is not None:
            return _epoch_ms(value)
        return None
    if not checks.coerce:
        return None
    if ra.root in (Root.INTEGER, Root.LONG):
        if isinstance(value, str) and _INT.fullmatch(value):
            return int(value)
        if isinstance(value, float) and math.isfinite(value) and value.is_integer():
            return int(value)
    if ra.root is Root.BOOLEAN and isinstance(value, str):
        return _BOOLS.get(value)
    return None


def _epoch_ms(value: datetime) -> int:
    offset = value.utcoffset()
    assert offset is not None
    naive = value.replace(tzinfo=None) - offset
    return (naive - datetime(1970, 1, 1)) // timedelta(milliseconds=1)


def _replace(visit: Visit, index: int | None, new: object) -> None:
    assert visit.attr is not None
    if index is None:
        visit.node[visit.attr] = new
    else:
        cast(list[object], visit.node[visit.attr])[index] = new


def _mismatch(visit: Visit, ra: ResolvedAttribute, path: Path, value: object, what: str) -> Finding:
    return Finding(
        kind=Kind.TYPE_MISMATCH,
        path=path,
        owner=visit.owner.key,
        message=f"{dotted(path)} is {type(value).__name__} {value!r}; expected {what}",
        tier=ra.requirement,
        profiles=ra.gated_by,
        expected=ra.type,
        actual=value,
    )


# --- node closes ---------------------------------------------------------------------------


def _absent(visit: Visit) -> Iterator[Finding]:
    owner, node = visit.owner, visit.node
    sources = {spec.label: id_attr for id_attr, spec in owner.siblings().items()}
    for name, ra in owner.attributes.items():
        kind = _MISSING.get(ra.requirement)
        if kind is None or ra.deprecated is not None or node.get(name) is not None:
            continue  # a deprecated attribute is never asked for
        # A derived value whose source is itself absent: the source's finding is the cause.
        source = "activity_id" if owner.is_class and name == "type_uid" else sources.get(name)
        if source is not None and node.get(source) is None:
            continue
        path = (*visit.path, name)
        yield Finding(
            kind=kind,
            path=path,
            owner=owner.key,
            message=f"{dotted(path)} is {ra.requirement}"
            + (f" under profiles {sorted(ra.gated_by)}" if ra.gated_by else "")
            + " and absent",
            tier=ra.requirement,
            profiles=ra.gated_by,
        )


def _constraints(visit: Visit) -> Iterator[Finding]:
    for c in visit.owner.constraints:
        present = [m for m in c.attributes if _lookup(visit.node, m) is not None]
        if (c.kind == "at_least_one" and present) or (c.kind == "just_one" and len(present) == 1):
            continue
        rule = "at least one" if c.kind == "at_least_one" else "exactly one"
        yield Finding(
            kind=Kind.CONSTRAINT_VIOLATED,
            path=visit.path,
            owner=visit.owner.key,
            message=f"{dotted(visit.path) or visit.owner.key} needs {rule} of"
            f" {list(c.attributes)}; present: {present}",
            expected=c.attributes,
            actual=tuple(present),
        )


def _item_deprecated(visit: Visit, schema: ResolvedSchema) -> Iterator[Finding]:
    dep = _item_def(schema, visit.owner).deprecated
    if dep is not None:
        yield Finding(
            kind=Kind.DEPRECATED,
            path=visit.path,
            owner=visit.owner.key,
            message=f"{visit.owner.key} is deprecated since {dep.since}: {dep.message}",
            expected=dep.superseded_by,
        )


def _lookup(node: dict[str, Any], dotted_name: str) -> object:
    value: object = node
    for part in dotted_name.split("."):
        if not isinstance(value, dict):
            return None
        value = cast(dict[str, object], value).get(part)
    return value


def _item_def(schema: ResolvedSchema, item: ResolvedItem) -> ItemDef:
    return (schema.ir.classes if item.is_class else schema.ir.objects)[item.key]


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)
