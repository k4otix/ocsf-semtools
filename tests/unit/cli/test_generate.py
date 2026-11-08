"""The ``generate`` subcommand: from a version, an IR file, or a lockfile version with --ext."""

import json
import shlex
import sys
from pathlib import Path
from typing import Any

import pytest

from ocsf_semtools.cli import _upstream, build_parser, generate, main
from ocsf_semtools.cli.trees import files_sha256, read_tree
from ocsf_semtools.export import load_ir
from ocsf_semtools.schema import load
from tests.conftest import DATA_DIR
from tests.fixtures.compiled import compiled
from tests.unit.cli.support import Upstream, entry, write_lockfile


def test_parser() -> None:
    args = build_parser().parse_args(["generate", "1.9.0", "--package", "p", "--out", "o", "--ext", "a", "--ext", "b"])
    assert (args.run, args.source, args.package, args.out) == (generate.run, "1.9.0", "p", "o")
    assert (args.profiles, args.extensions, args.ext) == (None, None, ["a", "b"])


def test_generate_a_shipped_version(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["generate", "1.9.0", "--package", "models", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "models" / "classes" / "detection_finding.py").is_file()
    out = capsys.readouterr().out
    modules = len(list((tmp_path / "models").rglob("*.py")))
    assert out == f"{tmp_path / 'models'}: OCSF 1.9.0, superset, {modules} modules\n"


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
def test_errors_exit_two(tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: list[str], message: str) -> None:
    argv = ["generate", "1.9.0", "--package", "m", "--out", str(tmp_path), *extra]
    assert main(argv) == 2
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
    pinned = entry(
        repo=upstream.url,
        commit=upstream.commits["1.9.0"],
        compiled_sha256="0" * 64,  # not checked: extensions change the output
        ship_models=False,
    )
    lockfile = write_lockfile(tmp_path / "ocsf.lock", pinned)
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
    assert ir.provenance.extensions == (("myext", "local", files_sha256(read_tree(ext))),)
    (ext / ".DS_Store").write_bytes(b"x")  # hidden files are no part of the extension
    (ext / ".git").mkdir()
    (ext / ".git" / "HEAD").write_text("ref: refs/heads/main")
    assert main(argv) == 0
    again = load_ir((tmp_path / "gen" / "acme_models" / "ir.json").read_bytes())
    assert again.provenance.extensions == ir.provenance.extensions
    assert not (tmp_path / "build" / "compiled").exists()  # the lockfile's output is untouched


def test_with_no_lockfile_the_shipped_ir_names_the_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A user who installed the library has no ocsf.lock; the shipped IR's provenance pins the same commit."""
    seen: dict[str, Any] = {}

    def compiled_output(**kwargs: Any) -> tuple[bytes, str]:
        seen.update(kwargs)
        raise ValueError("stop: no network in this test")

    monkeypatch.setattr(_upstream, "compiled_output", compiled_output)
    monkeypatch.chdir(tmp_path)  # no ocsf.lock here
    (tmp_path / "ext").mkdir()
    assert main(["generate", "1.9.0", "--ext", "ext", "--package", "m", "--out", "gen"]) == 2
    shipped = load("1.9.0").ir.provenance
    assert (seen["repo"], seen["commit"], seen["version"]) == (shipped.schema_repo, shipped.commit, "1.9.0")
    assert seen["compiler"] == _upstream.Compiler(shipped.compiler_version, "3.14", None)
