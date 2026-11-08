"""The isolated compile step: version assertion, canonical hash, checkout cache, ``compile``."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ocsf_semtools.cli import _upstream, build_parser, compile, lock, main
from ocsf_semtools.cli._upstream import Compiler
from tests.unit.cli.support import Upstream, entry, git, write_lockfile

# --- verify: version assertion + canonical sha256 -------------------------------------------

OUTPUT = b'{"version": "1.9.0", "classes": {"z": 1, "a": {"y": [3, 1], "b": "\xc3\xa9"}}}'
CANONICAL = '{"classes":{"a":{"b":"é","y":[3,1]},"z":1},"version":"1.9.0"}'.encode()


def test_verify_returns_canonical_bytes_and_their_sha() -> None:
    data, digest = _upstream.verify(OUTPUT, version="1.9.0", sha256=None)
    assert data == CANONICAL
    assert digest == hashlib.sha256(CANONICAL).hexdigest()


def test_verify_ignores_compiler_key_order() -> None:
    """The compiler's key order follows readdir order, which differs between machines."""
    reordered = b'{"classes": {"a": {"b": "\\u00e9", "y": [3, 1]}, "z": 1}, "version": "1.9.0"}'
    assert _upstream.verify(reordered, version="1.9.0", sha256=None) == _upstream.verify(
        OUTPUT, version="1.9.0", sha256=None
    )


def test_verify_accepts_matching_sha() -> None:
    expected = hashlib.sha256(CANONICAL).hexdigest()
    assert _upstream.verify(OUTPUT, version="1.9.0", sha256=expected)[1] == expected


def test_verify_rejects_sha_mismatch() -> None:
    with pytest.raises(ValueError, match="compiler output changed"):
        _upstream.verify(OUTPUT, version="1.9.0", sha256="0" * 64)


@pytest.mark.parametrize(
    "output",
    [
        b'{"version": "1.6.0-dev"}',  # upstream tag 1.5.0
        b'{"classes": {}}',
        b'["1.5.0"]',
    ],
)
def test_verify_rejects_version_mismatch(output: bytes) -> None:
    with pytest.raises(ValueError, match=r"lockfile declares '1\.5\.0'.*tag 1\.5\.0"):
        _upstream.verify(output, version="1.5.0", sha256=None)


def test_version_is_checked_before_sha() -> None:
    with pytest.raises(ValueError, match="reports version"):
        _upstream.verify(b'{"version": "1.6.0-dev"}', version="1.5.0", sha256="0" * 64)


def test_verify_rejects_non_json() -> None:
    with pytest.raises(ValueError, match="not JSON"):
        _upstream.verify(b"Traceback (most recent call last):", version="1.9.0", sha256=None)


# --- compiler command -----------------------------------------------------------------------


def test_compiler_argv_uses_pinned_uvx() -> None:
    argv = _upstream.compiler_argv(Path("build/src"), Compiler("1.1.1", "3.14"))
    assert argv == [
        "uvx",
        "--quiet",
        "--python",
        "3.14",
        "--from",
        "ocsf-schema-compiler==1.1.1",
        "python",
        "-m",
        "ocsf_schema_compiler",
        "build/src",
    ]


def test_compiler_argv_passes_extension_directories() -> None:
    argv = _upstream.compiler_argv(Path("src"), Compiler("1.1.1", "3.14", "compile"), [Path("a"), Path("b c")])
    assert argv == ["compile", "src", "-e", "a", "-e", "b c"]


def test_compiler_cmd_override_appends_checkout() -> None:
    argv = _upstream.compiler_argv(
        Path("build/src"), Compiler("1.1.1", "3.14", "'/opt/py 3.14/bin/python' -m ocsf_schema_compiler")
    )
    assert argv == ["/opt/py 3.14/bin/python", "-m", "ocsf_schema_compiler", "build/src"]


# --- checkout cache -------------------------------------------------------------------------


def test_checkout_clones_commit_then_reuses_cache(tmp_path: Path, upstream: Upstream) -> None:
    commit = upstream.commits["v1.2.0"]
    src = _upstream.checkout(upstream.url, commit, tmp_path / "cache")
    assert src == tmp_path / "cache" / commit
    assert git(src, "rev-parse", "HEAD") == commit
    assert json.loads((src / "version.json").read_text()) == {"version": "1.2.0"}
    # second call must not touch the network: an unreachable repo still succeeds
    assert _upstream.checkout("file:///nonexistent", commit, tmp_path / "cache") == src
    assert [p.name for p in (tmp_path / "cache").iterdir()] == [commit]  # no temp dirs left


def test_checkout_rejects_cache_at_wrong_commit(tmp_path: Path, upstream: Upstream) -> None:
    wrong = upstream.commits["1.9.0"]
    src = _upstream.checkout(upstream.url, upstream.commits["v1.2.0"], tmp_path / "cache")
    src.rename(tmp_path / "cache" / wrong)
    with pytest.raises(ValueError, match="delete it"):
        _upstream.checkout(upstream.url, wrong, tmp_path / "cache")


