"""Conformance: spec §1.5's error classes (acc. #14), many problems in one pass (acc. #13), R12."""

import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from ocsf_toolkit.engine import LENIENT, ConformanceError, Report, extract, run
from ocsf_toolkit.ir import Requirement
from ocsf_toolkit.models import build, parse, repair
from ocsf_toolkit.schema import ResolvedSchema, load
from ocsf_toolkit.walk import Kind
from tests.fixtures.events import (
    CLASS,
    base_detection_finding,
    error_fixtures,
    multi_error_fixture,
    profiled_detection_finding,
)


def _check(event: dict[str, Any], **kwargs: Any) -> Report:
    return parse(event, version="1.9.0", **kwargs)[1]


def _problems(report: Report) -> set[tuple[Kind, tuple[str | int, ...]]]:
    """Errors and the informational semantically_empty_enum; warnings are checked separately."""
    return {(f.kind, f.path) for f in report.findings if f.severity != "warning"}


EXPECTED = {
    "E1_sibling_violated": {(Kind.SIBLING_CONFLICT, ("activity_name",))},
    "E2_derived_hand_built": {(Kind.SIBLING_CONFLICT, ("type_name",))},
    "E3_wrong_primitive": {(Kind.TYPE_MISMATCH, ("observables", 0, "type_id"))},
    "E4_profile_gated_absent": {(Kind.MISSING_REQUIRED, ("cloud",))},
    "E5_missing_nested_required": {(Kind.MISSING_REQUIRED, ("metadata", "version"))},
    "E6_semantically_empty_enum": {
        (Kind.SIBLING_CONFLICT, ("severity",)),
        (Kind.SEMANTICALLY_EMPTY_ENUM, ("severity_id",)),
    },
    "E7_enum_out_of_range": {(Kind.ENUM_OUT_OF_RANGE, ("severity_id",))},
    "E8_unknown_field": {(Kind.UNKNOWN_ATTRIBUTE, ("totally_unknown_field",))},
    "E9_wrong_type_uid": {(Kind.IDENTITY_CONFLICT, ("type_uid",))},
}


def test_every_error_class_has_an_expectation(schema: ResolvedSchema) -> None:
    assert error_fixtures(schema).keys() == EXPECTED.keys()


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_each_error_class_is_detected_and_nothing_else(schema: ResolvedSchema, name: str) -> None:
    assert _problems(_check(error_fixtures(schema)[name])) == EXPECTED[name]


def test_the_conformant_bases_have_no_errors(schema: ResolvedSchema) -> None:
    assert _problems(_check(base_detection_finding(schema))) == set()
    profiled = profiled_detection_finding(load("1.9.0", profiles={"host", "security_control"}))
    assert _problems(_check(profiled)) == set()


def test_multi_error_reports_all_four_without_raising(schema: ResolvedSchema) -> None:
    report = _check(multi_error_fixture(schema))
    assert {(f.kind, f.path) for f in report.errors()} == {
        (Kind.SIBLING_CONFLICT, ("activity_name",)),
        (Kind.MISSING_REQUIRED, ("metadata", "version")),
        (Kind.ENUM_OUT_OF_RANGE, ("severity_id",)),
        (Kind.TYPE_MISMATCH, ("time",)),
    }


def test_findings_carry_tier_and_profile_attribution(schema: ResolvedSchema) -> None:
    report = _check(error_fixtures(schema)["E4_profile_gated_absent"])
    [cloud] = [f for f in report.findings if f.path == ("cloud",)]
    assert (cloud.tier, cloud.profiles, cloud.owner, cloud.severity) == (
        Requirement.REQUIRED,
        frozenset({"cloud"}),
        CLASS,
        "error",
    )
    report = _check(base_detection_finding(schema))
    message = next(f for f in report.findings if f.path == ("message",))
    assert (message.kind, message.tier, message.severity) == (
        Kind.MISSING_RECOMMENDED,
        Requirement.RECOMMENDED,
        "warning",
    )


def test_the_same_event_reads_against_the_declared_profiles(schema: ResolvedSchema) -> None:
    event = error_fixtures(schema)["E4_profile_gated_absent"]
    assert (Kind.MISSING_REQUIRED, ("cloud",)) not in _problems(_check(event, profiles=()))


