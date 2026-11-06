"""Shared test types and helpers for the CLI tests (fixtures live in conftest.py)."""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ocsf_toolkit.cli import lock

# The committed ocsf.lock's toolchain (test_lock checks that they agree).
TOOLCHAIN = lock.Toolchain(ocsf_schema_compiler="1.1.1", python_compile="3.14", msgspec="0.22.0", ir_version="1.0")


def entry(version: str = "1.9.0", **fields: Any) -> lock.SchemaEntry:
    """Return a valid lockfile entry for a version; ``fields`` override the placeholders."""
    placeholders: dict[str, Any] = {
        "repo": lock.UPSTREAM_REPO,
        "ref": version,
        "commit": "a" * 40,
        "compiled_sha256": "",
        "ir_sha256": "",
        "models_tree_sha256": "",
        "ship_models": True,
    }
    return lock.SchemaEntry(version=version, **(placeholders | fields))


def write_lockfile(path: Path, *entries: lock.SchemaEntry) -> Path:
    """Write a lockfile of the test toolchain and these entries; return its path."""
    lock.write(path, lock.Lockfile(TOOLCHAIN, entries))
    return path


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
