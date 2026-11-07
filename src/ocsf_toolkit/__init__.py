"""Version-pinned OCSF semantics: the schema as data, event building and checking, observables, diff."""

from ocsf_toolkit._version import __version__
from ocsf_toolkit.codegen import generate
from ocsf_toolkit.diff import SchemaDiff, diff
from ocsf_toolkit.engine import (
    ConformanceError,
    Finding,
    Kind,
    Report,
    extract,
)
from ocsf_toolkit.ir import SchemaIR
from ocsf_toolkit.models import build, parse, repair
from ocsf_toolkit.observables import Observable, merge
from ocsf_toolkit.schema import (
    ResolvedAttribute,
    ResolvedItem,
    ResolvedSchema,
    load,
    load_ir,
    resolve,
)

__all__ = [
    "ConformanceError",
    "Finding",
    "Kind",
    "Observable",
    "Report",
    "ResolvedAttribute",
    "ResolvedItem",
    "ResolvedSchema",
    "SchemaDiff",
    "SchemaIR",
    "__version__",
    "build",
    "diff",
    "extract",
    "generate",
    "load",
    "load_ir",
    "merge",
    "parse",
    "repair",
    "resolve",
]
