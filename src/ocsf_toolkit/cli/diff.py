"""The ``diff`` subcommand: what changed between two OCSF versions or IR files."""

import argparse
import json
import sys
from pathlib import Path

from ocsf_toolkit.cli.check import printable
from ocsf_toolkit.diff import Change, diff, plain
from ocsf_toolkit.ir import SchemaIR
from ocsf_toolkit.schema import load, load_ir


def register(parser: argparse.ArgumentParser) -> None:
    for name in ("a", "b"):
        parser.add_argument(name, metavar=name.upper(), help="a shipped OCSF version or an IR file")
    parser.add_argument("--json", action="store_true", help="print the diff as one JSON object")
    parser.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    """Exit 0 when the IRs are equal, 1 when they differ, 2 when one can't be read."""
    try:
        a, b = _ir(args.a), _ir(args.b)
    except (ValueError, OSError) as exc:
        print(f"ocsf-toolkit: error: {exc}", file=sys.stderr)
        return 2
    result = diff(a, b)
    if args.json:
        print(json.dumps(result.to_dict()))
    else:
        for change in result.changes:
            print(printable(_line(change)))
    return 1 if result.changes else 0


def _ir(source: str) -> SchemaIR:
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
