"""Helpers more than one subcommand uses."""

from pathlib import Path

from ocsf_toolkit.ir import SchemaIR
from ocsf_toolkit.schema import load, load_ir


def printable(text: str) -> str:
    """Escape control characters: paths and messages carry keys taken from the input."""
    return "".join(c if c.isprintable() else repr(c)[1:-1] for c in text)


def source_ir(source: str) -> SchemaIR:
    """Load a shipped OCSF version by number, or an IR JSON file by path."""
    path = Path(source)
    if source.endswith(".json") or path.is_file():
        return load_ir(path)
    return load(source).ir
