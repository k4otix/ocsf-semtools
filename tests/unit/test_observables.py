"""Observables (R5, D#16): markers, extraction, notations, caller checks, merge, acc. #4."""

import copy
import math
from typing import Any

import pytest

from ocsf_toolkit.engine import LENIENT, REPAIR, extract, run
from ocsf_toolkit.loader import load_compiled
from ocsf_toolkit.models import parse, repair
from ocsf_toolkit.observables import Observable, merge, stringify
from ocsf_toolkit.schema import ResolvedSchema, load, resolve
from ocsf_toolkit.walk import Kind
from tests.fixtures.compiled import PROVENANCE, compiled, compiled_bytes
from tests.fixtures.events import CLASS, base_detection_finding, error_fixtures
from tests.fixtures.notations import NOTATIONS


def _names(found: tuple[Observable, ...]) -> list[tuple[str, int, str | None]]:
    return [(o.name, o.type_id, o.value) for o in found]


# --- the marker table -----------------------------------------------------------------------


# --- extraction -----------------------------------------------------------------------------


def test_objects_nested_inside_arrays_get_indexed_names(schema: ResolvedSchema) -> None:
    """The prior implementation's verified case: four entries, indexed names, right type ids."""
    event = {"class_uid": 2004, "evidences": [{"process": {"name": "p", "file": {"name": "f"}}}]}
    found, report = extract(event, schema)
    assert _names(found) == [
        ("evidences[0].process", 25, None),
        ("evidences[0].process.name", 9, "p"),
        ("evidences[0].process.file", 24, None),
        ("evidences[0].process.file.name", 7, "f"),
    ]
    assert [o.type for o in found] == ["Process", "Process Name", "File", "File Name"]
    assert report.findings == ()


def test_arrays_yield_one_entry_per_element(schema: ResolvedSchema) -> None:
    email = {"to": ["a@x", None, "b@x"], "from": "c@x"}
    found, _ = extract({"email": email}, schema, "email_activity")
    assert _names(found) == [
        ("email", 22, None),
        ("email.to[0]", 5, "a@x"),
        ("email.to[2]", 5, "b@x"),
        ("email.from", 5, "c@x"),
    ]
    found, _ = extract({"users": [{"name": "a"}, {"name": "b"}]}, schema, "admin_group_query")
    assert _names(found) == [
        ("users[0]", 21, None),
        ("users[1]", 21, None),
        ("users[0].name", 4, "a"),
        ("users[1].name", 4, "b"),
    ]


def test_nulls_unknowns_and_wrong_shapes_yield_nothing(schema: ResolvedSchema) -> None:
    event = {"device": None, "evidences": [{"process": "x"}], "src_ip": "10.0.0.1"}
    found, _ = extract(event, schema, CLASS)
    assert found == ()


def test_recursion_terminates_on_data_not_depth(schema: ResolvedSchema) -> None:
    process: dict[str, Any] = {"name": "leaf"}
    for _ in range(2000):  # process.parent_process is self-referential
        process = {"parent_process": process}
    found, _ = extract({"evidences": [{"process": process}]}, schema, CLASS)
    assert len(found) == 2001 + 1  # every process object, plus the leaf's name
    assert found[-1].name.endswith(".parent_process.name")


def test_extract_infers_the_class_and_never_changes_the_event(schema: ResolvedSchema) -> None:
    event = base_detection_finding(schema) | {"device": {"hostname": "h"}}
    before = copy.deepcopy(event)
    found, report = extract(event, schema)
    assert report.class_key == CLASS
    assert event == before
    assert ("device.hostname", 1, "h") in _names(found)


def test_a_class_can_mark_paths_as_observables() -> None:
    """A class's own `observables` path map (definition kind 6) is empty in 1.9.0, so this uses the synthetic schema."""
    data = compiled()
    data["classes"]["finding"]["observables"] = {"method": 44, "cloud.hosted": 45}
    schema = resolve(load_compiled(compiled_bytes(data), PROVENANCE), profiles={"cloud"})
    event = {"method": "GET", "cloud": {"ip": "1.2.3.4"}}
    found, _ = extract(event, schema, "finding")
    assert _names(found) == [("method", 44, "GET"), ("cloud", 30, None), ("cloud.ip", 2, "1.2.3.4")]


@pytest.mark.parametrize(
    ("value", "text"),
    [
        ("x", "x"),
        (42, "42"),
        (-7, "-7"),
        (True, "true"),
        (False, "false"),
        (1.5, "1.5"),
        (1e100, "1e+100"),
        (math.nan, "nan"),
        (None, None),
        ({"a": 1}, None),
        ([1], None),
    ],
)
def test_stringify(value: object, text: str | None) -> None:
    assert stringify(value) == text


# --- validating the caller's entries (parse and every run) ----------------------------------


def _with(schema: ResolvedSchema, *entries: dict[str, Any]) -> dict[str, Any]:
    event = base_detection_finding(schema)
    event["resources"] = [{"uid": "r-1"}, {"uid": "r-2"}]
    event["observables"] = list(entries)
    return event


