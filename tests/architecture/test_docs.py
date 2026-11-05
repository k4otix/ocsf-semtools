"""Repo plan §3: a name enters ``__all__`` only with a docs page. Each public name is a heading in
docs/*.md, and every page the README links to exists. (The pages' examples run as doctests.)"""

import re
import tomllib

import ocsf_toolkit
from tests.conftest import ROOT

DOCS = ROOT / "docs"
PAGES = sorted(DOCS.glob("*.md"))  # docs/design and docs/prior-art are maintainer-local
HEADING = re.compile(r"^#{2,4} `([A-Za-z_][A-Za-z0-9_]*)`$", re.MULTILINE)


def _documented() -> set[str]:
    return {name for page in PAGES for name in HEADING.findall(page.read_text(encoding="utf-8"))}


def test_every_public_name_has_a_docs_heading() -> None:
    public = set(ocsf_toolkit.__all__) - {"__version__"}
    assert sorted(public - _documented()) == []


def test_every_docs_heading_is_public() -> None:
    """A heading for a name that left __all__ is a stale page."""
    public = set(ocsf_toolkit.__all__)
    commands = {"check", "diff", "generate"}  # docs/cli.md documents commands the same way
    assert sorted(_documented() - public - commands) == []


def test_readme_links_resolve() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    targets = re.findall(r"\]\(((?:docs/)?[\w./-]+\.(?:md|lock))\)", readme)
    assert targets
    assert [t for t in targets if not (ROOT / t).is_file()] == []
    for page in PAGES:
        for target in re.findall(r"\]\(([\w-]+\.md)\)", page.read_text(encoding="utf-8")):
            assert (DOCS / target).is_file(), f"{page.name} links to missing {target}"


def test_every_page_ships_in_the_sdist() -> None:
    """The sdist allowlist names docs pages one by one so docs/design can never slip in."""
    hatch = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["hatch"]
    allowed = set(hatch["build"]["targets"]["sdist"]["only-include"])
    assert sorted(f"docs/{p.name}" for p in PAGES if f"docs/{p.name}" not in allowed) == []
