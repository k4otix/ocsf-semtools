"""The ``diff`` subcommand: text and JSON output, IR files, and diff(1) exit statuses."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ocsf_semtools.cli import build_parser, diff, main
from tests.conftest import DATA_DIR


def test_parser() -> None:
    args = build_parser().parse_args(["diff", "1.7.0", "1.9.0", "--json"])
    assert (args.run, args.old, args.new, args.json) == (diff.run, "1.7.0", "1.9.0", True)


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
    assert (data["old"], data["new"], len(data["changes"])) == ("1.7.0", "1.9.0", 1115)


def test_equal_irs_exit_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["diff", "1.9.0", str(DATA_DIR / "ir-1.9.0.json")]) == 0
    assert capsys.readouterr().out == ""


def test_an_unshipped_version_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["diff", "1.9.0", "0.0.1"]) == 2
    assert capsys.readouterr().err.startswith("ocsf-semtools: error: ")


def test_a_missing_ir_file_exits_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["diff", "1.9.0", str(tmp_path / "missing.json")]) == 2
    assert capsys.readouterr().err.startswith("ocsf-semtools: error: ")


def _unchanged(ir: dict[str, Any]) -> None:
    pass


def _compiler_bump(ir: dict[str, Any]) -> None:
    ir["provenance"]["compiler_version"] = "9.9.9"


def _new_profile(ir: dict[str, Any]) -> None:
    profiles = ir["schema"]["profiles"]
    profiles["zz_new"] = {**profiles["cloud"], "name": "zz_new"}


def _class_removed(ir: dict[str, Any]) -> None:
    del ir["schema"]["classes"]["detection_finding"]


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        (_unchanged, "patch (0 change(s) outside provenance)"),
        (_compiler_bump, "patch (0 change(s) outside provenance)"),
        (_new_profile, "minor (1 change(s) outside provenance)"),
        (_class_removed, "major (1 change(s) outside provenance)"),
    ],
)
def test_semver_category(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    edit: Callable[[dict[str, Any]], None],
    expected: str,
) -> None:
    ir = json.loads((DATA_DIR / "ir-1.9.0.json").read_bytes())
    edit(ir)
    changed = tmp_path / "ir.json"
    changed.write_text(json.dumps(ir))
    main(["diff", "--semver", str(DATA_DIR / "ir-1.9.0.json"), str(changed)])
    assert capsys.readouterr().out.strip() == expected


def test_semver_and_json_are_exclusive() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["diff", "--semver", "--json", "1.7.0", "1.9.0"])
