"""The validation engine: one walk of an event with the visitors a policy selects, into a Report."""

import copy
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast, final

from ocsf_toolkit.conform import Checks, as_integer, check
from ocsf_toolkit.derive import derive
from ocsf_toolkit.ir import Requirement, SchemaIR
from ocsf_toolkit.observables import Observable, markers, observe, unfounded
from ocsf_toolkit.schema import ResolvedItem, ResolvedSchema, load
from ocsf_toolkit.walk import Finding as Finding  # re-exported: the engine's vocabulary
from ocsf_toolkit.walk import Kind as Kind
from ocsf_toolkit.walk import RepairAction as RepairAction
from ocsf_toolkit.walk import is_int, walk

FALLBACK_CLASS = "base_event"  # walked when an event names no class this schema knows


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Policy:
    """What a run may change in the event; each entry point in ``ocsf_toolkit.models`` picks one.

    Every run reports the same findings; a policy decides only which of them it also acts on.
    """

    fill: bool  # derive identity, siblings, *_dt, metadata.version; accept datetime timestamps
    overwrite: bool  # conflicting derived values <- the schema's; 1:1 deprecation migrations
    coerce: bool  # "4"->4, "true"->True, integral float->int
    relocate: bool  # unknown attributes move into the class's unmapped


STRICT = Policy(fill=True, overwrite=False, coerce=False, relocate=False)  # build
LENIENT = Policy(fill=False, overwrite=False, coerce=False, relocate=False)  # parse
REPAIR = Policy(fill=True, overwrite=True, coerce=True, relocate=True)  # repair


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Report:
    """Every finding from one run; the caller decides what is fatal (R12)."""

    schema: ResolvedSchema
    class_key: str
    findings: tuple[Finding, ...]

    def errors(self) -> tuple[Finding, ...]:
        """Return the findings of severity error; a repaired finding is no longer one."""
        return tuple(f for f in self.findings if f.severity == "error")

    def by_kind(self, kind: Kind) -> tuple[Finding, ...]:
        """Return the findings of one kind."""
        return tuple(f for f in self.findings if f.kind is kind)

    def ok(self) -> bool:
        """Tell whether the event has no errors."""
        return not self.errors()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable form, for CI gates and ingest pipelines."""
        return {
            "version": self.schema.version,
            "profiles": sorted(self.schema.profiles),
            "extensions": sorted(self.schema.extensions),
            "class": self.class_key,
            "ok": self.ok(),
            "findings": [_finding_dict(f) for f in self.findings],
        }


class ConformanceError(Exception):
    """Raised by build() only, carrying the whole report rather than the first problem.

    ``fatal`` names the findings that made the event unbuildable; it defaults to the errors.
    """

    def __init__(self, report: Report, fatal: tuple[Finding, ...] | None = None) -> None:
        """Hold the report; the message names the first five fatal findings."""
        errors = report.errors() if fatal is None else fatal
        shown = "; ".join(f.message for f in errors[:5])
        more = f"; and {len(errors) - 5} more" if len(errors) > 5 else ""
        super().__init__(f"{len(errors)} conformance error(s) in {report.class_key}: {shown}{more}")
        self.report = report


def run(
    event: dict[str, Any], schema: ResolvedSchema, class_key: str | None, policy: Policy
) -> tuple[dict[str, Any], Report]:
    """Walk the event once, deriving and checking; a policy that changes the event works on a copy."""
    mutates = policy.fill or policy.overwrite or policy.coerce or policy.relocate
    out = copy.deepcopy(event) if mutates else event
    item, identity, findings = _root_class(out, schema, class_key, coerce=policy.coerce)
    checks = Checks(
        schema=schema,
        root=out,
        root_item=item,
        coerce=policy.coerce,
        datetimes=policy.fill,
        relocate=policy.relocate,
        migrate=policy.overwrite,
    )
    marks = markers(schema, item)
    found: list[Observable] = []
    for visit in walk(out, item, schema):
        if isinstance(visit, Finding):
            findings.append(visit)
        elif visit.attr is None:  # a node's close: derivation runs on whole nodes
            findings += derive(visit, schema, fill=policy.fill, overwrite=policy.overwrite, identity=identity)
            findings += check(visit, checks)
        else:
            findings += check(visit, checks)
            found += observe(visit, marks)  # after check: values as coerced or relocated
    findings += unfounded(out, found)
    return out, Report(schema=schema, class_key=item.key, findings=tuple(findings))


def extract(
    event: dict[str, Any],
    schema: ResolvedSchema,
    class_key: str | None = None,
    *,
    max_nodes: int | None = None,
) -> tuple[tuple[Observable, ...], Report]:
    """Return the observables the schema derives from the event, in walk order; the event is unchanged.

    The report holds observable_mismatch for caller entries nothing backs, and max_nodes_exceeded
    (with a partial result) when the walk passes max_nodes attribute visits.
    """
    item, _, findings = _root_class(event, schema, class_key)
    marks = markers(schema, item)
    found: list[Observable] = []
    for visit in walk(event, item, schema, max_nodes=max_nodes):
        if isinstance(visit, Finding):
            findings.append(visit)
        else:
            found += observe(visit, marks)
    findings += unfounded(event, found)
    return tuple(found), Report(schema=schema, class_key=item.key, findings=tuple(findings))


def select(event: Mapping[str, Any], *, version: str | None, profiles: Iterable[str] | None) -> ResolvedSchema:
    """Select the schema an event is read against: arguments first, then its metadata.

    No version anywhere is a ValueError: no OCSF version is ever implied (D#6). Profile names in
    metadata.profiles that the version doesn't define are skipped here; derivation then reports
    metadata.profiles as a profile_mismatch.
    """
    version = version if version is not None else _metadata(event).get("version")
    if not isinstance(version, str):
        raise ValueError("no OCSF version: pass version= or set metadata.version")
    if profiles is None:
        profiles = declared_profiles(event, load(version).ir, frozenset())
    return load(version, profiles=profiles)


def declared_profiles(event: Mapping[str, Any], ir: SchemaIR, extensions: frozenset[str]) -> frozenset[str]:
    """Return the names in metadata.profiles that the IR defines under these extensions."""
    declared = _metadata(event).get("profiles")
    names = cast(list[object], declared) if isinstance(declared, list) else []
    defined = ir.profiles
    return frozenset(
        p for p in names if isinstance(p, str) and p in defined and defined[p].extension in (None, *extensions)
    )


def _metadata(event: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = event.get("metadata")
    return cast(Mapping[str, Any], metadata) if isinstance(metadata, dict) else {}


def _root_class(
    event: dict[str, Any], schema: ResolvedSchema, class_key: str | None, *, coerce: bool = False
) -> tuple[ResolvedItem, bool, list[Finding]]:
    """Return the class to walk, whether the event really is of it, and why not.

    With ``coerce``, an identity the walk will coerce ("2004", 2004.0) names its class already.
    """
    fallback = schema.classes[FALLBACK_CLASS]
    if class_key is not None:
        return schema.classes[class_key], True, []

    def by_type_uid(type_uid: int) -> ResolvedItem:
        return schema.class_by_type_uid(type_uid)[0]

    for attr, lookup in (("class_uid", schema.class_by_uid), ("type_uid", by_type_uid)):
        value = event.get(attr)
        if coerce and (number := as_integer(value)) is not None:
            value = number
        if not is_int(value):
            continue
        try:
            return lookup(value), True, []
        except KeyError:
            return (
                fallback,
                False,
                [
                    Finding(
                        kind=Kind.ENUM_OUT_OF_RANGE,
                        path=(attr,),
                        owner=FALLBACK_CLASS,
                        message=f"{attr} {value} names no class in OCSF {schema.version}; checked as {FALLBACK_CLASS}",
                        tier=Requirement.REQUIRED,
                        actual=value,
                    )
                ],
            )
    return fallback, False, []


def _finding_dict(f: Finding) -> dict[str, Any]:
    repair: RepairAction | None = f.repair
    return {
        "kind": f.kind.value,
        "severity": f.severity,
        "path": list(f.path),
        "owner": f.owner,
        "tier": f.tier.value if f.tier else None,
        "profiles": sorted(f.profiles),
        "expected": _jsonable(f.expected),
        "actual": _jsonable(f.actual),
        "message": f.message,
        "repair": None
        if repair is None
        else {
            "action": repair.action,
            "before": _jsonable(repair.before),
            "after": _jsonable(repair.after),
        },
    }


def _jsonable(value: object) -> object:
    return json.loads(json.dumps(value, default=_json_default, ensure_ascii=False))


def _json_default(value: object) -> object:
    if isinstance(value, frozenset | set):
        return sorted(cast(Iterable[object], value), key=repr)
    if isinstance(value, datetime):
        return value.isoformat()
    return repr(value)
