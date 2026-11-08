"""repair() undoes the damage it exists for, and repairing its output changes nothing.

A conformant event is damaged in the ways repair() calls unambiguous: integers sent as strings or
integral floats, booleans as strings, derived values dropped or contradicted, and attributes the
schema doesn't know. Repairing it gives what repairing the undamaged event gives, plus the unknown
attributes in ``unmapped``, keyed by where the walk met them: below a migrated attribute, its successor.
"""

import copy
from dataclasses import dataclass
from typing import Any, Literal, cast

import pytest
from hypothesis import given, note
from hypothesis import strategies as st

from ocsf_semtools import Report, ResolvedItem, ResolvedSchema, load, repair
from ocsf_semtools.derive import OTHER
from ocsf_semtools.ir import Root
from ocsf_semtools.walk import Path, Visit, dotted, walk
from tests.property.strategies import SETTINGS, derived, fields, profile_sets
from tests.property.test_roundtrip import CASES, build_event

type Damage = Literal["string", "float", "drop", "contradict", "unknown"]
IDENTITY = ("class_uid", "type_uid")  # what names an event's class
UNKNOWN = "zz_unknown"  # longer than any key the strategies draw, so it collides with none


@dataclass(frozen=True, slots=True)
class Site:
    damage: Damage
    node: dict[str, Any] | list[Any]  # the container to change
    slot: str | int  # the attribute, or the index in an array
    path: tuple[str | int, ...]  # of the node, for an unknown attribute's relocation key


def _other(item: ResolvedItem, node: dict[str, Any], id_attr: str) -> bool:
    """Tell whether an id's label is the caller's to keep: "Other", or a type_uid of activity Other."""
    value = node.get(id_attr)
    if isinstance(value, list):
        return OTHER in cast(list[object], value)
    return value == OTHER or (
        item.is_class and id_attr == "type_uid" and isinstance(value, int) and value % 100 == OTHER
    )


def _sites(schema: ResolvedSchema, key: str, event: dict[str, Any]) -> list[Site]:
    sites: list[Site] = []
    for visit in walk(event, schema.classes[key], schema):
        assert isinstance(visit, Visit)
        owner, node = visit.owner, visit.node
        if visit.attr is None:
            sites.append(Site("unknown", node, UNKNOWN, visit.path))
            continue
        attr, value, ra = visit.attr, visit.value, owner.attributes[visit.attr]
        labels = {spec.label: id_attr for id_attr, spec in owner.siblings.items()}
        if attr in derived(owner) and not (attr in labels and _other(owner, node, labels[attr])):
            if not (owner.key == "metadata" and attr in ("version", "profiles")):  # they pick the schema
                sites.append(Site("drop", node, attr, visit.path))
                if not (owner.is_class and attr == "class_uid"):  # a wrong one names another class
                    sites.append(Site("contradict", node, attr, visit.path))
            continue
        if ra.root in (Root.INTEGER, Root.LONG):
            elements = list(enumerate(cast(list[object], value))) if ra.is_array else [(attr, value)]
            container = cast(list[Any], value) if ra.is_array else node
            for slot, v in elements:
                if isinstance(v, int) and not isinstance(v, bool):
                    sites.append(Site("string", container, slot, visit.path))
                    if abs(v) < 2**53:  # exactly representable
                        sites.append(Site("float", container, slot, visit.path))
        elif ra.root is Root.BOOLEAN and isinstance(value, bool):
            sites.append(Site("string", node, attr, visit.path))
    return sites


def _apply(site: Site, value: object) -> dict[str, object]:
    """Damage one site; return the attribute it adds to unmapped, if any."""
    container = cast(dict[Any, Any], site.node)
    match site.damage:
        case "string":
            current = container[site.slot]
            container[site.slot] = str(current).lower() if isinstance(current, bool) else str(current)
        case "float":
            container[site.slot] = float(container[site.slot])
        case "drop":
            del container[site.slot]
        case "contradict":
            current = container[site.slot]
            if isinstance(current, int):  # an identity value: type_uid, category_uid
                container[site.slot] = current + 1
            elif isinstance(current, list):
                container[site.slot] = [f"not {v!r}" for v in cast(list[object], current)]
            else:
                container[site.slot] = f"not {current!r}"
        case "unknown":
            container[site.slot] = value
            return {dotted((*site.path, UNKNOWN)): value}
    return {}


def _migrations(report: Report) -> dict[str, str]:
    """Return each migration repair made, as dotted before -> after paths."""
    moves: dict[str, str] = {}
    for f in report.findings:
        if f.repair is not None and f.repair.action == "migrate":
            before, after = cast(Path, f.repair.before), cast(Path, f.repair.after)
            moves[dotted(before)] = dotted(after)
    return moves


def _moved(key: str, moves: dict[str, str]) -> str:
    """Name a relocation key by where the walk met it: under a migrated parent, at its successor."""
    for before, after in moves.items():
        if key.startswith((f"{before}.", f"{before}[")):
            return after + key[len(before) :]
    return key


@pytest.mark.parametrize("case", CASES, ids=[f"{v}-{k}" for v, k in CASES])
@SETTINGS
@given(data=st.data())
def test_repair_restores_and_is_idempotent(case: tuple[str, str], data: st.DataObject) -> None:
    version, key = case
    profiles = data.draw(st.sampled_from(profile_sets(version)), label="profiles")
    schema = load(version, profiles=profiles)
    event = build_event(schema, key, data.draw(fields(version, profiles, key), label="fields"))

    damaged = copy.deepcopy(event)
    sites = _sites(schema, key, damaged)
    chosen = data.draw(st.lists(st.sampled_from(range(len(sites))), min_size=1, max_size=4, unique=True), label="sites")
    relocated: dict[str, object] = {}
    damaged_slots: set[tuple[int, str | int]] = set()
    for site in (sites[n] for n in chosen):
        if (id(site.node), site.slot) in damaged_slots:
            continue  # one damage per slot: a string of a float is not an integer string
        if site.node is damaged and site.slot in IDENTITY and damaged_slots & {(id(damaged), i) for i in IDENTITY}:
            continue  # with both damaged, nothing says which class the event is
        damaged_slots.add((id(site.node), site.slot))
        value = data.draw(st.text(max_size=10), label="unknown value") if site.damage == "unknown" else None
        note(f"{site.damage} {dotted((*site.path, site.slot))}")
        relocated |= _apply(site, value)

    fixed, report = repair(damaged)
    assert report.errors() == ()
    expected, untouched = repair(event)
    # a conformant event changes only by moving deprecated attributes to their successors
    changes = {f.repair.action for f in untouched.findings if f.repair is not None}
    assert changes <= {"migrate"}
    if not changes:
        assert expected == event
    if relocated:
        moves = _migrations(report)
        relocated = {_moved(key, moves): value for key, value in relocated.items()}
        expected["unmapped"] = {**expected.get("unmapped", {}), **relocated}
    assert fixed == expected

    again, second = repair(fixed)
    assert again == fixed
    assert [f.message for f in second.findings if f.repair is not None] == []
