"""The ``diff`` subcommand: what changed between two OCSF versions or IR files."""

import argparse
import json
import sys
from pathlib import Path

from ocsf_toolkit.cli.check import printable
from ocsf_toolkit.diff import Change, SchemaDiff, diff, plain
from ocsf_toolkit.ir import SchemaIR
from ocsf_toolkit.schema import load, load_ir


def register(parser: argparse.ArgumentParser) -> None:
    """Add the ``diff`` arguments to its subparser."""
    for name in ("a", "b"):
        parser.add_argument(name, metavar=name.upper(), help="a shipped OCSF version or an IR file")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="print the diff as one JSON object")
    output.add_argument(
        "--semver",
        action="store_true",
        help="print only the release category for re-pinning A as B: patch, minor or major",
    )
    parser.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    """Exit 0 when the IRs are equal, 1 when they differ, 2 when one can't be read."""
    try:
        a, b = source_ir(args.a), source_ir(args.b)
    except (ValueError, OSError) as exc:
        print(f"ocsf-toolkit: error: {exc}", file=sys.stderr)
        return 2
    result = diff(a, b)
    if args.semver:
        category, counted = semver(result)
        print(f"{category} ({counted} change(s) outside provenance)")
    elif args.json:
        print(json.dumps(result.to_dict()))
    else:
        for change in result.changes:
            print(printable(_line(change)))
    return 1 if result.changes else 0


def semver(result: SchemaDiff) -> tuple[str, int]:
    """Return the library release a changed IR of an already-shipped OCSF version calls for.

    Repo plan §4.1 gray case: nothing -> patch, only additions -> minor, anything else -> major.

    Provenance is left out: a compiler bump rewrites it without changing what the IR says.
    """
    changes = [c for c in result.changes if c.path[:1] != ("provenance",)]
    if not changes:
        return "patch", 0
    if all(c.kind == "added" for c in changes):
        return "minor", len(changes)
    return "major", len(changes)


def source_ir(source: str) -> SchemaIR:
    """Load a shipped OCSF version by number, or an IR JSON file by path."""
    path = Path(source)
    if source.endswith(".json") or path.is_file():
        return load_ir(path)
    return load(source).ir


def _line(c: Change) -> str:
    where = ".".join(str(p) for p in c.path)
    if c.kind == "renamed":
        return f"renamed {where} -> {c.after}"
    if c.kind != "changed":
        return f"{c.kind} {where}"
    return f"changed {where}: {json.dumps(plain(c.before))} -> {json.dumps(plain(c.after))}"
