"""Tests over the dist `make generate` writes; every test here skips until it exists."""

from pathlib import Path

import pytest

from tests.conftest import ROOT

PACKAGE = "ocsf_toolkit_v1_9_0"
DIST = ROOT / "build" / "dists" / PACKAGE  # the installable project
TREE = DIST / PACKAGE  # the import package inside it
SAMPLES = Path(__file__).parent / "samples"


@pytest.fixture(autouse=True)
def require_dist() -> None:
    if not (TREE / "__init__.py").is_file():
        pytest.skip(f"{DIST.relative_to(ROOT)} absent; run `make generate`")
