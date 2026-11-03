"""Compiler JSON -> IR: key accounting over every lockfile version, and the format quirks."""

from collections import Counter
from typing import Any

import pytest

from ocsf_toolkit.ir import Constraint, Deprecation, Requirement, Root, SchemaIR
from ocsf_toolkit.loader import IGNORED_KEYS, KeyAccount, load_compiled
from tests.conftest import VERSIONS, compiled_path, provenance
from tests.fixtures.compiled import PROVENANCE, compiled, compiled_bytes

# Upstream source typos, never added to IGNORED_KEYS: a stray `description:` on
# analytic.related_analytics and a `name` on user.type_id (both OCSF 1.0.0 only).
KNOWN_JUNK = {"1.0.0": {("object.attr", "description:"), ("object.attr", "name")}}


# --- key accounting: every key in the compiler output is read or deliberately ignored ---------


@pytest.mark.parametrize("version", VERSIONS)
def test_every_compiler_key_is_accounted_for(version: str) -> None:
    account = KeyAccount()
    ir = load_compiled(compiled_path(version).read_bytes(), provenance(version), account=account)
    assert ir.version == version
    assert account.unaccounted() == KNOWN_JUNK.get(version, set())


def test_accounting_catches_a_new_compiler_key() -> None:
    data = compiled()
    data["classes"]["finding"]["attributes"]["severity_id"]["confidence"] = 0.5
    data["objects"]["cloud"]["new_marker"] = True
    account = KeyAccount()
    load_compiled(data, PROVENANCE, account=account)
    assert account.unaccounted() == {("class.attr", "confidence"), ("object", "new_marker")}


def test_ignored_keys_are_display_or_derivable() -> None:
    assert (
        frozenset(
            {
                "description",
                "references",
                "type_name",
                "object_name",
                "extension_id",
                "source",
                "meta",
            }
        )
        == IGNORED_KEYS
    )


# --- quirks (architecture §3.2) on the synthetic schema ------------------------------------------


@pytest.fixture(scope="module")
def ir() -> SchemaIR:
    return load_compiled(compiled_bytes(), PROVENANCE)


def test_compile_version_other_than_1_is_refused() -> None:
    data = compiled()
    data["compile_version"] = 2
    with pytest.raises(ValueError, match=r"\$\.compile_version: 2 is not supported"):
        load_compiled(data, PROVENANCE)


def test_profiles_null_absent_and_empty_collapse(ir: SchemaIR) -> None:
    attrs = ir.classes["finding"].attributes
    assert attrs["category_uid"].profiles == frozenset()  # null
    assert attrs["method"].profiles == frozenset()  # absent
    assert ir.classes["base_event"].profiles == frozenset()  # []
    assert attrs["cloud"].profiles == frozenset({"cloud"})


def test_requirement_defaults_to_optional(ir: SchemaIR) -> None:
    attrs = ir.classes["finding"].attributes
    assert attrs["method"].requirement is Requirement.OPTIONAL
    assert attrs["cloud"].requirement is Requirement.REQUIRED
    assert ir.dictionary["severity_id"].requirement is Requirement.OPTIONAL


def test_object_typed_attribute(ir: SchemaIR) -> None:
    cloud = ir.classes["finding"].attributes["cloud"]
    assert (cloud.type, cloud.object_type) == ("object_t", "cloud")


def test_enum_keys_follow_the_type_root(ir: SchemaIR) -> None:
    attrs = ir.classes["finding"].attributes
    severity = attrs["severity_id"].enum
    assert severity is not None
    assert list(severity) == [0, 3, 99]  # int keys, numeric order
    assert severity[3].value == 3
    assert severity[3].caption == "High"
    method = attrs["method"].enum
    assert method is not None
    assert list(method) == ["GET", "PUT"]


def test_enum_member_deprecation_and_redundant_uid(ir: SchemaIR) -> None:
    severity = ir.classes["finding"].attributes["severity_id"].enum
    category = ir.classes["finding"].attributes["category_uid"].enum
    assert severity is not None
    assert category is not None
    assert severity[3].deprecated == Deprecation(
        message="Use <code>new</code>.", since="9.8.0", superseded_by=("new",)
    )
    assert category[2].caption == "Findings"


