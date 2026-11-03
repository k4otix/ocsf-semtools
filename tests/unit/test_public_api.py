"""The public surface is exactly ``__all__``; a name enters it only with tests behind it."""

import pytest

import ocsf_toolkit
from tests.conftest import DATA_DIR


def test_all_is_exact() -> None:
    assert sorted(ocsf_toolkit.__all__) == [
        "ConformanceError",
        "Finding",
        "Kind",
        "LENIENT",
        "Observable",
        "Policy",
        "REPAIR",
        "Report",
        "ResolvedAttribute",
        "ResolvedItem",
        "ResolvedSchema",
        "STRICT",
        "SchemaIR",
        "__version__",
        "build",
        "extract",
        "load",
        "load_ir",
        "merge",
        "parse",
        "repair",
        "resolve",
    ]


def test_load_ir_reads_a_shipped_ir() -> None:
    ir = ocsf_toolkit.load_ir(DATA_DIR / "ir-1.9.0.json")
    assert isinstance(ir, ocsf_toolkit.SchemaIR)
    assert (ir.version, ir.compile_version, ir.provenance.ref) == ("1.9.0", 1, "1.9.0")


def test_load_and_resolve_return_resolved_schemas() -> None:
    schema = ocsf_toolkit.load("1.9.0", profiles={"cloud"})
    assert isinstance(schema, ocsf_toolkit.ResolvedSchema)
    assert ocsf_toolkit.resolve(schema.ir, profiles={"cloud"}) == schema
    item = schema.classes["detection_finding"]
    assert isinstance(item, ocsf_toolkit.ResolvedItem)
    assert isinstance(item.attributes["cloud"], ocsf_toolkit.ResolvedAttribute)


def test_build_parse_and_repair_share_one_report_type() -> None:
    schema = ocsf_toolkit.load("1.9.0")
    fields = {
        "activity_id": 1,
        "severity_id": 4,
        "time": 1,
        "metadata": {"product": {"name": "p"}},
        "finding_info": {"uid": "f"},
    }
    event = ocsf_toolkit.build((schema, "detection_finding"), **fields)
    assert event["type_uid"] == 200401
    for entry in (ocsf_toolkit.parse, ocsf_toolkit.repair):
        _, report = entry(event)
        assert isinstance(report, ocsf_toolkit.Report)
        assert report.ok()
        assert all(isinstance(f, ocsf_toolkit.Finding) for f in report.findings)


def test_policies_and_the_one_exception() -> None:
    presets = (ocsf_toolkit.STRICT, ocsf_toolkit.LENIENT, ocsf_toolkit.REPAIR)
    assert all(isinstance(p, ocsf_toolkit.Policy) for p in presets)
    assert ocsf_toolkit.Kind("sibling_conflict") is ocsf_toolkit.Kind.SIBLING_CONFLICT
    schema = ocsf_toolkit.load("1.9.0")
    with pytest.raises(ocsf_toolkit.ConformanceError) as raised:
        ocsf_toolkit.build((schema, "detection_finding"))
    assert isinstance(raised.value.report, ocsf_toolkit.Report)


def test_extract_and_merge() -> None:
    schema = ocsf_toolkit.load("1.9.0")
    event = {"class_uid": 2004, "device": {"hostname": "ws-01"}}
    found, report = ocsf_toolkit.extract(event, schema)
    assert all(isinstance(o, ocsf_toolkit.Observable) for o in found)
    assert isinstance(report, ocsf_toolkit.Report)
    merged = ocsf_toolkit.merge(event, found)
    assert {"name": "device.hostname", "type_id": 1, "type": "Hostname", "value": "ws-01"} in (
        merged["observables"]
    )