def test_profile_gated_attribute_without_its_profile(schema: ResolvedSchema) -> None:
    event = base_detection_finding(schema) | {"actor": {"user": {"name": "a"}}}
    [finding] = [f for f in _check(event).findings if f.path == ("actor",)]
    assert (finding.kind, finding.severity, finding.expected) == (
        Kind.PROFILE_MISMATCH,
        "warning",
        ["host"],
    )


def test_an_inactive_profile_attribute_is_still_type_checked(schema: ResolvedSchema) -> None:
    event = base_detection_finding(schema) | {"verdict_id": "yes", "cloud": {"provider": 5}}
    found = {(f.kind, f.path) for f in _check(event).findings if f.path[:1] in (("verdict_id",), ("cloud",))}
    assert found == {
        (Kind.PROFILE_MISMATCH, ("verdict_id",)),
        (Kind.TYPE_MISMATCH, ("verdict_id",)),
        (Kind.PROFILE_MISMATCH, ("cloud",)),
        (Kind.TYPE_MISMATCH, ("cloud", "provider")),
        (Kind.MISSING_RECOMMENDED, ("cloud", "region")),  # the object is walked like any other
    }


def test_nested_and_indexed_paths(schema: ResolvedSchema) -> None:
    event = base_detection_finding(schema)
    event["metadata"]["product"]["nope"] = 1
    event["observables"] = [{"name": "a", "type_id": 4}, {"name": 5, "type_id": 4}]
    event["finding_info"]["types"] = ["ok", 7]
    assert _problems(_check(event)) == {
        (Kind.UNKNOWN_ATTRIBUTE, ("metadata", "product", "nope")),
        (Kind.TYPE_MISMATCH, ("observables", 1, "name")),
        (Kind.TYPE_MISMATCH, ("finding_info", "types", 1)),
    }


@pytest.mark.parametrize(
    ("attr", "value"),
    [("metadata", "x"), ("observables", {"name": "a"}), ("finding_info", [{"uid": "u"}])],
)
def test_object_shape_mismatches(schema: ResolvedSchema, attr: str, value: object) -> None:
    event = base_detection_finding(schema) | {attr: value}
    assert (Kind.TYPE_MISMATCH, (attr,)) in _problems(_check(event))


def test_numeric_roots(schema: ResolvedSchema) -> None:
    event = base_detection_finding(schema) | {"confidence_score": True, "risk_score": 2.0}
    event["device"] = {"location": {"lat": 1, "long": 2.5}, "type_id": 2}
    problems = _problems(_check(event))
    assert (Kind.TYPE_MISMATCH, ("confidence_score",)) in problems  # a bool is not an integer
    assert (Kind.TYPE_MISMATCH, ("risk_score",)) in problems  # nor, unrepaired, is 2.0
    mismatched = {path for kind, path in problems if kind is Kind.TYPE_MISMATCH}
    assert not any(path[:2] == ("device", "location") for path in mismatched)  # 1 is a float


def test_constraints(schema: ResolvedSchema) -> None:
    event = base_detection_finding(schema)
    event["metadata"]["product"] = {"vendor_name": "v"}
    [finding] = [f for f in _check(event).findings if f.kind is Kind.CONSTRAINT_VIOLATED]
    assert (finding.path, finding.owner, finding.expected) == (
        ("metadata", "product"),
        "product",
        ("name", "uid"),
    )


def test_deprecated_attribute_is_a_warning(schema: ResolvedSchema) -> None:
    event = base_detection_finding(schema) | {"comment": "old"}
    [finding] = [f for f in _check(event).findings if f.path == ("comment",)]
    assert (finding.kind, finding.severity, finding.expected) == (
        Kind.DEPRECATED,
        "warning",
        ("notes",),
    )


def test_null_reads_as_absent(schema: ResolvedSchema) -> None:
    event = base_detection_finding(schema) | {"message": None, "time": None}
    report = _check(event)
    assert _problems(report) == {(Kind.MISSING_REQUIRED, ("time",))}
    assert (Kind.MISSING_RECOMMENDED, ("message",)) in {(f.kind, f.path) for f in report.findings}


