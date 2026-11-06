"""The ``ocsf-toolkit`` console script: an argparse root dispatching to one subcommand module."""

import argparse
import os
import subprocess
import sys
from collections.abc import Sequence

from ocsf_toolkit import __version__
from ocsf_toolkit.cli import check, compile, diff, generate, ir, lock, models
from ocsf_toolkit.cli._common import options

COMMANDS = (compile, lock, ir, check, diff, generate, models)  # in --help order


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser: the root options and one subparser per command."""
    shared = options()
    parser = argparse.ArgumentParser(prog="ocsf-toolkit", description="Version-pinned OCSF schema tooling.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    for command in COMMANDS:
        command.register(sub.add_parser, shared)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command and return its exit status; an expected failure prints one line to stderr."""
    args = build_parser().parse_args(argv)
    try:
        status: int = args.run(args)
        sys.stdout.flush()  # a closed pipe shows up here for output that fit in the buffer
    except BrokenPipeError:
        # The reader went away (`| head`): not an error of ours. Point stdout at devnull so the
        # interpreter's own flush at exit doesn't raise again, and stop quietly.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 1
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"ocsf-toolkit: error: {exc}", file=sys.stderr)
        return 1
    return status