def test_checkout_failure_leaves_no_cache_entry(tmp_path: Path, upstream: Upstream) -> None:
    with pytest.raises(subprocess.CalledProcessError):
        _upstream.checkout(upstream.url, "f" * 40, tmp_path / "cache")
    assert list((tmp_path / "cache").iterdir()) == []


# --- `ocsf-semtools compile`, end to end -----------------------------------------------------


def _lockfile(tmp_path: Path, upstream: Upstream, sha256: str) -> Path:
    pinned = entry(repo=upstream.url, commit=upstream.commits["1.9.0"], compiled_sha256=sha256)
    return write_lockfile(tmp_path / "ocsf.lock", pinned)


FAKE_SHA = hashlib.sha256(b'{"classes":{"a_class":{},"z_class":{}},"version":"1.9.0"}').hexdigest()


def test_compile_command(
    tmp_path: Path, upstream: Upstream, compiler_cmd: str, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _lockfile(tmp_path, upstream, FAKE_SHA)
    build = tmp_path / "build"
    argv = [
        "compile",
        "--lock",
        str(path),
        "--build-dir",
        str(build),
        "--compiler-cmd",
        compiler_cmd,
    ]
    assert main(argv) == 0
    out = build / "compiled" / "schema-1.9.0.json"
    assert hashlib.sha256(out.read_bytes()).hexdigest() == FAKE_SHA
    assert f"sha256 {FAKE_SHA} == lockfile" in capsys.readouterr().out


def test_compile_command_fails_closed_on_hash_mismatch(
    tmp_path: Path, upstream: Upstream, compiler_cmd: str, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _lockfile(tmp_path, upstream, "0" * 64)
    build = tmp_path / "build"
    argv = [
        "compile",
        "--lock",
        str(path),
        "--build-dir",
        str(build),
        "--compiler-cmd",
        compiler_cmd,
    ]
    assert main(argv) == 2
    assert "compiler output changed" in capsys.readouterr().err
    assert not (build / "compiled").exists()


def test_compile_command_unknown_version(
    tmp_path: Path, upstream: Upstream, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _lockfile(tmp_path, upstream, FAKE_SHA)
    assert main(["compile", "1.8.0", "--lock", str(path)]) == 2
    assert "1.8.0 is not in the lockfile" in capsys.readouterr().err


def test_parser_defaults() -> None:
    args = build_parser().parse_args(["compile"])
    assert args.run is compile.run
    assert args.versions == []
    assert (args.lock, args.build_dir, args.compiler_cmd) == ("ocsf.lock", "build", None)


def test_parser_options() -> None:
    args = build_parser().parse_args(
        [
            "compile",
            "1.9.0",
            "1.8.0",
            "--lock",
            "x.lock",
            "--build-dir",
            "out",
            "--compiler-cmd",
            "py -m c",
        ]
    )
    assert args.versions == ["1.9.0", "1.8.0"]
    assert (args.lock, args.build_dir, args.compiler_cmd) == ("x.lock", "out", "py -m c")


def test_compile_record_writes_the_new_hash(
    tmp_path: Path, upstream: Upstream, compiler_cmd: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """After a compiler bump every hash changes; --record writes them, and a plain compile then passes."""
    path = _lockfile(tmp_path, upstream, "0" * 64)
    argv = ["compile", "--lock", str(path), "--build-dir", str(tmp_path / "build"), "--compiler-cmd", compiler_cmd]
    assert main([*argv, "--record"]) == 0
    assert lock.find(lock.read(path), "1.9.0").compiled_sha256 == FAKE_SHA
    assert f"1.9.0: recorded compiled_sha256 {FAKE_SHA}" in capsys.readouterr().out
    assert main(argv) == 0


def test_without_git_the_error_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))  # nothing on it
    with pytest.raises(OSError, match="git not found"):
        _upstream.checkout("file:///nowhere", "0" * 40, tmp_path / "cache")


def test_without_uv_the_error_names_compiler_cmd(
    tmp_path: Path, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    only_git = tmp_path / "bin"
    only_git.mkdir()
    git = shutil.which("git")
    assert git is not None
    (only_git / "git").symlink_to(git)
    monkeypatch.setenv("PATH", str(only_git))
    commit = upstream.commits["1.9.0"]
    with pytest.raises(OSError, match=r"uvx not found: install uv .* or pass --compiler-cmd"):
        _upstream.compiled_output(
            repo=upstream.url,
            commit=commit,
            version="1.9.0",
            sha256=None,
            compiler=Compiler("1.1.1", "3.14"),
            build_dir=tmp_path / "build",
        )


def test_a_broken_cached_checkout_says_to_delete_it(tmp_path: Path) -> None:
    commit = "0" * 40
    (tmp_path / commit).mkdir()  # a directory, but no git repository
    with pytest.raises(ValueError, match="not a usable git checkout; delete it and retry"):
        _upstream.checkout("file:///nowhere", commit, tmp_path)
