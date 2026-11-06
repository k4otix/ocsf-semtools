"""Render an IR into a package of msgspec Struct modules, and install it into a directory."""

import keyword
import os
import re
import shutil
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path

from ocsf_toolkit.codegen.emit import (
    MARKER,
    Enum,
    Field,
    Imports,
    enum_class,
    header,
    lazy_init,
    module,
    string,
    struct_class,
)
from ocsf_toolkit.codegen.layout import Place, exports, plan
from ocsf_toolkit.codegen.naming import class_name, field_name
from ocsf_toolkit.codegen.types import OPEN_OBJECT, annotation
from ocsf_toolkit.export import export_ir
from ocsf_toolkit.ir import ItemDef, Requirement, SchemaIR
from ocsf_toolkit.schema import ResolvedAttribute, ResolvedItem, ResolvedSchema, resolve

IR_FILE = "ir.json"  # every package carries its IR, so it never depends on core's copy
_IDENTITY = ("class_uid", "class_name", "category_uid", "category_name")
_ANY = re.compile(r"\bAny\b")
_FIXED = "identity: fixed by the class"
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?")
_NAME = re.compile(r"[a-z0-9_]+(/[a-z0-9_]+)?")  # "cloud", "linux/linux_users"


def generate(
    ir: SchemaIR,
    *,
    package: str,
    out: Path,
    profiles: Iterable[str] | None = None,
    extensions: Iterable[str] | None = None,
) -> Path:
    """Write the package ``out/<package>`` and return its path.

    ``profiles=None`` emits superset classes (every profile attribute optional); a profile set
    emits exact classes for that set. ``extensions=None`` includes every extension in the IR.
    An existing ``out/<package>`` is replaced only when every file in it is one this generator
    writes; anything else there (a symlink, a hand-added file) is an error and nothing changes.
    """
    files = render(ir, package=package, profiles=profiles, extensions=extensions)
    target = out / package
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise ValueError(f"{target} is not a generated package directory; not replacing it")
    if target.exists() and (foreign := _foreign(target)):
        shown = ", ".join(str(p) for p in foreign[:3]) + (", ..." if len(foreign) > 3 else "")
        raise ValueError(f"{target} holds files ocsf-toolkit did not generate ({shown})")
    _install(files, target)
    return target