@pytest.mark.parametrize("name", NOTATIONS)
def test_caller_entries_match_in_any_notation(schema: ResolvedSchema, name: str) -> None:
    event = _with(schema, {"name": name, "type_id": 10, "value": "r-2"})
    _, report = parse(event)
    assert report.by_kind(Kind.OBSERVABLE_MISMATCH) == ()


@pytest.mark.parametrize(
    ("entry", "problem"),
    [
        ({"name": "nowhere.uid", "type_id": 10}, "names no observable attribute"),
        ({"name": "resources.uid", "type_id": 4, "value": "r-1"}, "type_id 4; the schema marks"),
        ({"name": "resources[0].uid", "type_id": 10, "value": "r-9"}, "does not occur"),
    ],
)
def test_unfounded_caller_entries_are_warnings(schema: ResolvedSchema, entry: dict[str, Any], problem: str) -> None:
    ok = {"name": "resources[1].uid", "type_id": 10, "value": "r-2"}
    _, report = parse(_with(schema, ok, entry))
    [finding] = report.by_kind(Kind.OBSERVABLE_MISMATCH)
    assert (finding.path, finding.severity) == (("observables", 1), "warning")
    assert problem in finding.message


def test_validation_sees_values_as_repaired(schema: ResolvedSchema) -> None:
    event = error_fixtures(schema)["E3_wrong_primitive"]
    event["actor"] = {"user": {"name": "alice"}}
    _, lenient = run(event, load("1.9.0", profiles={"host"}), CLASS, LENIENT)
    assert lenient.by_kind(Kind.OBSERVABLE_MISMATCH) == ()  # type_id "4" isn't checkable yet
    _, repaired = run(event, load("1.9.0", profiles={"host"}), CLASS, REPAIR)
    assert repaired.by_kind(Kind.OBSERVABLE_MISMATCH) == ()  # coerced to 4: now it matches
    event["observables"][0]["value"] = "bob"
    _, repaired = repair(event, profiles=["host"])
    assert len(repaired.by_kind(Kind.OBSERVABLE_MISMATCH)) == 1


def test_extract_reports_unfounded_entries_too(schema: ResolvedSchema) -> None:
    _, report = extract(_with(schema, {"name": "nowhere", "type_id": 1}), schema)
    assert [f.kind for f in report.findings] == [Kind.OBSERVABLE_MISMATCH]


# --- merge ----------------------------------------------------------------------------------


def test_merge_keeps_caller_entries_and_appends_the_rest(schema: ResolvedSchema) -> None:
    caller = {"name": "$.resources[*].uid", "type_id": 10, "value": "r-1", "reputation": {"x": 1}}
    event = _with(schema, caller)
    before = copy.deepcopy(event)
    found, _ = extract(event, schema)
    merged = merge(event, found)
    assert event == before
    assert merged["observables"][0] == caller  # verbatim, notation and extra fields included
    names = [e["name"] for e in merged["observables"]]
    assert "resources[0].uid" not in names  # already present in another notation
    assert names.count("resources[1].uid") == 1
    assert {"name": "resources[1].uid", "type_id": 10, "type": "Resource UID", "value": "r-2"} in (
        merged["observables"]
    )
    assert merge(merged, found) == merged  # idempotent


def test_merge_into_an_event_without_observables(schema: ResolvedSchema) -> None:
    event = {"device": {"hostname": "h"}}
    found, _ = extract(event, schema, CLASS)
    assert merge(event, found)["observables"] == [o.to_dict() for o in found]


def test_merge_refuses_a_non_array(schema: ResolvedSchema) -> None:
    with pytest.raises(ValueError, match="observables is str, not an array"):
        merge({"observables": "x"}, ())


def test_merged_event_is_conformant(schema: ResolvedSchema) -> None:
    device = {"hostname": "h", "ip": "10.0.0.1", "type_id": 2}
    event = base_detection_finding(schema) | {"device": device}
    merged = merge(event, extract(event, schema)[0])
    _, report = parse(merged)
    assert report.ok()
    assert report.by_kind(Kind.OBSERVABLE_MISMATCH) == ()


# --- the max_nodes guard (acc. #4) ----------------------------------------------------------


def _deep(n: int) -> dict[str, Any]:
    return {"evidences": [{"process": {"name": str(i)}} for i in range(n)]}


def test_max_nodes_is_off_by_default(schema: ResolvedSchema) -> None:
    found, report = extract(_deep(500), schema, CLASS)
    assert len(found) == 1000
    assert report.findings == ()


@pytest.mark.parametrize(
    ("limit", "count", "tripped"),
    [(21, 20, False), (20, 19, True), (11, 10, True), (1, 0, True)],
)
def test_max_nodes_boundary(schema: ResolvedSchema, limit: int, count: int, tripped: bool) -> None:
    """Ten evidences cost 21 attribute visits: evidences, then process and name per element."""
    found, report = extract(_deep(10), schema, CLASS, max_nodes=limit)
    assert len(found) == count
    trips = report.by_kind(Kind.MAX_NODES_EXCEEDED)
    assert len(trips) == (1 if tripped else 0)
    if tripped:
        assert trips[0].expected == limit
        assert trips[0].severity == "info"
