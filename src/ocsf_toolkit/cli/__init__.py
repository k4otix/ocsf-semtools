"""The ``ocsf-toolkit`` console script: an argparse root dispatching to one subcommand module."""

import argparse
import os
import subprocess
import sys
from collections.abc import Sequence

from ocsf_toolkit import __version__
from ocsf_toolkit.cli import check as check_command
from ocsf_toolkit.cli import compile as compile_command
from ocsf_toolkit.cli import diff as diff_command
from ocsf_toolkit.cli import generate as generate_command
from ocsf_toolkit.cli import ir as ir_command
from ocsf_toolkit.cli import lock as lock_command
from ocsf_toolkit.cli import models as models_command


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--lock", default="ocsf.lock", help="lockfile path (default: %(default)s)")
    common.add_argument("--build-dir", default="build", help="checkout cache and output root (default: %(default)s)")
    common.add_argument(
        "--compiler-cmd",
        help="command that compiles a schema checkout to stdout, replacing uvx "
        "(air-gapped builds); the checkout path is appended",
    )

    parser = argparse.ArgumentParser(prog="ocsf-toolkit", description="Version-pinned OCSF schema tooling.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    compile_command.register(
        sub.add_parser(
            "compile",
            parents=[common],
            help="compile lockfile schemas and check their hashes",
            description="Clone each pinned commit, compile it with ocsf-schema-compiler under "
            "Python 3.14, and assert compiled_sha256 and the compiled version.",
        )
    )
    lock_command.register(
        sub.add_parser(
            "lock",
            parents=[common],
            help="add an OCSF version to ocsf.lock",
            description="Resolve an upstream ref, pin its commit, compile it, and record compiled_sha256.",
        )
    )
    ir_command.register(
        sub.add_parser(
            "ir",
            help="export or check canonical IR JSON",
            description="Build the IR from build/compiled and write or check data/ir-<ver>.json.",
        ),
        common,
    )
    check_command.register(
        sub.add_parser(
            "check",
            help="report how events deviate from an OCSF version",
            description="Check each event in JSON Lines files against an OCSF version and print "
            "its findings; exit 1 if any event has an error.",
        )
    )
    diff_command.register(
        sub.add_parser(
            "diff",
            help="show what changed between two OCSF versions",
            description="Compare two IRs field by field, matching classes by uid so a renamed "
            "class shows as a rename; exit 0 if equal, 1 if they differ, 2 on error.",
        )
    )
    generate_command.register(
        sub.add_parser(
            "generate",
            parents=[common],
            help="generate a typed msgspec model package",
            description="Project an OCSF version or IR into a typed package of msgspec Structs: "
            "superset classes by default, exact classes for a fixed --profiles set.",
        )
    )
    models_command.register(
        sub.add_parser(
            "models",
            help="check or record the generated model trees (regeneration gate)",
            description="Render each model-window version twice from its shipped IR and assert "
            "the tree is stable and matches models_tree_sha256 in the lockfile.",
        ),
        common,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
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
