"""The engine: policies differ only in behaviour; build raises iff errors; repair records all."""

import copy
import dataclasses
import inspect
import json
import pickle
import warnings
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from ocsf_semtools.engine import (
    LENIENT,
    MAX_NODES,
    REPAIR,
    STRICT,
    ConformanceError,
    Policy,
    extract,
    run,
    select,
)
from ocsf_semtools.models import build, parse, repair
from ocsf_semtools.schema import ResolvedSchema, load, resolve
from ocsf_semtools.walk import (
    _SEVERITY,  # pyright: ignore[reportPrivateUsage]
    Finding,
    Kind,
    RepairAction,
)
from tests.fixtures.events import (
    CLASS,
    T0,
    base_detection_finding,
    error_fixtures,
    minimal_fields,
    multi_error_fixture,
)


def test_the_three_presets() -> None:
    assert Policy(fill=True, overwrite=False, coerce=False, relocate=False) == STRICT
    assert Policy(fill=False, overwrite=False, coerce=False, relocate=False) == LENIENT
    assert Policy(fill=True, overwrite=True, coerce=True, relocate=True) == REPAIR


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


# --- build ----------------------------------------------------------------------------------


def test_build_by_reference_raises_with_the_whole_report(schema: ResolvedSchema) -> None:
    with pytest.raises(ConformanceError) as raised:
        build((schema, CLASS), **minimal_fields(severity_id=42, time="yesterday", bogus=1))
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
        event = build((schema, CLASS), **minimal_fields(comment="old"))
    assert event["comment"] == "old"


def test_build_by_reference_warns_per_deprecated_attribute(schema: ResolvedSchema) -> None:
    with pytest.warns(DeprecationWarning, match="comment is deprecated"):
        build((schema, CLASS), **minimal_fields(comment="old"))


def test_build_never_mutates_nested_caller_dicts(schema: ResolvedSchema) -> None:
    metadata: dict[str, Any] = {"product": {"name": "p"}}
    build((schema, CLASS), **(minimal_fields() | {"metadata": metadata}))
    assert metadata == {"product": {"name": "p"}}


# --- repair: every change is a finding ------------------------------------------------------


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


@pytest.mark.parametrize("identity", [{"class_uid": "2004"}, {"class_uid": 2004.0}, {"type_uid": "200401"}])
def test_repair_finds_the_class_from_a_coerced_identity(schema: ResolvedSchema, identity: dict[str, Any]) -> None:
    """The class is read from the identity as repair coerces it; found by the repair property."""
    base = base_detection_finding(schema)
    event = {k: v for k, v in base.items() if k not in ("class_uid", "type_uid")} | identity
    out, report = repair(event)
    assert report.class_key == CLASS
    assert report.errors() == ()
    assert (out["class_uid"], out["type_uid"]) == (2004, 200401)


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


def test_repair_checks_a_migrated_value_as_its_successor() -> None:
    schema = load("1.9.0")
    cookie = {"name": "n", "value": "v", "http_only": "false"}  # is_http_only supersedes it
    event: dict[str, Any] = {"class_uid": 4002, "activity_id": 1, "http_cookies": [cookie]}
    out, _ = run(event, schema, None, REPAIR)
    assert out["http_cookies"] == [{"name": "n", "value": "v", "is_http_only": False}]


def test_repair_walks_below_a_migrated_object() -> None:
    schema = load("1.9.0")
    moved = {"group": {"uid": "g", "uid_numeric": "7"}}  # entity_result -> updated_entity
    event: dict[str, Any] = {"class_uid": 3004, "activity_id": 1, "entity_result": moved}
    out, _ = run(event, schema, None, REPAIR)
    assert out["updated_entity"] == {"group": {"uid": "g", "uid_numeric": 7}}


def test_repair_does_not_migrate_across_types(schema: ResolvedSchema) -> None:
    out, report = repair(base_detection_finding(schema) | {"comment": "old"})
    assert out["comment"] == "old"  # notes is an array of note objects
    assert next(f for f in report.findings if f.path == ("comment",)).repair is None


# --- parse: schema and class selection ------------------------------------------------------


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


# --- the report -----------------------------------------------------------------------------


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


@pytest.mark.parametrize("kind", list(Kind))
def test_a_repaired_finding_is_info_whatever_its_kind(kind: Kind) -> None:
    finding = Finding(kind=kind, path=(), owner="o", message="m")
    repaired = dataclasses.replace(finding, repair=RepairAction(action="fill", before=None, after=1))
    assert repaired.severity == "info"


