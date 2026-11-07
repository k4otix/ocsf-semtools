"""Arbitrary JSON in schema-shaped places: every entry point ends with a report, never another exception.

The other property tests draw conformant events and damage them in known ways. These draw values of
any JSON type, huge integers and non-finite floats, nested containers and attribute names the
schema knows or doesn't, wherever a real event would hold them.
"""

import copy
import json
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from ocsf_toolkit import extract, load, parse, repair
from tests.property.strategies import SETTINGS

SCHEMA = load("1.9.0")
CLASSES = sorted(SCHEMA.classes.values(), key=lambda item: item.key)
NAMES = sorted({name for item in (*CLASSES, *SCHEMA.objects.values()) for name in item.attributes})

scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.integers(min_value=2**63 - 2, max_value=2**80),  # around and past the 64-bit bound
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(max_size=8),
)
keys = st.one_of(st.sampled_from(NAMES), st.text(max_size=6))
json_values = st.recursive(
    scalars,
    lambda inner: st.one_of(st.lists(inner, max_size=4), st.dictionaries(keys, inner, max_size=4)),
    max_leaves=25,
)


@st.composite
def events(draw: st.DrawFn) -> dict[str, Any]:
    item = draw(st.sampled_from(CLASSES))
    names = sorted(item.attributes)
    fields = draw(st.dictionaries(st.one_of(st.sampled_from(names), keys), json_values, max_size=8))
    identity = {"class_uid": item.identity.class_uid} if item.identity and draw(st.booleans()) else {}
    metadata = draw(st.one_of(json_values, st.just({"version": "1.9.0"})))
    return {**fields, **identity, "metadata": metadata}


def _report_json(report: Any) -> str:
    return json.dumps(report.to_dict(), allow_nan=False, sort_keys=True)  # valid JSON, or this raises


@settings(parent=SETTINGS, max_examples=max(SETTINGS.max_examples, 60))
@given(event=events())
def test_every_entry_point_reports_on_any_json(event: dict[str, Any]) -> None:
    before = repr(event)  # repr, since NaN != NaN would make == useless here
    _, first = parse(event, version="1.9.0")
    assert repr(event) == before, "parse changed its input"
    _, second = parse(copy.deepcopy(event), version="1.9.0")
    assert _report_json(first) == _report_json(second), "two runs disagree"

    fixed, repaired = repair(event, version="1.9.0")
    assert repr(event) == before, "repair changed its input"
    _report_json(repaired)
    assert isinstance(fixed, dict)

    found, observed = extract(event, SCHEMA)
    assert repr(event) == before, "extract changed its input"
    _report_json(observed)
    assert all(o.value is None or isinstance(o.value, str) for o in found)
