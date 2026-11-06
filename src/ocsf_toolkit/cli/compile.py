"""The ``compile`` subcommand: rebuild ``build/compiled/schema-<ver>.json`` for lockfile entries."""

import argparse
from pathlib import Path

from ocsf_toolkit.cli import _uvx, lock


def register(parser: argparse.ArgumentParser) -> None:
    """Add the ``compile`` arguments to its subparser."""
    parser.add_argument(
        "versions",
        nargs="*",
        metavar="VERSION",
        help="OCSF versions to compile (default: every lockfile entry)",
    )
    parser.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    """Compile each requested lockfile schema and check its hash and version."""
    lockfile = lock.read(Path(args.lock))
    versions: list[str] = args.versions or [entry.version for entry in lockfile.schemas]
    for version in versions:
        entry = lock.find(lockfile, version)
        out, digest = _uvx.compile_commit(
            repo=entry.repo,
            commit=entry.commit,
            version=entry.version,
            sha256=entry.compiled_sha256,
            compiler_version=lockfile.toolchain.ocsf_schema_compiler,
            python=lockfile.toolchain.python_compile,
            build_dir=Path(args.build_dir),
            compiler_cmd=args.compiler_cmd,
        )
        print(
            f"{entry.version}: ok  ref {entry.ref}  commit {entry.commit}\n"
            f"{entry.version}: compiled version matches the declared one\n"
            f"{entry.version}: sha256 {digest} == lockfile -> {out}"
        )
    return 0
