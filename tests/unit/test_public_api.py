"""The public surface is exactly ``__all__``; a name enters it only with tests behind it."""

import ocsf_toolkit
from tests.conftest import DATA_DIR


def test_all_is_exact() -> None:
    assert sorted(ocsf_toolkit.__all__) == ["SchemaIR", "__version__", "load_ir"]


def test_load_ir_reads_a_shipped_ir() -> None:
    ir = ocsf_toolkit.load_ir(DATA_DIR / "ir-1.9.0.json")
    assert isinstance(ir, ocsf_toolkit.SchemaIR)
    assert (ir.version, ir.compile_version, ir.provenance.ref) == ("1.9.0", 1, "1.9.0")
