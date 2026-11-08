"""The ``ocsf-semtools`` console script as a whole: usage errors, --version, and a closed stdout."""

import subprocess
import sys

import pytest

from ocsf_semtools import __version__
from ocsf_semtools.cli import build_parser


@pytest.mark.parametrize("argv", [[], ["lock"], ["compile", "--bogus"], ["frobnicate"]])
def test_usage_errors_exit_2(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(argv)
    assert exc.value.code == 2


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"ocsf-semtools {__version__}"


def test_a_closed_pipe_ends_quietly() -> None:
    """`ocsf-semtools diff ... | head`: the reader leaving early is not an error to report.

    The 1.0.0 -> 1.9.0 diff (~250 KB) outgrows the pipe buffer, so the writer does hit the closed
    pipe; a smaller diff can finish into the buffer and never see it.
    """
    code = "from ocsf_semtools.cli import main; raise SystemExit(main(['diff', '1.0.0', '1.9.0']))"
    with subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        assert process.stdout is not None
        assert process.stderr is not None
        process.stdout.readline()
        process.stdout.close()  # what `head -1` does
        stderr = process.stderr.read().decode()
    assert stderr == ""
    assert process.returncode == 1