def test_the_severity_table() -> None:
    errors = {k for k, s in _SEVERITY.items() if s == "error"}
    warnings_ = {k for k, s in _SEVERITY.items() if s == "warning"}
    assert errors == {
        Kind.MISSING_REQUIRED,
        Kind.TYPE_MISMATCH,
        Kind.ENUM_OUT_OF_RANGE,
        Kind.SIBLING_CONFLICT,
        Kind.IDENTITY_CONFLICT,
        Kind.CONSTRAINT_VIOLATED,
        Kind.UNKNOWN_ATTRIBUTE,
        Kind.REPEATED_NODE,
    }
    assert warnings_ == {Kind.MISSING_RECOMMENDED, Kind.PROFILE_MISMATCH, Kind.DEPRECATED, Kind.OBSERVABLE_MISMATCH}


def test_every_kind_has_a_severity() -> None:
    # The table names every warning and error; the rest are info by design.
    info = {Kind.FILLED, Kind.COERCED, Kind.RELOCATED, Kind.SEMANTICALLY_EMPTY_ENUM, Kind.MAX_NODES_EXCEEDED}
    assert set(Kind) - _SEVERITY.keys() == info


# --- hostile structure: depth, cycles, shared nodes, size, non-dict input -------------------


def _chain(depth: int) -> dict[str, Any]:
    process: dict[str, Any] = {"pid": 0}
    for i in range(1, depth):
        process = {"pid": i, "parent_process": process}
    return process


@pytest.mark.parametrize("entry", ["build", "parse", "repair"])
def test_nesting_deeper_than_the_recursion_limit(schema: ResolvedSchema, entry: str) -> None:
    event = base_detection_finding(schema) | {"actor": {"process": _chain(3000)}}
    if entry == "build":
        fields = {k: v for k, v in event.items() if k not in ("class_uid", "category_uid", "type_uid")}
        built = build((schema, CLASS), **fields)
        assert built["actor"]["process"]["pid"] == 2999
    else:
        _, report = (parse if entry == "parse" else repair)(event)
        assert report.ok()


@pytest.mark.parametrize("entry", [parse, repair])
def test_a_cycle_is_one_finding_and_ends(schema: ResolvedSchema, entry: Any) -> None:
    event = base_detection_finding(schema)
    process: dict[str, Any] = {"pid": 1}
    process["parent_process"] = process
    event["actor"] = {"process": process}
    _, report = entry(event)
    [finding] = report.by_kind(Kind.REPEATED_NODE)
    assert (finding.path, finding.expected, finding.severity) == (
        ("actor", "process", "parent_process"),
        ("actor", "process"),
        "error",
    )


def test_one_dict_under_two_paths_is_walked_once(schema: ResolvedSchema) -> None:
    shared = {"name": "e", "bogus": 1}
    event = base_detection_finding(schema) | {"evidences": [shared, shared]}
    out, report = repair(event)
    assert {(f.kind, f.path) for f in report.findings if f.kind in (Kind.RELOCATED, Kind.REPEATED_NODE)} == {
        (Kind.RELOCATED, ("evidences", 0, "bogus")),
        (Kind.REPEATED_NODE, ("evidences", 1)),
    }
    assert out["evidences"][0] is out["evidences"][1]  # the copy keeps the shape the caller passed


def test_every_entry_point_has_the_default_budget(schema: ResolvedSchema) -> None:
    for entry in (parse, repair, extract):
        assert inspect.signature(entry).parameters["max_nodes"].default == MAX_NODES
    event = base_detection_finding(schema) | {"evidences": [{"name": str(i)} for i in range(50)]}
    for entry in (parse, repair):
        _, report = entry(event, max_nodes=40)
        assert len(report.by_kind(Kind.MAX_NODES_EXCEEDED)) == 1


@pytest.mark.parametrize("event", [["x"], "x", None, 1])
def test_an_event_that_is_not_a_dict_is_a_type_error(schema: ResolvedSchema, event: Any) -> None:
    for call in (
        lambda: parse(event, version="1.9.0"),
        lambda: repair(event, version="1.9.0"),
        lambda: extract(event, schema),
    ):
        with pytest.raises(TypeError, match="an event is a dict"):
            call()


# --- extensions: from metadata.extensions, or a schema passed in ----------------------------


def _prefetch(**metadata: Any) -> dict[str, Any]:
    return {
        "class_uid": 205019,
        "activity_id": 1,
        "severity_id": 1,
        "time": T0,
        "query_result_id": 1,
        "name": "x.pf",
        "metadata": {"version": "1.9.0", "product": {"name": "p", "vendor_name": "v"}, **metadata},
    }


def test_an_extension_class_is_found_through_metadata_extensions() -> None:
    out, report = repair(_prefetch(extensions=[{"name": "win", "uid": "2", "version": "1.9.0"}]))
    assert (report.class_key, report.schema.extensions) == ("win/prefetch_query", frozenset({"win"}))
    assert report.ok(), [f.message for f in report.errors()]
    assert out["type_uid"] == 20501901


def test_unshipped_and_malformed_extension_entries_are_skipped_and_reported() -> None:
    declared = [{"name": "win", "uid": "2", "version": "1.9.0"}, {"name": "nope", "uid": "9", "version": "1"}, "x"]
    _, report = parse(_prefetch(extensions=declared))
    assert report.schema.extensions == frozenset({"win"})
    assert report.class_key == "win/prefetch_query"
    assert "metadata.extensions[2] is str 'x'; expected an extension" in [f.message for f in report.errors()]