def test_enum_uid_must_match_its_key() -> None:
    data = compiled()
    data["classes"]["finding"]["attributes"]["category_uid"]["enum"]["2"]["uid"] = 5
    with pytest.raises(ValueError, match=r"category_uid\.enum\.2\.uid: 5 does not match"):
        load_compiled(data, PROVENANCE)


def test_empty_enum_placeholder_is_no_enum(ir: SchemaIR) -> None:
    assert ir.dictionary["flag_ids"].enum is None


def test_enum_on_a_non_enumerable_type_is_refused() -> None:
    data = compiled()
    data["classes"]["finding"]["attributes"]["cloud"]["enum"] = {"1": {"caption": "x"}}
    with pytest.raises(ValueError, match=r"cloud\.enum: enum on a non-primitive type"):
        load_compiled(data, PROVENANCE)


def test_base_event_has_no_category_name(ir: SchemaIR) -> None:
    base = ir.classes["base_event"]
    assert (base.uid, base.category, base.category_uid, base.category_name) == (0, "other", 0, None)


def test_class_and_object_fields(ir: SchemaIR) -> None:
    finding = ir.classes["finding"]
    assert finding.is_class
    assert finding.observable is None
    assert finding.constraints == (
        Constraint(kind="at_least_one", attributes=("severity", "severity_id")),
    )
    assert dict(finding.associations) == {"actor.user": ("device",)}
    assert dict(finding.class_observables) == {}
    cloud = ir.objects["cloud"]
    assert not cloud.is_class
    assert (cloud.observable, cloud.uid, cloud.category) == (30, None, None)


def test_scoped_keys_and_unscoped_names(ir: SchemaIR) -> None:
    reg_key = ir.objects["win/reg_key"]
    assert (reg_key.key, reg_key.name, reg_key.extension) == ("win/reg_key", "reg_key", "win")
    assert ir.dictionary["win/hosted"].extension == "win"
    assert ir.objects["cloud"].attributes["hosted"].extension == "win"
    assert ir.profiles["linux/linux_users"].name == "linux_users"


def test_type_roots_follow_parent_chains(ir: SchemaIR) -> None:
    types = ir.types
    assert types["string_t"].root is Root.STRING
    assert types["string_t"].parent is None
    assert (types["port_t"].root, types["port_t"].range) == (Root.INTEGER, (0, 65535))
    assert (types["ipv4_t"].parent, types["ipv4_t"].root) == ("ip_t", Root.STRING)
    assert types["boolean_t"].values == (False, True)
    assert (types["ip_t"].max_len, types["ip_t"].regex, types["ip_t"].observable) == (40, "^x$", 2)


@pytest.mark.parametrize(
    ("added", "message"),
    [
        ({"bad_t": {"caption": "Bad", "type": "nope_t"}}, r"bad_t\.type: bad parent 'nope_t'"),
        (
            {"a_t": {"caption": "A", "type": "b_t"}, "b_t": {"caption": "B", "type": "a_t"}},
            r"\.type: bad parent '[ab]_t'",
        ),
    ],
)
def test_bad_type_parent_is_refused(added: dict[str, object], message: str) -> None:
    data = compiled()
    data["dictionary"]["types"]["attributes"].update(added)
    with pytest.raises(ValueError, match=message):
        load_compiled(data, PROVENANCE)


def test_other_attribute_fields(ir: SchemaIR) -> None:
    attrs = ir.classes["finding"].attributes
    assert attrs["port"].default == 443
    assert attrs["old"].deprecated is not None
    assert attrs["severity_id"].suppress_checks == frozenset({"enum_convention"})
    assert attrs["category_uid"].group == "classification"
    assert ir.objects["cloud"].attributes["ip"].observable == 2


def test_profiles_extensions_categories(ir: SchemaIR) -> None:
    assert dict(ir.profiles["cloud"].annotations) == {"group": "primary"}
    win = ir.extensions["win"]
    assert (win.uid, win.platform, win.version) == (2, True, "9.9.9")
    assert ir.categories["findings"].uid == 2


