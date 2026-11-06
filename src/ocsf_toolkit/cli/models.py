"""The ``models`` subcommand: regeneration gate 3 over the lockfile's model window (D-7)."""

import argparse
import hashlib
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from ocsf_toolkit.cli import lock
from ocsf_toolkit.codegen import render
from ocsf_toolkit.schema import load

DISTS_DIR = "build/dists"


def register(parser: argparse.ArgumentParser, common: argparse.ArgumentParser) -> None:
    """Add the ``models check`` and ``models record`` subcommands."""
    actions = parser.add_subparsers(dest="models_action", required=True, metavar="ACTION")
    for name, help_text in (
        ("check", "assert each generated tree is stable and matches models_tree_sha256"),
        ("record", "write each generated tree's sha256 to the lockfile as models_tree_sha256"),
    ):
        action = actions.add_parser(name, parents=[common], help=help_text, description=help_text)
        action.add_argument(
            "versions",
            nargs="*",
            metavar="VERSION",
            help="OCSF versions (default: every lockfile entry with ship_models = true)",
        )
        if name == "check":
            action.add_argument(
                "--dists",
                default=DISTS_DIR,
                help="also check build/dists/<package>/<package> where it exists (default: %(default)s)",
            )
        action.set_defaults(run=run)


def package_name(version: str) -> str:
    """Return the import name of the model dist for an OCSF version: 1.9.0 -> ocsf_toolkit_v1_9_0."""
    return "ocsf_toolkit_v" + version.replace(".", "_")


def files_sha256(files: Mapping[str, bytes]) -> str:
    """sha256 over the (relative path, bytes) pairs of a file tree, sorted by path."""
    h = hashlib.sha256()
    for path in sorted(files):
        h.update(path.encode() + b"\0" + files[path] + b"\0")
    return h.hexdigest()


def read_tree(root: Path) -> dict[str, bytes]:
    """Every file under root by relative POSIX path, without bytecode caches."""
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.relative_to(root).parts
    }


def tree(version: str) -> dict[str, bytes]:
    """Render the model package a release ships for a version: superset, every extension."""
    first = render(load(version).ir, package=package_name(version))
    if render(load(version).ir, package=package_name(version)) != first:
        raise ValueError(f"OCSF {version}: two renders of the model package differ")
    return first


def run(args: argparse.Namespace) -> int:
    """Check or record each model-window tree against models_tree_sha256 in the lockfile."""
    path = Path(args.lock)
    lockfile = lock.read(path)
    versions: list[str] = args.versions or [e.version for e in lockfile.schemas if e.ship_models]
    for version in versions:
        entry = lock.find(lockfile, version)
        if not entry.ship_models:
            raise ValueError(f"OCSF {version} is outside the model window (ship_models = false)")
        digest = files_sha256(tree(version))
        if args.models_action == "record":
            entries = [replace(e, models_tree_sha256=digest) if e is entry else e for e in lockfile.schemas]
            lockfile = replace(lockfile, schemas=tuple(entries))
            lock.write(path, lockfile)
            print(f"{version}: recorded models_tree_sha256 {digest} -> {path}")
            continue
        if digest != entry.models_tree_sha256:
            raise ValueError(
                f"models for {version} have sha256 {digest}, lockfile has "
                f"{entry.models_tree_sha256 or 'none'}; review the change, then "
                f"`ocsf-toolkit models record {version}`"
            )
        print(f"{version}: ok  models_tree_sha256 {digest} == lockfile (rendered twice)")
        dist = Path(args.dists) / package_name(version) / package_name(version)
        if dist.is_dir():
            if files_sha256(read_tree(dist)) != digest:
                raise ValueError(f"{dist} is not the tree the lockfile pins; rerun `make dists`")
            print(f"{version}: ok  {dist} == lockfile")
    return 0