def test_without_metadata_extensions_the_class_is_unknown() -> None:
    _, report = parse(_prefetch())
    assert report.class_key == "base_event"
    assert ("class_uid",) in {f.path for f in report.errors()}


def test_parse_and_repair_take_a_schema() -> None:
    schema = load("1.9.0", extensions=["win"])
    for entry in (parse, repair):
        _, report = entry(_prefetch(), schema=schema)
        assert (report.class_key, report.schema) == ("win/prefetch_query", schema)


@pytest.mark.parametrize("extra", [{"version": "1.9.0"}, {"profiles": []}, {"as_": dict}])
def test_schema_excludes_version_profiles_and_as(extra: dict[str, Any]) -> None:
    with pytest.raises(TypeError):
        parse(_prefetch(), schema=load("1.9.0"), **extra)


def test_schema_must_be_a_resolved_schema() -> None:
    with pytest.raises(TypeError, match="takes a ResolvedSchema"):
        repair(_prefetch(), schema="1.9.0")  # type: ignore[arg-type]


# --- pickling: schemas, reports and the exception cross process boundaries ------------------


def test_a_shipped_schema_pickles_by_reference(schema: ResolvedSchema) -> None:
    cloud = load("1.9.0", profiles=["cloud"], extensions=["win"])
    assert pickle.loads(pickle.dumps(cloud)) is cloud  # load()'s cache hands back the same schema
    assert len(pickle.dumps(schema)) < 1000


def test_a_custom_schema_pickles_by_its_ir(schema: ResolvedSchema) -> None:
    custom = resolve(dataclasses.replace(schema.ir, version="1.9.0-acme"))
    again = pickle.loads(pickle.dumps(custom))
    assert (again, again.ir) == (custom, custom.ir)


def test_reports_and_conformance_errors_pickle(schema: ResolvedSchema) -> None:
    _, report = parse(multi_error_fixture(schema), version="1.9.0")
    assert pickle.loads(pickle.dumps(report)) == report
    with pytest.raises(ConformanceError) as caught:
        build((schema, CLASS))
    again = pickle.loads(pickle.dumps(caught.value))
    assert (str(again), again.report, again.fatal) == (str(caught.value), caught.value.report, caught.value.fatal)


def test_by_kind_takes_the_kind_or_its_name(schema: ResolvedSchema) -> None:
    _, report = parse(multi_error_fixture(schema), version="1.9.0")
    assert report.by_kind(Kind.SIBLING_CONFLICT) == report.by_kind("sibling_conflict")  # type: ignore[arg-type]
    assert report.by_kind(Kind.SIBLING_CONFLICT)


def test_observables_see_values_as_repair_left_them() -> None:
    entry = {"name": "src_endpoint.port", "type_id": 11, "value": "443"}
    event = {"class_uid": 4001, "src_endpoint": {"port": 443.0}, "observables": [entry]}
    out, report = repair(event, version="1.9.0")
    assert out["src_endpoint"]["port"] == 443
    assert report.by_kind(Kind.OBSERVABLE_MISMATCH) == ()


def test_a_deprecated_value_moves_only_into_an_absent_successor() -> None:
    def email(**fields: Any) -> dict[str, Any]:
        return {"class_uid": 4009, "email": fields}

    out, report = repair(email(smtp_from=123), version="1.9.0")
    assert out["email"] == {"from": 123}
    assert ("email", "from") in {f.path for f in report.by_kind(Kind.TYPE_MISMATCH)}  # checked where it landed
    out, report = repair(email(**{"from": None, "smtp_from": 123}), version="1.9.0")
    assert out["email"] == {"from": None, "smtp_from": 123}
    assert ("email", "smtp_from") in {f.path for f in report.by_kind(Kind.TYPE_MISMATCH)}


def test_unmapped_null_receives_relocations(schema: ResolvedSchema) -> None:
    out, report = repair(base_detection_finding(schema) | {"unmapped": None, "vendor": 1})
    assert out["unmapped"] == {"vendor": 1}
    assert [f.path for f in report.by_kind(Kind.RELOCATED)] == [("vendor",)]


@pytest.mark.parametrize("ref", [(), ("1.9.0", CLASS), (None, CLASS, 1)])
def test_a_malformed_class_reference_is_a_type_error(ref: tuple[object, ...]) -> None:
    with pytest.raises(TypeError, match=r"a class reference is \(ResolvedSchema, class key\)"):
        build(cast(Any, ref))


def test_an_unknown_class_key_names_the_schema(schema: ResolvedSchema) -> None:
    with pytest.raises(KeyError, match=r"no class 'nope' in OCSF 1\.9\.0"):
        parse(base_detection_finding(schema), class_key="nope")
