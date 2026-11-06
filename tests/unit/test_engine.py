"""The engine: policies differ only in behaviour; build raises iff errors; repair records all."""

import copy
import dataclasses
import json
import warnings
from datetime import UTC, datetime
from typing import Any

import pytest

from ocsf_toolkit.engine import (
    LENIENT,
    REPAIR,
    STRICT,
    ConformanceError,
    Policy,
    Report,
    run,
    select,
)
from ocsf_toolkit.models import build, parse, repair
from ocsf_toolkit.schema import ResolvedSchema, load
from ocsf_toolkit.walk import Finding, Kind
from tests.fixtures.events import (
    CLASS,
    T0,
    base_detection_finding,
    error_fixtures,
    multi_error_fixture,
)


@pytest.fixture(scope="module")
def schema() -> ResolvedSchema:
    return load("1.9.0")


def test_the_three_presets() -> None:
    assert [dataclasses.astuple(p) for p in (STRICT, LENIENT, REPAIR)] == [
        (True, False, False, "error"),
        (False, False, False, "report"),
        (True, True, True, "relocate"),
    ]
    assert [f.name for f in dataclasses.fields(Policy)] == [
        "fill",
        "overwrite",
        "coerce",
        "unknown",
    ]


@pytest.mark.parametrize("policy", [STRICT, REPAIR])
def test_changing_policies_work_on_a_copy(schema: ResolvedSchema, policy: Policy) -> None:
    event = multi_error_fixture(schema)
    before = copy.deepcopy(event)
    out, _ = run(event, schema, CLASS, policy)
    assert event == before
    assert out is not event


def test_policies_differ_only_in_behaviour(schema: ResolvedSchema) -> None:
    """The same walk finds the same problems; only what each policy does about them differs."""
    event = error_fixtures(schema)["E1_sibling_violated"]
    found = {p: {(f.kind, f.path) for f in run(event, schema, CLASS, p)[1].findings} for p in (STRICT, LENIENT, REPAIR)}
    assert found[STRICT] == found[LENIENT] == found[REPAIR]


# --- build ---------------------------------------------------------------------------------


def _fields(**extra: Any) -> dict[str, Any]:
    return {
        "activity_id": 1,
        "severity_id": 4,
        "time": T0,
        "metadata": {"product": {"name": "p"}},
        "finding_info": {"uid": "f"},
        **extra,
    }


def test_build_raises_with_the_whole_report(schema: ResolvedSchema) -> None:
    with pytest.raises(ConformanceError) as raised:
        build((schema, CLASS), **_fields(severity_id=42, time="yesterday", bogus=1))
    report = raised.value.report
    assert {(f.kind, f.path) for f in report.errors()} == {
        (Kind.ENUM_OUT_OF_RANGE, ("severity_id",)),
        (Kind.TYPE_MISMATCH, ("time",)),
        (Kind.UNKNOWN_ATTRIBUTE, ("bogus",)),
    }
    assert str(raised.value).startswith("3 conformance error(s) in detection_finding: ")


def test_build_does_not_raise_on_warnings(schema: ResolvedSchema) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        event = build((schema, CLASS), **_fields(comment="old"))
    assert event["comment"] == "old"


def test_build_warns_per_deprecated_attribute(schema: ResolvedSchema) -> None:
    with pytest.warns(DeprecationWarning, match="comment is deprecated"):
        build((schema, CLASS), **_fields(comment="old"))


def test_build_never_mutates_nested_caller_dicts(schema: ResolvedSchema) -> None:
    metadata: dict[str, Any] = {"product": {"name": "p"}}
    build((schema, CLASS), **(_fields() | {"metadata": metadata}))
    assert metadata == {"product": {"name": "p"}}


# --- repair (D#15) -------------------------------------------------------------------------


