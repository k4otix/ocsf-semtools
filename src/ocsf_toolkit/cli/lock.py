"""Owns ``ocsf.lock``, the pin of every OCSF version this repository builds."""

import argparse
import re
import subprocess
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any, cast

from ocsf_toolkit.cli import _uvx

UPSTREAM_REPO = "https://github.com/ocsf/ocsf-schema"
WINDOW = 3  # model dists ship for the newest WINDOW versions (D#25)

_VERSION = re.compile(r"\d+\.\d+\.\d+")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"(|[0-9a-f]{64})")
# Values reach git and uvx command lines: no leading "-", no exotic git transports.
_REPO = re.compile(r"(https|file)://[^\s]+")
_TOOL_VERSION = re.compile(r"\d+(\.\d+)*")

_HEADER = """\
# ocsf.lock: schema pins. Written by `ocsf-toolkit lock`.
# Edit [toolchain] by hand; add schemas with `ocsf-toolkit lock --add <version>`.
#   version  declared OCSF version; `compile` asserts the compiled schema's "version" equals it
#   ref      upstream tag or branch as spelled upstream (informational); commit is the pin
#   compiled_sha256  sha256 of build/compiled/schema-<version>.json, which is the compiler output
#                    re-serialized as canonical JSON (raw output key order varies by filesystem)
#   ir_sha256  sha256 of src/ocsf_toolkit/data/ir-<version>.json (`ocsf-toolkit ir export --record`)
#   models_tree_sha256  sha256 of the generated model package (`ocsf-toolkit models record`)
#   ship_models  in the model window: the newest versions get a model dist (`lock --add` moves it)
"""


@dataclass(frozen=True, slots=True)
class Toolchain:
    """The pinned tools every lockfile schema is compiled and exported with."""

    ocsf_schema_compiler: str
    python_compile: str
    msgspec: str
    ir_version: str


@dataclass(frozen=True, slots=True)
class SchemaEntry:
    """One pinned OCSF version: where it comes from, and the hashes that gate it."""

    version: str
    repo: str
    ref: str
    commit: str
    compiled_sha256: str
    ir_sha256: str
    models_tree_sha256: str
    ship_models: bool


@dataclass(frozen=True, slots=True)
class Lockfile:
    """The parsed ``ocsf.lock``: the toolchain and the pinned schemas, oldest first."""

    toolchain: Toolchain
    schemas: tuple[SchemaEntry, ...]


# TOML key -> dataclass field, in emission order.
_TOOLCHAIN_KEYS = {
    "ocsf-schema-compiler": "ocsf_schema_compiler",
    "python-compile": "python_compile",
    "msgspec": "msgspec",
    "ir_version": "ir_version",
}
_SCHEMA_KEYS = {f.name: f.name for f in fields(SchemaEntry)}
_BOOL_FIELDS = {"ship_models"}  # every other value is a string


def loads(text: str) -> Lockfile:
    """Parse lockfile text; unknown, missing or malformed keys raise ``ValueError``."""
    data = tomllib.loads(text)
    _check_keys(data, {"toolchain", "schemas"}, "ocsf.lock")
    toolchain = Toolchain(**_table(data["toolchain"], _TOOLCHAIN_KEYS, "[toolchain]"))
    for key, field_name in _TOOLCHAIN_KEYS.items():
        if not _TOOL_VERSION.fullmatch(getattr(toolchain, field_name)):
            raise ValueError(f"[toolchain]: {key} must be a plain version like 1.1.1")
    raw_schemas: object = data["schemas"]
    if not isinstance(raw_schemas, list):
        raise ValueError("ocsf.lock: 'schemas' must be an array of tables ([[schemas]])")
    schemas: list[SchemaEntry] = []
    for i, raw in enumerate(cast(list[object], raw_schemas)):
        where = f"[[schemas]] #{i + 1}"
        entry = SchemaEntry(**_table(raw, _SCHEMA_KEYS, where))
        _check_entry(entry, where)
        schemas.append(entry)
    versions = [s.version for s in schemas]
    if len(set(versions)) != len(versions):
        raise ValueError(f"ocsf.lock: duplicate schema versions in {versions}")
    return Lockfile(toolchain, tuple(schemas))


