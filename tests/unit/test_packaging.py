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
    spec = importlib.util.spec_from_file_location("version_dist", ROOT / "packaging" / "version_dist" / "dist.py")
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


def _wheel(requires: str, files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for name, data in files.items():
            z.writestr(f"pkg/{name}", data)
        z.writestr("pkg-0.1.0.dist-info/METADATA", f"Name: pkg\nRequires-Dist: {requires}\n")
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("requires", "core", "admitted"),
    [
        ("ocsf-toolkit<0.2,>=0.1.0", "0.1.5", True),
        ("ocsf-toolkit<0.2,>=0.1.0", "0.2.0", False),  # pre-1.0: the next minor may break
        ("ocsf-toolkit>=1.2.0,<2", "1.9.0", True),
        ("ocsf-toolkit>=1.2.0,<2", "2.0.0", False),
        ("ocsf-toolkit<2,>=1.2.0", "1.10.0", True),  # numeric, not text, comparison
        ("ocsf-toolkit==0.1.0", "0.1.0", False),  # an exact pin always means republish
    ],
)
def test_admits(requires: str, core: str, admitted: bool) -> None:
    assert dist.admits(_wheel(requires, {}), core) is admitted


def test_needs_release() -> None:
    files = {"__init__.py": b"x"}
    tree = dist.files_sha256(files)
    current = _wheel("ocsf-toolkit<0.2,>=0.1.0", files)
    assert dist.needs_release(None, "pkg", tree, "0.1.1")  # never published
    assert not dist.needs_release(current, "pkg", tree, "0.1.1")  # same tree, core admitted
    assert dist.needs_release(current, "pkg", "0" * 64, "0.1.1")  # the tree changed
    assert dist.needs_release(current, "pkg", tree, "0.2.0")  # core outside its range


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv")
def test_sdist_holds_only_the_allowlist(tmp_path: Path) -> None:
    """Files kept out of git only through .git/info/exclude must never reach an sdist."""
    subprocess.run(["uv", "build", "--sdist", "--quiet", "--out-dir", str(tmp_path), str(ROOT)], check=True)
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
        "docs",
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