def _install(files: Mapping[str, bytes], target: Path) -> None:
    """Write the files as the directory target, replacing whatever generated package is there.

    Everything is staged in a directory created here, so the only tree ever deleted is that one:
    the new files on failure, or the replaced (verified generated) package on success.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=target.parent, prefix=f".{target.name}-"))
    old = staging / "replaced"
    try:
        new = staging / target.name
        for name, data in files.items():
            path = new / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        if target.exists():
            target.rename(old)
        try:
            new.rename(target)
        except OSError:
            if old.exists() and not target.exists():
                old.rename(target)  # put the previous package back
            raise
    finally:
        shutil.rmtree(staging)


def _foreign(target: Path) -> list[Path]:
    """Paths under a package directory that this generator would not have written."""
    foreign: list[Path] = []
    for root, dirs, names in os.walk(target, followlinks=False):
        here = Path(root)
        foreign += [here / d for d in dirs if (here / d).is_symlink()]
        for name in names:
            path = here / name
            rel = path.relative_to(target)
            generated = (
                rel.as_posix() in ("py.typed", IR_FILE)
                or ("__pycache__" in rel.parts and path.suffix == ".pyc")
                or (path.suffix == ".py" and _first_line(path).startswith(MARKER))
            )
            if path.is_symlink() or not generated:
                foreign.append(path)
    return sorted(foreign)


def _first_line(path: Path) -> str:
    with path.open("rb") as f:
        return f.readline().decode("utf-8", "replace")


def render(
    ir: SchemaIR,
    *,
    package: str,
    profiles: Iterable[str] | None = None,
    extensions: Iterable[str] | None = None,
) -> dict[str, bytes]:
    """Return relative path -> file bytes: a pure function of the IR, the arguments and this version."""
    if not package.isidentifier() or keyword.iskeyword(package):
        raise ValueError(f"package name {package!r} is not a single Python identifier")
    _check_names(ir)
    active = frozenset(ir.extensions) if extensions is None else frozenset(extensions)
    if profiles is None:
        usable = [k for k, p in ir.profiles.items() if p.extension in (None, *active)]
        schema = resolve(ir, profiles=usable, extensions=active)
    else:
        schema = resolve(ir, profiles=profiles, extensions=active)
    gen = _Generator(ir, schema, exact=profiles is not None)
    return {path: text.encode() for path, text in gen.files(package).items()}


def _check_names(ir: SchemaIR) -> None:
    """Refuse IR names that would be written into comments or code unquoted.

    A custom extension's IR is input, and a newline in a profile name would end a comment.
    """
    if not _VERSION.fullmatch(ir.version):
        raise ValueError(f"OCSF version {ir.version!r} is not a plain version number")
    for what, names in (("profile", ir.profiles), ("extension", ir.extensions)):
        for name in names:
            if not _NAME.fullmatch(name):
                raise ValueError(f"{what} name {name!r} cannot appear in generated code")


class _Generator:
    def __init__(self, ir: SchemaIR, schema: ResolvedSchema, *, exact: bool) -> None:
        self.ir = ir
        self.schema = schema
        self.exact = exact
        self.defs: dict[str, ItemDef] = {
            **{k: ir.classes[k] for k in schema.classes},
            **{k: ir.objects[k] for k in schema.objects if k != OPEN_OBJECT},  # typed as a dict
        }
        self.places = plan(self.defs.values())
        self.shared: dict[str, Enum] = {}  # name -> enum, for enums.py
        self.header = header(schema.version, schema.digest)

    def files(self, package: str) -> dict[str, str]:
        out: dict[str, str] = {}
        items = {**self.schema.classes, **self.schema.objects}  # by key
        for key, place in sorted(self.places.items()):
            out[place.path] = self._module(items[key], place)
        imports = Imports()
        bodies = [enum_class(e, imports) for _, e in sorted(self.shared.items())]
        doc = f"Enums of the OCSF {self.schema.version} dictionary, shared by its classes and objects."
        out["enums.py"] = module(self.header, doc, imports, None, bodies)
        trees = exports(self.places.values())
        packages = {p.package[:n] for p in self.places.values() for n in (1, len(p.package))}
        for pkg in sorted(packages | {()}):
            path = "/".join((*pkg, "__init__.py"))
            dunders = self._dunders() if pkg == () else []
            out[path] = lazy_init(self.header, self._package_doc(pkg), trees.get(pkg, []), pkg, dunders)
        out["py.typed"] = ""
        out[IR_FILE] = export_ir(self.ir).decode()
        return dict(sorted(out.items()))

    def _dunders(self) -> list[str]:
        profiles = "None" if not self.exact else _tuple(sorted(self.schema.profiles))
        return [
            f"__ocsf_version__ = {string(self.schema.version)}",
            f"__schema_digest__ = {string(self.schema.digest)}",
            f"__ocsf_profiles__: tuple[str, ...] | None = {profiles}",
            f"__ocsf_extensions__: tuple[str, ...] = {_tuple(sorted(self.schema.extensions))}",
        ]

    def _package_doc(self, pkg: tuple[str, ...]) -> str:
        version = self.schema.version
        if pkg == ():
            if not self.exact:
                return (
                    f"Typed models for OCSF {version}, superset classes: profile and extension attributes are optional."
                )
            # the profile list goes below the summary line, which must fit on one line however many there are
            summary = f"Typed models for OCSF {version}, exact classes for one profile set."
            return f"{summary}\n\nProfiles: {sorted(self.schema.profiles)}."
        if pkg == ("classes",):
            return f"OCSF {version} classes; import them from the package root."
        if pkg == ("objects",):
            return f"OCSF {version} objects."
        if pkg == ("ext",):
            return f"OCSF {version} extensions, one subpackage each."
        return f"OCSF {version} classes and objects of the {pkg[1]} extension."

    def _module(self, item: ResolvedItem, here: Place) -> str:
        imports = Imports()
        local: list[Enum] = []
        groups: dict[str, list[Field]] = {}
        names = set(item.attributes)
        for name, a in sorted(item.attributes.items()):
            python = field_name(name)
            if python != name and python in names:
                raise ValueError(f"{item.key}.{name}: field name {python!r} is taken")
            # identity and derived values are written by build(), so they carry no enum type
            enum = self._fixed(item, a) is None and not self._derived(item, a)
            named = self._reference(item, a, here, imports, local, enum=enum)
            group, value, default = self._classify(item, a, annotation(a, named), imports)
            field = Field(python, name, value, default, always=group == _FIXED)
            groups.setdefault(group, []).append(field)
        identity = groups.get(_FIXED, [])
        identity.sort(key=lambda f: _IDENTITY.index(f.wire) if f.wire in _IDENTITY else 0)
        bodies = [enum_class(e, imports) for e in sorted(local, key=lambda e: e.name)]
        ocsf = (self.schema.version, item.key)
        bodies.append(struct_class(here.name, self._doc(item), ocsf, _ordered(groups), imports))
        kind = "class" if item.is_class else "object"
        return module(self.header, f"OCSF {self.schema.version} {kind} {item.key}.", imports, here, bodies)

    def _reference(
        self,
        item: ResolvedItem,
        a: ResolvedAttribute,
        here: Place,
        imports: Imports,
        local: list[Enum],
        *,
        enum: bool,
    ) -> str | None:
        if a.object_type is not None and a.object_type != OPEN_OBJECT:
            target = self.places[a.object_type]
            if target == here:
                return target.name
            imports.modules.add(target)
            return f"{target.alias}.{target.name}"
        if not a.enum or not enum:
            return None
        scoped = f"{a.extension}/{a.name}" if a.extension else None
        entry = (scoped and self.ir.dictionary.get(scoped)) or self.ir.dictionary.get(a.name)
        if entry is not None and entry.enum == a.enum:
            name = class_name(a.name)
            known = self.shared.get(name)
            if known is not None and known.enum != a.enum:
                raise ValueError(f"two dictionary enums would both be named {name}")
            self.shared[name] = Enum(name, f"{entry.caption}: the dictionary enum.", a.enum)
            imports.enums.add(name)
            return name
        name = class_name(self.defs[item.key].name) + class_name(a.name)
        local.append(Enum(name, f"{a.caption} of {item.key}.", a.enum))
        return name

    def _classify(
        self, item: ResolvedItem, a: ResolvedAttribute, value: str, imports: Imports
    ) -> tuple[str, str, str | None]:
        """Return (group, annotation, default) for one field."""
        fixed = self._fixed(item, a)
        if fixed is not None:
            imports.need("typing", "Literal")
            return _FIXED, f"Literal[{fixed}]", fixed
        if _ANY.search(value):
            imports.need("typing", "Any")
        owner = self.defs[item.key]
        derived = self._derived(item, a)
        group: str | None = None
        if a.gated_by:
            group = f"profile: {', '.join(sorted(a.gated_by))}"
        elif a.extension is not None and a.extension != owner.extension:
            group = f"extension: {a.extension}"
        elif derived:
            return "derived: build() fills these", value, "None"
        # Superset classes can't require what only a profile requires; extension attributes on
        # core items are opt-in; deprecated and derived values are never asked of the caller.
        required = (
            a.requirement is Requirement.REQUIRED
            and a.deprecated is None
            and not derived
            and not (a.gated_by and not self.exact)
            and not (group or "").startswith("extension")
        )
        if group is None:
            group = "required" if required else "recommended and optional"
        return group, value, None if required else "None"

    def _fixed(self, item: ResolvedItem, a: ResolvedAttribute) -> str | None:
        """Return the literal a class fixes for an identity attribute, or metadata.version."""
        if item.identity is not None and a.name in _IDENTITY:
            value = getattr(item.identity, a.name)
            return None if value is None else (string(value) if isinstance(value, str) else str(value))
        if item.key == "metadata" and a.name == "version":
            return string(self.schema.version)
        return None

    def _derived(self, item: ResolvedItem, a: ResolvedAttribute) -> bool:
        labels = {spec.label for spec in item.siblings.values()}
        twin = a.name.endswith("_dt") and _timestamp(item.attributes.get(a.name[:-3]))
        return a.name in labels or twin or (item.is_class and a.name == "type_uid")

    def _doc(self, item: ResolvedItem) -> str:
        owner = self.defs[item.key]
        kind = "class" if item.is_class else "object"
        uid = f" (class_uid {owner.uid})" if item.is_class else ""
        doc = f"{item.caption}: OCSF {self.schema.version} {kind} {item.key}{uid}."
        if owner.deprecated is not None:
            dep = owner.deprecated
            doc += f"\n\nDeprecated since {dep.since}: {dep.message}"
        return doc


def _timestamp(a: ResolvedAttribute | None) -> bool:
    return a is not None and a.type == "timestamp_t"


def _ordered(groups: Mapping[str, list[Field]]) -> list[tuple[str, list[Field]]]:
    fixed = [_FIXED, "required", "derived: build() fills these"]
    fixed.append("recommended and optional")
    rest = sorted(g for g in groups if g not in fixed)
    rest.sort(key=lambda g: not g.startswith("profile"))  # profiles, then extensions
    return [(g, groups[g]) for g in (*fixed, *rest) if g in groups]


def _tuple(values: list[str]) -> str:
    if len(values) == 1:
        return f"({string(values[0])},)"
    if not values:
        return "()"
    return "\n".join(["(", *(f"    {string(v)}," for v in values), ")"])
