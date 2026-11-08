"""Fixtures over the real lockfile versions; compiled-schema tests skip without build/compiled."""

import os
from pathlib import Path

import pytest

from ocsf_semtools.cli import ir as ir_cli
from ocsf_semtools.cli import lock
from ocsf_semtools.ir import Provenance, SchemaIR
from ocsf_semtools.loader import load_compiled

ROOT = Path(__file__).parent.parent
LOCKFILE = lock.read(ROOT / "ocsf.lock")
VERSIONS = [entry.version for entry in LOCKFILE.schemas]
DATA_DIR = ROOT / ir_cli.DATA_DIR


def compiled_path(version: str) -> Path:
    path = ROOT / "build" / "compiled" / f"schema-{version}.json"
    if not path.is_file():
        pytest.skip(f"build/compiled/{path.name} absent; run `ocsf-semtools compile {version}`")
    return path


def provenance(version: str) -> Provenance:
    entry = lock.find(LOCKFILE, version)
    return Provenance(
        schema_repo=entry.repo,
        ref=entry.ref,
        commit=entry.commit,
        compiler_version=LOCKFILE.toolchain.ocsf_schema_compiler,
        compiler_output_sha256=entry.compiled_sha256,
        extensions=(),
    )


@pytest.fixture(scope="session")
def ir_1_9_0() -> SchemaIR:
    return load_compiled(compiled_path("1.9.0").read_bytes(), provenance("1.9.0"))


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip the tests marked network when uv is told to stay offline."""
    if os.environ.get("UV_OFFLINE"):
        offline = pytest.mark.skip(reason="UV_OFFLINE is set; this test reaches PyPI")
        for item in items:
            if item.get_closest_marker("network"):
                item.add_marker(offline)
