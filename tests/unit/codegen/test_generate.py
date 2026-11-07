"""render() and generate(): the tree for 1.9.0 and the synthetic schema, and safe replacement."""

import dataclasses
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ocsf_toolkit.codegen import IR_FILE, generate, render
from ocsf_toolkit.export import export_ir
from ocsf_toolkit.ir import SchemaIR
from ocsf_toolkit.schema import load
from tests.conftest import ROOT

IR = load("1.9.0").ir
TREE = render(IR, package="pkg")


def _text(tree: dict[str, bytes], path: str) -> str:
    return tree[path].decode()


def test_render_is_deterministic() -> None:
    assert render(IR, package="pkg") == TREE


def test_one_module_per_item() -> None:
    expected = {f"classes/{k}.py" for k in IR.classes if "/" not in k}
    expected |= {f"objects/{k}.py" for k in IR.objects if "/" not in k and k != "object"}
    expected |= {f"ext/{k}.py" for k in (*IR.classes, *IR.objects) if "/" in k}
    modules = {p for p in TREE if p.endswith(".py") and not p.endswith("__init__.py")}
    assert modules - {"enums.py"} == expected
    assert len(expected) == 87 + 193  # 87 classes (7 from extensions); 194 objects less the open `object`
    assert TREE[IR_FILE] == export_ir(IR)  # bundled even though core ships 1.9.0


