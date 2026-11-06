"""``ocsf-toolkit ir export`` / ``ir check``, offline against a synthetic compiled schema."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from ocsf_toolkit.cli import build_parser, ir, lock, main
from ocsf_toolkit.export import load_ir
from tests.fixtures.compiled import compiled

VERSION = "9.9.9"


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A lockfile with one entry whose build/compiled schema is present and verified."""
    data = json.dumps(compiled(), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    out = tmp_path / "build" / "compiled" / f"schema-{VERSION}.json"
    out.parent.mkdir(parents=True)
    out.write_bytes(data)
    entry = lock.SchemaEntry(
        version=VERSION,
        repo="https://github.com/ocsf/ocsf-schema",
        ref="v9.9.9",
        commit="a" * 40,
        compiled_sha256=hashlib.sha256(data).hexdigest(),
        ir_sha256="",
        models_tree_sha256="",
        ship_models=False,
    )
    toolchain = lock.Toolchain(ocsf_schema_compiler="1.1.1", python_compile="3.14", msgspec="0.22.0", ir_version="1.0")
    lock.write(tmp_path / "ocsf.lock", lock.Lockfile(toolchain, (entry,)))
    return tmp_path


def _run(workspace: Path, *args: str) -> int:
    action, *rest = args
    return main(
        [
            "ir",
            action,
            *rest,
            "--lock",
            str(workspace / "ocsf.lock"),
            "--build-dir",
            str(workspace / "build"),
            "--data-dir",
            str(workspace / "data"),
        ]
    )


def test_export_requires_record_for_a_new_ir(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(workspace, "export", VERSION) == 1
    assert "pass --record to accept it" in capsys.readouterr().err
    assert not (workspace / "data").exists()


def test_export_record_then_check(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(workspace, "export", VERSION, "--record") == 0
    out = workspace / "data" / f"ir-{VERSION}.json"
    entry = lock.read(workspace / "ocsf.lock").schemas[0]
    assert entry.ir_sha256 == hashlib.sha256(out.read_bytes()).hexdigest()
    provenance = load_ir(out).provenance
    assert (provenance.ref, provenance.commit) == ("v9.9.9", "a" * 40)
    assert provenance.compiler_output_sha256 == entry.compiled_sha256
    capsys.readouterr()
    assert _run(workspace, "check") == 0  # default: every entry with an ir_sha256
    assert f"{VERSION}: ok  ir_sha256 {entry.ir_sha256}" in capsys.readouterr().out
    assert _run(workspace, "export") == 0  # unchanged IR needs no --record


def test_check_fails_on_tampered_ir(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(workspace, "export", VERSION, "--record") == 0
    (workspace / "data" / f"ir-{VERSION}.json").write_text("{}\n")
    assert _run(workspace, "check", VERSION) == 1
    assert "does not match the IR rebuilt" in capsys.readouterr().err


def test_check_fails_on_changed_ir(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = workspace / "ocsf.lock"
    lockfile = lock.read(path)
    lock.write(path, replace(lockfile, schemas=(replace(lockfile.schemas[0], ir_sha256="f" * 64),)))
    assert _run(workspace, "check", VERSION) == 1
    assert "the loader or the compiled schema changed" in capsys.readouterr().err


def test_check_without_recorded_ir(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(workspace, "check", VERSION) == 1
    assert "has no ir_sha256" in capsys.readouterr().err
    assert _run(workspace, "check") == 1
    assert "no lockfile entry has an ir_sha256" in capsys.readouterr().err


def test_missing_compiled_schema(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (workspace / "build" / "compiled" / f"schema-{VERSION}.json").unlink()
    assert _run(workspace, "export", VERSION, "--record") == 1
    assert f"run `ocsf-toolkit compile {VERSION}` first" in capsys.readouterr().err


def test_stale_compiled_schema(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (workspace / "build" / "compiled" / f"schema-{VERSION}.json").write_text("{}")
    assert _run(workspace, "export", VERSION, "--record") == 1
    assert "does not match compiled_sha256" in capsys.readouterr().err


def test_toolchain_ir_version_must_match(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = workspace / "ocsf.lock"
    lockfile = lock.read(path)
    lock.write(path, replace(lockfile, toolchain=replace(lockfile.toolchain, ir_version="2.0")))
    assert _run(workspace, "export", VERSION, "--record") == 1
    assert "ir_version is 2.0, this library writes 1.0" in capsys.readouterr().err


def test_parser() -> None:
    args = build_parser().parse_args(["ir", "export", "1.9.0", "--record", "--data-dir", "d"])
    assert (args.run, args.ir_action, args.versions, args.record) == (
        ir.export,
        "export",
        ["1.9.0"],
        True,
    )
    assert (args.data_dir, args.lock, args.build_dir) == ("d", "ocsf.lock", "build")
    args = build_parser().parse_args(["ir", "check"])
    assert (args.ir_action, args.versions, args.data_dir) == ("check", [], ir.DATA_DIR)
    assert not hasattr(args, "record")
    with pytest.raises(SystemExit):
        build_parser().parse_args(["ir"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["ir", "check", "--record"])
