"""The package tree: classes/, objects/ and ext/<ext>/, one module per item, plus enums.py."""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from ocsf_toolkit.codegen.naming import class_name, module_name
from ocsf_toolkit.ir import ItemDef

ENUMS = "enums"


@dataclass(frozen=True, slots=True)
class Place:
    """Where one item's Struct is defined."""

    package: tuple[str, ...]  # ("classes",), ("objects",) or ("ext", "<extension>")
    module: str
    name: str  # the Struct's class name

    @property
    def path(self) -> str:
        return "/".join((*self.package, f"{self.module}.py"))

    @property
    def alias(self) -> str:
        """The name another module imports this one as: ``_process``, ``_win_reg_key``."""
        return "_" + "_".join((*self.package[1:], self.module))

    @property
    def root(self) -> str:
        """The relative-import prefix that reaches the package root from this module."""
        return "." * (len(self.package) + 1)

    def import_line(self, target: "Place") -> str:
        return f"from {self.root}{'.'.join(target.package)} import {target.module} as {target.alias}"


def place(item: ItemDef) -> Place:
    module = module_name(item.name)
    package: tuple[str, ...]
    if item.extension is not None:
        package = ("ext", module_name(item.extension))
    else:
        package = ("classes",) if item.is_class else ("objects",)
    return Place(package=package, module=module, name=class_name(item.name))


def plan(items: Iterable[ItemDef]) -> dict[str, Place]:
    """Item key -> place; two items sharing a module, class name or import alias is an error."""
    places = {item.key: place(item) for item in items}
    for what in ("module", "import alias", "class name"):
        seen: defaultdict[object, list[str]] = defaultdict(list)
        for item_key, p in places.items():
            seen[_identities(p)[what]].append(item_key)
        clashes = sorted(keys for keys in seen.values() if len(keys) > 1)
        if clashes:
            raise ValueError(f"generated {what} collides for items {clashes}")
    return places


def _identities(p: Place) -> dict[str, object]:
    return {"module": p.path, "import alias": p.alias, "class name": (p.package, p.name)}


def exports(places: Iterable[Place]) -> dict[tuple[str, ...], list[Place]]:
    """Package -> the places it re-exports lazily: the root exports core classes."""
    out: defaultdict[tuple[str, ...], list[Place]] = defaultdict(list)
    for p in places:
        out[() if p.package == ("classes",) else p.package].append(p)
    return {package: sorted(ps, key=lambda p: p.name) for package, ps in sorted(out.items())}