def dumps(lock: Lockfile) -> str:
    """Emit canonical lockfile text: fixed key order, every key present; ``loads`` inverts it."""
    lines = [_HEADER, "[toolchain]"]
    lines += [f"{key} = {_value(getattr(lock.toolchain, f))}" for key, f in _TOOLCHAIN_KEYS.items()]
    for entry in lock.schemas:
        lines += ["", "[[schemas]]"]
        lines += [f"{key} = {_value(getattr(entry, f))}" for key, f in _SCHEMA_KEYS.items()]
    return "\n".join(lines) + "\n"


def read(path: Path) -> Lockfile:
    """Read and parse a lockfile."""
    return loads(path.read_text(encoding="utf-8"))


def write(path: Path, lock: Lockfile) -> None:
    """Write a lockfile in its canonical layout."""
    path.write_text(dumps(lock), encoding="utf-8")


def find(lock: Lockfile, version: str) -> SchemaEntry:
    """Return the entry for an OCSF version; a version not in the lockfile is a ValueError."""
    for entry in lock.schemas:
        if entry.version == version:
            return entry
    raise ValueError(f"OCSF {version} is not in the lockfile")


def version_key(version: str) -> tuple[int, ...]:
    """Return the sort key of an x.y.z version: 1.10.0 sorts after 1.9.0."""
    return tuple(int(part) for part in version.split("."))


def parse_ls_remote(output: str) -> dict[str, str]:
    """Return ``git ls-remote`` output as ``{ref: commit}``; peeled tags keep their ``^{}`` suffix."""
    refs: dict[str, str] = {}
    for line in output.splitlines():
        if line.strip():
            commit, name = line.split("\t", 1)
            refs[name] = commit
    return refs


def resolve_ref(version: str, refs: Mapping[str, str], ref: str | None = None) -> tuple[str, str]:
    """Pick the upstream ref for ``version``; return ``(ref, commit)``.

    Without ``ref``: tag ``<version>``, then tag ``v<version>`` (upstream spells tags ≤1.2.0 with a
    ``v``). With ``ref``: that tag, then that branch (e.g. branch ``v1.5.0``, because tag ``1.5.0``
    points at the wrong commit). Annotated tags resolve to the commit they point at.
    """
    if ref is None:
        candidates = [(version, f"refs/tags/{version}"), (f"v{version}", f"refs/tags/v{version}")]
    else:
        candidates = [(ref, f"refs/tags/{ref}"), (ref, f"refs/heads/{ref}")]
    for name, full in candidates:
        commit = refs.get(f"{full}^{{}}") or refs.get(full)
        if commit:
            return name, commit
    tried = ", ".join(full for _, full in candidates)
    raise ValueError(f"no upstream ref for OCSF {version} (tried {tried})")


