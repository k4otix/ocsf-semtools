"""Derivation visitor: fill and cross-check every value the schema computes from another one."""

import functools
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from ocsf_toolkit.schema import SCHEMAS_KEPT, ResolvedItem, ResolvedSchema, SiblingSpec
from ocsf_toolkit.walk import Finding, Kind, Path, RepairAction, Visit, attribute_finding, dotted, is_int

OTHER = 99  # the "Other" id: a caller-supplied label is source-specific and always kept
_UNKNOWN = 0
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_ROOT_SIBLINGS = ("class_uid", "category_uid")  # ids whose labels _identity sets from the class itself


def derive(
    visit: Visit, schema: ResolvedSchema, *, fill: bool, overwrite: bool, identity: bool = True
) -> list[Finding]:
    """Derive what the schema computes in a node, at its close.

    At the root of a class: class and category identity, metadata.version and metadata.profiles, and
    type_uid. In every node: each ``_id`` attribute's label, and under the datetime profile each
    timestamp's ``_dt`` twin. ``fill`` writes absent values and ``overwrite`` replaces conflicting
    ones; a conflict is a finding either way. ``identity`` off skips the root step, for an event whose
    class could not be determined.
    """
    if visit.attr is not None:
        return []
    site = _Site(visit.owner, visit.node, visit.path, fill=fill, overwrite=overwrite)
    root = identity and visit.owner.identity is not None and visit.path == ()
    if root:
        _identity(site, schema)
    for id_attr, spec in visit.owner.siblings.items():
        if not (root and id_attr in _ROOT_SIBLINGS):
            _sibling(site, schema, id_attr, spec)
    if "datetime" in schema.profiles:
        _timestamps(site, schema)
    return site.findings


@dataclass(slots=True)
class _Site:
    """One node being derived: its item, where it is, what derivation may change, and its findings."""

    owner: ResolvedItem
    node: dict[str, Any]
    path: Path
    fill: bool
    overwrite: bool
    findings: list[Finding] = field(default_factory=list[Finding])

    def put(
        self,
        attr: str,
        derived: object,
        conflict: Kind,
        *,
        equal: Callable[[object, object], bool] | None = None,
        keep: bool = False,
    ) -> None:
        """Fill an absent attr with derived; a present different value is a conflict finding."""
        ra = self.owner.attributes[attr]
        current = self.node.get(attr)
        path = (*self.path, attr)
        if current is None:
            if self.fill:
                self.node[attr] = derived
                filled = RepairAction(action="fill", before=None, after=derived)
                self.findings.append(
                    attribute_finding(ra, Kind.FILLED, path, f"{dotted(path)} derived", expected=derived, repair=filled)
                )
            return
        if (equal or _same)(current, derived):
            return
        repair: RepairAction | None = None
        if self.overwrite and not keep:
            self.node[attr] = derived
            repair = RepairAction(action="overwrite", before=current, after=derived)
        message = f"{dotted(path)} is {current!r}; the schema derives {derived!r}"
        self.findings.append(
            attribute_finding(ra, conflict, path, message, expected=derived, actual=current, repair=repair)
        )


def _identity(site: _Site, schema: ResolvedSchema) -> None:
    ident = site.owner.identity
    assert ident is not None
    attrs = site.owner.attributes
    for attr, value in (
        ("class_uid", ident.class_uid),
        ("class_name", ident.class_name),
        ("category_uid", ident.category_uid),
        ("category_name", ident.category_name),
    ):
        if value is not None and attr in attrs:
            site.put(attr, value, Kind.IDENTITY_CONFLICT)
    # metadata.version and .profiles come from the schema the class is read under, which only the root
    # knows; the root closes before its children, so this step reaches into metadata itself.
    metadata = site.node.get("metadata")
    meta_item = schema.objects.get("metadata")
    if isinstance(metadata, dict) and meta_item is not None and "metadata" in attrs:
        meta = _Site(meta_item, cast(dict[str, Any], metadata), ("metadata",), site.fill, site.overwrite)
        if "version" in meta_item.attributes:
            meta.put("version", schema.version, Kind.IDENTITY_CONFLICT)
        if "profiles" in meta_item.attributes:
            _profiles(meta, schema.profiles)
        site.findings.extend(meta.findings)
    activity_id = site.node.get("activity_id")
    if is_int(activity_id) and "type_uid" in attrs:
        site.put("type_uid", ident.class_uid * 100 + activity_id, Kind.IDENTITY_CONFLICT)


def _profiles(meta: _Site, active: frozenset[str]) -> None:
    declared = meta.node.get("profiles")
    if declared is None and not active:
        return  # nothing to state; an empty list would only add noise
    if declared is not None and not ((names := _list(declared)) is not None and all(isinstance(p, str) for p in names)):
        return  # conform reports the type

    def equal(a: object, b: object) -> bool:
        x, y = _list(a), _list(b)
        return x is not None and y is not None and set(x) == set(y)

    meta.put("profiles", sorted(active), Kind.PROFILE_MISMATCH, equal=equal)


