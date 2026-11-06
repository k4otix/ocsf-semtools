"""The one traversal of an event against a schema, and the vocabulary every visitor reports in.

``Finding``, ``Kind`` and ``RepairAction`` live here because the walk and all three visitors use them;
``ocsf_toolkit.engine`` re-exports them as the public names.
"""

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, TypeGuard, cast, final

from ocsf_toolkit.ir import Requirement
from ocsf_toolkit.schema import ResolvedItem, ResolvedSchema

type Path = tuple[str | int, ...]
type Severity = Literal["error", "warning", "info"]
type Action = Literal["fill", "coerce", "overwrite", "relocate", "migrate"]

# object_t attributes typed `object` hold free-form JSON: there is no schema below them.
OPEN_OBJECT = "object"
_INDEX = re.compile(r"\[[^\]]*\]")


class Kind(StrEnum):
    """What a finding is about; each kind has one fixed severity."""

    MISSING_REQUIRED = "missing_required"
    MISSING_RECOMMENDED = "missing_recommended"
    UNKNOWN_ATTRIBUTE = "unknown_attribute"
    TYPE_MISMATCH = "type_mismatch"
    ENUM_OUT_OF_RANGE = "enum_out_of_range"
    SIBLING_CONFLICT = "sibling_conflict"
    SEMANTICALLY_EMPTY_ENUM = "semantically_empty_enum"
    IDENTITY_CONFLICT = "identity_conflict"
    PROFILE_MISMATCH = "profile_mismatch"
    CONSTRAINT_VIOLATED = "constraint_violated"
    DEPRECATED = "deprecated"
    OBSERVABLE_MISMATCH = "observable_mismatch"
    MAX_NODES_EXCEEDED = "max_nodes_exceeded"
    FILLED = "filled"
    COERCED = "coerced"
    RELOCATED = "relocated"


_SEVERITY: Mapping[Kind, Severity] = {
    Kind.MISSING_REQUIRED: "error",
    Kind.TYPE_MISMATCH: "error",
    Kind.ENUM_OUT_OF_RANGE: "error",
    Kind.SIBLING_CONFLICT: "error",
    Kind.IDENTITY_CONFLICT: "error",
    Kind.CONSTRAINT_VIOLATED: "error",
    Kind.UNKNOWN_ATTRIBUTE: "error",
    Kind.MISSING_RECOMMENDED: "warning",
    Kind.PROFILE_MISMATCH: "warning",
    Kind.DEPRECATED: "warning",
    Kind.OBSERVABLE_MISMATCH: "warning",
}


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class RepairAction:
    """One change repair() made to the event, at the finding's path."""

    action: Action
    before: object
    after: object


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Finding:
    """One deviation of an event from its schema, located by an indexed path."""

    kind: Kind
    path: Path  # ("evidences", 0, "process", "file", "name")
    owner: str  # key of the class or object whose rule produced it
    message: str
    tier: Requirement | None = None
    profiles: frozenset[str] = frozenset()  # the active profiles that admitted the attribute
    expected: object = None
    actual: object = None
    repair: RepairAction | None = None

    @property
    def severity(self) -> Severity:
        """Return error, warning or info: a pure function of kind, except that a repaired finding is info."""
        return "info" if self.repair is not None else _SEVERITY.get(self.kind, "info")


@final
@dataclass(frozen=True, slots=True)
class Visit:
    """An attribute of an object node (attr set), or the close of that node (attr None).

    ``node`` is the dict that holds ``attr``, or for a close the node itself. A visitor may change
    the node at an attribute visit, and the walk descends into what the attribute holds afterwards;
    an attribute it adds to the node is visited too, after the node's other attributes.
    A node's close comes after all its attribute visits and before any visit below it.
    """

    owner: ResolvedItem
    attr: str | None
    value: object
    path: Path
    node: dict[str, Any]


def walk(
    event: dict[str, Any],
    item: ResolvedItem,
    schema: ResolvedSchema,
    *,
    max_nodes: int | None = None,
) -> Iterator[Visit | Finding]:
    """Yield a Visit for every attribute and node close of the event, depth-first.

    Past ``max_nodes`` attribute visits the walk yields one ``max_nodes_exceeded`` Finding and stops.
    """
    stack: list[tuple[ResolvedItem, dict[str, Any], Path]] = [(item, event, ())]
    count = 0
    while stack:
        owner, node, path = stack.pop()
        children: list[tuple[ResolvedItem, dict[str, Any], Path]] = []
        keys, seen = list(node), set[str]()
        while keys:  # again for any attribute a visitor added, such as a migration's successor
            for attr in keys:
                if attr not in node:
                    continue  # a visitor moved it while visiting an earlier attribute
                count += 1
                if max_nodes is not None and count > max_nodes:
                    yield Finding(
                        kind=Kind.MAX_NODES_EXCEEDED,
                        path=path,
                        owner=owner.key,
                        message=f"walk stopped after {max_nodes} attribute visits",
                        expected=max_nodes,
                    )
                    return
                yield Visit(owner, attr, node[attr], (*path, attr), node)
                children.extend(_children(owner, attr, node, path, schema))
            seen.update(keys)
            keys = [attr for attr in node if attr not in seen]
        yield Visit(owner, None, node, path, node)
        stack.extend(reversed(children))


def dotted(path: Path) -> str:
    """Return an indexed path in upstream notation: ("evidences", 0, "name") -> "evidences[0].name"."""
    try:
        return ".".join(cast(tuple[str, ...], path)).lstrip(".")  # no index: the common case
    except TypeError:
        return "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in path).lstrip(".")


def canonical(name: str | Path) -> str:
    """Return the dotted form a path is matched by, in any upstream notation or as an indexed path.

    Leading ``$`` and ``.`` go, as do ``[]``, ``[n]`` and ``[*]`` segments: ``$.resources[*].uid``,
    ``resources[0].uid`` and ``("resources", 0, "uid")`` are all ``resources.uid``.
    """
    if not isinstance(name, str):
        return ".".join(p for p in name if isinstance(p, str))
    return _INDEX.sub("", name.lstrip("$").lstrip("."))


def is_int(value: object) -> TypeGuard[int]:
    """Tell whether a value is an integer: JSON's true and false are not."""
    return isinstance(value, int) and not isinstance(value, bool)


def _children(
    owner: ResolvedItem, attr: str, node: dict[str, Any], path: Path, schema: ResolvedSchema
) -> Sequence[tuple[ResolvedItem, dict[str, Any], Path]]:
    """Return the object nodes an attribute holds after its visit, to walk next."""
    ra = owner.attributes.get(attr)
    if ra is None or ra.object_type is None or ra.object_type == OPEN_OBJECT or attr not in node:
        return ()  # unknown, a primitive, free-form JSON, or moved away by a visitor
    child = schema.objects.get(ra.object_type)
    if child is None:
        return ()
    value = node[attr]
    if isinstance(value, dict):
        return [(child, cast(dict[str, Any], value), (*path, attr))]
    if isinstance(value, list) and ra.is_array:
        elements = enumerate(cast(list[object], value))
        return [(child, cast(dict[str, Any], e), (*path, attr, i)) for i, e in elements if isinstance(e, dict)]
    return ()