def test_every_repair_change_is_a_finding_with_its_action(schema: ResolvedSchema) -> None:
    event: dict[str, Any] = error_fixtures(schema)["E8_unknown_field"] | {
        "activity_name": "wrong",
        "severity_id": "4",
        "is_alert": "true",
        "risk_score": 7.0,
    }
    del event["metadata"]["version"]
    out, report = repair(event, version="1.9.0")
    changed = {(f.kind, f.path, f.repair.action) for f in report.findings if f.repair is not None}
    assert changed == {
        (Kind.SIBLING_CONFLICT, ("activity_name",), "overwrite"),
        (Kind.COERCED, ("severity_id",), "coerce"),
        (Kind.COERCED, ("is_alert",), "coerce"),
        (Kind.COERCED, ("risk_score",), "coerce"),
        (Kind.FILLED, ("metadata", "version"), "fill"),
        (Kind.RELOCATED, ("totally_unknown_field",), "relocate"),
    }
    assert report.ok()
    assert (out["severity_id"], out["is_alert"], out["risk_score"]) == (4, True, 7)
    assert out["unmapped"] == {"totally_unknown_field": "x"}
    assert out["activity_name"] == "Create"


@pytest.mark.parametrize(
    ("attr", "value"),
    [
        ("severity_id", "4.0"),  # numeric strings other than base-10 integers
        ("risk_score", 7.5),  # non-integral float
        ("message", 5),  # int -> str is out of scope
        ("is_alert", "True"),  # booleans are exact lowercase
        ("is_alert", 1),
        ("time", "1762000000000.5"),
    ],
)
def test_coercion_is_limited_to_the_four_allowed_forms(schema: ResolvedSchema, attr: str, value: object) -> None:
    event = base_detection_finding(schema) | {attr: value}
    out, report = repair(event)
    assert out[attr] == value
    assert (Kind.TYPE_MISMATCH, (attr,)) in {(f.kind, f.path) for f in report.errors()}


def test_an_overlong_integer_string_is_reported_not_raised(schema: ResolvedSchema) -> None:
    for digits in ("9" * 20, "9" * 5000):
        out, report = repair(base_detection_finding(schema) | {"severity_id": digits})
        assert out["severity_id"] == digits
        assert (Kind.TYPE_MISMATCH, ("severity_id",)) in {(f.kind, f.path) for f in report.errors()}


def test_repair_coerces_array_elements_and_datetimes(schema: ResolvedSchema) -> None:
    event = base_detection_finding(schema) | {
        "observables": [{"name": "a", "type_id": "4"}],
        "start_time": datetime(2025, 11, 1, 12, 26, 40, tzinfo=UTC),
    }
    out, report = repair(event)
    assert out["observables"][0]["type_id"] == 4
    assert out["observables"][0]["type"] == "User Name"
    assert out["start_time"] == T0
    assert report.ok()


def test_relocation_keys_nested_unknowns_by_indexed_path(schema: ResolvedSchema) -> None:
    event = base_detection_finding(schema) | {"observables": [{"name": "a", "type_id": 4, "z": 1}]}
    event["unmapped"] = {"kept": True}
    out, report = repair(event)
    assert out["unmapped"] == {"kept": True, "observables[0].z": 1}
    assert "z" not in out["observables"][0]
    assert report.ok()


def test_relocation_never_overwrites_unmapped(schema: ResolvedSchema) -> None:
    event = base_detection_finding(schema) | {"x": 1, "unmapped": {"x": 0}}
    out, report = repair(event)
    assert (out["x"], out["unmapped"]) == (1, {"x": 0})
    assert [f.kind for f in report.errors()] == [Kind.UNKNOWN_ATTRIBUTE]


def test_repair_migrates_one_to_one_deprecations_of_the_same_shape() -> None:
    schema = load("1.9.0")
    event: dict[str, Any] = {"class_uid": 4009, "activity_id": 1, "smtp_hello": "EHLO x"}
    out, report = run(event, schema, None, REPAIR)
    assert ("smtp_hello" not in out, out.get("command")) == (True, "EHLO x")
    [finding] = [f for f in report.findings if f.path == ("smtp_hello",)]
    assert finding.repair is not None
    assert (finding.kind, finding.repair.action, finding.repair.after) == (
        Kind.DEPRECATED,
        "migrate",
        ("command",),
    )


def test_repair_does_not_migrate_across_types(schema: ResolvedSchema) -> None:
    out, report = repair(base_detection_finding(schema) | {"comment": "old"})
    assert out["comment"] == "old"  # notes is an array of note objects
    assert next(f for f in report.findings if f.path == ("comment",)).repair is None


# --- parse: schema and class selection -----------------------------------------------------