def add(
    lock: Lockfile,
    version: str,
    *,
    repo: str,
    ref: str | None,
    build_dir: Path,
    compiler_cmd: str | None,
) -> Lockfile:
    """Resolve, pin and compile a new OCSF version; return the lockfile with its entry added."""
    if not _VERSION.fullmatch(version):
        raise ValueError(f"OCSF version must look like x.y.z, got {version!r}")
    _check_repo(repo, "--repo")
    if any(entry.version == version for entry in lock.schemas):
        raise ValueError(f"OCSF {version} is already in the lockfile")
    listing = subprocess.run(
        ["git", "ls-remote", "--tags", "--heads", repo],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout
    ref_name, commit = resolve_ref(version, parse_ls_remote(listing), ref)
    _, digest = _uvx.compile_commit(
        repo=repo,
        commit=commit,
        version=version,
        sha256=None,
        compiler_version=lock.toolchain.ocsf_schema_compiler,
        python=lock.toolchain.python_compile,
        build_dir=build_dir,
        compiler_cmd=compiler_cmd,
    )
    entry = SchemaEntry(
        version=version,
        repo=repo,
        ref=ref_name,
        commit=commit,
        compiled_sha256=digest,
        ir_sha256="",
        models_tree_sha256="",
        ship_models=False,
    )
    schemas = sorted([*lock.schemas, entry], key=lambda e: version_key(e.version))
    return replace(lock, schemas=windowed(schemas))


def windowed(schemas: Sequence[SchemaEntry]) -> tuple[SchemaEntry, ...]:
    """Turn ship_models on for the newest WINDOW versions and off for the rest.

    An entry leaving the window drops its models_tree_sha256, which nothing checks any more.
    """
    newest = set(sorted((e.version for e in schemas), key=version_key)[-WINDOW:])
    return tuple(
        e
        if e.ship_models == (e.version in newest)
        else replace(
            e,
            ship_models=e.version in newest,
            models_tree_sha256=e.models_tree_sha256 if e.version in newest else "",
        )
        for e in schemas
    )


def register(parser: argparse.ArgumentParser) -> None:
    """Add the ``lock`` arguments to its subparser."""
    parser.add_argument("--add", required=True, metavar="VERSION", help="OCSF version, e.g. 1.9.0")
    parser.add_argument("--ref", help="upstream tag or branch to pin instead of <VERSION> / v<VERSION>")
    parser.add_argument("--repo", default=UPSTREAM_REPO, help="ocsf-schema repository URL")
    parser.set_defaults(run=run)


def run(args: argparse.Namespace) -> int:
    """Pin a new OCSF version in the lockfile and move the model window."""
    path = Path(args.lock)
    updated = add(
        read(path),
        args.add,
        repo=args.repo,
        ref=args.ref,
        build_dir=Path(args.build_dir),
        compiler_cmd=args.compiler_cmd,
    )
    write(path, updated)
    entry = find(updated, args.add)
    print(f"{entry.version}: pinned ref {entry.ref} commit {entry.commit}")
    print(f"{entry.version}: compiled_sha256 {entry.compiled_sha256} -> {path}")
    window = ", ".join(e.version for e in updated.schemas if e.ship_models)
    print(f"model window: {window}")
    return 0


def _check_keys(table: Mapping[str, object], expected: set[str], where: str) -> None:
    unknown = sorted(set(table) - expected)
    missing = sorted(expected - set(table))
    if unknown:
        raise ValueError(f"{where}: unknown keys {unknown}")
    if missing:
        raise ValueError(f"{where}: missing keys {missing}")


def _table(raw: object, keys: Mapping[str, str], where: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: expected a table")
    table = cast(dict[str, object], raw)
    _check_keys(table, set(keys), where)
    out: dict[str, Any] = {}
    for key, field_name in keys.items():
        value = table[key]
        expected = bool if field_name in _BOOL_FIELDS else str
        if type(value) is not expected:
            raise ValueError(f"{where}: {key} must be a {expected.__name__}, got {value!r}")
        out[field_name] = value
    return out


def _check_entry(entry: SchemaEntry, where: str) -> None:
    if not _VERSION.fullmatch(entry.version):
        raise ValueError(f"{where}: version must look like x.y.z, got {entry.version!r}")
    _check_repo(entry.repo, where)
    if not _COMMIT.fullmatch(entry.commit):
        raise ValueError(f"{where}: commit must be a full 40-hex SHA, got {entry.commit!r}")
    for name in ("compiled_sha256", "ir_sha256", "models_tree_sha256"):
        if not _SHA256.fullmatch(getattr(entry, name)):
            raise ValueError(f'{where}: {name} must be 64 hex chars or "", got {getattr(entry, name)!r}')


def _check_repo(repo: str, where: str) -> None:
    if not _REPO.fullmatch(repo):
        raise ValueError(f"{where}: repo must be an https:// or file:// URL, got {repo!r}")


_TOML_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def _value(value: str | bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return '"' + "".join(_toml_char(c) for c in value) + '"'


def _toml_char(c: str) -> str:
    if c in _TOML_ESCAPES:
        return _TOML_ESCAPES[c]
    if ord(c) < 0x20 or ord(c) == 0x7F:
        return f"\\u{ord(c):04X}"
    return c
