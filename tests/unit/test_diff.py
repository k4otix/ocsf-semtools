"""diff(): the uid rename pass, the generic structural diff, and its JSON form."""

import json
from typing import Any

import pytest

from ocsf_toolkit.diff import Change, SchemaDiff, diff, plain
from ocsf_toolkit.ir import Requirement, SchemaIR
from ocsf_toolkit.loader import load_compiled
from ocsf_toolkit.schema import load
from tests.fixtures.compiled import PROVENANCE, compiled, compiled_bytes


def _ir(data: dict[str, Any]) -> SchemaIR:
    return load_compiled(compiled_bytes(data), PROVENANCE)


def _at(d: SchemaDiff, *path: str | int) -> list[Change]:
    return [c for c in d.changes if c.path == path]


def _top(d: SchemaDiff, kind: str, table: str) -> set[str | int]:
    return {c.path[1] for c in d.changes if c.kind == kind and len(c.path) == 2 and c.path[0] == table}


# --- shipped IRs ----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def d_1_1_to_1_3() -> SchemaDiff:
    return diff(load("1.1.0").ir, load("1.3.0").ir)


@pytest.fixture(scope="module")
def d_1_7_to_1_9() -> SchemaDiff:
    return diff(load("1.7.0").ir, load("1.9.0").ir)


def test_info_to_query_renames_are_uid_matched_acc_7(d_1_1_to_1_3: SchemaDiff) -> None:
    renamed = {(c.before, c.after) for c in d_1_1_to_1_3.changes if c.kind == "renamed"}
    assert renamed == {
        ("win/prefetch_info", "win/prefetch_query"),
        ("win/registry_key_info", "win/registry_key_query"),
        ("win/registry_value_info", "win/registry_value_query"),
    }
    assert _top(d_1_1_to_1_3, "removed", "classes") == set()
    added = _top(d_1_1_to_1_3, "added", "classes")
    assert len(added) == 22  # the spec's 25 added, less the three that are renames
    assert not added & {after for _, after in renamed}


def test_a_renamed_class_is_diffed_under_its_new_key(d_1_1_to_1_3: SchemaDiff) -> None:
    key = "win/registry_key_query"
    assert _at(d_1_1_to_1_3, "classes", key, "key") == []  # the rename itself says the key changed
    caption = _at(d_1_1_to_1_3, "classes", key, "attributes", "activity_id", "enum", 1, "caption")
    assert [(c.before, c.after) for c in caption] == [("Exists", "Query")]


def test_classes_added_from_1_7_to_1_9(d_1_7_to_1_9: SchemaDiff) -> None:
    """ocsf-lib's own compare found these four; nothing was removed or renamed."""
    assert _top(d_1_7_to_1_9, "added", "classes") == {
        "clipboard_activity",
        "device_power_state_activity",
        "role_management",
        "user_management",
    }
    assert _top(d_1_7_to_1_9, "removed", "classes") == set()
    assert [c for c in d_1_7_to_1_9.changes if c.kind == "renamed"] == []


@pytest.mark.parametrize(
    ("path", "before", "after"),
    [
        (
            ("classes", "user_access", "attributes", "privileges", "requirement"),
            Requirement.REQUIRED,
            Requirement.RECOMMENDED,
        ),
        (
            ("classes", "application_lifecycle", "attributes", "app", "requirement"),
            Requirement.REQUIRED,
            Requirement.OPTIONAL,
        ),
        (
            ("objects", "job", "attributes", "name", "requirement"),
            Requirement.REQUIRED,
            Requirement.RECOMMENDED,
        ),
    ],
)
def test_requirement_drift_1_7_to_1_9(
    d_1_7_to_1_9: SchemaDiff, path: tuple[str, ...], before: object, after: object
) -> None:
    assert [(c.kind, c.before, c.after) for c in _at(d_1_7_to_1_9, *path)] == [("changed", before, after)]