def test_a_derived_value_with_an_absent_source_is_not_reported(schema: ResolvedSchema) -> None:
    event = base_detection_finding(schema)
    for attr in ("activity_id", "activity_name", "type_uid", "type_name"):
        del event[attr]
    assert _problems(_check(event)) == {(Kind.MISSING_REQUIRED, ("activity_id",))}


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"class_uid": 9999}, {(Kind.ENUM_OUT_OF_RANGE, ("class_uid",))}),
        ({"class_uid": None, "type_uid": 1}, {(Kind.ENUM_OUT_OF_RANGE, ("type_uid",))}),
        ({"class_uid": None, "type_uid": None}, {(Kind.MISSING_REQUIRED, ("class_uid",))}),
    ],
)
def test_an_unknown_class_is_still_walked_as_base_event(
    schema: ResolvedSchema, change: dict[str, Any], expected: set[tuple[Kind, tuple[str]]]
) -> None:
    event = base_detection_finding(schema) | change
    report = _check(event)
    assert report.class_key == "base_event"
    problems = _problems(report)
    assert expected <= problems
    # every detection_finding attribute is still visited and reported by path
    assert (Kind.UNKNOWN_ATTRIBUTE, ("finding_info",)) in problems


def test_lenient_never_changes_the_event(schema: ResolvedSchema) -> None:
    event = multi_error_fixture(schema)
    before = repr(event)
    out, _ = run(event, schema, CLASS, LENIENT)
    assert out is event
    assert repr(event) == before


# --- numbers: finite floats, 64-bit integers, values too large to print ------------------------


def _location(**fields: Any) -> dict[str, Any]:
    return {"device": {"type_id": 1, "hostname": "h", "location": fields}}


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_a_non_finite_float_is_a_type_mismatch(schema: ResolvedSchema, value: float) -> None:
    report = _check(base_detection_finding(schema) | _location(lat=value))
    [finding] = report.by_kind(Kind.TYPE_MISMATCH)
    assert finding.path == ("device", "location", "lat")
    assert "expected a finite float_t" in finding.message
    json.dumps(report.to_dict(), allow_nan=False)  # reported values are valid JSON too


@pytest.mark.parametrize(
    ("attr", "value", "ok"),
    [
        ("duration", 2**63 - 1, True),
        ("duration", 2**63, False),
        ("duration", -(2**63), True),
        ("duration", -(2**63) - 1, False),
        ("raw_data_size", 2**70, False),
    ],
)
def test_integers_must_fit_in_64_bits(schema: ResolvedSchema, attr: str, value: int, ok: bool) -> None:
    found = _check(base_detection_finding(schema) | {attr: value}).by_kind(Kind.TYPE_MISMATCH)
    assert (found == ()) is ok
    if not ok:
        assert "within the signed 64-bit range" in found[0].message


def test_coercion_stays_within_64_bits(schema: ResolvedSchema) -> None:
    _, report = repair(base_detection_finding(schema) | {"duration": "9999999999999999999"})
    assert [f.path for f in report.by_kind(Kind.TYPE_MISMATCH)] == [("duration",)]


def test_an_integer_too_long_to_print_is_still_reported(schema: ResolvedSchema) -> None:
    report = _check(base_detection_finding(schema) | {"duration": 10**5000, "bogus": [10**5000]})
    assert {f.kind for f in report.errors()} == {Kind.TYPE_MISMATCH, Kind.UNKNOWN_ATTRIBUTE}
    assert "<int too large to show>" in report.by_kind(Kind.TYPE_MISMATCH)[0].message
    json.dumps(report.to_dict(), allow_nan=False)


def test_an_observable_past_64_bits_has_no_value(schema: ResolvedSchema) -> None:
    network = load("1.9.0").classes["network_activity"]
    found, _ = extract({"src_endpoint": {"port": 10**5000}}, schema, network.key)
    assert [(o.name, o.value) for o in found] == [("src_endpoint", None)]  # the port has no usable value


def test_report_json_survives_cycles_and_datetimes(schema: ResolvedSchema) -> None:
    loop: dict[str, Any] = {}
    loop["self"] = loop
    naive = datetime(2025, 1, 1)
    report = _check(base_detection_finding(schema) | {"bogus": loop, "time": naive})
    data = report.to_dict()
    json.dumps(data, allow_nan=False)
    assert {f["path"][0]: f["actual"] for f in data["findings"] if f["kind"] != "missing_recommended"} == {
        "bogus": "{'self': {...}}",
        "time": "2025-01-01T00:00:00",
    }


def test_a_datetime_with_no_utc_instant_is_a_type_mismatch(schema: ResolvedSchema) -> None:
    early = datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=1)))
    fields = {k: v for k, v in base_detection_finding(schema).items() if k not in ("class_uid", "category_uid")}
    with pytest.raises(ConformanceError) as caught:
        build((schema, CLASS), **(fields | {"time": early}))
    assert [f.path for f in caught.value.report.errors()] == [("time",)]
