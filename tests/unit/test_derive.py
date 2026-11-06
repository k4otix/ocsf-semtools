"""Derivation: identity (acc. #12), the §5.2 sibling rules row by row (acc. #12b), *_dt."""

import copy
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from ocsf_toolkit.derive import rfc3339
from ocsf_toolkit.engine import LENIENT, REPAIR, STRICT, ConformanceError, Policy, Report, run
from ocsf_toolkit.models import build
from ocsf_toolkit.schema import ResolvedSchema, load
from ocsf_toolkit.walk import Kind, Path
from tests.fixtures.events import CLASS, T0, base_detection_finding, caption


@pytest.fixture(scope="module")
def schema() -> ResolvedSchema:
    return load("1.9.0")


def _run(
    event: dict[str, Any], policy: Policy = STRICT, profiles: frozenset[str] = frozenset()
) -> tuple[dict[str, Any], Report]:
    return run(event, load("1.9.0", profiles=profiles), CLASS, policy)


def _at(report: Report, path: Path) -> list[Kind]:
    return [f.kind for f in report.findings if f.path == path and f.kind is not Kind.FILLED]


def _minimal(**fields: Any) -> dict[str, Any]:
    return {
        "activity_id": 1,
        "severity_id": 4,
        "time": T0,
        "metadata": {"product": {"name": "p"}},
        "finding_info": {"uid": "f"},
        **fields,
    }


# --- identity (acc. #12, R4a) --------------------------------------------------------------


def test_class_and_activity_id_are_enough_for_every_identity_field(schema: ResolvedSchema) -> None:
    event = build((schema, CLASS), **_minimal())
    expected = base_detection_finding(schema)
    identity = ("class_uid", "class_name", "category_uid", "category_name", "activity_name")
    for attr in (*identity, "type_uid", "type_name", "severity"):
        assert event[attr] == expected[attr], attr
    assert event["metadata"]["version"] == "1.9.0"


def test_type_uid_is_never_a_caller_input(schema: ResolvedSchema) -> None:
    with pytest.raises(ConformanceError) as raised:
        build((schema, CLASS), **_minimal(type_uid=200403))
    assert [(f.kind, f.path) for f in raised.value.report.errors()] == [(Kind.IDENTITY_CONFLICT, ("type_uid",))]
    out, report = _run(_minimal(type_uid=200403), REPAIR)
    assert out["type_uid"] == 200401
    assert out["type_name"] == "Detection Finding: Create"
    assert report.ok()


@pytest.mark.parametrize(
    ("attr", "value"),
    [("class_uid", 2005), ("class_name", "X"), ("category_uid", 3), ("category_name", "Y")],
)
def test_class_constants_cannot_be_supplied_inconsistently(attr: str, value: object) -> None:
    _, report = _run(_minimal(**{attr: value}), LENIENT)
    assert _at(report, (attr,)) == [Kind.IDENTITY_CONFLICT]


# --- §5.2 sibling rules -------------------------------------------------------------------


def test_in_enum_without_label_fills_the_caption() -> None:
    out, _ = _run(_minimal(severity_id=3))
    assert out["severity"] == "Medium"
    out, report = _run(_minimal(severity_id=3), LENIENT)
    assert "severity" not in out
    assert _at(report, ("severity",)) == []


def test_in_enum_with_matching_label_is_ok() -> None:
    _, report = _run(_minimal(severity_id=3, severity="Medium"), LENIENT)
    assert _at(report, ("severity",)) == []


@pytest.mark.parametrize(("policy", "after"), [(STRICT, "Critical"), (REPAIR, "Medium")])
def test_in_enum_with_conflicting_label(policy: Policy, after: str) -> None:
    out, report = _run(_minimal(severity_id=3, severity="Critical"), policy)
    [finding] = [f for f in report.findings if f.path == ("severity",)]
    assert (finding.kind, finding.expected, finding.actual) == (
        Kind.SIBLING_CONFLICT,
        "Medium",
        "Critical",
    )
    assert out["severity"] == after
    assert (finding.repair is not None) is (policy is REPAIR)


def test_other_without_label_stays_absent() -> None:
    out, report = _run(_minimal(severity_id=99))
    assert "severity" not in out
    assert _at(report, ("severity",)) == []


