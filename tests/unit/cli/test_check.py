"""The ``check`` subcommand: one report per JSON Lines event, exit 1 on any error."""

import io
import json
from pathlib import Path

import pytest

from ocsf_toolkit.cli import build_parser, check, main
from ocsf_toolkit.schema import load
from tests.fixtures.events import base_detection_finding, error_fixtures


@pytest.fixture
def events(tmp_path: Path) -> Path:
    schema = load("1.9.0")
    lines = [
        json.dumps(base_detection_finding(schema)),
        "",
        json.dumps(error_fixtures(schema)["E7_enum_out_of_range"]),
    ]
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(lines) + "\n")
    return path


def test_parser() -> None:
    args = build_parser().parse_args(["check", "--version", "1.9.0", "a.jsonl", "-"])
    assert args.run is check.run
    assert (args.version, args.profiles, args.class_key, args.json) == ("1.9.0", None, None, False)
    assert args.files == ["a.jsonl", "-"]


def test_text_output_and_exit_status(events: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "--version", "1.9.0", str(events)]) == 1
    out = capsys.readouterr().out.splitlines()
    assert f"{events}:3: error enum_out_of_range severity_id: " in "\n".join(out)
    assert not any(line.startswith(f"{events}:1: error") for line in out)
    assert all(" info " not in line for line in out)


def test_clean_input_exits_zero(tmp_path: Path) -> None:
    path = tmp_path / "ok.jsonl"
    path.write_text(json.dumps(base_detection_finding(load("1.9.0"))) + "\n")
    assert main(["check", "--version", "1.9.0", str(path)]) == 0


def test_json_output(events: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["check", "--version", "1.9.0", "--json", str(events)])
    reports = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [(r["source"], r["ok"]) for r in reports] == [
        (f"{events}:1", True),
        (f"{events}:3", False),
    ]


def test_stdin_and_bad_lines(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("not json\n[1]\n"))
    assert main(["check", "--version", "1.9.0", "-"]) == 1
    out = capsys.readouterr().out
    assert "-:1: error: not JSON" in out
    assert "-:2: error: not a JSON object" in out


def test_profiles_and_class_options(events: Path, capsys: pytest.CaptureFixture[str]) -> None:
    argv = ["check", "--version", "1.9.0", "--profiles", "cloud", "--class", "detection_finding"]
    assert main([*argv, str(events)]) == 1
    assert ":1: error missing_required cloud: " in capsys.readouterr().out


@pytest.mark.parametrize(
    ("option", "message"),
    [
        (["--profiles", "nope"], "unknown profiles ['nope']"),
        (["--class", "nope"], "no class 'nope'"),
        (["--version", "0.0.1"], "no shipped IR"),
    ],
)
def test_bad_selection_fails_once(
    events: Path, capsys: pytest.CaptureFixture[str], option: list[str], message: str
) -> None:
    argv = ["check", "--version", "1.9.0", *option, str(events)]
    assert main(argv) == 1
    assert message in capsys.readouterr().err
