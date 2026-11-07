"""ResolvedSchema: the resolution rule, the derived tables, and schema access without a model."""

import dataclasses
import subprocess
import sys
import textwrap
from typing import Any

import pytest

from ocsf_toolkit.cli import lock
from ocsf_toolkit.export import digest
from ocsf_toolkit.ir import Attribute, Constraint, Requirement, Root, SchemaIR
from ocsf_toolkit.loader import load_compiled
from ocsf_toolkit.schema import (
    ClassIdentity,
    ResolvedAttribute,
    ResolvedSchema,
    load,
    resolve,
    shipped_versions,
)
from tests.conftest import LOCKFILE, ROOT
from tests.fixtures.compiled import PROVENANCE, compiled, compiled_bytes


def _ir(data: dict[str, Any] | None = None) -> SchemaIR:
    return load_compiled(compiled_bytes(data), PROVENANCE)


@pytest.fixture(scope="module")
def ir() -> SchemaIR:
    return _ir()


@pytest.fixture(scope="module")
def base() -> ResolvedSchema:
    return load("1.9.0")


@pytest.fixture(scope="module")
def everything(base: ResolvedSchema) -> ResolvedSchema:
    return load("1.9.0", profiles=base.ir.profiles, extensions=base.ir.extensions)


# --- the resolution rule on the synthetic schema ---------------------------------------------


def test_profile_gated_attribute_needs_its_profile(ir: SchemaIR) -> None:
    assert "cloud" not in resolve(ir).classes["finding"].attributes
    cloud = resolve(ir, profiles={"cloud"}).classes["finding"].attributes["cloud"]
    assert (cloud.requirement, cloud.gated_by, cloud.owner) == (
        Requirement.REQUIRED,
        frozenset({"cloud"}),
        "finding",
    )


def test_extension_gates_attributes_items_and_types(ir: SchemaIR) -> None:
    plain = resolve(ir)
    assert "hosted" not in plain.objects["cloud"].attributes
    assert "win/reg_key" not in plain.objects
    win = resolve(ir, extensions={"win"})
    assert win.objects["cloud"].attributes["hosted"].extension == "win"
    assert win.objects["win/reg_key"].name == "reg_key"


def test_resolved_attribute_carries_its_root(ir: SchemaIR) -> None:
    attributes = resolve(ir, profiles={"cloud"}).classes["finding"].attributes
    assert attributes["port"].root is Root.INTEGER  # port_t -> integer_t
    assert attributes["method"].root is Root.STRING
    assert attributes["cloud"].root is None  # object_t
    assert attributes["severity_id"].gated_by == frozenset()


def test_resolved_attribute_is_attribute_plus_context() -> None:
    names = {f.name for f in dataclasses.fields(ResolvedAttribute)}
    assert names - {f.name for f in dataclasses.fields(Attribute)} == {"gated_by", "root", "owner"}


def test_identity_is_set_on_classes_only(ir: SchemaIR) -> None:
    schema = resolve(ir)
    assert schema.classes["finding"].identity == ClassIdentity(
        class_uid=2001, class_name="Finding", category_uid=2, category_name="Findings"
    )
    assert schema.classes["base_event"].identity == ClassIdentity(
        class_uid=0, class_name="Base Event", category_uid=0, category_name=None
    )
    assert schema.objects["cloud"].identity is None


def test_constraint_members_gated_out_are_dropped() -> None:
    data = compiled()
    data["classes"]["finding"]["constraints"] = {
        "just_one": ["cloud", "cloud.ip", "method"],
        "at_least_one": ["cloud.ip", "severity"],  # severity is not an attribute: kept as declared
    }
    ir = _ir(data)
    assert resolve(ir).classes["finding"].constraints == (
        Constraint(kind="at_least_one", attributes=("severity",)),
        Constraint(kind="just_one", attributes=("method",)),
    )
    assert resolve(ir, profiles={"cloud"}).classes["finding"].constraints == ir.classes["finding"].constraints


def test_constraint_with_every_member_gated_out_is_dropped() -> None:
    data = compiled()
    data["classes"]["finding"]["constraints"] = {"at_least_one": ["cloud"]}
    assert resolve(_ir(data)).classes["finding"].constraints == ()


