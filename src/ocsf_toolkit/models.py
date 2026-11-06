"""The build, parse and repair entry points: each one a policy, an engine run and a conversion."""

from __future__ import annotations

import dataclasses
import functools
import sys
import warnings
from collections.abc import Callable, Iterable
from datetime import datetime
from importlib import resources
from types import ModuleType
from typing import TYPE_CHECKING, Any, cast, overload

from ocsf_toolkit.engine import (
    LENIENT,
    REPAIR,
    STRICT,
    ConformanceError,
    Finding,
    Report,
    declared_profiles,
    run,
    select,
)
from ocsf_toolkit.schema import ResolvedSchema, load_ir, resolve
from ocsf_toolkit.walk import Kind

if TYPE_CHECKING:  # imported where used: reading a schema never loads the modelling layer
    import msgspec

type ClassRef = tuple[ResolvedSchema, str]  # (schema, class key), for building without models

_AS_STRUCT = dataclasses.replace(LENIENT, unknown="relocate")  # a Struct has no field for them
_BUNDLED_IR = "ir.json"  # written by codegen into every generated package


@overload
def build(ref: ClassRef, /, **fields: Any) -> dict[str, Any]: ...
@overload
def build[**P, S: msgspec.Struct](ref: Callable[P, S], /, *args: P.args, **kwargs: P.kwargs) -> S: ...
def build(ref: Any, /, *args: Any, **fields: Any) -> Any:
    """Construct a conformant event, deriving everything the schema computes.

    ``ref`` is a generated class (returns an instance of it) or a ``(schema, class key)`` pair
    (returns a dict). Raises ConformanceError carrying the full report when any error remains,
    and emits a DeprecationWarning for each deprecated attribute or value used (D#18).
    """
    import msgspec

    if args:
        raise TypeError("build() takes the event's fields as keyword arguments only")
    cls = None if isinstance(ref, tuple) else _generated(ref)
    if cls is None:
        (schema, key), exact, event = cast(ClassRef, ref), False, fields
    else:
        schema, key, exact = _target(cls, None, fields)
        event = _wire(cls, fields)
    event, report = run(event, schema, key, STRICT)
    for finding in report.by_kind(Kind.DEPRECATED):
        warnings.warn(finding.message, DeprecationWarning, stacklevel=2)
    if fatal := _fatal(report, exact):
        raise ConformanceError(report, fatal)
    return event if cls is None else msgspec.convert(event, cls)


@overload
def parse(
    event: dict[str, Any],
    *,
    version: str | None = None,
    profiles: Iterable[str] | None = None,
    class_key: str | None = None,
) -> tuple[dict[str, Any], Report]: ...
@overload
def parse[S: msgspec.Struct](
    event: dict[str, Any], *, as_: type[S], profiles: Iterable[str] | None = None
) -> tuple[S | None, Report]: ...
def parse(
    event: dict[str, Any],
    *,
    version: str | None = None,
    profiles: Iterable[str] | None = None,
    class_key: str | None = None,
    as_: type[msgspec.Struct] | None = None,
) -> tuple[Any, Report]:
    """Check an event; version and profiles default to its metadata.

    With ``as_``, a generated class fixes the version and class: unknown attributes are relocated
    into ``unmapped`` and recorded, and the instance is None when the report has errors.
    """
    import msgspec

    if as_ is None:
        return run(event, select(event, version=version, profiles=profiles), class_key, LENIENT)
    if version is not None or class_key is not None:
        raise TypeError("parse(as_=...) takes its version and class from the generated class")
    schema, key, exact = _target(_generated(as_), profiles, event)
    out, report = run(event, schema, key, _AS_STRUCT)
    return (None if _fatal(report, exact) else msgspec.convert(out, as_)), report


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


def _generated(cls: object) -> type[msgspec.Struct]:
    import msgspec

    ocsf: object = vars(cls).get("__ocsf__") if isinstance(cls, type) else None
    if not (isinstance(cls, type) and issubclass(cls, msgspec.Struct) and isinstance(ocsf, tuple)):
        raise TypeError(f"{cls!r} is neither a generated OCSF class nor a (schema, key) pair")
    return cls


def _target(
    cls: type[msgspec.Struct], profiles: Iterable[str] | None, event: dict[str, Any]
) -> tuple[ResolvedSchema, str, bool]:
    """Return (schema, class key, exact) for a generated class; an exact class fixes its profiles."""
    _, key = cast(tuple[str, str], vars(cls)["__ocsf__"])
    package = sys.modules[cls.__module__.partition(".")[0]]
    fixed = cast(tuple[str, ...] | None, package.__ocsf_profiles__)
    if fixed is not None:
        if profiles is not None and frozenset(profiles) != frozenset(fixed):
            raise ValueError(f"{cls.__name__} was generated for exactly the profiles {fixed}")
        return _schema(package.__name__, frozenset(fixed)), key, True
    if profiles is None:
        base = _schema(package.__name__, frozenset[str]())
        profiles = declared_profiles(event, base.ir, base.extensions)
    return _schema(package.__name__, frozenset(profiles)), key, False


@functools.lru_cache(maxsize=32)
def _schema(package: str, profiles: frozenset[str]) -> ResolvedSchema:
    """Return the schema a generated package was made from: its bundled IR, checked against its digest.

    The package's own IR, not core's copy of that version, so a core that re-pins an OCSF
    version never changes what an installed model package means.
    """
    module: ModuleType = sys.modules[package]
    digest = cast(str, module.__schema_digest__)
    extensions = cast(tuple[str, ...], module.__ocsf_extensions__)
    bundled = resources.files(package).joinpath(_BUNDLED_IR)
    if not bundled.is_file():
        raise ValueError(f"{package} has no {_BUNDLED_IR}; regenerate it with this ocsf-toolkit")
    schema = resolve(load_ir(bundled.read_bytes()), profiles=profiles, extensions=extensions)
    if schema.digest != digest:
        raise ValueError(
            f"{package} was generated from IR {digest}, but its {_BUNDLED_IR} is IR"
            f" {schema.digest}; regenerate it with this ocsf-toolkit"
        )
    return schema


def _wire(cls: type[msgspec.Struct], fields: dict[str, Any]) -> dict[str, Any]:
    """Keyword arguments as an event dict: wire names, enums as values, Structs as dicts."""
    import msgspec

    names = dict(zip(cls.__struct_fields__, cls.__struct_encode_fields__, strict=True))
    return {names.get(k, k): msgspec.to_builtins(v, builtin_types=(datetime,)) for k, v in fields.items()}


def _fatal(report: Report, exact: bool) -> tuple[Finding, ...]:
    """Return what stops an event becoming an instance.

    That is its errors and, for an exact class, any attribute or declared profile outside its
    profile set (it has no field for them).
    """
    return report.errors() + (report.by_kind(Kind.PROFILE_MISMATCH) if exact else ())