def test_new_deprecations_1_7_to_1_9(d_1_7_to_1_9: SchemaDiff) -> None:
    [change] = _at(d_1_7_to_1_9, "classes", "detection_finding", "attributes", "comment", "deprecated")
    assert change.before is None
    assert plain(change.after) == {
        "message": "Use the <code>notes</code> attribute instead.",
        "since": "1.9.0",
        "superseded_by": ["notes"],
    }


def test_an_ir_equals_itself() -> None:
    ir = load("1.9.0").ir
    assert diff(ir, ir) == SchemaDiff(old="1.9.0", new="1.9.0", changes=())


def test_diff_is_deterministic_and_json(d_1_7_to_1_9: SchemaDiff) -> None:
    again = diff(load("1.7.0").ir, load("1.9.0").ir)
    assert again == d_1_7_to_1_9
    keys = [tuple((isinstance(p, str), p) for p in c.path) for c in d_1_7_to_1_9.changes]
    assert keys == sorted(keys)  # integer keys (enum values) in numeric order
    data = d_1_7_to_1_9.to_dict()
    assert json.loads(json.dumps(data)) == data
    assert (data["old"], data["new"], len(data["changes"])) == ("1.7.0", "1.9.0", 1115)


# --- the generic diff on the synthetic schema -----------------------------------------------


def test_reverse_diff_swaps_added_and_removed() -> None:
    a = compiled()
    b = compiled()
    b["objects"]["extra"] = {"name": "extra", "caption": "Extra", "profiles": [], "attributes": {}}
    forward, backward = diff(_ir(a), _ir(b)), diff(_ir(b), _ir(a))
    [added] = forward.changes
    [removed] = backward.changes
    assert (added.kind, added.path, added.before) == ("added", ("objects", "extra"), None)
    assert (removed.kind, removed.path, removed.after) == ("removed", ("objects", "extra"), None)
    assert plain(added.after) == plain(removed.before)


def test_rename_with_content_change() -> None:
    a, b = compiled(), compiled()
    b["classes"]["verdict"] = b["classes"].pop("finding") | {"name": "verdict", "caption": "V"}
    d = diff(_ir(a), _ir(b))
    assert [(c.kind, c.path) for c in d.changes] == [
        ("renamed", ("classes", "finding")),
        ("changed", ("classes", "verdict", "caption")),
        ("changed", ("classes", "verdict", "name")),
    ]


def test_a_key_that_still_exists_is_never_a_rename() -> None:
    """Two classes trading uids is two changes, not two renames."""
    a, b = compiled(), compiled()
    b["classes"]["base_event"]["uid"], b["classes"]["finding"]["uid"] = 2001, 0
    d = diff(_ir(a), _ir(b))
    assert {c.kind for c in d.changes} == {"changed"}
    assert {c.path for c in d.changes} == {
        ("classes", "base_event", "uid"),
        ("classes", "finding", "uid"),
    }


def test_whole_values_compare_as_values() -> None:
    a, b = compiled(), compiled()
    b["classes"]["finding"]["profiles"] = ["cloud", "host"]
    b["classes"]["finding"]["attributes"]["old"].pop("@deprecated")
    d = diff(_ir(a), _ir(b))
    profiles = _at(d, "classes", "finding", "profiles")[0]
    assert (profiles.before, profiles.after) == (frozenset({"cloud"}), frozenset({"cloud", "host"}))
    assert plain(profiles.after) == ["cloud", "host"]
    deprecated = _at(d, "classes", "finding", "attributes", "old", "deprecated")[0]
    assert (deprecated.kind, deprecated.after) == ("changed", None)


def test_plain_refuses_non_ir_values() -> None:
    with pytest.raises(TypeError, match="not an IR value: object"):
        plain(object())


def test_a_uid_two_classes_share_is_never_a_rename() -> None:
    a, b = compiled(), compiled()
    b["classes"]["verdict"] = b["classes"].pop("finding") | {"name": "verdict"}
    b["classes"]["other"] = b["classes"]["verdict"] | {"name": "other"}  # the same uid, twice
    d = diff(_ir(a), _ir(b))
    assert {c.kind for c in d.changes if len(c.path) == 2} == {"added", "removed"}
    assert [c for c in d.changes if c.kind == "renamed"] == []
