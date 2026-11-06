"""``ocsf-toolkit models check`` / ``models record``: regeneration gate 3 (D-7, acc. #6)."""

import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from ocsf_toolkit.cli import lock, main, models, trees
from tests.conftest import ROOT


@pytest.fixture
def lockfile(tmp_path: Path) -> Path:
    """The committed lockfile with 1.9.0 the only model version and its tree hash cleared."""
    committed = lock.read(ROOT / "ocsf.lock")
    schemas = tuple(replace(e, ship_models=e.version == "1.9.0", models_tree_sha256="") for e in committed.schemas)
    path = tmp_path / "ocsf.lock"
    lock.write(path, replace(committed, schemas=schemas))
    return path


def _run(path: Path, action: str, *args: str) -> int:
    extra = ["--dists", str(path.parent / "dists")] if action == "check" else []
    return main(["models", action, *args, "--lock", str(path), *extra])


def test_package_name() -> None:
    assert trees.package_name("1.10.0") == "ocsf_toolkit_v1_10_0"


def test_files_sha256_covers_paths_and_bytes() -> None:
    base = trees.files_sha256({"a.py": b"x", "b/c.py": b"y"})
    assert base == trees.files_sha256({"b/c.py": b"y", "a.py": b"x"})  # order-free
    assert base != trees.files_sha256({"a.py": b"x", "b/d.py": b"y"})
    assert base != trees.files_sha256({"a.py": b"x", "b/c.py": b"z"})
    assert base != trees.files_sha256({"a.pyb": b"", "/c.py": b"y"})  # no ambiguous joins


def test_record_then_check(lockfile: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(lockfile, "record") == 0
    digest = lock.find(lock.read(lockfile), "1.9.0").models_tree_sha256
    assert digest == trees.files_sha256(models.tree("1.9.0"))
    assert _run(lockfile, "check") == 0
    assert f"1.9.0: ok  models_tree_sha256 {digest}" in capsys.readouterr().out


def test_check_fails_on_a_changed_tree(lockfile: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(lockfile, "check") == 1
    assert "lockfile has none; review the change" in capsys.readouterr().err


def test_check_refuses_a_version_outside_the_window(lockfile: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(lockfile, "check", "1.8.0") == 1
    assert "outside the model window" in capsys.readouterr().err


def test_check_compares_a_built_dist(lockfile: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _run(lockfile, "record") == 0
    package = lockfile.parent / "dists" / "ocsf_toolkit_v1_9_0" / "ocsf_toolkit_v1_9_0"
    for name, data in models.tree("1.9.0").items():
        (package / name).parent.mkdir(parents=True, exist_ok=True)
        (package / name).write_bytes(data)
    (package / "__pycache__").mkdir()
    (package / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"\0")  # caches don't count
    assert _run(lockfile, "check") == 0
    assert f"{package} == lockfile" in capsys.readouterr().out
    (package / "enums.py").write_text("# edited\n")
    assert _run(lockfile, "check") == 1
    assert "rerun `make dists`" in capsys.readouterr().err
    shutil.rmtree(package)  # an absent dist is not checked
    assert _run(lockfile, "check") == 0
