"""Model trees: the package a version's dist ships, and the hash ``ocsf.lock`` pins it by."""

import hashlib
from collections.abc import Mapping
from pathlib import Path


def package_name(version: str) -> str:
    """Return the import name of the model dist for an OCSF version: 1.9.0 -> ocsf_toolkit_v1_9_0."""
    return "ocsf_toolkit_v" + version.replace(".", "_")


def files_sha256(files: Mapping[str, bytes]) -> str:
    """Return the sha256 over the (relative path, bytes) pairs of a file tree, sorted by path."""
    h = hashlib.sha256()
    for path in sorted(files):
        h.update(path.encode() + b"\0" + files[path] + b"\0")
    return h.hexdigest()


def read_tree(root: Path) -> dict[str, bytes]:
    """Return every file under root by relative POSIX path, leaving out bytecode caches."""
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.relative_to(root).parts
    }