def test_version_and_profiles_come_from_metadata(schema: ResolvedSchema) -> None:
    event = base_detection_finding(schema)
    event["metadata"]["profiles"] = ["cloud", "not_a_profile"]
    chosen = select(event, version=None, profiles=None)
    assert (chosen.version, chosen.profiles) == ("1.9.0", frozenset({"cloud"}))
    _, report = parse(event)
    assert (Kind.PROFILE_MISMATCH, ("metadata", "profiles")) in {(f.kind, f.path) for f in report.findings}


def test_arguments_win_over_metadata(schema: ResolvedSchema) -> None:
    event = base_detection_finding(schema)
    event["metadata"] |= {"version": "1.8.0", "profiles": ["cloud"]}
    _, report = parse(event, version="1.9.0", profiles=[])
    assert report.schema == load("1.9.0")
    kinds = {(f.kind, f.path) for f in report.findings}
    assert (Kind.IDENTITY_CONFLICT, ("metadata", "version")) in kinds
    assert (Kind.PROFILE_MISMATCH, ("metadata", "profiles")) in kinds


@pytest.mark.parametrize("metadata", [None, {}, {"version": 1.9}])
def test_no_version_is_a_caller_error(metadata: object) -> None:
    with pytest.raises(ValueError, match="no OCSF version"):
        parse({"metadata": metadata})


def test_unshipped_version_is_a_caller_error(schema: ResolvedSchema) -> None:
    with pytest.raises(ValueError, match="no shipped IR"):
        parse(base_detection_finding(schema), version="0.0.1")


def test_class_is_inferred_from_class_uid_then_type_uid(schema: ResolvedSchema) -> None:
    event = base_detection_finding(schema)
    assert parse(event)[1].class_key == CLASS
    del event["class_uid"]
    assert parse(event)[1].class_key == CLASS
    event["class_uid"] = 3002  # class_uid beats a contradicting type_uid
    _, report = parse(event)
    assert report.class_key == "authentication"
    assert (Kind.IDENTITY_CONFLICT, ("type_uid",)) in {(f.kind, f.path) for f in report.findings}


def test_explicit_class_key_wins(schema: ResolvedSchema) -> None:
    _, report = parse(base_detection_finding(schema), class_key="authentication")
    assert report.class_key == "authentication"
    assert (Kind.IDENTITY_CONFLICT, ("class_uid",)) in {(f.kind, f.path) for f in report.errors()}


# --- the report ----------------------------------------------------------------------------


def test_report_queries(schema: ResolvedSchema) -> None:
    _, report = parse(multi_error_fixture(schema), version="1.9.0")
    assert not report.ok()
    assert len(report.errors()) == 4
    assert [f.path for f in report.by_kind(Kind.TYPE_MISMATCH)] == [("time",)]
    assert parse(base_detection_finding(schema))[1].ok()


def test_report_to_dict_is_json(schema: ResolvedSchema) -> None:
    event = multi_error_fixture(schema) | {"x": {"nested": [1, 2]}}
    _, report = repair(event, version="1.9.0")
    data = report.to_dict()
    assert json.loads(json.dumps(data)) == data
    assert (data["version"], data["class"], data["ok"]) == ("1.9.0", CLASS, False)
    relocated = next(f for f in data["findings"] if f["kind"] == "relocated")
    assert relocated["repair"] == {
        "action": "relocate",
        "before": ["x"],
        "after": ["unmapped", "x"],
    }


@pytest.mark.parametrize(
    ("kind", "severity"),
    [
        *((k, "error") for k in ("missing_required", "type_mismatch", "enum_out_of_range")),
        *((k, "error") for k in ("sibling_conflict", "identity_conflict", "unknown_attribute")),
        ("constraint_violated", "error"),
        *((k, "warning") for k in ("missing_recommended", "profile_mismatch", "deprecated")),
        ("observable_mismatch", "warning"),
        *((k, "info") for k in ("semantically_empty_enum", "max_nodes_exceeded")),
        *((k, "info") for k in ("filled", "coerced", "relocated")),
    ],
)
def test_severity_is_a_function_of_kind(kind: str, severity: str) -> None:
    assert Finding(kind=Kind(kind), path=(), owner="o", message="m").severity == severity


def test_kind_table_is_complete() -> None:
    assert len(Kind) == 16


def test_report_is_frozen(schema: ResolvedSchema) -> None:
    report = Report(schema=schema, class_key=CLASS, findings=())
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.class_key = "x"  # type: ignore[misc]
