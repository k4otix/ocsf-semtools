"""The ``generate`` subcommand: from a version, an IR file, or a lockfile version with --ext."""

import json
import shlex
import sys
from pathlib import Path

import pytest

from ocsf_toolkit.cli import build_parser, generate, lock, main
from ocsf_toolkit.export import load_ir
from tests.conftest import DATA_DIR
from tests.fixtures.compiled import compiled
from tests.unit.cli.support import Upstream


def test_parser() -> None:
    args = build_parser().parse_args(
        ["generate", "1.9.0", "--package", "p", "--out", "o", "--ext", "a", "--ext", "b"]
    )
    assert (args.run, args.source, args.package, args.out) == (generate.run, "1.9.0", "p", "o")
    assert (args.profiles, args.extensions, args.ext) == (None, None, ["a", "b"])


def test_generate_a_shipped_version(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["generate", "1.9.0", "--package", "models", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "models" / "classes" / "detection_finding.py").is_file()
    out = capsys.readouterr().out
    assert out.startswith(f"{tmp_path / 'models'}: OCSF 1.9.0, superset, 2")


def test_generate_exact_from_an_ir_file(tmp_path: Path) -> None:
    ir = str(DATA_DIR / "ir-1.9.0.json")
    argv = ["generate", ir, "--package", "m", "--out", str(tmp_path)]
    assert main([*argv, "--profiles", "cloud, host", "--extensions", ""]) == 0
    init = (tmp_path / "m" / "__init__.py").read_text()
    assert '__ocsf_profiles__: tuple[str, ...] | None = (\n    "cloud",\n    "host",\n)' in init
    assert "__ocsf_extensions__: tuple[str, ...] = ()" in init
    assert not (tmp_path / "m" / "ext").exists()


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--profiles", "nope"], "unknown profiles ['nope']"),
        (["--package", "my.pkg"], "single Python identifier"),
        (["--ext", "missing-dir"], "not a directory"),
    ],
)
def test_errors_exit_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: list[str], message: str
) -> None:
    argv = ["generate", "1.9.0", "--package", "m", "--out", str(tmp_path), *extra]
    assert main(argv) == 1
    assert message in capsys.readouterr().err


# --- custom extensions: compile a lockfile version with -e PATH -----------------------------

EXT_COMPILER = """\
import json, pathlib, sys
src, rest = pathlib.Path(sys.argv[1]), sys.argv[2:]
pathlib.Path(sys.argv[0]).with_suffix(".argv").write_text(json.dumps(rest))
data = json.loads(pathlib.Path(sys.argv[0]).with_suffix(".json").read_text())
data["version"] = json.loads((src / "version.json").read_text())["version"]
print(json.dumps(data))
"""


def test_generate_with_custom_extensions(tmp_path: Path, upstream: Upstream) -> None:
    script = tmp_path / "ext_compiler.py"
    script.write_text(EXT_COMPILER)
    script.with_suffix(".json").write_text(json.dumps(compiled()))  # what the compiler "emits"
    ext = tmp_path / "myext"
    (ext / "acme").mkdir(parents=True)
    (ext / "acme" / "extension.json").write_text('{"name": "acme"}')
    lockfile = tmp_path / "ocsf.lock"
    toolchain = lock.Toolchain(
        ocsf_schema_compiler="1.1.1", python_compile="3.14", msgspec="0.22.0", ir_version="1.0"
    )
    entry = lock.SchemaEntry(
        version="1.9.0",
        repo=upstream.url,
        ref="1.9.0",
        commit=upstream.commits["1.9.0"],
        compiled_sha256="0" * 64,  # not checked: extensions change the output
        ir_sha256="",
        models_tree_sha256="",
        ship_models=False,
    )
    lock.write(lockfile, lock.Lockfile(toolchain, (entry,)))
    argv = [
        "generate",
        "1.9.0",
        "--ext",
        str(ext),
        "--package",
        "acme_models",
        "--out",
        str(tmp_path / "gen"),
        "--lock",
        str(lockfile),
        "--build-dir",
        str(tmp_path / "build"),
        "--compiler-cmd",
        f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}",
    ]
    assert main(argv) == 0
    assert json.loads(script.with_suffix(".argv").read_text()) == ["-e", str(ext)]
    ir = load_ir((tmp_path / "gen" / "acme_models" / "ir.json").read_bytes())  # bundled
    assert ir.version == "1.9.0"
    assert ir.provenance.commit == upstream.commits["1.9.0"]
    assert ir.provenance.extensions == (("myext", "local", generate.tree_sha256(ext)),)
    assert not (tmp_path / "build" / "compiled").exists()  # the lockfile's output is untouched
