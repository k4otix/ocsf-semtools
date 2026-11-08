"""Generated model packages for tests, written under a temp directory and imported from there."""

import importlib
import sys
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType
from typing import cast

import msgspec

from ocsf_semtools.codegen import generate
from ocsf_semtools.ir import SchemaIR


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


def generated_class(package: ModuleType, key: str) -> type[msgspec.Struct]:
    """Return the class a generated package exports for an OCSF class key."""
    names = cast(list[str], package.__all__)
    classes = [cast(type[msgspec.Struct], getattr(package, name)) for name in names]
    (cls,) = [c for c in classes if vars(c)["__ocsf__"][1] == key]
    return cls
