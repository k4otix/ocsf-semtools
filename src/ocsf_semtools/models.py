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

from ocsf_semtools.engine import (
    LENIENT,
    MAX_NODES,
    REPAIR,
    STRICT,
    ConformanceError,
    Finding,
    Kind,
    Report,
    declared_profiles,
    require_event,
    run,
    select,
)
from ocsf_semtools.schema import ResolvedSchema, load_ir, resolve

if TYPE_CHECKING:  # imported where used: reading a schema never loads the modelling layer
    import msgspec

type ClassRef = tuple[ResolvedSchema, str]  # (schema, class key), for building without models

_AS_STRUCT = dataclasses.replace(LENIENT, relocate=True)  # a Struct has no field for unknown attributes
_BUNDLED_IR = "ir.json"  # written by codegen into every generated package


@overload
def build(ref: ClassRef, /, **fields: Any) -> dict[str, Any]: ...
@overload
def build[**P, S: msgspec.Struct](ref: Callable[P, S], /, *args: P.args, **kwargs: P.kwargs) -> S: ...
def build(ref: Any, /, *args: Any, **fields: Any) -> Any:
    """Construct a conformant event, deriving everything the schema computes.

    ``ref`` is a generated class (returns an instance of it) or a ``(schema, class key)`` pair
    (returns a dict). Raises ConformanceError carrying the full report when any error remains,
    and emits a DeprecationWarning for each deprecated attribute or value used. The walk stops
    after ``engine.MAX_NODES`` attribute visits; the fields are the event's own, so there is no
    keyword to change that.
    """
    import msgspec

    if args:
        raise TypeError("build() takes the event's fields as keyword arguments only")
    cls = None if isinstance(ref, tuple) else _generated(ref)
    if cls is None:
        (schema, key), exact, event = _class_ref(cast(tuple[object, ...], ref)), False, fields
    else:
        event = _wire(cls, fields)  # first: a Metadata instance declares profiles only once it is a dict
        schema, key, exact = _target(cls, None, event)
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
    schema: ResolvedSchema | None = None,
    max_nodes: int | None = MAX_NODES,
) -> tuple[dict[str, Any], Report]: ...
@overload
def parse[S: msgspec.Struct](
    event: dict[str, Any],
    *,
    as_: type[S],
    profiles: Iterable[str] | None = None,
    max_nodes: int | None = MAX_NODES,
) -> tuple[S | None, Report]: ...
def parse(
    event: dict[str, Any],
    *,
    version: str | None = None,
    profiles: Iterable[str] | None = None,
    class_key: str | None = None,
    schema: ResolvedSchema | None = None,
    as_: type[msgspec.Struct] | None = None,
    max_nodes: int | None = MAX_NODES,
) -> tuple[Any, Report]:
    """Check an event; version, profiles and extensions default to its metadata.

    ``schema`` reads it against a schema you resolved yourself, such as one with your own
    extension, in place of ``version`` and ``profiles``. With ``as_``, a generated class fixes the
    schema and class: unknown attributes are relocated into ``unmapped`` and recorded, and the
    instance is None when the report has errors. The walk stops with a max_nodes_exceeded finding
    after ``max_nodes`` attribute visits; None walks all.
    """
    import msgspec

    if as_ is None:
        chosen = _chosen(event, schema, version, profiles)
        return run(event, chosen, class_key, LENIENT, max_nodes=max_nodes)
    if version is not None or class_key is not None or schema is not None:
        raise TypeError("parse(as_=...) takes its schema and class from the generated class")
    require_event(event)
    target, key, exact = _target(_generated(as_), profiles, event)
    out, report = run(event, target, key, _AS_STRUCT, max_nodes=max_nodes)
    return (None if _fatal(report, exact) else msgspec.convert(out, as_)), report


def repair(
    event: dict[str, Any],
    *,
    version: str | None = None,
    profiles: Iterable[str] | None = None,
    class_key: str | None = None,
    schema: ResolvedSchema | None = None,
    max_nodes: int | None = MAX_NODES,
) -> tuple[dict[str, Any], Report]:
    """Return a normalized copy of the event; every change is a finding with its repair action.

    The schema is chosen as for ``parse``. The walk stops with a max_nodes_exceeded finding after
    ``max_nodes`` attribute visits.
    """
    return run(event, _chosen(event, schema, version, profiles), class_key, REPAIR, max_nodes=max_nodes)


def _chosen(
    event: dict[str, Any], schema: ResolvedSchema | None, version: str | None, profiles: Iterable[str] | None
) -> ResolvedSchema:
    """Return the schema given, or the one the arguments and the event's metadata select."""
    if schema is None:
        return select(event, version=version, profiles=profiles)
    if version is not None or profiles is not None:
        raise TypeError("schema= fixes the version and profiles; pass it without version= or profiles=")
    if not isinstance(cast(object, schema), ResolvedSchema):  # a runtime check: callers may be untyped
        raise TypeError(f"schema= takes a ResolvedSchema, from load() or resolve(); got {type(schema).__name__}")
    return schema


def _class_ref(ref: tuple[object, ...]) -> ClassRef:
    match ref:
        case (ResolvedSchema() as schema, str() as key):
            return schema, key
        case _:
            raise TypeError(f"a class reference is (ResolvedSchema, class key); got {ref!r}")


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
    package = _package(cls)
    digest = cast(str, package.__schema_digest__)
    fixed = cast(tuple[str, ...] | None, package.__ocsf_profiles__)
    if fixed is not None:
        if profiles is not None and frozenset(profiles) != frozenset(fixed):
            raise ValueError(f"{cls.__name__} was generated for exactly the profiles {fixed}")
        return _schema(package.__name__, digest, frozenset(fixed)), key, True
    if profiles is None:
        base = _schema(package.__name__, digest, frozenset[str]())
        profiles = declared_profiles(event, base.ir, base.extensions)
    return _schema(package.__name__, digest, frozenset(profiles)), key, False


def _package(cls: type[msgspec.Struct]) -> ModuleType:
    """Return the root of the generated package a class is in, wherever that package is nested.

    The root is the module that sets ``__ocsf_version__``: ``myapp.acme_models`` for a package
    generated into ``src/myapp/`` and imported from there.
    """
    parts = cls.__module__.split(".")
    for n in range(len(parts), 0, -1):
        module = sys.modules.get(".".join(parts[:n]))
        if module is not None and hasattr(module, "__ocsf_version__"):
            return module
    raise TypeError(f"{cls.__name__} has __ocsf__ but no generated package around it")


@functools.lru_cache(maxsize=32)
def _schema(package: str, digest: str, profiles: frozenset[str]) -> ResolvedSchema:
    """Return the schema a generated package was made from: its bundled IR, checked against its digest.

    The schema comes from the package's bundled IR, so a core that re-pins an OCSF version never
    changes what an installed model package means. The cache is keyed by the digest too, so a
    package regenerated and reloaded in one process is read again.
    """
    module: ModuleType = sys.modules[package]
    extensions = cast(tuple[str, ...], module.__ocsf_extensions__)
    bundled = resources.files(package).joinpath(_BUNDLED_IR)
    if not bundled.is_file():
        raise ValueError(f"{package} has no {_BUNDLED_IR}; regenerate it with this ocsf-semtools")
    schema = resolve(load_ir(bundled.read_bytes()), profiles=profiles, extensions=extensions)
    if schema.digest != digest:
        raise ValueError(
            f"{package} was generated from IR {digest}, but its {_BUNDLED_IR} is IR"
            f" {schema.digest}; regenerate it with this ocsf-semtools"
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
