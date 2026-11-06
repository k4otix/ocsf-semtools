"""argparse wiring of the ``ocsf-toolkit`` console script."""

import subprocess
import sys

import pytest

from ocsf_toolkit import __version__
from ocsf_toolkit.cli import build_parser, compile, lock


def test_compile_defaults() -> None:
    args = build_parser().parse_args(["compile"])
    assert args.run is compile.run
    assert args.versions == []
    assert (args.lock, args.build_dir, args.compiler_cmd) == ("ocsf.lock", "build", None)


def test_compile_options() -> None:
    args = build_parser().parse_args(
        [
            "compile",
            "1.9.0",
            "1.8.0",
            "--lock",
            "x.lock",
            "--build-dir",
            "out",
            "--compiler-cmd",
            "py -m c",
        ]
    )
    assert args.versions == ["1.9.0", "1.8.0"]
    assert (args.lock, args.build_dir, args.compiler_cmd) == ("x.lock", "out", "py -m c")


def test_lock_add() -> None:
    args = build_parser().parse_args(["lock", "--add", "1.5.0", "--ref", "v1.5.0"])
    assert args.run is lock.run
    assert (args.add, args.ref, args.repo) == ("1.5.0", "v1.5.0", lock.UPSTREAM_REPO)
    assert (args.lock, args.build_dir, args.compiler_cmd) == ("ocsf.lock", "build", None)


@pytest.mark.parametrize("argv", [[], ["lock"], ["compile", "--bogus"], ["frobnicate"]])
def test_usage_errors_exit_2(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(argv)
    assert exc.value.code == 2


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"ocsf-toolkit {__version__}"


def test_a_closed_pipe_ends_quietly() -> None:
    """`ocsf-toolkit diff ... | head`: the reader leaving early is not an error to report.

    The 1.0.0 -> 1.9.0 diff (~250 KB) outgrows the pipe buffer, so the writer does hit the closed
    pipe; a smaller diff can finish into the buffer and never see it.
    """
    code = "from ocsf_toolkit.cli import main; raise SystemExit(main(['diff', '1.0.0', '1.9.0']))"
    with subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        assert process.stdout is not None
        assert process.stderr is not None
        process.stdout.readline()
        process.stdout.close()  # what `head -1` does
        stderr = process.stderr.read().decode()
    assert stderr == ""
    assert process.returncode == 1
