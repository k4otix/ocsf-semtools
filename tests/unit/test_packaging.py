"""Release tooling in packaging/: the model dists' core range and project files, and the sdist."""

import importlib.util
import io
import shutil
import subprocess
import tarfile
import tomllib
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

from tests.conftest import ROOT


def _dist_module() -> ModuleType:
    # loaded by path: a top-level "packaging" would collide with the PyPI package of that name
    spec = importlib.util.spec_from_file_location(
        "version_dist", ROOT / "packaging" / "version_dist" / "dist.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dist = _dist_module()


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("0.1.dev37+g72bc5c44e.d20261105", ">=0.1.dev37,<0.2"),  # local part dropped
        ("0.3.0", ">=0.3.0,<0.4"),  # before 1.0 a minor may break the generated-code contract
        ("1.2.3", ">=1.2.3,<2"),
        ("2.0.0rc1", ">=2.0.0rc1,<3"),
    ],
)
def test_core_range(version: str, expected: str) -> None:
    assert dist.core_range(version) == expected


def test_core_range_rejects_junk() -> None:
    with pytest.raises(ValueError, match="not a release"):
        dist.core_range("main")


def test_render_writes_the_project_files(tmp_path: Path) -> None:
    dist.render("1.10.0", "0.4.0", tmp_path)
    project = tomllib.loads((tmp_path / "pyproject.toml").read_text())["project"]
    assert project["name"] == "ocsf-toolkit-v1-10-0"
    assert project["version"] == "0.4.0"
    assert project["dependencies"] == ["ocsf-toolkit>=0.4.0,<0.5", "msgspec>=0.22,<1"]
    readme = (tmp_path / "README.md").read_text()
    assert "from ocsf_toolkit_v1_10_0 import DetectionFinding" in readme
    for name in ("pyproject.toml", "README.md"):
        assert "@" not in (tmp_path / name).read_text()  # no placeholder left
    assert (tmp_path / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()


def test_wheel_tree_is_the_package_only() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("pkg/__init__.py", b"a")
        z.writestr("pkg/sub/m.py", b"b")
        z.writestr("pkg-1.0.dist-info/METADATA", b"c")
        z.writestr("pkg_other/x.py", b"d")
    assert dist.wheel_tree(buffer.getvalue(), "pkg") == {"__init__.py": b"a", "sub/m.py": b"b"}


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv")
def test_sdist_holds_only_the_allowlist(tmp_path: Path) -> None:
    """Files kept out of git only through .git/info/exclude must never reach an sdist."""
    subprocess.run(
        ["uv", "build", "--sdist", "--quiet", "--out-dir", str(tmp_path), str(ROOT)], check=True
    )
    (sdist,) = tmp_path.glob("*.tar.gz")
    with tarfile.open(sdist) as tar:
        top = {name.split("/")[1] for name in tar.getnames() if "/" in name}
    assert top == {
        ".gitignore",
        ".importlinter",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "Makefile",
        "PKG-INFO",
        "README.md",
        "SECURITY.md",
        "ocsf.lock",
        "packaging",
        "pyproject.toml",
        "src",
        "tests",
        "uv.lock",
    }
