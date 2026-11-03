"""The dist is exactly what the generator renders, and one module is snapshotted for review."""

from ocsf_toolkit.codegen import render
from ocsf_toolkit.schema import load
from tests.conftest import ROOT
from tests.generated.conftest import PACKAGE, TREE

GOLDEN = ROOT / "tests" / "golden" / "detection_finding_1_9_0.py"


def test_dist_matches_a_fresh_render() -> None:
    """Regeneration is byte-identical (the M7 tree hash gate rests on this)."""
    on_disk = {
        p.relative_to(TREE).as_posix(): p.read_bytes()
        for p in TREE.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    assert on_disk == render(load("1.9.0").ir, package=PACKAGE)


def test_golden_snapshot() -> None:
    """A generator change shows up as a reviewable diff of this file; copy the new one in."""
    assert (TREE / "classes" / "detection_finding.py").read_text() == GOLDEN.read_text()
