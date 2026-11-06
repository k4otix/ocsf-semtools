"""Architecture rules (architecture §0.2) that can be checked mechanically over the core source."""

import ast
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from importlinter.cli import lint_imports

from tests.conftest import ROOT

PACKAGE = ROOT / "src" / "ocsf_toolkit"
CORE = {
    "ir",
    "loader",
    "export",
    "schema",
    "walk",
    "derive",
    "conform",
    "observables",
    "engine",
    "diff",
    "models",
    "codegen",
    "cli",
}
SOURCES = sorted(p for p in PACKAGE.rglob("*.py") if p.name != "_version.py")


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_import_contracts_hold() -> None:
    """Rule 2: the layering, the msgspec boundary, no generated-package imports."""
    assert lint_imports(config_filename=str(ROOT / ".importlinter"), no_logo=True) == 0


def test_module_budget() -> None:
    """Rule 6: core is thirteen modules; a fourteenth is argued in a PR first."""
    modules = {p.stem for p in PACKAGE.glob("*.py")} | {
        p.name for p in PACKAGE.iterdir() if (p / "__init__.py").is_file()
    }
    assert modules - {"__init__", "_version"} <= CORE


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(PACKAGE)))
def test_no_module_getattr(path: Path) -> None:
    """Rule 4: lazy loading exists only in generated packages."""
    names = {n.name for n in _tree(path).body if isinstance(n, ast.FunctionDef)}
    assert "__getattr__" not in names


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(PACKAGE)))
def test_no_exception_types_beyond_conformance_error(path: Path) -> None:
    """Rule 3: the one exception type is ConformanceError; everything else is a builtin."""
    defined = {
        node.name
        for node in ast.walk(_tree(path))
        if isinstance(node, ast.ClassDef)
        and any(isinstance(base, ast.Name) and (base.id.endswith(("Error", "Exception"))) for base in node.bases)
    }
    assert defined <= {"ConformanceError"}


# Modules that work over events: none of them may traverse one itself (rule 1).
VISITORS = ("derive", "conform", "observables", "engine", "models")


@pytest.mark.parametrize("name", VISITORS)
def test_only_walk_traverses_an_event(name: str) -> None:
    """Rule 1, statically: no while loop and no self-recursive function outside walk.py."""
    path = PACKAGE / f"{name}.py"
    if not path.is_file():
        pytest.skip(f"{name}.py arrives in a later milestone")
    tree = _tree(path)
    assert not [n.lineno for n in ast.walk(tree) if isinstance(n, ast.While)]
    recursive = [
        f.name
        for f in ast.walk(tree)
        if isinstance(f, ast.FunctionDef)
        and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == f.name for c in ast.walk(f))
    ]
    assert recursive == []


def test_engine_run_is_exactly_one_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rule 1, at runtime: one run is one walk, and each attribute is visited once."""
    from ocsf_toolkit import engine
    from ocsf_toolkit.schema import load
    from tests.fixtures.events import multi_error_fixture

    calls: list[list[object]] = []
    real = vars(engine)["walk"]

    def counting(*args: Any, **kwargs: Any) -> Iterator[object]:
        visits: list[object] = []
        calls.append(visits)
        for visit in real(*args, **kwargs):
            visits.append((getattr(visit, "attr", None), getattr(visit, "path", None)))
            yield visit

    monkeypatch.setattr(engine, "walk", counting)
    schema = load("1.9.0")
    for policy in (engine.STRICT, engine.LENIENT, engine.REPAIR):
        calls.clear()
        engine.run(multi_error_fixture(schema), schema, "detection_finding", policy)
        assert len(calls) == 1
        assert len(calls[0]) == len(set(map(repr, calls[0])))
