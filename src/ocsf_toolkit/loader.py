"""Translate compiler JSON (compile_version 1) into a SchemaIR, recording every key it reads."""

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import cast

from ocsf_toolkit.ir import (
    Attribute,
    Category,
    Constraint,
    Deprecation,
    EnumMember,
    EnumValue,
    Extension,
    ItemDef,
    JsonObject,
    Profile,
    Provenance,
    Root,
    SchemaIR,
    TypeDef,
    constraint_kind,
    enum_key,
    frozen_sorted,
    parse_requirement,
)

SUPPORTED_COMPILE_VERSION = 1

# Keys deliberately left out of the IR, wherever they occur: display text (descriptions are ~70% of
# the compiled JSON), captions and ids derivable from other tables, and free-text provenance.
IGNORED_KEYS = frozenset({"description", "references", "type_name", "object_name", "extension_id", "source", "meta"})


@dataclass(slots=True)
class KeyAccount:
    """``(context, key)`` pairs present in the compiler output and read by the loader."""

    seen: set[tuple[str, str]] = field(default_factory=set[tuple[str, str]])
    read: set[tuple[str, str]] = field(default_factory=set[tuple[str, str]])

    def unaccounted(self) -> set[tuple[str, str]]:
        """Return the keys in the input that the loader neither read nor deliberately ignores."""
        return {(ctx, key) for ctx, key in self.seen - self.read if key not in IGNORED_KEYS}


def load_compiled(
    data: bytes | Mapping[str, object],
    provenance: Provenance,
    *,
    account: KeyAccount | None = None,
) -> SchemaIR:
    """Build the IR from compiler output; malformed input raises ``ValueError`` naming the key."""
    if isinstance(data, bytes):
        try:
            data = cast(Mapping[str, object], json.loads(data))
        except ValueError as exc:
            raise ValueError(f"compiler output is not JSON: {exc}") from exc
    top = _Node(data, "top", "$", account if account is not None else KeyAccount())
    compile_version = top.integer("compile_version")
    if compile_version != SUPPORTED_COMPILE_VERSION:
        raise ValueError(
            f"$.compile_version: {compile_version} is not supported; this loader reads {SUPPORTED_COMPILE_VERSION}"
        )

    categories = top.node("categories", "categories")
    categories.ignore("caption", "name")
    dictionary = top.node("dictionary", "dictionary")
    dictionary.ignore("caption", "name")
    types_node = dictionary.node("types", "types")
    types_node.ignore("caption")
    types = _types(dict(types_node.children("attributes", "type")))
    roots = {name: t.root for name, t in types.items()}

    return SchemaIR(
        version=top.text("version"),
        compile_version=compile_version,
        provenance=provenance,
        categories=frozen_sorted(
            {
                name: Category(
                    name=name,
                    uid=node.integer("uid"),
                    caption=node.text("caption"),
                    extension=node.opt_text("extension"),
                )
                for name, node in categories.children("attributes", "category")
            }
        ),
        types=frozen_sorted(types),
        dictionary=frozen_sorted(
            {name: _attribute(node, name, roots) for name, node in dictionary.children("attributes", "dict.attr")}
        ),
        classes=frozen_sorted({key: _item(node, key, True, roots) for key, node in top.children("classes", "class")}),
        objects=frozen_sorted({key: _item(node, key, False, roots) for key, node in top.children("objects", "object")}),
        profiles=frozen_sorted({key: _profile(node, key) for key, node in top.children("profiles", "profile")}),
        extensions=frozen_sorted(
            {
                key: Extension(
                    name=node.text("name"),
                    uid=node.integer("uid"),
                    caption=node.text("caption"),
                    version=node.text("version"),
                    platform=node.flag("platform_extension?"),
                )
                for key, node in top.children("extensions", "extension")
            }
        ),
    )


def _types(nodes: dict[str, "_Node"]) -> dict[str, TypeDef]:
    out: dict[str, TypeDef] = {}
    for name, node in nodes.items():
        parent = node.opt_text("type")
        out[name] = TypeDef(
            name=name,
            caption=node.text("caption"),
            root=_root(name, nodes),
            parent=parent,
            max_len=node.opt_integer("max_len"),
            regex=node.opt_text("regex"),
            range=node.range("range"),
            values=node.scalars("values"),
            observable=node.opt_integer("observable"),
            extension=node.opt_text("extension"),
        )
    return out


def _root(name: str, nodes: Mapping[str, "_Node"]) -> Root:
    seen: list[str] = []
    current = name
    while (parent := nodes[current].peek_text("type")) is not None:
        seen.append(current)
        if parent not in nodes or parent in seen:
            raise ValueError(f"$.dictionary.types.attributes.{current}.type: bad parent {parent!r}")
        current = parent
    try:
        return Root(current)
    except ValueError:
        raise ValueError(
            f"$.dictionary.types.attributes.{name}: resolves to {current!r}, not a primitive root"
        ) from None