def _sibling(site: _Site, schema: ResolvedSchema, id_attr: str, spec: SiblingSpec) -> None:
    value = site.node.get(id_attr)
    if value is None:
        return
    if spec.is_array:
        _sibling_list(site, id_attr, spec, value)
        return
    caption = _caption(schema, spec, value)
    if caption is None:
        return  # wrong type or out of range: conform reports it; nothing to derive from
    label = site.node.get(spec.label)
    if _is_other(id_attr, value):
        if label is None and id_attr == "type_uid" and site.path == () and site.fill:
            _other_type_name(site, spec.label)
        return
    if value == _UNKNOWN and isinstance(label, str) and not label.strip():
        ra = site.owner.attributes[id_attr]
        site.findings.append(
            attribute_finding(
                ra,
                Kind.SEMANTICALLY_EMPTY_ENUM,
                (*site.path, id_attr),
                f"{dotted((*site.path, id_attr))} is 0 (Unknown) with an empty {spec.label}",
                actual=label,
            )
        )
    site.put(spec.label, caption, Kind.SIBLING_CONFLICT)


def _other_type_name(site: _Site, label: str) -> None:
    # activity_id 99: type_name follows the caller's activity label, not the enum's "…: Other".
    activity, ident = site.node.get("activity_name"), site.owner.identity
    if isinstance(activity, str) and activity and ident is not None:
        site.put(label, f"{ident.class_name}: {activity}", Kind.SIBLING_CONFLICT)


def _sibling_list(site: _Site, id_attr: str, spec: SiblingSpec, value: object) -> None:
    ids = _list(value)
    if ids is None or spec.enum is None:
        return
    captions: list[str] = []
    for i in ids:
        member = spec.enum.get(i) if is_int(i) else None
        if member is None:
            return  # conform reports the element; nothing to derive from
        captions.append(member.caption)
    labels = site.node.get(spec.label)
    if labels is None:
        if OTHER not in ids:
            site.put(spec.label, captions, Kind.SIBLING_CONFLICT)
        return
    given = _list(labels)
    if given is None:
        return
    # An "Other" element keeps the caller's label at the same index.
    expected = [(given[n] if n < len(given) else None) if i == OTHER else captions[n] for n, i in enumerate(ids)]
    keep = len(given) != len(ids) and OTHER in ids  # no sound list to write back
    site.put(spec.label, expected, Kind.SIBLING_CONFLICT, keep=keep)


def _timestamps(site: _Site, schema: ResolvedSchema) -> None:
    for name, twin in _twins(schema, site.owner):
        value = site.node.get(name)
        if not is_int(value):
            continue
        try:
            text = rfc3339(value)
        except OverflowError:
            continue  # out of datetime's range; nothing sound to derive
        site.put(twin, text, Kind.SIBLING_CONFLICT, equal=_same_instant)


def _twins(schema: ResolvedSchema, item: ResolvedItem) -> tuple[tuple[str, str], ...]:
    """Return an item's (timestamp_t attribute, its _dt twin) pairs, worked out once per item and schema."""
    known = _twin_table(schema)
    key = (item.is_class, item.key)
    pairs = known.get(key)
    if pairs is None:
        attrs = item.attributes
        pairs = known[key] = tuple(
            (name, f"{name}_dt") for name, ra in attrs.items() if ra.type == "timestamp_t" and f"{name}_dt" in attrs
        )
    return pairs


@functools.lru_cache(maxsize=SCHEMAS_KEPT)
def _twin_table(schema: ResolvedSchema) -> dict[tuple[bool, str], tuple[tuple[str, str], ...]]:
    """Return the schema's timestamp twins by (is_class, item key), filled as items are met."""
    return {}


def rfc3339(ms: int) -> str:
    """Return epoch milliseconds as YYYY-MM-DDTHH:MM:SS.mmmZ, the form of OCSF's datetime_t."""
    m = _EPOCH + timedelta(milliseconds=ms)
    # Each field by hand: strftime's %Y is not zero-padded below year 1000 on glibc.
    date, time = f"{m.year:04d}-{m.month:02d}-{m.day:02d}", f"{m.hour:02d}:{m.minute:02d}:{m.second:02d}"
    return f"{date}T{time}.{m.microsecond // 1000:03d}Z"


def _same_instant(given: object, derived: object) -> bool:
    if not isinstance(given, str) or not isinstance(derived, str):
        return False
    try:
        a, b = datetime.fromisoformat(given), datetime.fromisoformat(derived)
    except ValueError:
        return False
    return a.tzinfo is not None and a == b


def _caption(schema: ResolvedSchema, spec: SiblingSpec, value: object) -> str | None:
    if not is_int(value):
        return None
    if spec.enum is not None:
        member = spec.enum.get(value)
        return member.caption if member else None
    # *.type_uid carries no enum of its own: every class's type_uid enum is the registry.
    try:
        item, _ = schema.class_by_type_uid(value)
    except KeyError:
        return None
    enum = item.attributes["type_uid"].enum
    return enum[value].caption if enum else None


def _is_other(id_attr: str, value: int) -> bool:
    if id_attr == "type_uid":
        return value % 100 == OTHER
    return value == OTHER


def _same(a: object, b: object) -> bool:
    """Equal and of the same type (True is not 1); lists element-wise, as labels are flat."""
    x, y = _list(a), _list(b)
    if x is not None and y is not None:
        return len(x) == len(y) and all(type(p) is type(q) and p == q for p, q in zip(x, y, strict=True))
    return type(a) is type(b) and a == b


def _list(value: object) -> list[object] | None:
    return cast(list[object], value) if isinstance(value, list) else None
