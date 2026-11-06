"""Translate compiler JSON (compile_version 1) into a SchemaIR, recording every key it reads."""

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import cast

from ocsf_toolkit.ir import (
    CONSTRAINT_KINDS,
    Attribute,
    Category,
    Constraint,
    Deprecation,
    EnumMember,
    EnumValue,
    Extension,
    ItemDef,
    Profile,
    Provenance,
    Requirement,
    Root,
    ScalarValue,
    SchemaIR,
    TypeDef,
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
        """Keys present in the input that the loader neither read nor deliberately ignores."""
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
        categories=_sorted(
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
        types=_sorted(types),
        dictionary=_sorted(
            {name: _attribute(node, name, roots) for name, node in dictionary.children("attributes", "dict.attr")}
        ),
        classes=_sorted({key: _item(node, key, True, roots) for key, node in top.children("classes", "class")}),
        objects=_sorted({key: _item(node, key, False, roots) for key, node in top.children("objects", "object")}),
        profiles=_sorted({key: _profile(node, key) for key, node in top.children("profiles", "profile")}),
        extensions=_sorted(
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
    constraints: list[Constraint] = []
    for name, attributes in node.text_lists("constraints").items():
        kind = CONSTRAINT_KINDS.get(name)
        if kind is None:
            raise ValueError(f"{node.path}.constraints: unknown constraint kind {name!r}")
        constraints.append(Constraint(kind=kind, attributes=attributes))
    return ItemDef(
        key=key,
        name=node.text("name"),
        caption=node.text("caption"),
        is_class=is_class,
        extends=node.opt_text("extends"),
        extension=node.opt_text("extension"),
        deprecated=_deprecation(node),
        attributes=_sorted(
            {name: _attribute(child, name, roots) for name, child in node.children("attributes", attr_ctx)}
        ),
        profiles=frozenset(node.texts("profiles")),
        constraints=tuple(sorted(constraints, key=lambda c: c.kind)),
        observable=None if is_class else node.opt_integer("observable"),
        uid=node.integer("uid") if is_class else None,
        category=node.text("category") if is_class else None,
        category_uid=node.integer("category_uid") if is_class else None,
        category_name=node.opt_text("category_name") if is_class else None,
        associations=_sorted(node.text_lists("associations")),
        class_observables=_sorted(node.int_map("observables") if is_class else {}),
    )


def _attribute(node: "_Node", name: str, roots: Mapping[str, Root]) -> Attribute:
    type_key = node.text("type")
    requirement = node.opt_text("requirement")
    try:
        req = Requirement(requirement) if requirement is not None else Requirement.OPTIONAL
    except ValueError:
        raise ValueError(f"{node.path}.requirement: unknown value {requirement!r}") from None
    return Attribute(
        name=name,
        caption=node.text("caption"),
        type=type_key,
        object_type=node.opt_text("object_type"),
        is_array=node.flag("is_array"),
        requirement=req,
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
    for raw_key, member in members.children_of_self(node.ctx + ".enum"):
        value: EnumValue = raw_key
        if root is not Root.STRING:
            try:
                value = int(raw_key)
            except ValueError:
                raise ValueError(f"{member.path}: enum key is not an integer") from None
        uid = member.opt_integer("uid")  # category_uid enums repeat their key; nothing else uses it
        if uid is not None and uid != value:
            raise ValueError(f"{member.path}.uid: {uid} does not match its key")
        out[value] = EnumMember(
            value=value,
            caption=member.text("caption"),
            deprecated=_deprecation(member),
        )
    return _sorted(out)


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
        annotations=_sorted(node.text_map("annotations")),
    )


def _sorted[K: int | str, V](items: Mapping[K, V]) -> Mapping[K, V]:
    """Freeze a mapping in key order, so iteration never depends on compiler output order."""
    return MappingProxyType(dict(sorted(items.items(), key=lambda kv: kv[0])))


def _as_list(value: object) -> list[object] | None:
    return cast(list[object], value) if isinstance(value, list) else None


def _kind(value: object) -> str:
    return "null" if value is None else type(value).__name__


class _Node:
    """One JSON object of the compiler output: typed reads, key accounting, error paths."""

    __slots__ = ("_account", "_data", "ctx", "path")

    def __init__(self, data: object, ctx: str, path: str, account: KeyAccount) -> None:
        if not isinstance(data, dict):
            raise ValueError(f"{path}: expected an object, got {_kind(data)}")
        self._data = cast(dict[str, object], data)
        self._account = account
        self.ctx = ctx
        self.path = path
        account.seen.update((ctx, key) for key in self._data)

    def _get(self, key: str) -> object:
        self._account.read.add((self.ctx, key))
        return self._data.get(key)

    def _fail(self, key: str, expected: str, value: object) -> ValueError:
        return ValueError(f"{self.path}.{key}: expected {expected}, got {_kind(value)}")

    def ignore(self, *keys: str) -> None:
        self._account.read.update((self.ctx, key) for key in keys)

    def peek_text(self, key: str) -> str | None:
        """Read without recording (for lookahead; the real read happens later)."""
        value = self._data.get(key)
        return value if isinstance(value, str) else None

    def text(self, key: str) -> str:
        value = self._get(key)
        if not isinstance(value, str):
            raise self._fail(key, "a string", value)
        return value

    def opt_text(self, key: str) -> str | None:
        value = self._get(key)
        if value is not None and not isinstance(value, str):
            raise self._fail(key, "a string", value)
        return value

    def integer(self, key: str) -> int:
        value = self._get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            raise self._fail(key, "an integer", value)
        return value

    def opt_integer(self, key: str) -> int | None:
        if self._get(key) is None:
            return None
        return self.integer(key)

    def flag(self, key: str) -> bool:
        value = self._get(key)
        if value is None:
            return False
        if not isinstance(value, bool):
            raise self._fail(key, "a boolean", value)
        return value

    def texts(self, key: str) -> tuple[str, ...]:
        value = self._get(key)
        if value is None:
            return ()
        items = _as_list(value)
        if items is None or not all(isinstance(v, str) for v in items):
            raise self._fail(key, "a list of strings", value)
        return tuple(cast(list[str], items))

    def scalar(self, key: str) -> ScalarValue | None:
        value = self._get(key)
        if value is not None and not isinstance(value, bool | int | float | str):
            raise self._fail(key, "a scalar", value)
        return value

    def scalars(self, key: str) -> tuple[ScalarValue, ...] | None:
        value = self._get(key)
        if value is None:
            return None
        items = _as_list(value)
        if items is None or not all(isinstance(v, bool | int | float | str) for v in items):
            raise self._fail(key, "a list of scalars", value)
        return tuple(cast(list[ScalarValue], items))

    def range(self, key: str) -> tuple[int | float, int | float] | None:
        value = self._get(key)
        if value is None:
            return None
        items = _as_list(value) or []
        if len(items) != 2 or not all(isinstance(v, int | float) and not isinstance(v, bool) for v in items):
            raise self._fail(key, "a [min, max] pair", value)
        low, high = cast(list[int | float], items)
        return (low, high)

    def node(self, key: str, ctx: str) -> "_Node":
        return _Node(self._get(key), ctx, f"{self.path}.{key}", self._account)

    def opt_node(self, key: str, ctx: str) -> "_Node | None":
        if self._get(key) is None:
            return None
        return self.node(key, ctx)

    def children(self, key: str, ctx: str) -> Iterator[tuple[str, "_Node"]]:
        """The entries of a JSON object whose values are objects, e.g. a classes table."""
        container = self.opt_node(key, ctx + ".map")
        return iter(()) if container is None else container.children_of_self(ctx)

    def children_of_self(self, ctx: str) -> Iterator[tuple[str, "_Node"]]:
        for key, value in self._data.items():
            self._account.read.add((self.ctx, key))
            yield key, _Node(value, ctx, f"{self.path}.{key}", self._account)

    def text_lists(self, key: str) -> dict[str, tuple[str, ...]]:
        container = self.opt_node(key, self.ctx + "." + key)
        if container is None:
            return {}
        return {k: container.texts(k) for k in list(container.names())}

    def text_map(self, key: str) -> dict[str, str]:
        container = self.opt_node(key, self.ctx + "." + key)
        if container is None:
            return {}
        return {k: container.text(k) for k in list(container.names())}

    def int_map(self, key: str) -> dict[str, int]:
        container = self.opt_node(key, self.ctx + "." + key)
        if container is None:
            return {}
        return {k: container.integer(k) for k in list(container.names())}

    def names(self) -> Iterator[str]:
        return iter(self._data)

    def empty(self) -> bool:
        return not self._data
