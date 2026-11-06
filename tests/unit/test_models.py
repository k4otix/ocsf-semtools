"""Typed build() and parse() over generated packages: superset, exact and custom-IR models."""

import importlib
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import msgspec
import pytest

from ocsf_toolkit import ConformanceError, Kind, build, parse
from ocsf_toolkit.ir import SchemaIR
from tests.fixtures.packages import import_generated

TIME = 1_700_000_000_000


def _fields(m: ModuleType, **extra: Any) -> dict[str, Any]:
    objects = m.objects
    return {
        "activity_id": m.classes.detection_finding.DetectionFindingActivityId.CREATE,
        "severity_id": m.enums.SeverityId.HIGH,
        "time": TIME,
        "metadata": objects.Metadata(product=objects.Product(name="p", vendor_name="v")),
        "finding_info": {"uid": "f-1", "title": "t"},  # nested dicts are accepted at runtime
        **extra,
    }


def _kinds(found: Any) -> list[tuple[str, tuple[str | int, ...]]]:
    return [(f.kind.value, f.path) for f in found]


# --- build ------------------------------------------------------------------------------------


def test_build_returns_an_instance_with_everything_derived(v1_9_0: ModuleType) -> None:
    """Identity, type_uid and sibling labels are derived on a generated class too (acc. #12)."""
    finding: Any = build(v1_9_0.DetectionFinding, **_fields(v1_9_0))
    assert isinstance(finding, v1_9_0.DetectionFinding)
    assert (finding.type_uid, finding.type_name) == (200401, "Detection Finding: Create")
    assert (finding.activity_name, finding.severity, finding.metadata.version) == (
        "Create",
        "High",
        "1.9.0",
    )
    assert isinstance(finding.finding_info, v1_9_0.objects.FindingInfo)
    wire = msgspec.json.decode(msgspec.json.encode(finding))
    assert {k: wire[k] for k in ("class_uid", "class_name", "category_uid", "category_name")} == {
        "class_uid": 2004,
        "class_name": "Detection Finding",
        "category_uid": 2,
        "category_name": "Findings",
    }  # identity is encoded even though it equals the default


def test_enum_members_are_their_values(v1_9_0: ModuleType) -> None:
    """Members are the schema's values, named from their captions (acc. #5)."""
    severity = v1_9_0.enums.SeverityId
    assert severity.HIGH is severity(4)
    packet = importlib.import_module(f"{v1_9_0.__name__}.objects.packet")
    assert packet.PacketFormatId.N_5VIEWS == 6  # caption "5Views"


def test_build_accepts_datetimes_and_keyword_aliases(v1_9_0: ModuleType) -> None:
    moment = datetime(2026, 11, 3, tzinfo=UTC)
    email = v1_9_0.objects.Email(from_="a@example.com")
    activity: Any = build(
        v1_9_0.EmailActivity,
        activity_id=1,
        direction_id=1,
        severity_id=1,
        time=moment,
        metadata={"product": {"name": "p"}},
        email=email,
        from_="b@example.com",
    )
    assert activity.time == int(moment.timestamp() * 1000)
    wire = msgspec.json.decode(msgspec.json.encode(activity))
    assert (wire["from"], wire["email"]["from"]) == ("b@example.com", "a@example.com")


def test_build_of_a_generated_class_raises_with_the_whole_report(v1_9_0: ModuleType) -> None:
    with pytest.raises(ConformanceError) as caught:
        build(v1_9_0.DetectionFinding, time="soon")
    assert set(_kinds(caught.value.report.errors())) >= {
        ("missing_required", ("activity_id",)),
        ("missing_required", ("finding_info",)),
        ("missing_required", ("metadata",)),
        ("missing_required", ("severity_id",)),
        ("type_mismatch", ("time",)),
    }


def test_a_supplied_type_uid_that_disagrees_is_an_identity_conflict(v1_9_0: ModuleType) -> None:
    """200499 is a valid type_uid, but not for activity 1 (acc. #12)."""
    with pytest.raises(ConformanceError) as caught:
        build(v1_9_0.DetectionFinding, **_fields(v1_9_0, type_uid=200499))
    assert _kinds(caught.value.report.errors()) == [("identity_conflict", ("type_uid",))]


def test_build_of_a_generated_class_warns_per_deprecated_attribute(v1_9_0: ModuleType) -> None:
    with pytest.warns(DeprecationWarning, match="comment"):
        build(v1_9_0.DetectionFinding, **_fields(v1_9_0, comment="old"))


def test_build_refuses_positional_and_foreign_classes(v1_9_0: ModuleType) -> None:
    with pytest.raises(TypeError, match="keyword arguments only"):
        build(v1_9_0.DetectionFinding, 1)
    with pytest.raises(TypeError, match="neither a generated OCSF class"):
        build(dict)  # type: ignore[call-overload]


# --- parse(as_=...) ---------------------------------------------------------------------------


def _wire(m: ModuleType) -> dict[str, Any]:
    return cast(dict[str, Any], msgspec.to_builtins(build(m.DetectionFinding, **_fields(m))))


def test_parse_as_a_class_round_trips(v1_9_0: ModuleType) -> None:
    found, report = parse(_wire(v1_9_0), as_=v1_9_0.DetectionFinding)
    assert found == build(v1_9_0.DetectionFinding, **_fields(v1_9_0))
    assert report.ok()