def test_siblings_need_both_attributes_present(ir: SchemaIR) -> None:
    data = compiled()
    data["classes"]["finding"]["attributes"]["severity"] = {"caption": "Sev", "type": "string_t"}
    siblings = resolve(_ir(data)).classes["finding"].siblings
    assert set(siblings) == {"severity_id"}
    assert siblings["severity_id"].label == "severity"
    assert siblings["severity_id"].enum is not None
    assert resolve(ir).classes["finding"].siblings == {}  # the label attribute is absent


def test_markers_follow_precedence() -> None:
    data = compiled()
    dictionary = data["dictionary"]["attributes"]
    dictionary["method"] = {"caption": "Method", "type": "string_t", "observable": 7}
    dictionary["hosted"] = {"caption": "Hosted", "type": "string_t", "observable": 8}
    dictionary["win/hosted"]["observable"] = 9
    finding = data["classes"]["finding"]["attributes"]
    finding["source_ip"] = {"caption": "Source IP", "type": "ip_t"}
    finding["local"] = {"caption": "Local", "type": "ip_t", "observable": 5}
    markers = resolve(_ir(data), profiles={"cloud"}, extensions={"win"}).observable_markers
    assert markers[("finding", "local")] == 5  # attribute-local beats type
    assert markers[("finding", "method")] == 7  # dictionary attribute
    assert markers[("cloud", "hosted")] == 9  # the "<ext>/<name>" dictionary entry is tried first
    assert markers[("finding", "source_ip")] == 2  # type
    assert markers[("finding", "cloud")] == 30  # object
    assert ("finding", "severity_id") not in markers


def test_markers_cover_only_present_attributes(ir: SchemaIR) -> None:
    assert ("finding", "cloud") not in resolve(ir).observable_markers


# --- inputs ----------------------------------------------------------------------------------


def test_unknown_profile_or_extension_raises_keyerror(ir: SchemaIR) -> None:
    with pytest.raises(KeyError, match="unknown profiles"):
        resolve(ir, profiles={"nope"})
    with pytest.raises(KeyError, match="unknown extensions"):
        resolve(ir, extensions={"nope"})


def test_extension_profile_needs_its_extension_active(base: ResolvedSchema) -> None:
    with pytest.raises(KeyError, match=r"unknown profiles \['linux/linux_users'\]"):
        load("1.9.0", profiles={"linux/linux_users"})
    linux = load("1.9.0", profiles={"linux/linux_users"}, extensions={"linux"})
    assert linux.objects["process"].attributes["auid"].gated_by == {"linux/linux_users"}
    assert "auid" not in base.objects["process"].attributes


def test_a_bare_string_is_not_a_profile_set() -> None:
    with pytest.raises(TypeError, match="not the string 'cloud'"):
        load("1.9.0", profiles="cloud")


@pytest.mark.parametrize("version", ["0.0.1", "1.9", "../1.9.0", "1.9.0/../1.9.0"])
def test_unshipped_version_raises_valueerror(version: str) -> None:
    with pytest.raises(ValueError, match="no shipped IR"):
        load(version)


def test_shipped_versions_match_the_lockfile() -> None:
    assert shipped_versions() == tuple(e.version for e in LOCKFILE.schemas if e.ir_sha256)


# --- identity, caching and immutability ------------------------------------------------------


def test_load_is_cached_on_the_active_set() -> None:
    assert load("1.9.0", profiles=["host", "cloud"]) is load("1.9.0", profiles=("cloud", "host"))


def test_equality_and_hash_follow_version_profiles_extensions_digest(
    base: ResolvedSchema,
) -> None:
    again = resolve(base.ir)
    assert again is not base
    assert again == base
    assert hash(again) == hash(base)
    assert base != load("1.9.0", profiles={"cloud"})


def test_digest_is_the_lockfile_ir_sha256(base: ResolvedSchema) -> None:
    assert base.digest == lock.find(LOCKFILE, "1.9.0").ir_sha256 == digest(base.ir)


def test_resolved_schema_is_frozen(base: ResolvedSchema) -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        base.version = "1.8.0"  # type: ignore[misc]
    with pytest.raises(TypeError):
        base.classes["x"] = base.classes["detection_finding"]  # type: ignore[index]
    with pytest.raises(TypeError):
        base.classes["detection_finding"].attributes["x"] = None  # type: ignore[index]


# --- OCSF 1.9.0: the facts verified in the design spike ------------------------------------


def test_no_profiles(base: ResolvedSchema) -> None:
    attributes = base.classes["detection_finding"].attributes
    assert {"cloud", "actor", "time_dt"}.isdisjoint(attributes)
    assert "device" in attributes  # un-gated in 1.9.0