def test_tree_is_ruff_clean(tmp_path: Path) -> None:
    """N4: generator output is final text; no formatter or fixer runs over it."""
    out = generate(IR, package="pkg", out=tmp_path)
    for args in (["format", "--check"], ["check"]):
        cmd = [sys.executable, "-m", "ruff", *args, "--config", str(ROOT / "pyproject.toml")]
        result = subprocess.run([*cmd, str(out)], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr


LONG_CLASS = "detection_finding_with_a_long_name_" + "x" * 21  # DetectionFindingWithALongNameXxx..., 50


def _with_long_names(class_name: str, attribute: str) -> SchemaIR:
    finding = IR.classes["detection_finding"]
    attrs = dict(finding.attributes)
    attrs[attribute] = dataclasses.replace(attrs["severity_id"], name=attribute, sibling=None)  # its own enum
    attrs[attribute + "s"] = dataclasses.replace(attrs["evidences"], name=attribute + "s")  # a list of objects
    item = dataclasses.replace(finding, name=class_name, attributes=attrs)
    return dataclasses.replace(IR, classes={**IR.classes, "detection_finding": item})


def test_long_names_are_ruff_clean(tmp_path: Path) -> None:
    """Names that push lines past the width take the wrapping paths no shipped schema reaches."""
    # a 50-character class name wraps the Struct's head; a 90-character enum name wraps its fields
    long = _with_long_names(LONG_CLASS, "an_attribute_named_at_length_" + "y" * 16)
    tree = render(long, package="pkg")
    module = tree[next(path for path in tree if path.startswith("classes/detection_finding_with_a_long_name"))]
    path = tmp_path / "detection_finding.py"
    path.write_bytes(module)
    for args in (["format", "--check"], ["check"]):
        cmd = [sys.executable, "-m", "ruff", *args, "--config", str(ROOT / "pyproject.toml"), str(path)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("class_name", "attribute", "what"),
    [
        ("detection_finding_" + "x" * 100, "short", "class name"),
        (LONG_CLASS, "an_attribute_" * 9, "field name"),
        (LONG_CLASS, "an_attribute_named_at_length_" + "y" * 40, "enum name"),
    ],
)
def test_a_name_too_long_to_wrap_is_refused(class_name: str, attribute: str, what: str) -> None:
    with pytest.raises(ValueError, match=f"{what} .* characters; generated names are at most 100"):
        render(_with_long_names(class_name, attribute), package="pkg")


def test_class_module_shape() -> None:
    text = _text(TREE, "classes/detection_finding.py")
    for line in (
        '    __ocsf__ = ("1.9.0", "detection_finding")',
        "    class_uid: Literal[2004] = field(default_factory=lambda: 2004)",
        "    activity_id: DetectionFindingActivityId",  # owner-specific enum
        "    severity_id: SeverityId",  # shared enum, imported from enums.py
        "    finding_info: _finding_info.FindingInfo",
        "    type_uid: int | None = None",  # derived: no enum, never required
        "    severity: str | None = None",
        "    cloud: _cloud.Cloud | None = None",  # superset: required under cloud, optional here
        "    unmapped: dict[str, Any] | None = None",
    ):
        assert line in text.splitlines(), line
    assert any(line.startswith("from ..enums import ") for line in text.splitlines())
    assert "class DetectionFindingTypeUid" not in text
    assert "    # profile: cloud" in text


def test_keywords_and_extension_attributes() -> None:
    assert '    from_: str | None = field(default=None, name="from")' in _text(TREE, "objects/email.py")
    process = _text(TREE, "objects/process.py")
    assert "    # extension: win" in process
    assert "    hosted_services: list[_win_win_service.WinService] | None = None" in process
    assert "from ..ext.win import win_service as _win_win_service" in process


def test_package_inits() -> None:
    root = _text(TREE, "__init__.py")
    assert "    from .classes.detection_finding import DetectionFinding as DetectionFinding" in root
    assert '__ocsf_version__ = "1.9.0"' in root
    assert f'__schema_digest__ = "{load("1.9.0").digest}"' in root
    assert "__ocsf_profiles__: tuple[str, ...] | None = None" in root
    assert '    "win",\n)' in root
    assert '    "Process": ".process",' in _text(TREE, "objects/__init__.py")
    assert '    "RegKey": ".reg_key",' in _text(TREE, "ext/win/__init__.py")
    assert _text(TREE, "classes/__init__.py").endswith("__all__: list[str] = []\n")
    assert TREE["py.typed"] == b""


def test_exact_mode_takes_real_requiredness() -> None:
    tree = render(IR, package="pkg", profiles=["cloud"], extensions=[])
    text = _text(tree, "classes/detection_finding.py")
    assert "    cloud: _cloud.Cloud" in text.splitlines()  # required under the active profile
    assert not any(line.startswith("    actor:") for line in text.splitlines())  # host inactive
    assert '__ocsf_profiles__: tuple[str, ...] | None = ("cloud",)' in _text(tree, "__init__.py")
    assert "__ocsf_extensions__: tuple[str, ...] = ()" in _text(tree, "__init__.py")
    assert not any(p.startswith("ext/") for p in tree)
    assert "hosted_services" not in _text(tree, "objects/process.py")


def test_a_custom_ir_is_bundled(synthetic_ir: SchemaIR) -> None:
    tree = render(synthetic_ir, package="pkg")
    assert tree[IR_FILE] == export_ir(synthetic_ir)
    finding = _text(tree, "classes/finding.py")
    assert "    method: FindingMethod | None = None" in finding  # a StrEnum
    assert "class FindingMethod(StrEnum):" in finding
    assert "    old: str | None = None" in finding  # deprecated: never required


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"version": "1.9.0\nimport os"}, "not a plain version number"),
        ({"profiles": {"x\nimport os": IR.profiles["cloud"]}}, "profile name"),
        ({"extensions": {"Win #": IR.extensions["win"]}}, "extension name"),
    ],
)
def test_names_that_would_reach_code_unquoted_are_refused(change: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        render(dataclasses.replace(IR, **change), package="pkg")  # type: ignore[arg-type]


def test_bad_package_name() -> None:
    with pytest.raises(ValueError, match="single Python identifier"):
        render(IR, package="my.pkg")


# --- replacing an existing package ----------------------------------------------------------


def test_regenerating_replaces_a_generated_package(tmp_path: Path) -> None:
    first = generate(IR, package="pkg", out=tmp_path, extensions=[])
    (first / "objects" / "__pycache__").mkdir()
    (first / "objects" / "__pycache__" / "process.cpython-312.pyc").write_bytes(b"x")
    second = generate(IR, package="pkg", out=tmp_path)
    assert second == first
    assert (second / "ext" / "win").is_dir()
    assert not (second / "objects" / "__pycache__").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["pkg"]  # no staging left behind


def test_a_hand_added_file_blocks_replacement(tmp_path: Path) -> None:
    out = generate(IR, package="pkg", out=tmp_path)
    (out / "notes.txt").write_text("mine")
    (out / "objects" / "process.py").write_text("# edited by hand\n")
    with pytest.raises(ValueError, match=r"did not generate \(.*notes.txt, .*process.py\)"):
        generate(IR, package="pkg", out=tmp_path)
    assert (out / "notes.txt").read_text() == "mine"


def test_symlinks_block_replacement(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.py").write_text("keep")
    out = generate(IR, package="pkg", out=tmp_path / "gen")
    os.symlink(outside, out / "link")
    with pytest.raises(ValueError, match="did not generate"):
        generate(IR, package="pkg", out=tmp_path / "gen")
    os.symlink(outside, tmp_path / "gen" / "lnk")
    with pytest.raises(ValueError, match="not a generated package directory"):
        generate(IR, package="lnk", out=tmp_path / "gen")
    assert (outside / "keep.py").read_text() == "keep"


def test_a_render_that_fails_writes_nothing(tmp_path: Path) -> None:
    out = generate(IR, package="pkg", out=tmp_path)
    before = sorted(p.relative_to(out) for p in out.rglob("*"))
    with pytest.raises(KeyError, match="unknown profiles"):
        generate(IR, package="pkg", out=tmp_path, profiles=["nope"])
    assert sorted(p.relative_to(out) for p in out.rglob("*")) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["pkg"]


def _snapshot(root: Path) -> dict[Path, bytes]:
    return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _failing_renames(monkeypatch: pytest.MonkeyPatch, into: Path, times: int) -> None:
    """Make the next ``times`` renames onto ``into`` fail, as a full disk would."""
    real, left = Path.rename, [times]

    def rename(self: Path, target: Path) -> Path:
        if Path(target) == into and left[0]:
            left[0] -= 1
            raise OSError("disk full")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", rename)


def test_a_failed_swap_puts_the_old_package_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = generate(IR, package="pkg", out=tmp_path, profiles=["cloud"])
    before = _snapshot(out)
    _failing_renames(monkeypatch, out, times=1)
    with pytest.raises(OSError, match="disk full"):
        generate(IR, package="pkg", out=tmp_path)  # superset classes: different files
    assert _snapshot(out) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["pkg"]  # the staging directory is gone


def test_a_package_that_cant_be_put_back_is_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = generate(IR, package="pkg", out=tmp_path, profiles=["cloud"])
    before = _snapshot(out)
    _failing_renames(monkeypatch, out, times=2)
    with pytest.raises(OSError, match=r"nor put it back; it is at (.+)") as caught:
        generate(IR, package="pkg", out=tmp_path)
    kept = Path(str(caught.value).rsplit("it is at ", 1)[1])
    assert _snapshot(kept) == before


def test_the_golden_module_matches_a_fresh_render() -> None:
    """A generator change shows up as a reviewable diff of tests/golden; copy the new render in."""
    golden = ROOT / "tests" / "golden" / "detection_finding_1_9_0.py"
    assert TREE["classes/detection_finding.py"].decode() == golden.read_text()
