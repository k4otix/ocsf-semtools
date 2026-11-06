"""Python names for schema names: enum members from captions, classes and fields from keys."""

import builtins
import keyword
import re
from collections import Counter
from collections.abc import Mapping

from ocsf_toolkit.ir import EnumMember, EnumValue

_NON_ALNUM = re.compile(r"[^0-9A-Za-z]+")

# Names a generated module binds or evaluates in a class body: an attribute of the same name
# would shadow one of them, so the generator refuses it rather than emit broken code.
RESERVED = frozenset(
    {"Any", "IntEnum", "Literal", "StrEnum", "Struct", "bool", "dict", "field", "float", "int"} | {"list", "str"}
)


def class_name(name: str) -> str:
    """Return the class name of an item key: ``detection_finding`` -> ``DetectionFinding``."""
    return "".join(part.capitalize() for part in name.split("_"))


def field_name(attr: str) -> str:
    """Return the Python name of an attribute: keywords take a trailing underscore (``from_``)."""
    if not attr.isidentifier() or not attr.islower() or attr.startswith("_") or attr in RESERVED:
        raise ValueError(f"attribute name {attr!r} cannot be a generated field")
    return f"{attr}_" if keyword.iskeyword(attr) else attr


def module_name(name: str) -> str:
    """Return a submodule name that no generated module body can mistake for something else."""
    shadows = hasattr(builtins, name) or name in {"enums", "importlib"}
    if not name.isidentifier() or keyword.iskeyword(name) or not name.islower() or shadows:
        raise ValueError(f"item name {name!r} cannot be a generated module")
    return name


def member_name(caption: str) -> str:
    """Return the member name of a caption.

    ``"Identity & Access"`` -> ``IDENTITY_AND_ACCESS``; ``"5Views"`` -> ``N_5VIEWS``.
    """
    name = _NON_ALNUM.sub("_", caption.replace("&", " and ")).strip("_").upper()
    return f"N_{name}" if name[:1].isdigit() else name


def member_names(enum: Mapping[EnumValue, EnumMember]) -> dict[EnumValue, str]:
    """Return member names in value order; names that collide all take the value as a suffix."""
    base = {value: member_name(m.caption) or "VALUE" for value, m in sorted(enum.items())}
    counts = Counter(base.values())
    names = {v: f"{n}_{_suffix(v)}" if counts[n] > 1 else n for v, n in base.items()}
    if len(set(names.values())) != len(names):
        raise ValueError(f"enum member names collide after suffixing: {sorted(names.values())}")
    return names


def _suffix(value: EnumValue) -> str:
    if isinstance(value, int):
        return f"NEG{-value}" if value < 0 else str(value)
    suffix = member_name(value)
    if not suffix:
        raise ValueError(f"enum value {value!r} cannot name a member")
    return suffix
