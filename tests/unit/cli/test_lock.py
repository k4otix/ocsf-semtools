"""ocsf.lock parsing, canonical emission, ref resolution and ``lock --add``."""

import configparser
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ocsf_toolkit.cli import lock, trees
from tests.conftest import ROOT
from tests.unit.cli.support import TOOLCHAIN, Upstream, entry

COMMIT = "856d462bd20dc46cc1ffed2dfffe3b91ef0fbeba"
SHA = "b246764d43d036fa875df643fd28496815e98050ccf93fa6edec0f8b47b1debf"
IR_SHA = "7e2ecdfbeb12b53e03b2aaef27662072154b7c0dd5627b642200b1d485d858f8"

ENTRY = entry(commit=COMMIT, compiled_sha256=SHA)
LOCK = lock.Lockfile(TOOLCHAIN, (ENTRY,))


# --- parse / emit ---------------------------------------------------------------------------


def test_committed_lockfile_is_canonical() -> None:
    text = (ROOT / "ocsf.lock").read_text(encoding="utf-8")
    assert lock.dumps(lock.loads(text)) == text


# Commits verified independently in the design spikes (S2 and prior-art PROVENANCE.txt).
PINS = {
    "1.0.0": ("v1.0.0", "9608805fe0b61035cb821bb9068096fe47fed12d"),
    "1.1.0": ("v1.1.0", "8ea34523a316c4bad7e360e870a23a8e5cb29bd5"),
    "1.2.0": ("v1.2.0", "7234a336ddcd5a2359e1afa79f9651150061e0dc"),
    "1.3.0": ("1.3.0", "c8bde8c4cc7e93bb4a36e873623bbe099da22fb5"),
    "1.4.0": ("1.4.0", "6109cc5e834c1adfee67dda6c9bcfa5e37f79012"),
    "1.5.0": ("v1.5.0", "f0c227b4383ba64f223c3c9d82a2936ee96e7f04"),  # branch, not the 1.5.0 tag
    "1.6.0": ("1.6.0", "d0cd8a0fef198bf93086044e33fda6d80c74b9ef"),
    "1.7.0": ("1.7.0", "dc6359b49cd3c8f9e84dd657ec4524ab244c12ca"),
    "1.8.0": ("1.8.0", "6fa6499a0f8c9f449d342816e90e5f687c224b0a"),
    "1.9.0": ("1.9.0", COMMIT),
}


def test_committed_lockfile_pins() -> None:
    committed = lock.read(ROOT / "ocsf.lock")
    assert committed.toolchain == TOOLCHAIN
    assert {e.version: (e.ref, e.commit) for e in committed.schemas} == PINS
    entry = lock.find(committed, "1.9.0")
    assert (entry.compiled_sha256, entry.ir_sha256) == (SHA, IR_SHA)


def test_round_trip() -> None:
    assert lock.loads(lock.dumps(LOCK)) == LOCK


@given(st.text())
def test_any_string_survives_emission(value: str) -> None:
    changed = replace(LOCK, schemas=(replace(ENTRY, ref=value),))
    assert lock.loads(lock.dumps(changed)) == changed


