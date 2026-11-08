"""Version-pinned OCSF semantics: the schema as data, event building and checking, observables, diff."""

from ocsf_semtools._version import __version__
from ocsf_semtools.codegen import generate
from ocsf_semtools.diff import SchemaDiff, diff
from ocsf_semtools.engine import (
    ConformanceError,
    Finding,
    Kind,
    RepairAction,
    Report,
    extract,
)
from ocsf_semtools.export import export_ir
from ocsf_semtools.ir import SchemaIR
from ocsf_semtools.models import build, parse, repair
from ocsf_semtools.observables import Observable, merge
from ocsf_semtools.schema import (
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
    "RepairAction",
    "Report",
    "ResolvedAttribute",
    "ResolvedItem",
    "ResolvedSchema",
    "SchemaDiff",
    "SchemaIR",
    "__version__",
    "build",
    "diff",
    "export_ir",
    "extract",
    "generate",
    "load",
    "load_ir",
    "merge",
    "parse",
    "repair",
    "resolve",
]
