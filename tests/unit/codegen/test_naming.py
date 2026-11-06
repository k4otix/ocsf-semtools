"""Names in generated code: the caption -> member rule (acc. #5), fields, modules and classes."""

import re

import pytest

from ocsf_toolkit.codegen.naming import (
    class_name,
    field_name,
    member_name,
    member_names,
    module_name,
)
from ocsf_toolkit.ir import EnumMember, EnumValue
from ocsf_toolkit.schema import load


def _enum(captions: dict[EnumValue, str]) -> dict[EnumValue, EnumMember]:
    return {v: EnumMember(value=v, caption=c, deprecated=None) for v, c in captions.items()}


@pytest.mark.parametrize(
    ("caption", "name"),
    [
        ("Identity & Access Management", "IDENTITY_AND_ACCESS_MANAGEMENT"),
        ("5Views", "N_5VIEWS"),  # the one 1.9.0 caption that starts with a digit (packet.format_id)
        ("Other", "OTHER"),
        ("Re-Auth (MFA)", "RE_AUTH_MFA"),
        ("TLS 1.3", "TLS_1_3"),
        ("  --  ", ""),
    ],
)
def test_member_name(caption: str, name: str) -> None:
    assert member_name(caption) == name


def test_colliding_members_all_take_their_value() -> None:
    names = member_names(_enum({2: "A-b", 1: "A b", 3: "C", -1: "A b", 4: "!!"}))
    assert names == {-1: "A_B_NEG1", 1: "A_B_1", 2: "A_B_2", 3: "C", 4: "VALUE"}
    assert list(names) == [-1, 1, 2, 3, 4]  # value order


def test_string_enum_members_suffix_by_value() -> None:
    assert member_names(_enum({"put": "Write", "post": "Write"})) == {
        "post": "WRITE_POST",
        "put": "WRITE_PUT",
    }


def test_members_that_still_collide_are_an_error() -> None:
    with pytest.raises(ValueError, match="collide"):
        member_names(_enum({"get": "X", "GET": "X"}))


def test_every_1_9_0_enum_names_cleanly() -> None:
    """Every 1.9.0 enum member is named from its caption, with no integer_4-style fallbacks (acc. #5)."""
    ir = load("1.9.0").ir
    attrs = [a for t in (ir.classes, ir.objects) for i in t.values() for a in i.attributes.values()]
    enums = [a.enum for a in (*attrs, *ir.dictionary.values()) if a.enum]
    assert len(enums) == 1071 + 71  # attribute enums on classes and objects, then dictionary enums
    for enum in enums:
        names = list(member_names(enum).values())
        assert len(set(names)) == len(names)
        for name in names:
            assert name.isidentifier()
            assert name.isupper()
            assert not name.startswith("_")
            assert not re.fullmatch(r"(INTEGER|VALUE)_?\d*", name)


@pytest.mark.parametrize(("attr", "name"), [("from", "from_"), ("class", "class_"), ("type", "type"), ("x509", "x509")])
def test_field_name(attr: str, name: str) -> None:
    assert field_name(attr) == name


@pytest.mark.parametrize("attr", ["_private", "str", "field", "Literal", "Upper", "a-b"])
def test_field_names_that_would_shadow_or_break_are_refused(attr: str) -> None:
    with pytest.raises(ValueError, match="cannot be a generated field"):
        field_name(attr)


@pytest.mark.parametrize("name", ["object", "class", "enums", "importlib", "Process", "a-b"])
def test_module_names_that_would_shadow_or_break_are_refused(name: str) -> None:
    with pytest.raises(ValueError, match="cannot be a generated module"):
        module_name(name)


def test_class_name() -> None:
    assert class_name("detection_finding") == "DetectionFinding"
    assert class_name("cis_csc") == "CisCsc"


def test_module_name() -> None:
    assert module_name("process") == "process"
