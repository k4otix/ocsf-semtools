"""Offline stand-ins for upstream: a local ocsf-schema-shaped git repo and a fake compiler."""

import shlex
import sys
from pathlib import Path

import pytest

from tests.unit.cli.support import FAKE_COMPILER, Upstream, commit_version, git


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's git config (signing, hooks, templates) out of the tests."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@example.invalid")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@example.invalid")


@pytest.fixture
def upstream(tmp_path: Path) -> Upstream:
    """A local ocsf-schema repository with upstream's traps: tags <=1.2.0 carry a "v"; tag 1.5.0
    points at a 1.6.0-dev commit while the real 1.5.0 is the head of branch v1.5.0; 1.9.0 is an
    annotated tag."""
    repo = tmp_path / "ocsf-schema"
    repo.mkdir()
    git(repo, "init", "--quiet", "--initial-branch", "main")
    git(repo, "config", "uploadpack.allowAnySHA1InWant", "true")
    commits = {"v1.2.0": commit_version(repo, "1.2.0")}
    git(repo, "tag", "v1.2.0")
    git(repo, "checkout", "--quiet", "-b", "v1.5.0")
    commits["branch v1.5.0"] = commit_version(repo, "1.5.0")
    git(repo, "checkout", "--quiet", "main")
    commits["tag 1.5.0"] = commit_version(repo, "1.6.0-dev")
    git(repo, "tag", "1.5.0")
    commits["1.9.0"] = commit_version(repo, "1.9.0")
    git(repo, "tag", "-a", "1.9.0", "-m", "release 1.9.0")
    return Upstream(url=repo.as_uri(), commits=commits)


@pytest.fixture
def compiler_cmd(tmp_path: Path) -> str:
    script = tmp_path / "fake_compiler.py"
    script.write_text(FAKE_COMPILER)
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"
