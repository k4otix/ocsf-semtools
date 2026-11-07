"""Conformance visitor: report how each visited value deviates from its attribute's declaration."""

import functools
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, cast, final

from ocsf_toolkit.ir import Deprecation, ItemDef, Requirement, Root
from ocsf_toolkit.schema import SCHEMAS_KEPT, ResolvedAttribute, ResolvedItem, ResolvedSchema
from ocsf_toolkit.walk import (
    OPEN_OBJECT,
    Finding,
    Kind,
    Path,
    RepairAction,
    Visit,
    attribute_finding,
    dotted,
    is_int,
    is_int64,
    shown,
)

UNMAPPED = "unmapped"  # the class attribute that holds what the schema has no place for
# 19 digits covers the 64-bit bound, and caps int() well below its 4300-digit limit.
_INT = re.compile(r"[+-]?[0-9]{1,19}")
_BOOLS = {"true": True, "false": False}
_MISSING = {
    Requirement.REQUIRED: Kind.MISSING_REQUIRED,
    Requirement.RECOMMENDED: Kind.MISSING_RECOMMENDED,
}
# Derivation owns these at the class root and reports them as identity_conflict, so their enums
# are not checked here.
_DERIVED_IDENTITY = frozenset({("class_uid",), ("category_uid",), ("type_uid",)})


@final
@dataclass(frozen=True, slots=True)
class _Plan:
    """What a node's close checks, from its item alone: worked out once per item and schema."""

    asked: tuple[tuple[str, ResolvedAttribute, Kind, str | None], ...]  # (name, decl, kind if absent, source)
    deprecated: Deprecation | None  # of the item itself


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Checks:
    """What the conformance visitor needs for one run.

    That is the event, the changes the policy allows, and the node plans that every run against the
    schema shares: held here so that no node's close has to hash the schema to find them.
    """

    schema: ResolvedSchema
    root: dict[str, Any]  # the event, whose unmapped attribute receives relocations
    root_item: ResolvedItem  # the class the event is checked against
    coerce: bool  # "4"->4, "true"->True, integral float->int
    datetimes: bool  # tz-aware datetime -> epoch ms on timestamp_t (D#17)
    relocate: bool  # unknown attributes move into the root's unmapped
    migrate: bool  # a deprecated attribute's value moves to its one superseding attribute
    plans: dict[tuple[bool, str], _Plan] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Share the node plans of every run against this schema."""
        object.__setattr__(self, "plans", _plans(self.schema))


def check(visit: Visit, checks: Checks) -> list[Finding]:
    """Check one attribute visit, or at a node's close its absent attributes and constraints."""
    if visit.attr is None:
        plan = _plan(checks, visit.owner)
        return [*_absent(visit, plan), *_constraints(visit), *_item_deprecated(visit, plan)]
    ra = visit.owner.attributes.get(visit.attr)
    if ra is None:
        return _unknown(visit, visit.attr, checks)
    value = visit.value
    if value is None:
        return []  # JSON null reads as absent
    if ra.deprecated is None:
        if _plain(value, ra):
            return []  # the common case, without the generators below
        findings: list[Finding] = []
    else:
        findings = list(_deprecated(visit, ra, checks))
        if visit.attr not in visit.node:
            return findings  # migrated to its successor, which is not this attribute's concern
    return findings + list(_value(visit, ra, checks))


def _plain(value: object, ra: ResolvedAttribute) -> bool:
    """Tell whether a single value needs nothing checked beyond its shape: an object, or a non-enum scalar."""
    if ra.is_array:
        return False
    if ra.object_type is not None:
        return isinstance(value, dict)
    return ra.enum is None and ra.root is not None and _conforms(value, ra.root)


# --- attribute visits ----------------------------------------------------------------------


