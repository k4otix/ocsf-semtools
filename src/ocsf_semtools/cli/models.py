"""The ``models`` subcommand: regeneration gate 3, each model-window tree against its lockfile hash."""

import argparse
from collections.abc import Callable
from pathlib import Path

from ocsf_semtools.cli import lock
from ocsf_semtools.cli._common import AddParser, Options
from ocsf_semtools.cli.trees import files_sha256, package_name, read_tree
from ocsf_semtools.codegen import render
from ocsf_semtools.schema import load

DISTS_DIR = "build/dists"


def register(add_parser: AddParser, shared: Options) -> None:
    """Add the ``models check`` and ``models record`` subcommands."""
    parser = add_parser(
        "models",
        help="check or record the generated model trees (regeneration gate)",
        description="Render each model-window version twice from its shipped IR. check asserts the "
        "tree is stable and matches models_tree_sha256 in the lockfile; record writes that hash.",
    )
    parents = [shared.lockfile]
    actions = parser.add_subparsers(dest="models_action", required=True, metavar="ACTION")
    run: Callable[[argparse.Namespace], int]
    for name, help_text, run in (
        ("check", "assert each generated tree is stable and matches models_tree_sha256", check),
        ("record", "write each generated tree's sha256 to the lockfile as models_tree_sha256", record),
    ):
        action = actions.add_parser(name, parents=parents, help=help_text, description=help_text)
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
                help="also check <dists>/<package>/<package> where it exists (default: %(default)s)",
            )
        action.set_defaults(run=run)


def tree(version: str) -> dict[str, bytes]:
    """Render the model package a release ships for a version (superset, every extension), twice.

    The two renders must be identical; a difference is a ValueError.
    """
    rendered = render(load(version).ir, package=package_name(version))
    if render(load(version).ir, package=package_name(version)) != rendered:
        raise ValueError(f"OCSF {version}: two renders of the model package differ")
    return rendered


def check(args: argparse.Namespace) -> int:
    """Assert each model-window tree matches models_tree_sha256, and so does its built dist."""
    lockfile = lock.read(Path(args.lock))
    for entry in _entries(lockfile, args.versions):
        digest = files_sha256(tree(entry.version))
        if digest != entry.models_tree_sha256:
            raise ValueError(
                f"models for {entry.version} have sha256 {digest}, lockfile has "
                f"{entry.models_tree_sha256 or 'none'}; review the change, then "
                f"`ocsf-semtools models record {entry.version}`"
            )
        print(f"{entry.version}: ok  models_tree_sha256 {digest} == lockfile (rendered twice)")
        dist = Path(args.dists) / package_name(entry.version) / package_name(entry.version)
        if dist.is_dir():
            if files_sha256(read_tree(dist)) != digest:
                raise ValueError(f"{dist} is not the tree the lockfile pins; rerun `make dists`")
            print(f"{entry.version}: ok  {dist} == lockfile")
    return 0


def record(args: argparse.Namespace) -> int:
    """Write each model-window tree's sha256 to the lockfile as models_tree_sha256."""
    path = Path(args.lock)
    lockfile = lock.read(path)
    for entry in _entries(lockfile, args.versions):
        digest = files_sha256(tree(entry.version))
        lockfile = lock.with_entry(lockfile, lock.find(lockfile, entry.version), models_tree_sha256=digest)
        lock.write(path, lockfile)
        print(f"{entry.version}: recorded models_tree_sha256 {digest} -> {path}")
    return 0


def _entries(lockfile: lock.Lockfile, versions: list[str]) -> list[lock.SchemaEntry]:
    """Return the entries to work on: the named versions, or the whole model window."""
    entries = [lock.find(lockfile, v) for v in versions or lock.window(lockfile)]
    for entry in entries:
        if not entry.ship_models:
            raise ValueError(f"OCSF {entry.version} is outside the model window (ship_models = false)")
    return entries
