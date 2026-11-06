"""Hypothesis strategies drawn from the IR (acc. #3, S5): fields build() makes a conformant event of.

Everything comes from the resolved schema. Required attributes are always drawn and the rest are
sampled; enum attributes take their members' values; arrays hold up to three elements. Nesting is
bounded by a node budget rather than a depth limit: once the budget is spent only required
attributes are drawn, which terminates because no chain of required objects is a cycle (checked
in test_strategies). Values build() derives are left to it: identity, type_uid, sibling labels,
datetime twins and metadata.version. A caller-supplied label is drawn only where derivation keeps
one, beside an "Other" (99) id.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from hypothesis import HealthCheck, settings
from hypothesis import strategies as st

from ocsf_toolkit import ResolvedAttribute, ResolvedItem, ResolvedSchema, load
from ocsf_toolkit.derive import OTHER
from ocsf_toolkit.ir import Requirement, Root
from ocsf_toolkit.walk import OPEN_OBJECT
from tests.conftest import LOCKFILE

WINDOW = [e.version for e in LOCKFILE.schemas if e.ship_models]

# `ci`, the default, is derandomized so a failure reproduces from the test id alone; `thorough`
# searches with fresh randomness and the example database (HYPOTHESIS_PROFILE=thorough).
settings.register_profile(
    "ci",
    derandomize=True,
    database=None,
    max_examples=6,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
settings.register_profile(
    "thorough",
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
SETTINGS = settings.get_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))

INT32 = (-(2**31), 2**31 - 1)
INT64 = (-(2**63), 2**63 - 1)  # what msgspec and every OCSF consumer can hold
_ROOT_DERIVED = frozenset({"class_uid", "category_uid", "type_uid"})
_METADATA_DERIVED = frozenset({"version", "profiles"})  # profiles is set from the active set

_scalars = (
    st.booleans()
    | st.integers(*INT64)
    | st.floats(allow_nan=False, allow_infinity=False)  # JSON has neither
    | st.text(max_size=20)
)
# JSON as json_t and open objects hold it; null only nested, since a null attribute reads as absent
_json = st.recursive(
    st.none() | _scalars,
    lambda inner: st.lists(inner, max_size=3) | st.dictionaries(st.text(max_size=8), inner, max_size=3),
    max_leaves=8,
)
_json_object = st.dictionaries(st.text(max_size=8), _json, max_size=3)
_PRIMITIVES: Mapping[Root, st.SearchStrategy[Any]] = {
    Root.STRING: st.text(max_size=30),
    Root.INTEGER: st.integers(*INT32),
    Root.LONG: st.integers(*INT64),
    Root.FLOAT: st.floats(allow_nan=False, allow_infinity=False),
    Root.BOOLEAN: st.booleans(),
    Root.JSON: _json.filter(lambda v: v is not None),
}


def profile_sets(version: str) -> list[frozenset[str]]:
    """Return the profile sets a version is tested under: none, each half, all, and datetime alone.

    A fixed menu rather than any subset: resolving a new set costs ~50 ms, and ``load`` keeps 32.
    """
    core = sorted(p for p, d in load(version).ir.profiles.items() if d.extension is None)
    menu = [(), ("datetime",), core[::2], core[1::2], core]
    return list(dict.fromkeys(frozenset(m) for m in menu))


@dataclass(slots=True)
class _Budget:
    nodes: int


def derived(item: ResolvedItem) -> frozenset[str]:
    """Return the attributes of an item that build() derives rather than takes."""
    names = {spec.label for spec in item.siblings.values()}
    names |= {f"{n}_dt" for n, a in item.attributes.items() if a.type == "timestamp_t"}
    if item.is_class:
        names |= _ROOT_DERIVED
    if item.key == "metadata":
        names |= _METADATA_DERIVED
    return frozenset(names)


@st.composite
def fields(draw: st.DrawFn, version: str, profiles: frozenset[str], key: str, *, budget: int = 12) -> dict[str, Any]:
    """Draw the keyword arguments of ``build((load(version, profiles=profiles), key), ...)``.

    The schema is named rather than passed so the strategy's repr, which Hypothesis builds, stays small.
    """
    schema = load(version, profiles=profiles)
    node = _object(draw, schema, schema.classes[key], _Budget(draw(st.integers(0, budget))))
    if schema.profiles:
        metadata = cast(dict[str, Any], node["metadata"])
        metadata["profiles"] = sorted(schema.profiles)
    return node


def _object(draw: st.DrawFn, schema: ResolvedSchema, item: ResolvedItem, budget: _Budget) -> dict[str, Any]:
    budget.nodes -= 1
    skip = derived(item)
    node: dict[str, Any] = {}
    for name, a in sorted(item.attributes.items()):
        if name in skip:
            continue
        if a.requirement is not Requirement.REQUIRED:
            nested = a.object_type is not None and a.object_type != OPEN_OBJECT
            if (nested and budget.nodes <= 0) or not draw(st.booleans()):
                continue
        node[name] = _value(draw, schema, a, budget)
    _constrain(draw, schema, item, node, budget)
    _other_labels(draw, item, node)
    return node


def _value(draw: st.DrawFn, schema: ResolvedSchema, a: ResolvedAttribute, budget: _Budget) -> Any:
    if not a.is_array:
        return _element(draw, schema, a, budget)
    nested = a.object_type is not None and a.object_type != OPEN_OBJECT
    size = draw(st.integers(0, 2 if budget.nodes > 0 else 0)) if nested else draw(st.integers(0, 3))
    return [_element(draw, schema, a, budget) for _ in range(size)]


def _element(draw: st.DrawFn, schema: ResolvedSchema, a: ResolvedAttribute, budget: _Budget) -> Any:
    if a.object_type == OPEN_OBJECT:
        return draw(_json_object)
    if a.object_type is not None:
        return _object(draw, schema, schema.objects[a.object_type], budget)
    if a.enum is not None:
        return draw(st.sampled_from(sorted(a.enum, key=str)))
    assert a.root is not None
    return draw(_PRIMITIVES[a.root])


def _constrain(
    draw: st.DrawFn, schema: ResolvedSchema, item: ResolvedItem, node: dict[str, Any], budget: _Budget
) -> None:
    """Make the node meet its at_least_one and just_one constraints."""
    skip = derived(item)
    for c in sorted(item.constraints, key=lambda c: c.kind):  # at_least_one, then just_one
        present = [m for m in c.attributes if _lookup(node, m) is not None]
        if not present:
            # upstream names members an item lacks (event_log_actvity's log_file): never present
            usable = [m for m in c.attributes if m.split(".")[0] in item.attributes.keys() - skip]
            member = draw(st.sampled_from(usable))
            _force(draw, schema, item, node, member.split("."), budget)
            present = [member]
        if c.kind == "just_one":
            keep = draw(st.sampled_from(present))
            for m in present:
                if m != keep:
                    _remove(node, m.split("."))


def _force(
    draw: st.DrawFn,
    schema: ResolvedSchema,
    item: ResolvedItem,
    node: dict[str, Any],
    parts: list[str],
    budget: _Budget,
) -> None:
    """Set a (dotted) attribute, creating the objects on the way with their required attributes."""
    a = item.attributes[parts[0]]
    if len(parts) == 1:
        node[parts[0]] = _value(draw, schema, a, budget)
        return
    assert a.object_type is not None
    assert not a.is_array
    child = schema.objects[a.object_type]
    inner = node.get(parts[0])
    if not isinstance(inner, dict):
        inner = node[parts[0]] = _object(draw, schema, child, budget)
    _force(draw, schema, child, cast(dict[str, Any], inner), parts[1:], budget)


def _remove(node: dict[str, Any], parts: list[str]) -> None:
    for part in parts[:-1]:
        node = cast(dict[str, Any], node[part])
    del node[parts[-1]]


def _lookup(node: dict[str, Any], dotted: str) -> object:
    value: object = node
    for part in dotted.split("."):
        if not isinstance(value, dict):
            return None
        value = cast(dict[str, object], value).get(part)
    return value


def _other_labels(draw: st.DrawFn, item: ResolvedItem, node: dict[str, Any]) -> None:
    """Give an "Other" (99) id the label its source would send; derivation keeps it as given."""
    label = st.text(min_size=1, max_size=20)
    for id_attr, spec in item.siblings.items():
        value = node.get(id_attr)
        if value == OTHER:
            node[spec.label] = draw(label)
        elif isinstance(value, list) and OTHER in value and spec.enum is not None:
            ids = cast(list[int], value)
            node[spec.label] = [draw(label) if i == OTHER else spec.enum[i].caption for i in ids]
