"""S4 laziness and acc. #1: each dist imports on demand, and installs into a clean environment."""

import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tests.conftest import ROOT
from tests.generated.conftest import Dist

LAZY = """
import json, sys, time
import msgspec  # timed apart: it is a dependency, not part of the generated tree
package = sys.argv[1]
start = time.perf_counter()
root_module = __import__(package)
root = sorted(m for m in sys.modules if m.startswith(package + "."))
root_module.DetectionFinding
elapsed = time.perf_counter() - start
classes = sorted(m for m in sys.modules if m.startswith(package + ".classes."))
print(json.dumps({"root": root, "classes": classes, "ms": elapsed * 1000}))
"""


def test_classes_load_on_demand(dist: Dist) -> None:
    env = {"PYTHONPATH": str(dist.root), "PATH": ""}
    result = subprocess.run(
        [sys.executable, "-c", LAZY, dist.package],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    found = json.loads(result.stdout)
    assert found["root"] == []  # the package root imports nothing until a name is used
    assert found["classes"] == [f"{dist.package}.classes.detection_finding"]
    print(f"{dist.version} cold import of DetectionFinding: {found['ms']:.1f} ms")  # reported, not gated


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv")
def test_installs_into_a_clean_environment(dist: Dist, tmp_path: Path) -> None:
    """Install the dist and the core into a fresh venv; msgspec comes as a declared dependency (acc. #1)."""
    requires = tomllib.loads((dist.root / "pyproject.toml").read_text())["project"]["dependencies"]
    venv = tmp_path / "venv"
    subprocess.run(["uv", "venv", "--quiet", "--python", sys.executable, str(venv)], check=True)
    python = venv / "bin" / "python"
    install = subprocess.run(
        ["uv", "pip", "install", "--quiet", "--python", str(python), str(ROOT), str(dist.root)],
        capture_output=True,
        text=True,
    )
    assert install.returncode == 0, f"{install.stderr}\n(dist requires {requires}; `make dists`)"
    check = f"""
from importlib.metadata import requires
from {dist.package} import DetectionFinding
print(DetectionFinding.__ocsf__, sorted(requires("{dist.package.replace("_", "-")}")))
"""
    result = subprocess.run([str(python), "-I", "-c", check], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"('{dist.version}', 'detection_finding')")
    assert "msgspec<1,>=0.22" in result.stdout
    assert "'ocsf-toolkit<" in result.stdout  # a range of core versions, not one pin
    assert "ocsf-toolkit==" not in result.stdout
