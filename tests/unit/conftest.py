"""Generated packages shared by the codegen and models tests (generating 1.9.0 takes ~0.2 s)."""

from types import ModuleType

import pytest

from ocsf_toolkit.ir import SchemaIR
from ocsf_toolkit.loader import load_compiled
from ocsf_toolkit.schema import load
from tests.fixtures.compiled import PROVENANCE, compiled_bytes
from tests.fixtures.packages import import_generated


@pytest.fixture(scope="session")
def synthetic_ir() -> SchemaIR:
    """OCSF "9.9.9": not a shipped version, so its packages bundle their IR."""
    return load_compiled(compiled_bytes(), PROVENANCE)


@pytest.fixture(scope="session")
def v1_9_0(tmp_path_factory: pytest.TempPathFactory) -> ModuleType:
    """Superset models for 1.9.0 with every extension."""
    return import_generated(load("1.9.0").ir, "gen_v1_9_0", tmp_path_factory.mktemp("superset"))


@pytest.fixture(scope="session")
def v1_9_0_cloud(tmp_path_factory: pytest.TempPathFactory) -> ModuleType:
    """Exact models for 1.9.0 under the cloud profile alone."""
    out = tmp_path_factory.mktemp("exact")
    return import_generated(load("1.9.0").ir, "gen_v1_9_0_cloud", out, profiles=["cloud"])


@pytest.fixture(scope="session")
def synthetic(tmp_path_factory: pytest.TempPathFactory, synthetic_ir: SchemaIR) -> ModuleType:
    return import_generated(synthetic_ir, "gen_synthetic", tmp_path_factory.mktemp("synthetic"))