def test_parse_relocates_what_the_class_has_no_field_for(v1_9_0: ModuleType) -> None:
    event = _wire(v1_9_0) | {"vendor_score": 7}
    found, report = parse(event, as_=v1_9_0.DetectionFinding)
    assert found is not None
    assert found.unmapped == {"vendor_score": 7}
    assert _kinds(report.by_kind(Kind.RELOCATED)) == [("relocated", ("vendor_score",))]
    assert "vendor_score" in event  # the input is not changed


def test_parse_gives_none_when_the_report_has_errors(v1_9_0: ModuleType) -> None:
    event = _wire(v1_9_0)
    event["metadata"]["version"] = "1.8.0"
    found, report = parse(event, as_=v1_9_0.DetectionFinding)
    assert found is None
    assert _kinds(report.errors()) == [("identity_conflict", ("metadata", "version"))]


def test_superset_build_takes_profiles_from_a_typed_metadata(v1_9_0: ModuleType) -> None:
    """A Metadata instance declares profiles as a dict does; found by the typed round-trip property."""
    objects = importlib.import_module(f"{v1_9_0.__name__}.objects")
    metadata = objects.Metadata(product=objects.Product(name="p"), profiles=["cloud", "datetime"])
    with pytest.raises(ConformanceError, match="cloud is required under profiles"):
        build(v1_9_0.DetectionFinding, **_fields(v1_9_0, metadata=metadata))
    finding: Any = build(
        v1_9_0.DetectionFinding, **_fields(v1_9_0, metadata=metadata, cloud=objects.Cloud(provider="x"))
    )
    assert finding.time_dt == "2023-11-14T22:13:20.000Z"


def test_superset_parse_takes_profiles_from_metadata(v1_9_0: ModuleType) -> None:
    """E4 through a superset class: cloud is required only once metadata declares it."""
    event = _wire(v1_9_0)
    event["metadata"]["profiles"] = ["cloud"]
    found, report = parse(event, as_=v1_9_0.DetectionFinding)
    assert found is None
    assert ("missing_required", ("cloud",)) in _kinds(report.errors())
    assert parse(event, as_=v1_9_0.DetectionFinding, profiles=[])[0] is not None


def test_parse_as_fixes_version_and_class(v1_9_0: ModuleType) -> None:
    with pytest.raises(TypeError, match="takes its version and class"):
        parse({}, as_=v1_9_0.DetectionFinding, version="1.9.0")  # type: ignore[call-overload]


# --- exact classes ------------------------------------------------------------------------------


def test_exact_build_requires_what_its_profiles_require(v1_9_0_cloud: ModuleType) -> None:
    with pytest.raises(ConformanceError) as caught:
        build(v1_9_0_cloud.DetectionFinding, **_fields(v1_9_0_cloud))
    assert ("missing_required", ("cloud",)) in _kinds(caught.value.report.errors())
    cloud = {"provider": "aws", "region": "eu-west-1"}
    finding: Any = build(v1_9_0_cloud.DetectionFinding, **_fields(v1_9_0_cloud, cloud=cloud))
    assert finding.metadata.profiles == ["cloud"]


def test_exact_parse_refuses_attributes_outside_its_profiles(v1_9_0_cloud: ModuleType) -> None:
    cloud = {"provider": "aws", "region": "eu-west-1"}
    event = msgspec.to_builtins(build(v1_9_0_cloud.DetectionFinding, **_fields(v1_9_0_cloud, cloud=cloud)))
    event["actor"] = {"app_name": "x"}  # host profile
    found, report = parse(event, as_=v1_9_0_cloud.DetectionFinding)
    assert found is None
    assert report.ok()
    assert _kinds(report.by_kind(Kind.PROFILE_MISMATCH)) == [("profile_mismatch", ("actor",))]
    with pytest.raises(ValueError, match="exactly the profiles"):
        parse(event, as_=v1_9_0_cloud.DetectionFinding, profiles=["host"])


def test_exact_build_refuses_a_profile_outside_its_set(v1_9_0_cloud: ModuleType) -> None:
    cloud = {"provider": "aws", "region": "eu-west-1"}
    with pytest.raises(ConformanceError) as caught:
        build(v1_9_0_cloud.DetectionFinding, **_fields(v1_9_0_cloud, cloud=cloud, actor={}))
    assert _kinds(caught.value.report.by_kind(Kind.PROFILE_MISMATCH)) == [("profile_mismatch", ("actor",))]
    assert "1 conformance error(s)" in str(caught.value)


# --- packages from an IR this library does not ship ---------------------------------------------


def test_a_bundled_ir_is_loaded_from_the_package(synthetic: ModuleType) -> None:
    finding: Any = build(synthetic.Finding, severity_id=0, method=synthetic.classes.finding.FindingMethod.GET)
    assert (finding.method, finding.severity_id) == ("GET", 0)


def test_a_package_from_another_ir_is_refused(tmp_path: Path, synthetic_ir: SchemaIR) -> None:
    module = import_generated(synthetic_ir, "gen_tampered", tmp_path)
    vars(module)["__schema_digest__"] = "0" * 64  # as if generated from a different IR
    with pytest.raises(ValueError, match="regenerate it"):
        build(module.Finding, severity_id=0)


def test_a_package_without_its_ir_is_refused(tmp_path: Path, synthetic_ir: SchemaIR) -> None:
    module = import_generated(synthetic_ir, "gen_no_ir", tmp_path)
    (tmp_path / "gen_no_ir" / "ir.json").unlink()  # as if generated before IRs were bundled
    with pytest.raises(ValueError, match=r"has no ir\.json"):
        build(module.Finding, severity_id=0)
