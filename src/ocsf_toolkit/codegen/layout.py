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
        """The module's file path inside the package."""
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
        """Return the line that imports ``target``'s module from this one, under its alias."""
        return f"from {self.root}{'.'.join(target.package)} import {target.module} as {target.alias}"


def place(item: ItemDef) -> Place:
    """Return where a class or object is generated: its package, module and class name."""
    module = module_name(item.name)
    package: tuple[str, ...]
    if item.extension is not None:
        package = ("ext", module_name(item.extension))
    else:
        package = ("classes",) if item.is_class else ("objects",)
    return Place(package=package, module=module, name=class_name(item.name))


def plan(items: Iterable[ItemDef]) -> dict[str, Place]:
    """Return each item's place by key; two items sharing a module, class name or import alias is an error."""
    places = {item.key: place(item) for item in items}
    seen: dict[str, defaultdict[object, list[str]]] = {what: defaultdict(list) for what in _IDENTITIES}
    for item_key, p in places.items():
        for what, identity in zip(_IDENTITIES, (p.path, p.alias, (p.package, p.name)), strict=True):
            seen[what][identity].append(item_key)
    for what in _IDENTITIES:
        clashes = sorted(keys for keys in seen[what].values() if len(keys) > 1)
        if clashes:
            raise ValueError(f"generated {what} collides for items {clashes}")
    return places


_IDENTITIES = ("module", "import alias", "class name")  # what must be unique per place, checked in this order


def exports(places: Iterable[Place]) -> dict[tuple[str, ...], list[Place]]:
    """Return the places each package re-exports lazily: the root exports core classes."""
    out: defaultdict[tuple[str, ...], list[Place]] = defaultdict(list)
    for p in places:
        out[() if p.package == ("classes",) else p.package].append(p)
    return {package: sorted(ps, key=lambda p: p.name) for package, ps in sorted(out.items())}
