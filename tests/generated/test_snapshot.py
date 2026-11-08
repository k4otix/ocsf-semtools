"""Each dist is exactly what the generator renders, and one module is snapshotted for review."""

from ocsf_semtools.cli.models import tree
from ocsf_semtools.cli.trees import read_tree
from tests.conftest import ROOT
from tests.generated.conftest import Dist, require

GOLDEN = ROOT / "tests" / "golden" / "detection_finding_1_9_0.py"


def test_dist_matches_a_fresh_render(dist: Dist) -> None:
    """Regeneration is byte-identical; `ocsf-semtools models check` pins it in the lockfile."""
    assert read_tree(dist.tree) == tree(dist.version)


def test_golden_snapshot() -> None:
    """A generator change shows up as a reviewable diff of this file; copy the new one in."""
    d = require(Dist.of("1.9.0"))
    assert (d.tree / "classes" / "detection_finding.py").read_text() == GOLDEN.read_text()
