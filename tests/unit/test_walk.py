"""walk(): the one traversal — order, indexed paths, closes, live mutation, the max_nodes guard."""

from typing import Any

import pytest

from ocsf_toolkit.schema import ResolvedSchema, load
from ocsf_toolkit.walk import Finding, Kind, Path, Visit, dotted, walk


@pytest.fixture(scope="module")
def schema() -> ResolvedSchema:
    return load("1.9.0")


def _event() -> dict[str, Any]:
    return {
        "class_uid": 2004,
        "metadata": {"version": "1.9.0", "product": {"name": "p"}},
        "observables": [{"name": "a", "type_id": 4}, {"name": "b", "type_id": 1}],
        "unmapped": {"anything": {"nested": True}},
        "bogus": {"x": 1},
    }


def _visits(schema: ResolvedSchema, event: dict[str, Any]) -> list[tuple[str | None, Path]]:
    out: list[tuple[str | None, Path]] = []
    for v in walk(event, schema.classes["detection_finding"], schema):
        assert isinstance(v, Visit)
        out.append((v.attr, v.path))
    return out


def test_depth_first_with_indexed_paths_and_closes(schema: ResolvedSchema) -> None:
    assert _visits(schema, _event()) == [
        ("class_uid", ("class_uid",)),
        ("metadata", ("metadata",)),
        ("observables", ("observables",)),
        ("unmapped", ("unmapped",)),
        ("bogus", ("bogus",)),
        (None, ()),
        ("version", ("metadata", "version")),
        ("product", ("metadata", "product")),
        (None, ("metadata",)),
        ("name", ("metadata", "product", "name")),
        (None, ("metadata", "product")),
        ("name", ("observables", 0, "name")),
        ("type_id", ("observables", 0, "type_id")),
        (None, ("observables", 0)),
        ("name", ("observables", 1, "name")),
        ("type_id", ("observables", 1, "type_id")),
        (None, ("observables", 1)),
    ]


def test_owner_is_the_item_that_declares_the_attribute(schema: ResolvedSchema) -> None:
    owners = {
        v.path: v.owner.key for v in walk(_event(), schema.classes["detection_finding"], schema) if isinstance(v, Visit)
    }
    assert owners[("metadata", "product", "name")] == "product"
    assert owners[("observables", 1, "type_id")] == "observable"


def test_open_objects_and_unknown_attributes_are_not_descended(schema: ResolvedSchema) -> None:
    paths = [p for _, p in _visits(schema, _event())]
    assert not any(p[:1] in {("unmapped",), ("bogus",)} and len(p) > 1 for p in paths)


def test_the_walk_follows_changes_made_at_a_visit(schema: ResolvedSchema) -> None:
    event = _event()
    seen: list[Path] = []
    for v in walk(event, schema.classes["detection_finding"], schema):
        assert isinstance(v, Visit)
        seen.append(v.path)
        if v.attr == "metadata":
            v.node["metadata"] = {"version": "1.9.0"}  # replaced before the walk descends
        if v.attr == "observables":
            del v.node["observables"]
    assert ("metadata", "product") not in seen
    assert ("metadata", "version") in seen
    assert not any(p[:1] == ("observables",) and len(p) > 1 for p in seen)


def test_the_walk_visits_attributes_added_at_a_visit(schema: ResolvedSchema) -> None:
    event = _event()
    seen: list[Path] = []
    for v in walk(event, schema.classes["detection_finding"], schema):
        assert isinstance(v, Visit)
        seen.append(v.path)
        if v.attr == "class_uid":
            v.node["finding_info"] = {"uid": "u"}  # as a migration adds its successor
    assert seen.index(("finding_info",)) < seen.index(())  # before the node's close
    assert ("finding_info", "uid") in seen


def test_wrongly_shaped_values_are_visited_not_descended(schema: ResolvedSchema) -> None:
    event: dict[str, Any] = {"metadata": "nope", "observables": [1, {"name": "x"}]}
    assert _visits(schema, event) == [
        ("metadata", ("metadata",)),
        ("observables", ("observables",)),
        (None, ()),
        ("name", ("observables", 1, "name")),
        (None, ("observables", 1)),
    ]


def test_iterative_walk_handles_depth_beyond_the_recursion_limit(schema: ResolvedSchema) -> None:
    process: dict[str, Any] = {"name": "leaf"}
    for _ in range(3000):  # process.parent_process is self-referential
        process = {"parent_process": process}
    item = schema.objects["process"]
    visits = [v for v in walk(process, item, schema) if isinstance(v, Visit) and v.attr]
    assert len(visits) == 3001


@pytest.mark.parametrize(("limit", "tripped"), [(12, False), (11, True), (1, True)])
def test_max_nodes_counts_attribute_visits(schema: ResolvedSchema, limit: int, tripped: bool) -> None:
    out = list(walk(_event(), schema.classes["detection_finding"], schema, max_nodes=limit))
    attribute_visits = [v for v in out if isinstance(v, Visit) and v.attr is not None]
    findings = [f for f in out if isinstance(f, Finding)]
    assert len(attribute_visits) == min(limit, 12)
    assert [f.kind for f in findings] == ([Kind.MAX_NODES_EXCEEDED] if tripped else [])
    if tripped:
        assert out[-1] is findings[0]
        assert findings[0].expected == limit


def test_dotted_uses_upstream_indexed_notation() -> None:
    assert dotted(("evidences", 0, "process", "file", "name")) == "evidences[0].process.file.name"
    assert dotted(("time",)) == "time"
    assert dotted(()) == ""
