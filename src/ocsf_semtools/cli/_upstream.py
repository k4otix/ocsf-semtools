"""Upstream ocsf-schema: check out a pinned commit and compile it into a verified schema (on 3.14)."""

import hashlib
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast, final

from ocsf_semtools.cli._common import atomic_write

COMPILE_PYTHON = "3.14"  # what ocsf-schema-compiler runs under, when no lockfile pins it


@final
@dataclass(frozen=True, slots=True)
class Compiler:
    """How to run ocsf-schema-compiler: the pinned version under its Python, or a replacement command."""

    version: str  # ocsf-schema-compiler release, from the lockfile's [toolchain]
    python: str  # the interpreter uvx runs it under
    cmd: str | None = None  # replaces the uvx invocation (air-gapped builds); the checkout is appended


def compiler_argv(src: Path, compiler: Compiler, extensions: Sequence[Path] = ()) -> list[str]:
    """Return the command line that compiles the schema checkout at ``src`` to JSON on stdout.

    Each of ``extensions`` is a directory of custom extensions, passed as ``-e PATH``.
    """
    ext_args = [arg for path in extensions for arg in ("-e", str(path))]
    if compiler.cmd is not None:
        return [*shlex.split(compiler.cmd), str(src), *ext_args]
    return [
        "uvx",
        "--quiet",
        "--python",
        compiler.python,
        "--from",
        f"ocsf-schema-compiler=={compiler.version}",
        "python",
        "-m",
        "ocsf_schema_compiler",
        str(src),
        *ext_args,
    ]


def checkout(repo: str, commit: str, cache_dir: Path) -> Path:
    """Return a checkout of ``repo`` at ``commit``, reusing ``cache_dir/<commit>`` when present."""
    dest = cache_dir / commit
    if dest.is_dir():
        try:
            head = run_git("rev-parse", "HEAD", cwd=dest)
        except subprocess.CalledProcessError:
            raise ValueError(f"{dest} is not a usable git checkout; delete it and retry") from None
        if head != commit:
            raise ValueError(f"{dest} is a checkout of {head}, not {commit}; delete it and retry")
        return dest
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=cache_dir, prefix=f".{commit[:12]}-"))
    try:
        run_git("init", "--quiet", cwd=tmp)
        run_git("fetch", "--quiet", "--depth", "1", "--", repo, commit, cwd=tmp)
        run_git("-c", "advice.detachedHead=false", "checkout", "--quiet", "FETCH_HEAD", cwd=tmp)
        tmp.rename(dest)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)
    return dest


def verify(output: bytes, *, version: str, sha256: str | None) -> tuple[bytes, str]:
    """Check compiler output against the lockfile; return its canonical bytes and their sha256.

    The compiler emits keys in filesystem directory order (unsorted ``os.walk``), so its raw bytes
    differ between machines; the hash is over canonical JSON (sorted keys, compact separators,
    UTF-8), the same rule as the IR export. The compiled ``version`` must equal the declared one:
    an upstream tag can point at the wrong commit (tag ``1.5.0`` carries ``1.6.0-dev``).
    ``sha256=None`` skips the hash check, for an entry whose hash is being recorded.
    """
    try:
        compiled = json.loads(output)
    except ValueError as exc:
        raise ValueError(f"compiler output is not JSON: {exc}") from exc
    actual: object = None
    if isinstance(compiled, dict):
        actual = cast(dict[str, object], compiled).get("version")
    if actual != version:
        raise ValueError(
            f"compiled schema reports version {actual!r} but the lockfile declares {version!r}; "
            "the pinned commit is not that release (upstream tag 1.5.0 is a known case)"
        )
    canonical = json.dumps(compiled, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    if sha256 is not None and digest != sha256:
        raise ValueError(
            f"compiled schema {version} has sha256 {digest}, lockfile has {sha256}; compiler output changed"
        )
    return canonical, digest


def compile_commit(
    *, repo: str, commit: str, version: str, sha256: str | None, compiler: Compiler, build_dir: Path
) -> tuple[Path, str]:
    """Check out, compile and verify one pinned schema; write it to ``build/compiled/``.

    Returns the output path and its sha256. Nothing is written unless verification passes.
    """
    data, digest = compiled_output(
        repo=repo, commit=commit, version=version, sha256=sha256, compiler=compiler, build_dir=build_dir
    )
    out = build_dir / "compiled" / f"schema-{version}.json"
    atomic_write(out, data)
    return out, digest


def compiled_output(
    *,
    repo: str,
    commit: str,
    version: str,
    sha256: str | None,
    compiler: Compiler,
    build_dir: Path,
    extensions: Sequence[Path] = (),
) -> tuple[bytes, str]:
    """Check out and compile one pinned schema, with any custom extensions.

    Return the verified canonical bytes and their sha256 without writing them anywhere.
    """
    print(f"{version}: checkout {repo} @ {commit}", file=sys.stderr)
    src = checkout(repo, commit, build_dir / "ocsf-schema")
    argv = compiler_argv(src, compiler, extensions)
    print(f"{version}: {shlex.join(argv)}", file=sys.stderr)
    try:
        output = subprocess.run(argv, check=True, stdout=subprocess.PIPE).stdout
    except FileNotFoundError:
        raise OSError(f"{argv[0]} not found: install uv (https://docs.astral.sh/uv/), or pass --compiler-cmd") from None
    return verify(output, version=version, sha256=sha256)


def run_git(*args: str, cwd: Path | None = None) -> str:
    """Run git and return its stripped output; a missing git says how to get one."""
    try:
        result = subprocess.run(["git", *args], cwd=cwd, check=True, stdout=subprocess.PIPE, text=True)
    except FileNotFoundError:
        raise OSError("git not found: compiling an OCSF schema needs git on PATH") from None
    return result.stdout.strip()