def test_host_and_security_control() -> None:
    schema = load("1.9.0", profiles={"host", "security_control"})
    attributes = schema.classes["detection_finding"].attributes
    assert attributes["actor"].requirement is Requirement.OPTIONAL
    assert attributes["action_id"].requirement is Requirement.RECOMMENDED


def test_datetime_profile_adds_dt_twins() -> None:
    attributes = load("1.9.0", profiles={"datetime"}).classes["detection_finding"].attributes
    assert attributes["time_dt"].type == "datetime_t"
    assert attributes["time_dt"].root is Root.STRING


def test_no_extensions_drops_extension_attributes_and_items(base: ResolvedSchema, everything: ResolvedSchema) -> None:
    assert "hosted_services" not in base.objects["process"].attributes
    hosted = everything.objects["process"].attributes["hosted_services"]
    assert hosted.object_type == "win/win_service"
    assert not any("/" in key for key in (*base.classes, *base.objects))
    assert (len(base.classes), len(everything.classes)) == (80, 87)
    assert (len(base.objects), len(everything.objects)) == (190, 194)


@pytest.mark.parametrize("which", ["base", "everything"])
def test_resolution_is_closed(which: str, request: pytest.FixtureRequest) -> None:
    """Every attribute's object or type resolves inside the same active set."""
    schema: ResolvedSchema = request.getfixturevalue(which)
    for item in (*schema.classes.values(), *schema.objects.values()):
        for name, a in item.attributes.items():
            if a.object_type:
                assert a.object_type in schema.objects, (item.key, name)
            else:
                assert a.type in schema.types, (item.key, name)


def test_marker_table_size(base: ResolvedSchema, everything: ResolvedSchema) -> None:
    # (owner, attribute) pairs 1.9.0 marks as observables, without and with every profile and
    # extension: pinned, so a resolver change that drops or invents markers shows up here
    assert len(base.observable_markers) == 478
    assert len(everything.observable_markers) == 594


def test_type_uid_registry_matches_class_and_activity(everything: ResolvedSchema) -> None:
    for item in everything.classes.values():
        assert item.identity is not None
        type_uid = item.attributes["type_uid"].enum
        activity = item.attributes["activity_id"].enum
        assert type_uid is not None
        assert activity is not None
        for value in type_uid:
            found, activity_id = everything.class_by_type_uid(int(value))
            assert found is item
            assert activity_id in activity
            assert everything.class_by_uid(item.identity.class_uid) is item


def test_unknown_uids_raise_keyerror(base: ResolvedSchema) -> None:
    with pytest.raises(KeyError, match="no class with uid 1"):
        base.class_by_uid(1)
    with pytest.raises(KeyError, match="no class declares type_uid 200498"):
        base.class_by_type_uid(200498)
    with pytest.raises(KeyError, match="no class with uid 201001"):  # win, not active
        base.class_by_uid(201001)


def test_list_siblings_are_the_five_the_spec_names(everything: ResolvedSchema) -> None:
    arrays = {
        (item.key, name, spec.label)
        for item in (*everything.classes.values(), *everything.objects.values())
        for name, spec in item.siblings.items()
        if spec.is_array
    }
    assert arrays == {
        ("dns_activity", "flag_ids", "flags"),
        ("dns_answer", "flag_ids", "flags"),
        ("malware", "classification_ids", "classifications"),
        ("startup_item", "run_mode_ids", "run_modes"),
        ("url", "category_ids", "categories"),
    }


def test_sibling_without_label_attribute_has_no_rule(everything: ResolvedSchema) -> None:
    for key in ("observable", "prev_event"):
        assert "type_uid" in everything.objects[key].attributes
        assert "type_uid" not in everything.objects[key].siblings
    assert everything.objects["related_event"].siblings["type_uid"].enum is None


# --- acceptance criteria --------------------------------------------------------------------


