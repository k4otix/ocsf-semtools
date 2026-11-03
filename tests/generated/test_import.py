"""S4 laziness and acc. #1: the dist imports on demand, and installs into a clean environment."""

import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tests.conftest import ROOT
from tests.generated.conftest import DIST, PACKAGE

LAZY = f"""
import json, sys, time
import msgspec  # timed apart: it is a dependency, not part of the generated tree
start = time.perf_counter()
import {PACKAGE}
root = sorted(m for m in sys.modules if m.startswith("{PACKAGE}."))
from {PACKAGE} import DetectionFinding
elapsed = time.perf_counter() - start
classes = sorted(m for m in sys.modules if m.startswith("{PACKAGE}.classes."))
print(json.dumps({{"root": root, "classes": classes, "ms": elapsed * 1000}}))
"""


def test_classes_load_on_demand() -> None:
    env = {"PYTHONPATH": str(DIST), "PATH": ""}
    result = subprocess.run(
        [sys.executable, "-c", LAZY], capture_output=True, text=True, env=env, check=True
    )
    found = json.loads(result.stdout)
    assert found["root"] == []  # the package root imports nothing until a name is used
    assert found["classes"] == [f"{PACKAGE}.classes.detection_finding"]
    print(f"cold import of DetectionFinding: {found['ms']:.1f} ms")  # S4 target: < 50 ms


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv")
def test_installs_into_a_clean_environment(tmp_path: Path) -> None:
    """acc. #1: install the dist and the core into a fresh venv; msgspec comes as a declared dep."""
    pin = tomllib.loads((DIST / "pyproject.toml").read_text())["project"]["dependencies"]
    venv = tmp_path / "venv"
    subprocess.run(["uv", "venv", "--quiet", "--python", sys.executable, str(venv)], check=True)
    python = venv / "bin" / "python"
    install = subprocess.run(
        ["uv", "pip", "install", "--quiet", "--python", str(python), str(ROOT), str(DIST)],
        capture_output=True,
        text=True,
    )
    assert install.returncode == 0, f"{install.stderr}\n(dist pins {pin}; rerun `make generate`)"
    check = f"""
from importlib.metadata import requires
from {PACKAGE} import DetectionFinding
print(DetectionFinding.__ocsf__, sorted(requires("{PACKAGE.replace("_", "-")}")))
"""
    result = subprocess.run([str(python), "-I", "-c", check], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("('1.9.0', 'detection_finding')")
    assert "msgspec<1,>=0.22" in result.stdout
