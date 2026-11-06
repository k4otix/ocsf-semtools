"""Derivation visitor: fill and cross-check every value the schema computes from another one."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, TypeGuard, cast

from ocsf_toolkit.schema import ResolvedAttribute, ResolvedItem, ResolvedSchema, SiblingSpec
from ocsf_toolkit.walk import Finding, Kind, Path, RepairAction, Visit, dotted

OTHER = 99  # the "Other" id: a caller-supplied label is source-specific and always kept
UNKNOWN = 0
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_IDENTITY = ("class_uid", "category_uid")


def derive(
    visit: Visit, schema: ResolvedSchema, *, fill: bool, overwrite: bool, identity: bool = True
) -> list[Finding]:
    """At the close of a node, apply architecture §5.1 steps 0-5 to it.

    ``fill`` writes absent derivable values; ``overwrite`` replaces conflicting ones. Conflicts are
    findings either way. ``identity`` off skips steps 0-3 (the class could not be determined).
    """
    if visit.attr is not None:
        return []
    out = _Node(visit.owner, visit.node, visit.path, fill=fill, overwrite=overwrite)
    root = identity and visit.owner.identity is not None and visit.path == ()
    if root:
        _identity(out, schema)
    for id_attr, spec in visit.owner.siblings().items():
        if not (root and id_attr in _IDENTITY):
            _sibling(out, schema, id_attr, spec)
    if "datetime" in schema.profiles:
        _timestamps(out)
    return out.findings


@dataclass(slots=True)
class _Node:
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
                self._add(Kind.FILLED, ra, path, derived, None, f"{dotted(path)} derived", "fill")
            return
        if (equal or _same)(current, derived):
            return
        action = "overwrite" if self.overwrite and not keep else None
        if action:
            self.node[attr] = derived
        message = f"{dotted(path)} is {current!r}; the schema derives {derived!r}"
        self._add(conflict, ra, path, derived, current, message, action)

    def _add(
        self,
        kind: Kind,
        ra: ResolvedAttribute,
        path: Path,
        expected: object,
        actual: object,
        message: str,
        action: str | None,
    ) -> None:
        repair = None
        if action == "fill":
            repair = RepairAction(action="fill", before=None, after=expected)
        elif action == "overwrite":
            repair = RepairAction(action="overwrite", before=actual, after=expected)
        self.findings.append(
            Finding(
                kind=kind,
                path=path,
                owner=self.owner.key,
                message=message,
                tier=ra.requirement,
                profiles=ra.gated_by,
                expected=expected,
                actual=actual,
                repair=repair,
            )
        )


def _identity(out: _Node, schema: ResolvedSchema) -> None:
    ident = out.owner.identity
    assert ident is not None
    attrs = out.owner.attributes
    for attr, value in (
        ("class_uid", ident.class_uid),
        ("class_name", ident.class_name),
        ("category_uid", ident.category_uid),
        ("category_name", ident.category_name),
    ):
        if value is not None and attr in attrs:
            out.put(attr, value, Kind.IDENTITY_CONFLICT)
    metadata = out.node.get("metadata")
    meta_item = schema.objects.get("metadata")
    if isinstance(metadata, dict) and meta_item is not None and "metadata" in attrs:
        meta = _Node(meta_item, cast(dict[str, Any], metadata), ("metadata",), out.fill, out.overwrite)
        if "version" in meta_item.attributes:
            meta.put("version", schema.version, Kind.IDENTITY_CONFLICT)
        if "profiles" in meta_item.attributes:
            _profiles(meta, schema.profiles)
        out.findings.extend(meta.findings)
    activity_id = out.node.get("activity_id")
    if _is_int(activity_id) and "type_uid" in attrs:
        out.put("type_uid", ident.class_uid * 100 + activity_id, Kind.IDENTITY_CONFLICT)


def _profiles(meta: _Node, active: frozenset[str]) -> None:
    declared = meta.node.get("profiles")
    if declared is None and not active:
        return  # nothing to state; an empty list would only add noise
    if declared is not None and not ((names := _list(declared)) is not None and all(isinstance(p, str) for p in names)):
        return  # conform reports the type

    def equal(a: object, b: object) -> bool:
        x, y = _list(a), _list(b)
        return x is not None and y is not None and set(x) == set(y)

    meta.put("profiles", sorted(active), Kind.PROFILE_MISMATCH, equal=equal)


def _sibling(out: _Node, schema: ResolvedSchema, id_attr: str, spec: SiblingSpec) -> None:
    value = out.node.get(id_attr)
    if value is None:
        return
    if spec.is_array:
        _sibling_list(out, id_attr, spec, value)
        return
    caption = _caption(schema, spec, value)
    if caption is None:
        return  # wrong type or out of range: conform reports it; nothing to derive from
    label = out.node.get(spec.label)
    if _is_other(id_attr, value):
        if label is None and id_attr == "type_uid" and out.path == () and out.fill:
            _other_type_name(out, spec.label)
        return
    if value == UNKNOWN and isinstance(label, str) and not label.strip():
        ra = out.owner.attributes[id_attr]
        out.findings.append(
            Finding(
                kind=Kind.SEMANTICALLY_EMPTY_ENUM,
                path=(*out.path, id_attr),
                owner=out.owner.key,
                message=f"{dotted((*out.path, id_attr))} is 0 (Unknown) with an empty {spec.label}",
                tier=ra.requirement,
                profiles=ra.gated_by,
                actual=label,
            )
        )
    out.put(spec.label, caption, Kind.SIBLING_CONFLICT)


def _other_type_name(out: _Node, label: str) -> None:
    # activity_id 99: type_name follows the caller's activity label, not the enum's "…: Other".
    activity, ident = out.node.get("activity_name"), out.owner.identity
    if isinstance(activity, str) and activity and ident is not None:
        out.put(label, f"{ident.class_name}: {activity}", Kind.SIBLING_CONFLICT)


def _sibling_list(out: _Node, id_attr: str, spec: SiblingSpec, value: object) -> None:
    ids = _list(value)
    if ids is None or spec.enum is None:
        return
    captions: list[str] = []
    for i in ids:
        member = spec.enum.get(i) if _is_int(i) else None
        if member is None:
            return  # conform reports the element; nothing to derive from
        captions.append(member.caption)
    labels = out.node.get(spec.label)
    if labels is None:
        if OTHER not in ids:
            out.put(spec.label, captions, Kind.SIBLING_CONFLICT)
        return
    given = _list(labels)
    if given is None:
        return
    # An "Other" element keeps the caller's label at the same index.
    expected = [(given[n] if n < len(given) else None) if i == OTHER else captions[n] for n, i in enumerate(ids)]
    keep = len(given) != len(ids) and OTHER in ids  # no sound list to write back
    out.put(spec.label, expected, Kind.SIBLING_CONFLICT, keep=keep)


def _timestamps(out: _Node) -> None:
    attrs = out.owner.attributes
    for name, ra in attrs.items():
        twin = f"{name}_dt"
        value = out.node.get(name)
        if ra.type != "timestamp_t" or twin not in attrs or not _is_int(value):
            continue
        try:
            text = rfc3339(value)
        except OverflowError:
            continue  # out of datetime's range; nothing sound to derive
        out.put(twin, text, Kind.SIBLING_CONFLICT, equal=_same_instant)


def rfc3339(ms: int) -> str:
    """Epoch milliseconds as YYYY-MM-DDTHH:MM:SS.mmmZ, the form of OCSF's datetime_t."""
    moment = _EPOCH + timedelta(milliseconds=ms)
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}Z"


def _same_instant(given: object, derived: object) -> bool:
    if not isinstance(given, str) or not isinstance(derived, str):
        return False
    try:
        a, b = datetime.fromisoformat(given), datetime.fromisoformat(derived)
    except ValueError:
        return False
    return a.tzinfo is not None and a == b


def _caption(schema: ResolvedSchema, spec: SiblingSpec, value: object) -> str | None:
    if not _is_int(value):
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


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _same(a: object, b: object) -> bool:
    """Equal and of the same type (True is not 1); lists element-wise, as labels are flat."""
    x, y = _list(a), _list(b)
    if x is not None and y is not None:
        return len(x) == len(y) and all(type(p) is type(q) and p == q for p, q in zip(x, y, strict=True))
    return type(a) is type(b) and a == b


def _list(value: object) -> list[object] | None:
    return cast(list[object], value) if isinstance(value, list) else None
