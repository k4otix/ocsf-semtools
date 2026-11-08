"""Helpers more than one subcommand uses."""

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ocsf_semtools.ir import SchemaIR
from ocsf_semtools.schema import load, load_ir

type AddParser = Callable[..., argparse.ArgumentParser]  # the root's add_parser, which each register calls


@dataclass(frozen=True, slots=True)
class Options:
    """The shared options, as argparse parents; each command takes only the ones it reads."""

    lockfile: argparse.ArgumentParser
    build: argparse.ArgumentParser
    compiler: argparse.ArgumentParser

    @property
    def compiling(self) -> list[argparse.ArgumentParser]:
        """Return the parents of a command that compiles a schema checkout."""
        return [self.lockfile, self.build, self.compiler]


def options() -> Options:
    """Build the shared option parents."""
    lockfile = argparse.ArgumentParser(add_help=False)
    lockfile.add_argument("--lock", default="ocsf.lock", help="lockfile path (default: %(default)s)")
    build = argparse.ArgumentParser(add_help=False)
    build.add_argument("--build-dir", default="build", help="checkout cache and output root (default: %(default)s)")
    compiler = argparse.ArgumentParser(add_help=False)
    compiler.add_argument(
        "--compiler-cmd",
        help="command that compiles a schema checkout to stdout, replacing uvx "
        "(air-gapped builds); the checkout path is appended",
    )
    return Options(lockfile, build, compiler)


def printable(text: str) -> str:
    """Escape control characters: paths and messages carry keys taken from the input."""
    return "".join(c if c.isprintable() else repr(c)[1:-1] for c in text)


def source_ir(source: str) -> SchemaIR:
    """Load a shipped OCSF version by number, or an IR JSON file by path."""
    path = Path(source)
    if source.endswith(".json") or path.is_file():
        return load_ir(path)
    return load(source).ir


def atomic_write(path: Path, data: bytes) -> None:
    """Write data to path through a sibling ``.partial`` file, so a reader never sees half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_bytes(data)
    partial.replace(path)