def test_mappings_are_read_only_and_sorted(ir: SchemaIR) -> None:
    assert list(ir.classes["finding"].attributes) == sorted(ir.classes["finding"].attributes)
    with pytest.raises(TypeError):
        ir.classes["x"] = ir.classes["finding"]  # type: ignore[index]


def _set(data: dict[str, Any], path: tuple[str, ...], value: object) -> None:
    for key in path[:-1]:
        data = data[key]
    data[path[-1]] = value


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("version",), 9, r"\$\.version: expected a string, got int"),
        (("classes", "finding", "uid"), "2001", r"\$\.classes\.finding\.uid: expected an integer"),
        (("classes", "finding", "attributes", "cloud", "requirement"), "must", "unknown value"),
        (("classes", "finding", "attributes", "cloud", "is_array"), "yes", "expected a boolean"),
        (("classes", "finding", "profiles"), "cloud", "expected a list of strings"),
        (("classes", "finding", "constraints"), {"exactly_two": ["a"]}, "unknown constraint kind"),
        (("objects", "cloud"), [], r"\$\.objects\.cloud: expected an object, got list"),
        (("dictionary", "types", "attributes", "port_t", "range"), [1], r"\[min, max\] pair"),
        (("classes", "finding", "attributes", "severity_id", "enum", "x"), {}, "not an integer"),
    ],
)
def test_malformed_input_names_the_key(path: tuple[str, ...], value: object, message: str) -> None:
    data = compiled()
    _set(data, path, value)
    with pytest.raises(ValueError, match=message):
        load_compiled(data, PROVENANCE)


def test_bytes_that_are_not_json() -> None:
    with pytest.raises(ValueError, match="compiler output is not JSON"):
        load_compiled(b"INFO: Compiling schema", PROVENANCE)


# --- facts about real OCSF 1.9.0 that later milestones lean on -------------------------------


def test_1_9_0_shape(ir_1_9_0: SchemaIR) -> None:
    ir = ir_1_9_0
    assert (len(ir.classes), len(ir.objects), len(ir.types), len(ir.profiles)) == (87, 194, 24, 15)
    assert sorted(ir.extensions) == ["linux", "macos", "win"]
    assert Counter(t.root for t in ir.types.values()) == {
        Root.STRING: 17,
        Root.INTEGER: 2,
        Root.LONG: 2,
        Root.BOOLEAN: 1,
        Root.FLOAT: 1,
        Root.JSON: 1,
    }
    assert ir.provenance.commit == "856d462bd20dc46cc1ffed2dfffe3b91ef0fbeba"


def test_1_9_0_detection_finding(ir_1_9_0: SchemaIR) -> None:
    df = ir_1_9_0.classes["detection_finding"]
    assert (df.uid, df.category_uid, df.category_name) == (2004, 2, "Findings")
    activity = df.attributes["activity_id"].enum
    type_uid = df.attributes["type_uid"].enum
    assert activity is not None
    assert type_uid is not None
    assert activity[1].caption == "Create"
    assert type_uid[200401].caption == "Detection Finding: Create"
    assert sorted(n for n, a in df.attributes.items() if a.deprecated) == ["comment", "ticket"]
    assert df.attributes["cloud"].profiles == frozenset({"cloud"})


def test_1_9_0_siblings_and_string_enums(ir_1_9_0: SchemaIR) -> None:
    items = {**ir_1_9_0.classes, **ir_1_9_0.objects}
    list_siblings = {
        (k, n)
        for k, i in items.items()
        for n, a in i.attributes.items()
        if a.sibling and a.is_array
    }
    assert list_siblings == {
        ("dns_activity", "flag_ids"),
        ("dns_answer", "flag_ids"),
        ("malware", "classification_ids"),
        ("startup_item", "run_mode_ids"),
        ("url", "category_ids"),
    }
    string_enums = {
        (k, n)
        for k, i in items.items()
        for n, a in i.attributes.items()
        if a.enum is not None and a.type == "string_t"
    }
    assert string_enums == {("cvss", "depth"), ("http_request", "http_method"), ("osint", "tlp")}


def test_1_9_0_extension_attribute(ir_1_9_0: SchemaIR) -> None:
    hosted = ir_1_9_0.objects["process"].attributes["hosted_services"]
    assert (hosted.extension, hosted.object_type) == ("win", "win/win_service")
