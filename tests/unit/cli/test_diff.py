"""The ``diff`` subcommand: text and JSON output, IR files, and diff(1) exit statuses."""

import json
from pathlib import Path

import pytest

from ocsf_toolkit.cli import build_parser, diff, main
from tests.conftest import DATA_DIR


def test_parser() -> None:
    args = build_parser().parse_args(["diff", "1.7.0", "1.9.0", "--json"])
    assert (args.run, args.a, args.b, args.json) == (diff.run, "1.7.0", "1.9.0", True)


def test_text_output(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["diff", "1.1.0", "1.3.0"]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert "renamed classes.win/registry_key_info -> win/registry_key_query" in lines
    assert "added classes.win/registry_key_query" not in lines
    caption = "classes.win/registry_key_query.attributes.activity_id.enum.1.caption"
    assert f'changed {caption}: "Exists" -> "Query"' in lines


def test_json_output_and_ir_files(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["diff", "--json", "1.7.0", str(DATA_DIR / "ir-1.9.0.json")]) == 1
    data = json.loads(capsys.readouterr().out)
    assert (data["a"], data["b"], len(data["changes"])) == ("1.7.0", "1.9.0", 1115)


def test_equal_irs_exit_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["diff", "1.9.0", str(DATA_DIR / "ir-1.9.0.json")]) == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("source", ["0.0.1", "missing.json"])
def test_unreadable_input_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], source: str
) -> None:
    assert (
        main(["diff", "1.9.0", str(tmp_path / source) if source.endswith("json") else source]) == 2
    )
    assert capsys.readouterr().err.startswith("ocsf-toolkit: error: ")