def _item(node: "_Node", key: str, is_class: bool, roots: Mapping[str, Root]) -> ItemDef:
    attr_ctx = ("class" if is_class else "object") + ".attr"
    constraints = [
        Constraint(kind=constraint_kind(name, f"{node.path}.constraints"), attributes=attributes)
        for name, attributes in node.entries("constraints", _Node.texts).items()
    ]
    return ItemDef(
        key=key,
        name=node.text("name"),
        caption=node.text("caption"),
        is_class=is_class,
        extends=node.opt_text("extends"),
        extension=node.opt_text("extension"),
        deprecated=_deprecation(node),
        attributes=frozen_sorted(
            {name: _attribute(child, name, roots) for name, child in node.children("attributes", attr_ctx)}
        ),
        profiles=frozenset(node.texts("profiles")),
        constraints=tuple(sorted(constraints, key=lambda c: c.kind)),
        observable=None if is_class else node.opt_integer("observable"),
        uid=node.integer("uid") if is_class else None,
        category=node.text("category") if is_class else None,
        category_uid=node.integer("category_uid") if is_class else None,
        category_name=node.opt_text("category_name") if is_class else None,
        associations=node.entries("associations", _Node.texts),
        class_observables=node.entries("observables", _Node.integer) if is_class else frozen_sorted({}),
    )


def _attribute(node: "_Node", name: str, roots: Mapping[str, Root]) -> Attribute:
    type_key = node.text("type")
    return Attribute(
        name=name,
        caption=node.text("caption"),
        type=type_key,
        object_type=node.opt_text("object_type"),
        is_array=node.flag("is_array"),
        requirement=parse_requirement(node.opt_text("requirement"), node.path),
        profiles=frozenset(node.texts("profiles")),
        sibling=node.opt_text("sibling"),
        enum=_enum(node, roots.get(type_key)),
        observable=node.opt_integer("observable"),
        deprecated=_deprecation(node),
        extension=node.opt_text("extension"),
        group=node.opt_text("group"),
        suppress_checks=frozenset(node.texts("suppress_checks")),
        default=node.scalar("default"),
    )


def _enum(node: "_Node", root: Root | None) -> Mapping[EnumValue, EnumMember] | None:
    members = node.opt_node("enum", node.ctx + ".enum.map")
    if members is None or members.empty():
        return None  # OCSF <=1.2.0 dictionary placeholders: members are declared per owner
    if root not in (Root.INTEGER, Root.LONG, Root.STRING):
        raise ValueError(f"{node.path}.enum: enum on a {root or 'non-primitive'} type")
    out: dict[EnumValue, EnumMember] = {}
    for raw_key, member in members.members(node.ctx + ".enum"):
        value = enum_key(raw_key, root, member.path)
        uid = member.opt_integer("uid")  # category_uid enums repeat their key; nothing else uses it
        if uid is not None and uid != value:
            raise ValueError(f"{member.path}.uid: {uid} does not match its key")
        out[value] = EnumMember(
            value=value,
            caption=member.text("caption"),
            deprecated=_deprecation(member),
        )
    return frozen_sorted(out)


def _deprecation(node: "_Node") -> Deprecation | None:
    raw = node.opt_node("@deprecated", node.ctx + ".@deprecated")
    if raw is None:
        return None
    return Deprecation(
        message=raw.text("message"),
        since=raw.text("since"),
        superseded_by=raw.texts("superseded_by"),
    )


def _profile(node: "_Node", key: str) -> Profile:
    return Profile(
        key=key,
        name=node.text("name"),
        caption=node.text("caption"),
        extension=node.opt_text("extension"),
        annotations=node.entries("annotations", _Node.text),
    )


class _Node(JsonObject):
    """One JSON object of the compiler output: the shared typed reads, plus key accounting."""

    __slots__ = ("_account", "ctx")

    def __init__(self, data: object, ctx: str, path: str, account: KeyAccount) -> None:
        """Wrap one object read at path, recording its keys under ctx as seen."""
        super().__init__(data, path)
        self._account = account
        self.ctx = ctx
        account.seen.update((ctx, key) for key in self._data)

    def _get(self, key: str) -> object:
        self._account.read.add((self.ctx, key))
        return self._data.get(key)

    def _child(self, key: str) -> "_Node | None":
        return self.opt_node(key, self.ctx + "." + key)

    def ignore(self, *keys: str) -> None:
        """Record keys as read without reading them: display text the IR leaves out."""
        self._account.read.update((self.ctx, key) for key in keys)

    def peek_text(self, key: str) -> str | None:
        """Read without recording (for lookahead; the real read happens later)."""
        value = self._data.get(key)
        return value if isinstance(value, str) else None

    def node(self, key: str, ctx: str) -> "_Node":
        """Return the nested object at key, accounted under ctx."""
        return _Node(self._get(key), ctx, f"{self.path}.{key}", self._account)

    def opt_node(self, key: str, ctx: str) -> "_Node | None":
        """Return the nested object at key, or None when absent."""
        if self._get(key) is None:
            return None
        return self.node(key, ctx)

    def children(self, key: str, ctx: str) -> Iterator[tuple[str, "_Node"]]:
        """Yield the entries of a JSON object whose values are objects, e.g. a classes table."""
        container = self.opt_node(key, ctx + ".map")
        return iter(()) if container is None else container.members(ctx)

    def members(self, ctx: str) -> Iterator[tuple[str, "_Node"]]:
        """Yield this object's own entries as nodes accounted under ctx, e.g. an enum's members."""
        for key, value in self._data.items():
            self._account.read.add((self.ctx, key))
            yield key, _Node(value, ctx, f"{self.path}.{key}", self._account)

    def empty(self) -> bool:
        """Tell whether the object has no keys."""
        return not self._data
