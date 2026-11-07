"""The ``diff`` subcommand: what changed between two OCSF versions or IR files."""

import argparse
import json

from ocsf_toolkit.cli._common import AddParser, Options, printable, source_ir
from ocsf_toolkit.diff import Change, SchemaDiff, diff, plain


def register(add_parser: AddParser, shared: Options) -> None:
    """Add the ``diff`` subcommand."""
    parser = add_parser(
        "diff",
        help="show what changed between two OCSF versions",
        description="Compare two IRs field by field, matching classes by uid so a renamed "
        "class shows as a rename; exit 0 if equal, 1 if they differ, 2 on error.",
    )
    for name in ("old", "new"):
        parser.add_argument(name, metavar=name.upper(), help="a shipped OCSF version or an IR file")
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="print the diff as one JSON object")
    output.add_argument(
        "--semver",
        action="store_true",
        help="print only the release category for re-pinning OLD as NEW: patch, minor or major",
    )
    parser.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    """Print the diff of two IRs; return 0 when they are equal and 1 when they differ."""
    result = diff(source_ir(args.old), source_ir(args.new))
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

    Nothing changed is patch, only additions minor, anything else major.

    Provenance is left out: a compiler bump rewrites it without changing what the IR says.
    """
    changes = [c for c in result.changes if c.path[:1] != ("provenance",)]
    if not changes:
        return "patch", 0
    if all(c.kind == "added" for c in changes):
        return "minor", len(changes)
    return "major", len(changes)


def _line(c: Change) -> str:
    where = ".".join(str(p) for p in c.path)
    if c.kind == "renamed":
        return f"renamed {where} -> {c.after}"
    if c.kind != "changed":
        return f"{c.kind} {where}"
    return f"changed {where}: {json.dumps(plain(c.before))} -> {json.dumps(plain(c.after))}"