def _broken(old: str, new: str) -> str:
    text = lock.dumps(LOCK)
    assert old in text
    return text.replace(old, new, 1)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (_broken('msgspec = "0.22.0"', 'msgspec = "0.22.0"\nextra = "x"'), "unknown keys"),
        (_broken('ir_version = "1.0"\n', ""), "missing keys"),
        (_broken('ref = "1.9.0"\n', ""), "missing keys"),
        (_broken("ship_models = true", 'ship_models = "yes"'), "must be a bool"),
        (_broken('version = "1.9.0"', "version = 190"), "must be a str"),
        (_broken('version = "1.9.0"', 'version = "1.9"'), "x.y.z"),
        (_broken(COMMIT, COMMIT[:12]), "40-hex"),
        (_broken(SHA, SHA.upper()), "64 hex"),
        (lock.dumps(replace(LOCK, schemas=(ENTRY, ENTRY))), "duplicate"),
        (_broken("[[schemas]]", "[schemas]"), "array of tables"),
        (lock.dumps(LOCK).split("[[schemas]]")[0], "missing keys"),
        (_broken(lock.UPSTREAM_REPO, "--upload-pack=touch /tmp/x"), "https:// or file://"),
        (_broken(lock.UPSTREAM_REPO, "ext::sh -c touch% /tmp/x"), "https:// or file://"),
        (_broken('python-compile = "3.14"', 'python-compile = "-c"'), "plain version"),
        (_broken('"1.1.1"', '"1.1.1 --with evil"'), "plain version"),
    ],
)
def test_loads_rejects(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        lock.loads(text)


def test_find() -> None:
    assert lock.find(LOCK, "1.9.0") is ENTRY
    with pytest.raises(ValueError, match="not in the lockfile"):
        lock.find(LOCK, "1.8.0")


def test_version_key_orders_numerically() -> None:
    assert sorted(["1.10.0", "1.9.0", "1.2.0"], key=lock.version_key) == [
        "1.2.0",
        "1.9.0",
        "1.10.0",
    ]


def test_every_shipped_version_is_in_the_generated_package_contract() -> None:
    """Rule 2: nothing imports ocsf_toolkit_v*; import-linter needs each name listed explicitly."""
    config = configparser.ConfigParser()
    config.read(ROOT / ".importlinter")
    forbidden = config["importlinter:contract:no-generated-packages"]["forbidden_modules"].split()
    expected = {trees.package_name(v) for v in lock.window(lock.read(ROOT / "ocsf.lock"))}
    assert expected <= set(forbidden)


# --- ref resolution -------------------------------------------------------------------------

LS_REMOTE = (
    "1111111111111111111111111111111111111111\trefs/heads/main\n"
    "2222222222222222222222222222222222222222\trefs/heads/v1.5.0\n"
    "3333333333333333333333333333333333333333\trefs/tags/1.5.0\n"
    "4444444444444444444444444444444444444444\trefs/tags/1.9.0\n"
    "5555555555555555555555555555555555555555\trefs/tags/1.9.0^{}\n"
    "6666666666666666666666666666666666666666\trefs/tags/v1.2.0\n"
)


def test_parse_ls_remote() -> None:
    refs = lock.parse_ls_remote(LS_REMOTE + "\n")
    assert refs["refs/tags/1.9.0^{}"] == "5" * 40
    assert len(refs) == 6


@pytest.mark.parametrize(
    ("version", "ref", "expected"),
    [
        ("1.9.0", None, ("1.9.0", "5" * 40)),  # annotated tag: the peeled commit wins
        ("1.2.0", None, ("v1.2.0", "6" * 40)),  # pre-1.3.0 tags carry a "v"
        (
            "1.5.0",
            None,
            ("1.5.0", "3" * 40),
        ),  # the mislabelled tag; compile's version check refuses it
        ("1.5.0", "v1.5.0", ("v1.5.0", "2" * 40)),  # explicit branch
        ("1.9.0", "1.9.0", ("1.9.0", "5" * 40)),  # explicit tag
    ],
)
def test_resolve_ref(version: str, ref: str | None, expected: tuple[str, str]) -> None:
    assert lock.resolve_ref(version, lock.parse_ls_remote(LS_REMOTE), ref) == expected


def test_resolve_ref_prefers_unprefixed_tag() -> None:
    refs = {"refs/tags/v2.0.0": "a" * 40, "refs/tags/2.0.0": "b" * 40}
    assert lock.resolve_ref("2.0.0", refs) == ("2.0.0", "b" * 40)


def test_resolve_ref_explicit_tag_beats_branch() -> None:
    refs = {"refs/heads/x": "a" * 40, "refs/tags/x": "b" * 40}
    assert lock.resolve_ref("2.0.0", refs, "x") == ("x", "b" * 40)


def test_resolve_ref_missing() -> None:
    with pytest.raises(ValueError, match=r"tried refs/tags/1\.8\.0, refs/tags/v1\.8\.0"):
        lock.resolve_ref("1.8.0", lock.parse_ls_remote(LS_REMOTE))


# --- lock --add, offline against a local upstream ------------------------------------------


def _add(tmp_path: Path, upstream: Upstream, compiler_cmd: str, version: str, ref: str | None = None) -> lock.Lockfile:
    return lock.add(
        LOCK,
        version,
        repo=upstream.url,
        ref=ref,
        build_dir=tmp_path / "build",
        compiler_cmd=compiler_cmd,
    )


def test_add_v_prefixed_release(tmp_path: Path, upstream: Upstream, compiler_cmd: str) -> None:
    updated = _add(tmp_path, upstream, compiler_cmd, "1.2.0")
    assert [e.version for e in updated.schemas] == ["1.2.0", "1.9.0"]  # sorted by version
    entry = lock.find(updated, "1.2.0")
    assert (entry.ref, entry.commit) == ("v1.2.0", upstream.commits["v1.2.0"])
    compiled = (tmp_path / "build" / "compiled" / "schema-1.2.0.json").read_bytes()
    assert entry.compiled_sha256 == hashlib.sha256(compiled).hexdigest()
    # two versions fit the window, so the new one ships models too (IR and tree still to record)
    assert (entry.ir_sha256, entry.models_tree_sha256, entry.ship_models) == ("", "", True)


def test_committed_window_is_the_newest_versions() -> None:
    committed = lock.read(ROOT / "ocsf.lock")
    assert lock.windowed(committed.schemas) == committed.schemas
    shipping = [e for e in committed.schemas if e.ship_models]
    assert [e.version for e in shipping] == ["1.7.0", "1.8.0", "1.9.0"]
    assert all(e.ir_sha256 and e.models_tree_sha256 for e in shipping)


def test_window_moves_to_the_newest_versions() -> None:
    def entry(version: str, ship: bool) -> lock.SchemaEntry:
        return replace(ENTRY, version=version, ship_models=ship, models_tree_sha256="ab" * 32)

    old = [entry("1.6.0", False), entry("1.7.0", True), entry("1.8.0", True)]
    old += [entry("1.9.0", True), entry("1.10.0", False)]
    new = lock.windowed(old)
    assert [(e.version, e.ship_models) for e in new] == [
        ("1.6.0", False),
        ("1.7.0", False),
        ("1.8.0", True),
        ("1.9.0", True),
        ("1.10.0", True),  # sorted numerically, not as text
    ]
    assert new[1].models_tree_sha256 == ""  # left the window: nothing checks it any more
    assert new[0] is old[0]  # unchanged entries are kept as they are
    assert new[2] is old[2]


def test_add_refuses_mislabelled_tag(tmp_path: Path, upstream: Upstream, compiler_cmd: str) -> None:
    with pytest.raises(ValueError, match=r"reports version '1\.6\.0-dev'.*declares '1\.5\.0'"):
        _add(tmp_path, upstream, compiler_cmd, "1.5.0")
    assert not (tmp_path / "build" / "compiled" / "schema-1.5.0.json").exists()


def test_add_with_explicit_branch(tmp_path: Path, upstream: Upstream, compiler_cmd: str) -> None:
    entry = lock.find(_add(tmp_path, upstream, compiler_cmd, "1.5.0", ref="v1.5.0"), "1.5.0")
    assert (entry.ref, entry.commit) == ("v1.5.0", upstream.commits["branch v1.5.0"])


@pytest.mark.parametrize(
    ("version", "repo", "message"),
    [
        ("1.9.0", lock.UPSTREAM_REPO, "already in"),
        ("1.9", lock.UPSTREAM_REPO, "x.y.z"),
        ("1.8.0", "--upload-pack=evil", "https:// or file://"),
    ],
)
def test_add_rejects(tmp_path: Path, version: str, repo: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        lock.add(LOCK, version, repo=repo, ref=None, build_dir=tmp_path, compiler_cmd=None)
