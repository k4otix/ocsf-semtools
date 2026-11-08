"""The ``ocsf-semtools`` console script: an argparse root dispatching to one subcommand module."""

import argparse
import os
import subprocess
import sys
from collections.abc import Sequence

from ocsf_semtools import __version__
from ocsf_semtools.cli import check, compile, diff, generate, ir, lock, models
from ocsf_semtools.cli._common import options

COMMANDS = (compile, lock, ir, check, diff, generate, models)  # in --help order


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser: the root options and one subparser per command."""
    shared = options()
    parser = argparse.ArgumentParser(prog="ocsf-semtools", description="Version-pinned OCSF schema tooling.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    for command in COMMANDS:
        command.register(sub.add_parser, shared)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command and return its exit status: 0 clean, 1 findings or differences, 2 the command failed.

    A failure (bad input, a missing file, an unknown version, profile or class) prints one line to
    stderr, as argparse does for a usage error, which also exits 2.
    """
    args = build_parser().parse_args(argv)
    try:
        status: int = args.run(args)
        sys.stdout.flush()  # a closed pipe shows up here for output that fit in the buffer
    except BrokenPipeError:
        # The reader went away (`| head`): not an error of ours. Point stdout at devnull so the
        # interpreter's own flush at exit doesn't raise again, and stop quietly.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 1
    except KeyError as exc:  # its str() quotes the message
        print(f"ocsf-semtools: error: {exc.args[0] if exc.args else exc}", file=sys.stderr)
        return 2
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"ocsf-semtools: error: {exc}", file=sys.stderr)
        return 2
    return status