@pytest.mark.parametrize("policy", [STRICT, LENIENT, REPAIR])
def test_other_with_label_is_preserved_and_never_compared(policy: Policy) -> None:
    out, report = _run(_minimal(severity_id=99, severity="Spicy"), policy)
    assert out["severity"] == "Spicy"
    assert _at(report, ("severity",)) == []


def test_activity_other_takes_type_name_from_the_callers_label() -> None:
    out, report = _run(_minimal(activity_id=99, activity_name="Triage"))
    assert (out["type_uid"], out["type_name"]) == (200499, "Detection Finding: Triage")
    assert report.ok()
    out, report = _run(_minimal(activity_id=99, activity_name="Triage", type_name="Mine"), REPAIR)
    assert out["type_name"] == "Mine"
    assert _at(report, ("type_name",)) == []


def test_unknown_id_derives_unknown_and_flags_an_empty_label() -> None:
    out, _ = _run(_minimal(severity_id=0))
    assert out["severity"] == "Unknown"
    _, report = _run(_minimal(severity_id=0, severity=""), LENIENT)
    assert _at(report, ("severity",)) == [Kind.SIBLING_CONFLICT]
    assert _at(report, ("severity_id",)) == [Kind.SEMANTICALLY_EMPTY_ENUM]
    assert next(f for f in report.findings if f.path == ("severity_id",)).severity == "info"


def test_out_of_range_id_derives_nothing() -> None:
    out, report = _run(_minimal(severity_id=42, severity="Bogus"), REPAIR)
    assert out["severity"] == "Bogus"
    assert _at(report, ("severity_id",)) == [Kind.ENUM_OUT_OF_RANGE]
    assert _at(report, ("severity",)) == []


def test_wrongly_typed_id_derives_nothing() -> None:
    out, report = _run(_minimal(severity_id=True))
    assert "severity" not in out
    assert _at(report, ("severity_id",)) == [Kind.TYPE_MISMATCH]


# --- list siblings (D-12) and the owning type (D-13) ---------------------------------------


def _malware(**fields: Any) -> dict[str, Any]:
    return _minimal(malware=[{"name": "m", **fields}])


def _classifications(schema: ResolvedSchema, *ids: int) -> list[str]:
    return [caption(schema, "malware", "classification_ids", i) for i in ids]


def test_list_sibling_fills_element_wise(schema: ResolvedSchema) -> None:
    out, _ = _run(_malware(classification_ids=[1, 2]))
    assert out["malware"][0]["classifications"] == _classifications(schema, 1, 2)


def test_list_sibling_conflict_is_element_wise(schema: ResolvedSchema) -> None:
    labels = _classifications(schema, 1, 2)
    swapped = list(reversed(labels))
    out, report = _run(_malware(classification_ids=[1, 2], classifications=swapped), REPAIR)
    assert _at(report, ("malware", 0, "classifications")) == [Kind.SIBLING_CONFLICT]
    assert out["malware"][0]["classifications"] == labels


def test_list_sibling_length_mismatch_is_a_conflict(schema: ResolvedSchema) -> None:
    labels = _classifications(schema, 1)
    _, report = _run(_malware(classification_ids=[1, 2], classifications=labels), LENIENT)
    assert _at(report, ("malware", 0, "classifications")) == [Kind.SIBLING_CONFLICT]


def test_list_sibling_other_element_keeps_its_label(schema: ResolvedSchema) -> None:
    labels = [*_classifications(schema, 1), "Custom"]
    out, report = _run(_malware(classification_ids=[1, 99], classifications=labels), REPAIR)
    assert out["malware"][0]["classifications"] == labels
    assert _at(report, ("malware", 0, "classifications")) == []
    out, _ = _run(_malware(classification_ids=[1, 99]))
    assert "classifications" not in out["malware"][0]  # no sound label for the Other element


def test_derivation_attaches_to_the_nested_owner() -> None:
    out, _ = _run(_malware(classification_ids=[1], severity_id=5))
    assert out["malware"][0]["severity"] == "Critical"


