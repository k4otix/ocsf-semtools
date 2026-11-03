"""The build, parse and repair entry points: each one a policy, an engine run and a conversion."""

import warnings
from collections.abc import Iterable
from typing import Any

from ocsf_toolkit.engine import LENIENT, REPAIR, STRICT, ConformanceError, Report, run, select
from ocsf_toolkit.schema import ResolvedSchema
from ocsf_toolkit.walk import Kind

type ClassRef = tuple[ResolvedSchema, str]  # (schema, class key); generated classes come in M6


def build(ref: ClassRef, /, **fields: Any) -> dict[str, Any]:
    """Construct a conformant event from fields, deriving everything the schema computes.

    Raises ConformanceError carrying the full report when any error remains, and emits a
    DeprecationWarning for each deprecated attribute or value used (D#18).
    """
    schema, class_key = ref
    event, report = run(fields, schema, class_key, STRICT)
    for finding in report.by_kind(Kind.DEPRECATED):
        warnings.warn(finding.message, DeprecationWarning, stacklevel=2)
    if not report.ok():
        raise ConformanceError(report)
    return event


def parse(
    event: dict[str, Any],
    *,
    version: str | None = None,
    profiles: Iterable[str] | None = None,
    class_key: str | None = None,
) -> tuple[dict[str, Any], Report]:
    """Check an event without changing it; version and profiles default to its metadata."""
    schema = select(event, version=version, profiles=profiles)
    return run(event, schema, class_key, LENIENT)


def repair(
    event: dict[str, Any],
    *,
    version: str | None = None,
    profiles: Iterable[str] | None = None,
    class_key: str | None = None,
) -> tuple[dict[str, Any], Report]:
    """Return a normalized copy of the event; every change is a finding with its repair action."""
    schema = select(event, version=version, profiles=profiles)
    return run(event, schema, class_key, REPAIR)
