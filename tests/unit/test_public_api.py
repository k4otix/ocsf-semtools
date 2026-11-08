"""The public surface is exactly ``__all__``; a name enters it only with tests behind it."""

import pytest

import ocsf_semtools
from tests.conftest import DATA_DIR


def test_all_is_exact() -> None:
    assert sorted(ocsf_semtools.__all__) == [
        "ConformanceError",
        "Finding",
        "Kind",
        "Observable",
        "RepairAction",
        "Report",
        "ResolvedAttribute",
        "ResolvedItem",
        "ResolvedSchema",
        "SchemaDiff",
        "SchemaIR",
        "__version__",
        "build",
        "diff",
        "export_ir",
        "extract",
        "generate",
        "load",
        "load_ir",
        "merge",
        "parse",
        "repair",
        "resolve",
    ]


def test_load_ir_reads_a_shipped_ir() -> None:
    ir = ocsf_semtools.load_ir(DATA_DIR / "ir-1.9.0.json")
    assert isinstance(ir, ocsf_semtools.SchemaIR)
    assert (ir.version, ir.compile_version, ir.provenance.ref) == ("1.9.0", 1, "1.9.0")


def test_load_and_resolve_return_resolved_schemas() -> None:
    schema = ocsf_semtools.load("1.9.0", profiles={"cloud"})
    assert isinstance(schema, ocsf_semtools.ResolvedSchema)
    assert ocsf_semtools.resolve(schema.ir, profiles={"cloud"}) == schema
    item = schema.classes["detection_finding"]
    assert isinstance(item, ocsf_semtools.ResolvedItem)
    assert isinstance(item.attributes["cloud"], ocsf_semtools.ResolvedAttribute)


def test_build_parse_and_repair_share_one_report_type() -> None:
    schema = ocsf_semtools.load("1.9.0")
    fields = {
        "activity_id": 1,
        "severity_id": 4,
        "time": 1,
        "metadata": {"product": {"name": "p"}},
        "finding_info": {"uid": "f"},
    }
    event = ocsf_semtools.build((schema, "detection_finding"), **fields)
    assert event["type_uid"] == 200401
    for entry in (ocsf_semtools.parse, ocsf_semtools.repair):
        _, report = entry(event)
        assert isinstance(report, ocsf_semtools.Report)
        assert report.ok()
        assert all(isinstance(f, ocsf_semtools.Finding) for f in report.findings)


def test_the_one_exception() -> None:
    assert ocsf_semtools.Kind("sibling_conflict") is ocsf_semtools.Kind.SIBLING_CONFLICT
    schema = ocsf_semtools.load("1.9.0")
    with pytest.raises(ocsf_semtools.ConformanceError) as raised:
        ocsf_semtools.build((schema, "detection_finding"))
    assert isinstance(raised.value.report, ocsf_semtools.Report)


def test_extract_and_merge() -> None:
    schema = ocsf_semtools.load("1.9.0")
    event = {"class_uid": 2004, "device": {"hostname": "ws-01"}}
    found, report = ocsf_semtools.extract(event, schema)
    assert all(isinstance(o, ocsf_semtools.Observable) for o in found)
    assert isinstance(report, ocsf_semtools.Report)
    merged = ocsf_semtools.merge(event, found)
    assert {"name": "device.hostname", "type_id": 1, "type": "Hostname", "value": "ws-01"} in (merged["observables"])


def test_diff_compares_shipped_irs() -> None:
    result = ocsf_semtools.diff(ocsf_semtools.load("1.1.0").ir, ocsf_semtools.load("1.3.0").ir)
    assert isinstance(result, ocsf_semtools.SchemaDiff)
    assert sum(c.kind == "renamed" for c in result.changes) == 3