def test_type_uid_without_an_enum_derives_through_the_class_registry() -> None:
    related = {"uid": "r", "type_uid": 300201}
    out, _ = _run(_minimal(finding_info={"uid": "f", "related_events": [related]}))
    derived = out["finding_info"]["related_events"][0]
    assert derived["type_name"] == "Authentication: Logon"
    related["type_name"] = "Authentication: Logoff"
    _, report = _run(_minimal(finding_info={"uid": "f", "related_events": [related]}), LENIENT)
    assert _at(report, ("finding_info", "related_events", 0, "type_name")) == [Kind.SIBLING_CONFLICT]


# --- profiles: no rule for an attribute outside the resolved class (acc. #12a) -------------


def test_no_sibling_rule_for_a_profile_gated_out_attribute() -> None:
    out, report = _run(_minimal(action_id=2))
    assert "action" not in out
    assert _at(report, ("action_id",)) == [Kind.PROFILE_MISMATCH]
    out, report = _run(
        _minimal(action_id=2, metadata={"product": {"name": "p"}, "profiles": ["security_control"]}),
        profiles=frozenset({"security_control"}),
    )
    assert out["action"] == "Denied"
    assert report.ok()


# --- metadata --------------------------------------------------------------------------------


def test_metadata_version_and_profiles() -> None:
    out, _ = _run(_minimal(), profiles=frozenset({"host", "cloud"}))
    assert out["metadata"]["version"] == "1.9.0"
    assert out["metadata"]["profiles"] == ["cloud", "host"]
    out, _ = _run(_minimal())
    assert "profiles" not in out["metadata"]

    event = _minimal(metadata={"product": {"name": "p"}, "version": "1.8.0", "profiles": ["x"]})
    _, report = _run(copy.deepcopy(event), LENIENT)
    assert _at(report, ("metadata", "version")) == [Kind.IDENTITY_CONFLICT]
    assert _at(report, ("metadata", "profiles")) == [Kind.PROFILE_MISMATCH]
    out, _ = _run(event, REPAIR)
    assert (out["metadata"]["version"], out["metadata"]["profiles"]) == ("1.9.0", [])


# --- timestamps (D#17) ---------------------------------------------------------------------

DATETIME = frozenset({"datetime"})


def test_rfc3339() -> None:
    assert rfc3339(T0) == "2025-11-01T12:26:40.000Z"
    assert rfc3339(T0 + 7) == "2025-11-01T12:26:40.007Z"
    assert rfc3339(-1) == "1969-12-31T23:59:59.999Z"


def test_dt_twins_derive_only_under_the_datetime_profile() -> None:
    out, _ = _run(_minimal(), profiles=DATETIME)
    assert out["time_dt"] == "2025-11-01T12:26:40.000Z"
    out, _ = _run(_minimal())
    assert "time_dt" not in out


def test_dt_twin_compares_instants_not_spelling() -> None:
    _, report = _run(_minimal(time_dt="2025-11-01T13:26:40+01:00"), LENIENT, DATETIME)
    assert _at(report, ("time_dt",)) == []
    out, report = _run(_minimal(time_dt="2020-01-01T00:00:00Z"), REPAIR, DATETIME)
    assert _at(report, ("time_dt",)) == [Kind.SIBLING_CONFLICT]
    assert out["time_dt"] == "2025-11-01T12:26:40.000Z"


def test_aware_datetime_becomes_epoch_ms() -> None:
    moment = datetime(2025, 11, 1, 13, 26, 40, tzinfo=timezone(timedelta(hours=1)))
    out, report = _run(_minimal(time=moment), profiles=DATETIME)
    assert out["time"] == T0
    assert out["time_dt"] == "2025-11-01T12:26:40.000Z"
    assert report.ok()
    assert _run(_minimal(time=datetime(2025, 11, 1, tzinfo=UTC)))[0]["time"] == T0 - 44_800_000


@pytest.mark.parametrize("policy", [STRICT, LENIENT])
def test_naive_datetime_and_lenient_datetime_are_mismatches(policy: Policy) -> None:
    naive = datetime(2025, 11, 1)  # naive on purpose
    _, report = _run(_minimal(time=naive), policy)
    assert _at(report, ("time",)) == [Kind.TYPE_MISMATCH]
    if policy is LENIENT:
        aware = datetime(2025, 11, 1, tzinfo=UTC)
        assert _at(_run(_minimal(time=aware), policy)[1], ("time",)) == [Kind.TYPE_MISMATCH]
