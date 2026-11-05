"""Tests over the dists `make dists` writes, one per model-window version; each test skips until
its dist exists."""

from dataclasses import dataclass
from pathlib import Path

import pytest

from ocsf_toolkit.cli.models import package_name
from tests.conftest import LOCKFILE, ROOT

SAMPLES = Path(__file__).parent / "samples"  # typed usage of the 1.9.0 dist
WINDOW = [e.version for e in LOCKFILE.schemas if e.ship_models]


@dataclass(frozen=True)
class Dist:
    version: str
    package: str
    root: Path  # the installable project, build/dists/<package>
    tree: Path  # the import package inside it

    @classmethod
    def of(cls, version: str) -> "Dist":
        package = package_name(version)
        root = ROOT / "build" / "dists" / package
        return cls(version, package, root, root / package)

    @property
    def built(self) -> bool:
        return (self.tree / "__init__.py").is_file()


def require(d: Dist) -> Dist:
    if not d.built:
        pytest.skip(f"{d.root.relative_to(ROOT)} absent; run `make dists`")
    return d


@pytest.fixture(params=WINDOW)
def dist(request: pytest.FixtureRequest) -> Dist:
    return require(Dist.of(request.param))
