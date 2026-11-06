"""The ``check`` subcommand: a conformance report for each event in JSON Lines files."""

import argparse
import contextlib
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TextIO, cast

from ocsf_toolkit.models import parse
from ocsf_toolkit.schema import load
from ocsf_toolkit.walk import dotted


def register(parser: argparse.ArgumentParser) -> None:
    """Add the ``check`` arguments to its subparser."""
    parser.add_argument("--version", required=True, help="OCSF version to check against")
    parser.add_argument(
        "--profiles",
        help="comma-separated active profiles (default: each event's metadata.profiles)",
    )
    parser.add_argument(
        "--class",
        dest="class_key",
        metavar="KEY",
        help="class key (default: inferred from class_uid, then type_uid)",
    )
    parser.add_argument("--json", action="store_true", help="print one JSON report per event instead of text")
    parser.add_argument("files", nargs="+", metavar="FILE", help="JSON Lines file of events; - reads stdin")
    parser.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    """Check each event and print its findings; return 1 if any event has an error."""
    profiles = None if args.profiles is None else [p for p in args.profiles.split(",") if p]
    try:  # fail once on a bad selection rather than once per event
        schema = load(args.version, profiles=profiles or ())
    except KeyError as exc:
        raise ValueError(exc.args[0]) from None
    if args.class_key is not None and args.class_key not in schema.classes:
        raise ValueError(f"no class {args.class_key!r} in OCSF {args.version}")
    failed = 0
    for name, line, text in _lines(args.files):
        where = f"{name}:{line}"
        try:
            event: object = json.loads(text)
        except ValueError as exc:
            print(f"{where}: error: not JSON: {exc}")
            failed += 1
            continue
        if not isinstance(event, dict):
            print(f"{where}: error: not a JSON object")
            failed += 1
            continue
        _, report = parse(
            cast(dict[str, Any], event),
            version=args.version,
            profiles=profiles,
            class_key=args.class_key,
        )
        failed += not report.ok()
        if args.json:
            print(json.dumps({"source": where, **report.to_dict()}))  # ASCII: no raw controls
            continue
        for f in report.findings:
            if f.severity != "info":
                path = dotted(f.path) or "."
                print(printable(f"{where}: {f.severity} {f.kind.value} {path}: {f.message}"))
    return 1 if failed else 0


def printable(text: str) -> str:
    """Escape control characters: paths and messages carry keys taken from the input."""
    return "".join(c if c.isprintable() else repr(c)[1:-1] for c in text)


def _lines(files: list[str]) -> Iterator[tuple[str, int, str]]:
    for name in files:
        stream: contextlib.AbstractContextManager[TextIO] = (
            contextlib.nullcontext(sys.stdin) if name == "-" else Path(name).open(encoding="utf-8")  # noqa: SIM115 -- entered on the next line
        )
        with stream as lines:
            for n, text in enumerate(lines, 1):
                if text.strip():
                    yield name, n, text
