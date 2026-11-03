"""Architecture rules (architecture §0.2) that can be checked mechanically over the core source."""

import ast
from pathlib import Path

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
        and any(
            isinstance(base, ast.Name) and (base.id.endswith(("Error", "Exception")))
            for base in node.bases
        )
    }
    assert defined <= {"ConformanceError"}
