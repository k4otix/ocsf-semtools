"""Shared test types and helpers for the CLI tests (fixtures live in conftest.py)."""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Mirrors the upstream traps: tags <=1.2.0 carry a "v"; tag 1.5.0 points at a 1.6.0-dev commit
# while the real 1.5.0 is the head of branch v1.5.0; 1.9.0 is an annotated tag here.
FAKE_COMPILER = """\
import json, pathlib, sys
src = pathlib.Path(sys.argv[1])
version = json.loads((src / "version.json").read_text())["version"]
print(json.dumps({"version": version, "classes": {"z_class": {}, "a_class": {}}}))
"""


@dataclass(frozen=True)
class Upstream:
    url: str
    commits: dict[str, str]  # label -> commit SHA


def git(cwd: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return out.stdout.strip()


def commit_version(repo: Path, version: str) -> str:
    (repo / "version.json").write_text(json.dumps({"version": version}))
    git(repo, "add", "version.json")
    git(repo, "commit", "--quiet", "-m", f"version {version}")
    return git(repo, "rev-parse", "HEAD")
