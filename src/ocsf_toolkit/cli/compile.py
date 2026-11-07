"""The ``compile`` subcommand: rebuild ``build/compiled/schema-<ver>.json`` for lockfile entries."""

import argparse
from pathlib import Path

from ocsf_toolkit.cli import _upstream, lock
from ocsf_toolkit.cli._common import AddParser, Options


def register(add_parser: AddParser, shared: Options) -> None:
    """Add the ``compile`` subcommand."""
    parser = add_parser(
        "compile",
        parents=shared.compiling,
        help="compile lockfile schemas and check their hashes",
        description="Clone each pinned commit, compile it with ocsf-schema-compiler under "
        "Python 3.14, and assert compiled_sha256 and the compiled version.",
    )
    parser.add_argument(
        "versions",
        nargs="*",
        metavar="VERSION",
        help="OCSF versions to compile (default: every lockfile entry)",
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="write each compiled sha256 to the lockfile instead of checking it, after a compiler bump",
    )
    parser.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    """Compile each requested lockfile schema and check its hash and version, or record the hash."""
    path = Path(args.lock)
    lockfile = lock.read(path)
    versions: list[str] = args.versions or [entry.version for entry in lockfile.schemas]
    for version in versions:
        entry = lock.find(lockfile, version)
        out, digest = _upstream.compile_commit(
            repo=entry.repo,
            commit=entry.commit,
            version=entry.version,
            sha256=None if args.record else entry.compiled_sha256,  # the version is checked either way
            compiler=lock.compiler(lockfile, args.compiler_cmd),
            build_dir=Path(args.build_dir),
        )
        if args.record and digest != entry.compiled_sha256:
            lockfile = lock.with_entry(lockfile, entry, compiled_sha256=digest)
            lock.write(path, lockfile)
            print(f"{entry.version}: recorded compiled_sha256 {digest} -> {path}")
        print(
            f"{entry.version}: ok  ref {entry.ref}  commit {entry.commit}\n"
            f"{entry.version}: compiled version matches the declared one\n"
            f"{entry.version}: sha256 {digest} == lockfile -> {out}"
        )
    return 0
