"""IR type -> annotation, and where each item lives in the generated tree."""

import dataclasses

import pytest

from ocsf_semtools.codegen.layout import Place, exports, place, plan
from ocsf_semtools.codegen.types import annotation
from ocsf_semtools.schema import load

S = load("1.9.0", profiles=["host"])


@pytest.mark.parametrize(
    ("owner", "attr", "expected"),
    [
        ("detection_finding", "time", "int"),  # timestamp_t -> long_t
        ("detection_finding", "message", "str"),
        ("detection_finding", "is_alert", "bool"),
        ("location", "lat", "float"),
        ("evidences", "data", "Any"),  # json_t is any JSON value, not only an object
        ("detection_finding", "unmapped", "dict[str, Any]"),  # the open `object` type
        ("detection_finding", "evidences", "list[X]"),
    ],
)
def test_annotation(owner: str, attr: str, expected: str) -> None:
    item = S.classes.get(owner) or S.objects[owner]
    a = item.attributes[attr]
    assert annotation(a, "X" if a.object_type not in (None, "object") else None) == expected


def test_places_and_imports() -> None:
    ir = S.ir
    process = place(ir.objects["process"])
    assert process == Place(package=("objects",), module="process", name="Process")
    assert (process.path, process.alias, process.root) == ("objects/process.py", "_process", "..")
    key = place(ir.objects["win/reg_key"])
    assert (key.package, key.alias, key.root) == (("ext", "win"), "_win_reg_key", "...")
    finding = place(ir.classes["detection_finding"])
    assert finding.import_line(key) == "from ..ext.win import reg_key as _win_reg_key"


def test_plan_refuses_colliding_items() -> None:
    ir = S.ir
    twin = dataclasses.replace(ir.objects["file"], key="other_file")
    with pytest.raises(ValueError, match=r"module collides for items \[\['file', 'other_file'\]\]"):
        plan([ir.objects["file"], twin])


def test_exports_by_package() -> None:
    ir = S.ir
    objects = [o for k, o in ir.objects.items() if k != "object"]  # typed as a dict instead
    places = plan([*ir.classes.values(), *objects]).values()
    tree = exports(places)
    assert set(tree) == {(), ("objects",), ("ext", "win")}
    assert "DetectionFinding" in {p.name for p in tree[()]}
    assert {p.name for p in tree[("ext", "win")]} >= {"RegKey", "RegistryKeyActivity"}
