"""The ``generate`` subcommand: a typed model package from an OCSF version or IR."""

import argparse
from pathlib import Path

from ocsf_semtools.cli import _upstream, lock
from ocsf_semtools.cli._common import AddParser, Options, source_ir
from ocsf_semtools.cli.trees import files_sha256, read_tree
from ocsf_semtools.codegen import generate
from ocsf_semtools.ir import Provenance, SchemaIR
from ocsf_semtools.loader import load_compiled
from ocsf_semtools.schema import load


def register(add_parser: AddParser, shared: Options) -> None:
    """Add the ``generate`` subcommand."""
    parser = add_parser(
        "generate",
        parents=shared.compiling,
        help="generate a typed msgspec model package",
        description="Project an OCSF version or IR into a typed package of msgspec Structs: "
        "superset classes by default, exact classes for a fixed --profiles set.",
    )
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
        help="a directory of custom extensions to compile SOURCE with (repeatable). SOURCE's upstream commit "
        "comes from --lock, or, with no lockfile there, from the shipped IR's provenance; compiling needs git "
        "and uv, or --compiler-cmd",
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
    """Compile an OCSF version with custom extension directories into an IR."""
    repo, ref, commit, compiler = _pin(args)
    paths = [Path(p) for p in args.ext]
    for path in paths:
        if not path.is_dir():
            raise ValueError(f"--ext {path}: not a directory")
    data, digest = _upstream.compiled_output(
        repo=repo,
        commit=commit,
        version=args.source,
        sha256=None,  # extensions change the output; the declared version is still checked
        compiler=compiler,
        build_dir=Path(args.build_dir),
        extensions=paths,
    )
    provenance = Provenance(
        schema_repo=repo,
        ref=ref,
        commit=commit,
        compiler_version=compiler.version,
        compiler_output_sha256=digest,
        extensions=tuple((p.resolve().name, "local", files_sha256(_extension_files(p))) for p in paths),
    )
    return load_compiled(data, provenance)


def _pin(args: argparse.Namespace) -> tuple[str, str, str, _upstream.Compiler]:
    """Return SOURCE's repo, ref, commit and compiler: from the lockfile, or the shipped IR's provenance.

    A user installs the library without ``ocsf.lock``; the IR it ships records the same pin.
    """
    path = Path(args.lock)
    if path.is_file():
        lockfile = lock.read(path)
        entry = lock.find(lockfile, args.source)
        return entry.repo, entry.ref, entry.commit, lock.compiler(lockfile, args.compiler_cmd)
    shipped = load(args.source).ir.provenance  # an unshipped version is a ValueError here
    compiler = _upstream.Compiler(shipped.compiler_version, _upstream.COMPILE_PYTHON, args.compiler_cmd)
    return shipped.schema_repo, shipped.ref, shipped.commit, compiler


def _extension_files(root: Path) -> dict[str, bytes]:
    """Return an extension directory's files, leaving out hidden ones such as .git and .DS_Store."""
    return {name: data for name, data in read_tree(root).items() if not any(p.startswith(".") for p in name.split("/"))}
