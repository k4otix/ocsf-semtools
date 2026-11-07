"""What the architecture tests read: the package's source files."""

from tests.conftest import ROOT

PACKAGE = ROOT / "src" / "ocsf_toolkit"
SOURCES = sorted(p for p in PACKAGE.rglob("*.py") if p.name != "_version.py")
