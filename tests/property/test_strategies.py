"""S5: what makes the IR strategies finite holds for every model-window version."""

import pytest

from ocsf_toolkit import load
from ocsf_toolkit.ir import Requirement
from ocsf_toolkit.walk import OPEN_OBJECT
from tests.property.strategies import WINDOW, profile_sets


@pytest.mark.parametrize("version", WINDOW)
def test_no_chain_of_required_objects_is_a_cycle(version: str) -> None:
    """With the node budget spent only required attributes are drawn; this is why that ends."""
    schema = load(version, profiles=frozenset[str]().union(*profile_sets(version)))  # every core profile
    items = {**schema.objects, **schema.classes}
    edges = {
        key: {
            a.object_type
            for a in item.attributes.values()
            if a.requirement is Requirement.REQUIRED and a.object_type not in (None, OPEN_OBJECT)
        }
        for key, item in items.items()
    }
    done: set[str] = set()

    def visit(key: str, path: tuple[str, ...]) -> None:
        assert key not in path, f"required cycle: {' -> '.join((*path, key))}"
        if key in done:
            return
        for child in edges.get(key, ()):
            visit(child, (*path, key))
        done.add(key)

    for key in items:
        visit(key, ())