def test_acc_11_schema_semantics_without_a_model() -> None:
    """acc. #11 for ("1.9.0", "detection_finding", {"cloud"})."""
    schema = load("1.9.0", profiles={"cloud"})
    finding = schema.classes["detection_finding"]
    attributes = finding.attributes
    required = {n for n, a in attributes.items() if a.requirement is Requirement.REQUIRED}
    assert {"activity_id", "class_uid", "metadata", "severity_id", "type_uid", "cloud"} <= required
    metadata = schema.objects[attributes["metadata"].object_type or ""]
    assert metadata.attributes["version"].requirement is Requirement.REQUIRED  # nested
    assert attributes["time"].root is Root.LONG
    assert schema.objects["observable"].attributes["type_id"].root is Root.INTEGER
    activity = attributes["activity_id"]
    assert activity.enum is not None
    assert activity.enum[1].caption == "Create"
    assert finding.siblings["activity_id"].label == "activity_name"
    assert attributes["cloud"].gated_by == {"cloud"}
    assert finding.identity == ClassIdentity(
        class_uid=2004, class_name="Detection Finding", category_uid=2, category_name="Findings"
    )


def test_acc_12a_profiles_change_attributes_and_rules(base: ResolvedSchema) -> None:
    plain = base.classes["detection_finding"]
    control = load("1.9.0", profiles={"security_control"}).classes["detection_finding"]
    assert "action_id" not in plain.attributes
    assert "action_id" not in plain.siblings  # no sibling rule for a gated-out attribute
    assert control.siblings["action_id"].label == "action"
    cloud = load("1.9.0", profiles={"cloud"}).classes["detection_finding"]
    assert "cloud" not in plain.attributes  # so E4 (cloud absent) is not a finding here
    assert cloud.attributes["cloud"].requirement is Requirement.REQUIRED  # but is here


NO_OBJECT_CONSUMER = textwrap.dedent(
    """
    import sys
    from ocsf_toolkit import load

    schema = load("1.9.0", profiles={"cloud", "security_control"})
    finding = schema.classes["detection_finding"]
    identity = finding.identity
    activity = finding.attributes["activity_id"].enum
    item, activity_id = schema.class_by_type_uid(200401)
    observable = schema.objects["observable"].attributes["type_id"]
    severity = finding.attributes["severity_id"].enum
    print(
        identity.class_uid, identity.class_name, identity.category_uid, identity.category_name,
        activity[1].caption, item.attributes["type_uid"].enum[200401].caption, activity_id,
        observable.root, observable.enum[4].caption, severity[0].caption,
        finding.attributes["cloud"].requirement, finding.attributes["action_id"].requirement,
        sep="|",
    )
    assert "msgspec" not in sys.modules, "schema access must not load the modelling layer"
    """
)


def test_section_1_5_no_object_consumer() -> None:
    """M2's done-criterion: every schema-determined value in the spec §1.5 translation block is
    reachable as data, in a fresh interpreter that never imports msgspec."""
    result = subprocess.run(
        [sys.executable, "-c", NO_OBJECT_CONSUMER],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    assert result.stdout.strip().split("|") == [
        "2004",
        "Detection Finding",
        "2",
        "Findings",
        "Create",
        "Detection Finding: Create",  # type_name: the hand-built "<Class>: <analytic>" is wrong
        "1",
        "integer_t",  # observables[].type_id is an integer, not "4"
        "User Name",  # type_id 4 is "User Name" in 1.9.0, not "User"
        "Unknown",
        "required",  # cloud: required under the cloud profile only
        "recommended",  # action_id: recommended under security_control in 1.9.0
    ]


# --- an IR whose tables disagree is refused by resolve() --------------------------------------


def _broken(ir: SchemaIR, **changes: object) -> SchemaIR:
    return dataclasses.replace(ir, **changes)


def test_resolve_refuses_an_ir_without_base_event(base: ResolvedSchema) -> None:
    classes = {k: v for k, v in base.ir.classes.items() if k != "base_event"}
    with pytest.raises(ValueError, match="no base_event"):
        resolve(_broken(base.ir, classes=classes))


def test_resolve_refuses_an_object_in_the_classes_table(base: ResolvedSchema) -> None:
    classes = dict(base.ir.classes) | {"user": base.ir.objects["user"]}
    with pytest.raises(ValueError, match=r"classes\.user\.is_class: False in the classes table"):
        resolve(_broken(base.ir, classes=classes))


def test_resolve_refuses_a_dangling_object_type(base: ResolvedSchema) -> None:
    finding = base.ir.classes["detection_finding"]
    attrs = dict(finding.attributes)
    attrs["device"] = dataclasses.replace(attrs["device"], object_type="no_such_object")
    classes = dict(base.ir.classes) | {"detection_finding": dataclasses.replace(finding, attributes=attrs)}
    with pytest.raises(ValueError, match=r"detection_finding\.attributes\.device\.object_type: no object"):
        resolve(_broken(base.ir, classes=classes))
