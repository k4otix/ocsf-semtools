"""The ``generate`` subcommand: a typed model package from an OCSF version or IR (D#5)."""

import argparse
from pathlib import Path

from ocsf_toolkit.cli import _upstream, lock
from ocsf_toolkit.cli._common import source_ir
from ocsf_toolkit.cli.trees import files_sha256, read_tree
from ocsf_toolkit.codegen import generate
from ocsf_toolkit.ir import Provenance, SchemaIR
from ocsf_toolkit.loader import load_compiled


def register(parser: argparse.ArgumentParser) -> None:
    """Add the ``generate`` arguments to its subparser."""
    parser.add_argument(
        "source",
        metavar="SOURCE",
        help="a shipped OCSF version or an IR file; with --ext, a lockfile version to compile",
    )
    parser.add_argument("--package", required=True, help="import name of the generated package")
    parser.add_argument("--out", required=True, help="directory to write <package>/ into")
    parser.add_argument(
        "--profiles",
        help="comma-separated profiles: emit exact classes for this set (default: superset)",
    )
    parser.add_argument(
        "--extensions",
        help="comma-separated extensions to include (default: all; empty for none)",
    )
    parser.add_argument(
        "--ext",
        action="append",
        default=[],
        metavar="PATH",
        help="a directory of custom extensions to compile SOURCE with (repeatable; compiled through uvx "
        "under the lockfile's python-compile)",
    )
    parser.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    """Generate the model package and print where it was written."""
    ir = _extended(args) if args.ext else source_ir(args.source)
    try:
        path = generate(
            ir,
            package=args.package,
            out=Path(args.out),
            profiles=_names(args.profiles),
            extensions=_names(args.extensions),
        )
    except KeyError as exc:  # an unknown profile or extension name
        raise ValueError(exc.args[0]) from exc
    modules = sum(1 for _ in path.rglob("*.py"))
    mode = f"exact for profiles {args.profiles}" if args.profiles is not None else "superset"
    print(f"{path}: OCSF {ir.version}, {mode}, {modules} modules")
    return 0


def _names(value: str | None) -> list[str] | None:
    return None if value is None else [n.strip() for n in value.split(",") if n.strip()]


def _extended(args: argparse.Namespace) -> SchemaIR:
    """Compile a lockfile version with custom extension directories into an IR."""
    lockfile = lock.read(Path(args.lock))
    entry = lock.find(lockfile, args.source)
    paths = [Path(p) for p in args.ext]
    for path in paths:
        if not path.is_dir():
            raise ValueError(f"--ext {path}: not a directory")
    data, digest = _upstream.compiled_output(
        repo=entry.repo,
        commit=entry.commit,
        version=entry.version,
        sha256=None,  # extensions change the output; the declared version is still checked
        compiler=lock.compiler(lockfile, args.compiler_cmd),
        build_dir=Path(args.build_dir),
        extensions=paths,
    )
    provenance = Provenance(
        schema_repo=entry.repo,
        ref=entry.ref,
        commit=entry.commit,
        compiler_version=lockfile.toolchain.ocsf_schema_compiler,
        compiler_output_sha256=digest,
        extensions=tuple((p.resolve().name, "local", files_sha256(read_tree(p))) for p in paths),
    )
    return load_compiled(data, provenance)
