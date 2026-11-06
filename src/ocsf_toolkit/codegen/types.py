"""IR attribute type -> annotation text; refinements (range, regex, max_len) are never encoded."""

from ocsf_toolkit.ir import Root
from ocsf_toolkit.schema import ResolvedAttribute

OPEN_OBJECT = "object"  # an object_t typed `object` holds free-form JSON

_ROOTS = {
    Root.STRING: "str",
    Root.INTEGER: "int",
    Root.LONG: "int",
    Root.FLOAT: "float",
    Root.BOOLEAN: "bool",
    Root.JSON: "Any",  # json_t is any JSON value, as the engine accepts it, not only an object
}


def annotation(a: ResolvedAttribute, named: str | None) -> str:
    """Return the value type of a field; ``named`` is the Struct or enum it refers to, if any."""
    if named is not None:
        value = named
    elif a.object_type == OPEN_OBJECT:
        value = "dict[str, Any]"
    else:
        assert a.root is not None, f"{a.owner}.{a.name} names no object and has no root"
        value = _ROOTS[a.root]
    return f"list[{value}]" if a.is_array else value