def _unknown(visit: Visit, attr: str, checks: Checks) -> list[Finding]:
    declared = visit.owner.inactive.get(attr)
    if declared is not None:
        mismatch = Finding(
            kind=Kind.PROFILE_MISMATCH,
            path=visit.path,
            owner=visit.owner.key,
            message=f"{dotted(visit.path)} belongs to profiles {sorted(declared.profiles)}, none of which is active",
            tier=declared.requirement,
            expected=sorted(declared.profiles),
            actual=sorted(checks.schema.profiles),
        )
        # its value is checked all the same: a superset model class has a typed field for it
        return [mismatch] if visit.value is None else [mismatch, *_value(visit, declared, checks)]
    key = dotted(visit.path)
    unmapped = checks.root.get(UNMAPPED)
    if unmapped is None:
        unmapped = {}  # JSON null reads as absent here too
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
        and successor.name not in visit.node  # one already there, even as null, was visited already
    )
    repair = None
    if movable and target is not None:
        visit.node[target] = visit.node.pop(ra.name)
        repair = RepairAction(action="migrate", before=visit.path, after=(*visit.path[:-1], target))
    yield attribute_finding(
        ra,
        Kind.DEPRECATED,
        visit.path,
        f"{dotted(visit.path)} is deprecated since {dep.since}: {dep.message}",
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
            what = "a JSON object" if ra.object_type == OPEN_OBJECT else _article(ra.object_type)
            yield _mismatch(visit, ra, path, value, what)
        return
    assert ra.root is not None
    if not _conforms(value, ra.root):
        new = _coerced(value, ra, checks)
        if new is None:
            yield _mismatch(visit, ra, path, value, _expected(value, ra))
            return
        _replace(visit, index, new)
        yield attribute_finding(
            ra,
            Kind.COERCED,
            path,
            f"{dotted(path)} coerced from {shown(value)} to {shown(new)}",
            expected=ra.type,
            actual=value,
            repair=RepairAction(action="coerce", before=value, after=new),
        )
        value = new
    if ra.enum is None or (visit.owner.identity is not None and visit.path in _DERIVED_IDENTITY):
        return
    member = ra.enum.get(cast(int | str, value))
    if member is None:
        yield attribute_finding(
            ra,
            Kind.ENUM_OUT_OF_RANGE,
            path,
            f"{dotted(path)} is {shown(value)}, not one of {sorted(ra.enum, key=_enum_order)}",
            expected=sorted(ra.enum, key=_enum_order),
            actual=value,
        )
    elif member.deprecated is not None:
        yield attribute_finding(
            ra,
            Kind.DEPRECATED,
            path,
            f"{dotted(path)} value {shown(value)} ({member.caption}) is deprecated"
            f" since {member.deprecated.since}: {member.deprecated.message}",
            expected=member.deprecated.superseded_by,
            actual=value,
        )


def _conforms(value: object, root: Root) -> bool:
    match root:
        case Root.STRING:
            return isinstance(value, str)
        case Root.INTEGER | Root.LONG:
            return is_int64(value)
        case Root.FLOAT:
            return is_int(value) or (isinstance(value, float) and math.isfinite(value))  # a JSON 1 is a float
        case Root.BOOLEAN:
            return isinstance(value, bool)
        case Root.JSON:
            return True


def _expected(value: object, ra: ResolvedAttribute) -> str:
    """Say what a value that failed its root's check should have been."""
    if ra.root in (Root.INTEGER, Root.LONG) and is_int(value):
        return f"{ra.type} within the signed 64-bit range"
    if ra.root is Root.FLOAT and isinstance(value, float):
        return f"a finite {ra.type}"
    return ra.type


def _article(noun: str) -> str:
    return f"{'an' if noun[:1] in 'aeiou' else 'a'} {noun}"


def _enum_order(value: int | str) -> tuple[bool, int | str]:
    return isinstance(value, str), value  # numbers in numeric order; an enum's keys share one type


def _coerced(value: object, ra: ResolvedAttribute, checks: Checks) -> object | None:
    """Return the coercion this policy allows for value, or None."""
    if isinstance(value, datetime):
        if checks.datetimes and ra.type == "timestamp_t" and value.utcoffset() is not None:
            try:
                return _epoch_ms(value)
            except OverflowError:  # datetime(1, 1, 1, tzinfo=+01:00) has no UTC instant
                return None
        return None
    if not checks.coerce:
        return None
    if ra.root in (Root.INTEGER, Root.LONG):
        return as_integer(value)
    if ra.root is Root.BOOLEAN and isinstance(value, str):
        return _BOOLS.get(value)
    return None


def as_integer(value: object) -> int | None:
    """Return the signed 64-bit integer a value unambiguously spells: an integer string or an integral float."""
    number: int | None = None
    if (isinstance(value, str) and _INT.fullmatch(value)) or (
        isinstance(value, float) and math.isfinite(value) and value.is_integer()
    ):
        number = int(value)
    return number if is_int64(number) else None


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
    return attribute_finding(
        ra,
        Kind.TYPE_MISMATCH,
        path,
        f"{dotted(path)} is {type(value).__name__} {shown(value)}; expected {what}",
        expected=ra.type,
        actual=value,
    )


# --- node closes ---------------------------------------------------------------------------


def _absent(visit: Visit, plan: _Plan) -> Iterator[Finding]:
    node = visit.node
    for name, ra, kind, source in plan.asked:
        if node.get(name) is not None:
            continue
        # A derived value whose source is itself absent: the source's finding is the cause.
        if source is not None and node.get(source) is None:
            continue
        path = (*visit.path, name)
        yield attribute_finding(
            ra,
            kind,
            path,
            f"{dotted(path)} is {ra.requirement}"
            + (f" under profiles {sorted(ra.gated_by)}" if ra.gated_by else "")
            + " and absent",
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
            message=f"{dotted(visit.path) or visit.owner.key} needs {rule} of {list(c.attributes)}; present: {present}",
            expected=c.attributes,
            actual=tuple(present),
        )


def _item_deprecated(visit: Visit, plan: _Plan) -> Iterator[Finding]:
    dep = plan.deprecated
    if dep is not None:
        yield Finding(
            kind=Kind.DEPRECATED,
            path=visit.path,
            owner=visit.owner.key,
            message=f"{visit.owner.key} is deprecated since {dep.since}: {dep.message}",
            expected=dep.superseded_by,
        )


def _plan(checks: Checks, item: ResolvedItem) -> _Plan:
    key = (item.is_class, item.key)
    plan = checks.plans.get(key)
    if plan is None:
        sources = {spec.label: id_attr for id_attr, spec in item.siblings.items()}
        asked = tuple(
            (name, ra, kind, "activity_id" if item.is_class and name == "type_uid" else sources.get(name))
            for name, ra in item.attributes.items()
            if (kind := _MISSING.get(ra.requirement)) is not None and ra.deprecated is None  # never asked for
        )
        plan = checks.plans[key] = _Plan(asked, _item_def(checks.schema, item).deprecated)
    return plan


@functools.lru_cache(maxsize=SCHEMAS_KEPT)
def _plans(schema: ResolvedSchema) -> dict[tuple[bool, str], _Plan]:
    """Return the schema's node plans by (is_class, item key), filled as items are met."""
    return {}


def _lookup(node: dict[str, Any], dotted_name: str) -> object:
    value: object = node
    for part in dotted_name.split("."):
        if not isinstance(value, dict):
            return None
        value = cast(dict[str, object], value).get(part)
    return value


def _item_def(schema: ResolvedSchema, item: ResolvedItem) -> ItemDef:
    return (schema.ir.classes if item.is_class else schema.ir.objects)[item.key]
