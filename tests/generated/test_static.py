"""N4 / acc. #9 / S3: ruff and pyright strict over the generated tree, with no exclusions."""

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.conftest import ROOT
from tests.generated.conftest import DIST, SAMPLES, TREE


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", *args], capture_output=True, text=True)


def test_ruff_format_and_lint() -> None:
    config = str(ROOT / "pyproject.toml")
    for args in (("format", "--check"), ("check",)):
        result = _run("ruff", *args, "--config", config, "--no-respect-gitignore", str(TREE))
        assert result.returncode == 0, result.stdout + result.stderr


def test_pyright_strict(tmp_path: Path) -> None:
    config = tmp_path / "pyrightconfig.json"

    def rel(path: Path) -> str:  # pyright ignores absolute include paths
        return os.path.relpath(path, tmp_path)

    config.write_text(
        json.dumps(
            {
                "include": [rel(TREE), rel(SAMPLES)],
                "extraPaths": [rel(DIST), rel(ROOT / "src")],
                "typeCheckingMode": "strict",
                "pythonVersion": "3.12",
                "reportUnnecessaryTypeIgnoreComment": "error",
            }
        )
    )
    result = _run("pyright", "-p", str(config), "--pythonpath", sys.executable, "--outputjson")
    report = json.loads(result.stdout)
    problems = [
        f"{d['file']}:{d['range']['start']['line'] + 1}: {d['message']}"
        for d in report["generalDiagnostics"]
        if d["severity"] != "information"
    ]
    assert problems == []
    assert report["summary"]["filesAnalyzed"] > 280
