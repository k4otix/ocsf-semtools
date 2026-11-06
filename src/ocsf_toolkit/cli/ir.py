"""The ``ir`` subcommand: write canonical IR JSON for lockfile entries, or check it."""

import argparse
import hashlib
from collections.abc import Callable
from pathlib import Path

from ocsf_toolkit.cli import lock
from ocsf_toolkit.cli._common import AddParser, Options
from ocsf_toolkit.cli._upstream import atomic_write
from ocsf_toolkit.export import IR_VERSION, export_ir
from ocsf_toolkit.ir import Provenance
from ocsf_toolkit.loader import load_compiled

DATA_DIR = "src/ocsf_toolkit/data"


def register(add_parser: AddParser, shared: Options) -> None:
    """Add the ``ir export`` and ``ir check`` subcommands."""
    parser = add_parser(
        "ir",
        help="export or check canonical IR JSON",
        description="Build the IR from build/compiled and write or check data/ir-<ver>.json.",
    )
    parents = [shared.lockfile, shared.build]
    actions = parser.add_subparsers(dest="ir_action", required=True, metavar="ACTION")
    run: Callable[[argparse.Namespace], int]
    for name, help_text, run in (
        ("export", "write data/ir-<ver>.json from build/compiled/schema-<ver>.json", export),
        ("check", "assert the IR rebuilt from build/compiled matches ir_sha256 and data/", check),
    ):
        action = actions.add_parser(name, parents=parents, help=help_text, description=help_text)
        action.add_argument(
            "versions",
            nargs="*",
            metavar="VERSION",
            help="OCSF versions (default: every lockfile entry with an ir_sha256)",
        )
        action.add_argument("--data-dir", default=DATA_DIR, help="IR JSON directory (default: %(default)s)")
        if name == "export":
            action.add_argument(
                "--record",
                action="store_true",
                help="record the IR's sha256 as ir_sha256 in the lockfile (new or changed IR)",
            )
        action.set_defaults(run=run)


def ir_bytes(entry: lock.SchemaEntry, toolchain: lock.Toolchain, build_dir: Path) -> bytes:
    """Return the canonical IR JSON for one lockfile entry, from its verified compiled schema."""
    if toolchain.ir_version != IR_VERSION:
        raise ValueError(f"lockfile [toolchain] ir_version is {toolchain.ir_version}, this library writes {IR_VERSION}")
    path = build_dir / "compiled" / f"schema-{entry.version}.json"
    if not path.is_file():
        raise ValueError(f"{path} is missing; run `ocsf-toolkit compile {entry.version}` first")
    compiled = path.read_bytes()
    if hashlib.sha256(compiled).hexdigest() != entry.compiled_sha256:
        raise ValueError(f"{path} does not match compiled_sha256; run `ocsf-toolkit compile {entry.version}`")
    provenance = Provenance(
        schema_repo=entry.repo,
        ref=entry.ref,
        commit=entry.commit,
        compiler_version=toolchain.ocsf_schema_compiler,
        compiler_output_sha256=entry.compiled_sha256,
        extensions=(),
    )
    ir = load_compiled(compiled, provenance)
    if ir.version != entry.version:
        raise ValueError(f"{path} holds OCSF {ir.version}, not {entry.version}")
    return export_ir(ir)


def export(args: argparse.Namespace) -> int:
    """Write each version's IR to the data directory; with --record, accept a new ir_sha256."""
    lock_path = Path(args.lock)
    lockfile = lock.read(lock_path)
    for entry in _entries(lockfile, args.versions):
        data = ir_bytes(entry, lockfile.toolchain, Path(args.build_dir))
        digest = hashlib.sha256(data).hexdigest()
        if digest != entry.ir_sha256 and not args.record:
            raise ValueError(
                f"IR for {entry.version} has sha256 {digest}, lockfile has {entry.ir_sha256 or 'none'}; "
                "pass --record to accept it"
            )
        out = Path(args.data_dir) / f"ir-{entry.version}.json"
        atomic_write(out, data)
        if digest != entry.ir_sha256:
            lockfile = lock.with_entry(lockfile, lock.find(lockfile, entry.version), ir_sha256=digest)
            lock.write(lock_path, lockfile)
            print(f"{entry.version}: recorded ir_sha256 {digest} -> {lock_path}")
        print(f"{entry.version}: wrote {out} ({len(data)} bytes)")
    return 0


def check(args: argparse.Namespace) -> int:
    """Assert each version's rebuilt IR matches its ir_sha256 and the file in the data directory."""
    lockfile = lock.read(Path(args.lock))
    for entry in _entries(lockfile, args.versions):
        data = ir_bytes(entry, lockfile.toolchain, Path(args.build_dir))
        digest = hashlib.sha256(data).hexdigest()
        out = Path(args.data_dir) / f"ir-{entry.version}.json"
        if not entry.ir_sha256:
            raise ValueError(f"OCSF {entry.version} has no ir_sha256 in the lockfile")
        if digest != entry.ir_sha256:
            raise ValueError(
                f"IR for {entry.version} has sha256 {digest}, lockfile has {entry.ir_sha256}; "
                "the loader or the compiled schema changed"
            )
        if not out.is_file() or out.read_bytes() != data:
            raise ValueError(f"{out} does not match the IR rebuilt from build/compiled")
        print(f"{entry.version}: ok  ir_sha256 {digest} == lockfile == {out}")
    return 0


def _entries(lockfile: lock.Lockfile, versions: list[str]) -> list[lock.SchemaEntry]:
    """Return the entries to work on: the named versions, or every entry with an ir_sha256."""
    names = versions or [e.version for e in lockfile.schemas if e.ir_sha256]
    if not names:
        raise ValueError("no lockfile entry has an ir_sha256; name the versions to export")
    return [lock.find(lockfile, v) for v in names]
