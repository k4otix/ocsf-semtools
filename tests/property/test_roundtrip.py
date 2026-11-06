"""acc. #3: every class of every model-window version survives build -> JSON -> parse unchanged.

Dict events for each core class, and typed instances of the generated class for the same fields:
build -> to_builtins -> JSON -> parse(as_=) gives back an equal instance.
"""

import json
import warnings
from pathlib import Path
from types import ModuleType
from typing import Any

import msgspec
import pytest
from hypothesis import given
from hypothesis import strategies as st

from ocsf_toolkit import ResolvedSchema, build, load, parse
from tests.fixtures.packages import generated_class, import_generated
from tests.property.strategies import SETTINGS, WINDOW, fields, profile_sets

CASES = [(v, key) for v in WINDOW for key in sorted(load(v).classes)]


def _ids(case: tuple[str, str]) -> str:
    return f"{case[0]}-{case[1]}"


def build_event(schema: ResolvedSchema, key: str, values: dict[str, Any]) -> dict[str, Any]:
    """Build quietly: deprecated attributes warn by design, and the strategies use them."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return build((schema, key), **values)


@pytest.fixture(scope="module")
def packages(tmp_path_factory: pytest.TempPathFactory) -> dict[str, ModuleType]:
    """Superset models for every window version, generated as the dists are."""
    out: Path = tmp_path_factory.mktemp("roundtrip")
    return {v: import_generated(load(v).ir, f"rt_v{v.replace('.', '_')}", out / v) for v in WINDOW}


@pytest.mark.parametrize("case", CASES, ids=map(_ids, CASES))
@SETTINGS
@given(data=st.data())
def test_dict_round_trip(case: tuple[str, str], data: st.DataObject) -> None:
    version, key = case
    profiles = data.draw(st.sampled_from(profile_sets(version)), label="profiles")
    schema = load(version, profiles=profiles)
    event = build_event(schema, key, data.draw(fields(version, profiles, key), label="fields"))
    decoded = json.loads(json.dumps(event))
    assert decoded == event
    parsed, report = parse(decoded)
    assert report.errors() == ()
    assert report.class_key == key
    assert parsed == event


@pytest.mark.parametrize("case", CASES, ids=map(_ids, CASES))
@SETTINGS
@given(data=st.data())
def test_typed_round_trip(case: tuple[str, str], data: st.DataObject, packages: dict[str, ModuleType]) -> None:
    version, key = case
    cls = generated_class(packages[version], key)
    profiles = data.draw(st.sampled_from(profile_sets(version)), label="profiles")
    schema = load(version, profiles=profiles)
    values = data.draw(fields(version, profiles, key), label="fields")
    typed = msgspec.structs.asdict(msgspec.convert(values, cls))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        instance = build(cls, **{k: v for k, v in typed.items() if v is not None})
    assert msgspec.to_builtins(instance) == build_event(schema, key, values)
    back, report = parse(json.loads(msgspec.json.encode(instance)), as_=cls)
    assert report.errors() == ()
    assert back == instance
