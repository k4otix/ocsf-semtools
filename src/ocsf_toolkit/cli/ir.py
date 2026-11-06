"""The ``ir`` subcommand: write canonical IR JSON for lockfile entries, or check it."""

import argparse
import hashlib
from dataclasses import replace
from pathlib import Path

from ocsf_toolkit.cli import lock
from ocsf_toolkit.export import IR_VERSION, export_ir
from ocsf_toolkit.ir import Provenance
from ocsf_toolkit.loader import load_compiled

DATA_DIR = "src/ocsf_toolkit/data"


def register(parser: argparse.ArgumentParser, common: argparse.ArgumentParser) -> None:
    """Add the ``ir export`` and ``ir check`` subcommands."""
    actions = parser.add_subparsers(dest="ir_action", required=True, metavar="ACTION")
    for name, help_text in (
        ("export", "write data/ir-<ver>.json from build/compiled/schema-<ver>.json"),
        ("check", "assert the IR rebuilt from build/compiled matches ir_sha256 and data/"),
    ):
        action = actions.add_parser(name, parents=[common], help=help_text, description=help_text)
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


def build(entry: lock.SchemaEntry, toolchain: lock.Toolchain, build_dir: Path) -> bytes:
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


def run(args: argparse.Namespace) -> int:
    """Export or check the IR of each requested lockfile version against its ir_sha256."""
    lock_path = Path(args.lock)
    lockfile = lock.read(lock_path)
    versions: list[str] = args.versions or [e.version for e in lockfile.schemas if e.ir_sha256]
    if not versions:
        raise ValueError("no lockfile entry has an ir_sha256; name the versions to export")
    data_dir = Path(args.data_dir)
    for version in versions:
        entry = lock.find(lockfile, version)
        data = build(entry, lockfile.toolchain, Path(args.build_dir))
        digest = hashlib.sha256(data).hexdigest()
        out = data_dir / f"ir-{version}.json"
        if args.ir_action == "check":
            _check(entry, digest, data, out)
            print(f"{version}: ok  ir_sha256 {digest} == lockfile == {out}")
            continue
        if digest != entry.ir_sha256 and not args.record:
            raise ValueError(
                f"IR for {version} has sha256 {digest}, lockfile has {entry.ir_sha256 or 'none'}; "
                "pass --record to accept it"
            )
        data_dir.mkdir(parents=True, exist_ok=True)
        partial = out.with_suffix(".json.partial")
        partial.write_bytes(data)
        partial.replace(out)
        if digest != entry.ir_sha256:
            entries = [replace(e, ir_sha256=digest) if e is entry else e for e in lockfile.schemas]
            lockfile = replace(lockfile, schemas=tuple(entries))
            lock.write(lock_path, lockfile)
            print(f"{version}: recorded ir_sha256 {digest} -> {lock_path}")
        print(f"{version}: wrote {out} ({len(data)} bytes)")
    return 0


def _check(entry: lock.SchemaEntry, digest: str, data: bytes, out: Path) -> None:
    if not entry.ir_sha256:
        raise ValueError(f"OCSF {entry.version} has no ir_sha256 in the lockfile")
    if digest != entry.ir_sha256:
        raise ValueError(
            f"IR for {entry.version} has sha256 {digest}, lockfile has {entry.ir_sha256}; "
            "the loader or the compiled schema changed"
        )
    if not out.is_file() or out.read_bytes() != data:
        raise ValueError(f"{out} does not match the IR rebuilt from build/compiled")
