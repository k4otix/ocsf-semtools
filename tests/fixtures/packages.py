"""Generated model packages for tests, written under a temp directory and imported from there."""

import importlib
import sys
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType

from ocsf_toolkit.codegen import generate
from ocsf_toolkit.ir import SchemaIR


def import_generated(
    ir: SchemaIR,
    package: str,
    out: Path,
    *,
    profiles: Iterable[str] | None = None,
    extensions: Iterable[str] | None = None,
) -> ModuleType:
    """Generate ``package`` into ``out`` and import it; each test package needs its own name.

    ``out`` leaves sys.path again once the package is imported: its submodules load through
    the package's own ``__path__``.
    """
    generate(ir, package=package, out=out, profiles=profiles, extensions=extensions)
    sys.path.insert(0, str(out))
    try:
        return importlib.import_module(package)
    finally:
        sys.path.remove(str(out))
