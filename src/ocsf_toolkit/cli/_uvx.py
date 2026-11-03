"""The isolated compile step: a pinned ocsf-schema commit becomes a verified schema (on 3.14)."""

import hashlib
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import cast


def compiler_argv(
    src: Path,
    *,
    compiler_version: str,
    python: str,
    compiler_cmd: str | None = None,
    extensions: Sequence[Path] = (),
) -> list[str]:
    """Command line that compiles the schema checkout at ``src`` to JSON on stdout.

    ``compiler_cmd`` replaces the ``uvx`` invocation (air-gapped builds); ``src`` is appended to it.
    Each of ``extensions`` is a directory of custom extensions, passed as ``-e PATH``.
    """
    ext_args = [arg for path in extensions for arg in ("-e", str(path))]
    if compiler_cmd is not None:
        return [*shlex.split(compiler_cmd), str(src), *ext_args]
    return [
        "uvx",
        "--quiet",
        "--python",
        python,
        "--from",
        f"ocsf-schema-compiler=={compiler_version}",
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
        head = _git("rev-parse", "HEAD", cwd=dest)
        if head != commit:
            raise ValueError(f"{dest} is a checkout of {head}, not {commit}; delete it and retry")
        return dest
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=cache_dir, prefix=f".{commit[:12]}-"))
    try:
        _git("init", "--quiet", cwd=tmp)
        _git("fetch", "--quiet", "--depth", "1", "--", repo, commit, cwd=tmp)
        _git("-c", "advice.detachedHead=false", "checkout", "--quiet", "FETCH_HEAD", cwd=tmp)
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
    canonical = json.dumps(
        compiled, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    if sha256 is not None and digest != sha256:
        raise ValueError(
            f"compiled schema {version} has sha256 {digest}, lockfile has {sha256}; "
            "compiler output changed"
        )
    return canonical, digest


def compile_commit(
    *,
    repo: str,
    commit: str,
    version: str,
    sha256: str | None,
    compiler_version: str,
    python: str,
    build_dir: Path,
    compiler_cmd: str | None = None,
) -> tuple[Path, str]:
    """Check out, compile and verify one pinned schema; write it to ``build/compiled/``.

    Returns the output path and its sha256. Nothing is written unless verification passes.
    """
    data, digest = compiled_output(
        repo=repo,
        commit=commit,
        version=version,
        sha256=sha256,
        compiler_version=compiler_version,
        python=python,
        build_dir=build_dir,
        compiler_cmd=compiler_cmd,
    )
    out = build_dir / "compiled" / f"schema-{version}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_suffix(".json.partial")
    partial.write_bytes(data)
    partial.replace(out)
    return out, digest


def compiled_output(
    *,
    repo: str,
    commit: str,
    version: str,
    sha256: str | None,
    compiler_version: str,
    python: str,
    build_dir: Path,
    compiler_cmd: str | None = None,
    extensions: Sequence[Path] = (),
) -> tuple[bytes, str]:
    """Check out and compile one pinned schema, with any custom extensions; return the verified
    canonical bytes and their sha256 without writing them anywhere."""
    print(f"{version}: checkout {repo} @ {commit}", file=sys.stderr)
    src = checkout(repo, commit, build_dir / "ocsf-schema")
    argv = compiler_argv(
        src,
        compiler_version=compiler_version,
        python=python,
        compiler_cmd=compiler_cmd,
        extensions=extensions,
    )
    print(f"{version}: {shlex.join(argv)}", file=sys.stderr)
    output = subprocess.run(argv, check=True, stdout=subprocess.PIPE).stdout
    return verify(output, version=version, sha256=sha256)


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, stdout=subprocess.PIPE, text=True)
    return result.stdout.strip()
