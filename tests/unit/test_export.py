"""Canonical IR JSON: bytes, digest, round-trip, idempotence, and the ir_version policy."""

import hashlib
import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

from ocsf_toolkit.cli import ir as ir_cli
from ocsf_toolkit.cli import lock
from ocsf_toolkit.export import IR_VERSION, digest, export_ir, load_ir
from ocsf_toolkit.ir import SchemaIR
from ocsf_toolkit.loader import load_compiled
from tests.conftest import DATA_DIR, LOCKFILE, ROOT, VERSIONS, compiled_path, provenance
from tests.fixtures.compiled import PROVENANCE, compiled_bytes

COMMITTED = [e.version for e in LOCKFILE.schemas if e.ir_sha256]


@pytest.fixture(scope="module")
def ir() -> SchemaIR:
    return load_compiled(compiled_bytes(), PROVENANCE)


# --- committed IR files (no build/ needed) -----------------------------------------------------


def test_some_ir_is_committed() -> None:
    assert "1.9.0" in COMMITTED


@pytest.mark.parametrize("version", COMMITTED)
def test_committed_ir_matches_lockfile(version: str) -> None:
    data = (DATA_DIR / f"ir-{version}.json").read_bytes()
    assert hashlib.sha256(data).hexdigest() == lock.find(LOCKFILE, version).ir_sha256


@pytest.mark.parametrize("version", COMMITTED)
def test_committed_ir_is_canonical(version: str) -> None:
    data = (DATA_DIR / f"ir-{version}.json").read_bytes()
    loaded = load_ir(data)
    assert export_ir(loaded) == data
    assert loaded.version == version
    assert loaded.provenance.commit == lock.find(LOCKFILE, version).commit


def test_every_committed_ir_has_a_lockfile_entry() -> None:
    files = sorted(p.name for p in DATA_DIR.glob("ir-*.json"))
    assert files == sorted(f"ir-{v}.json" for v in COMMITTED)


# --- rebuilt from build/compiled (gate 2 in test form) ------------------------------------------


@pytest.mark.parametrize("version", VERSIONS)
def test_round_trip_and_idempotence(version: str) -> None:
    loaded = load_compiled(compiled_path(version).read_bytes(), provenance(version))
    data = export_ir(loaded)
    assert load_ir(data) == loaded
    assert export_ir(load_ir(data)) == data


@pytest.mark.parametrize("version", COMMITTED)
def test_rebuilt_ir_matches_committed(version: str) -> None:
    compiled_path(version)  # skip without build/compiled
    built = ir_cli.ir_bytes(lock.find(LOCKFILE, version), LOCKFILE.toolchain, ROOT / "build")
    assert built == (DATA_DIR / f"ir-{version}.json").read_bytes()


# --- canonical form ---------------------------------------------------------------------------


def test_canonical_form(ir: SchemaIR) -> None:
    data = export_ir(ir)
    text = data.decode("utf-8")
    assert text.endswith("}\n")
    assert text.startswith('{\n "ir_version": "1.0",\n "provenance": {\n')
    doc = json.loads(data)
    assert data == (
        json.dumps(doc, sort_keys=True, indent=1, separators=(",", ": "), ensure_ascii=False) + "\n"
    ).encode("utf-8")


def test_absent_means_none_or_empty(ir: SchemaIR) -> None:
    doc = json.loads(export_ir(ir))
    method = doc["schema"]["classes"]["finding"]["attributes"]["method"]
    assert method == {
        "caption": "Method",
        "type": "string_t",
        "requirement": "optional",
        "is_array": False,
        "enum": {"GET": {"caption": "Get"}, "PUT": {"caption": "Put"}},
    }
    assert "name" not in method  # the mapping key already says it


def test_equal_irs_export_equal_bytes(ir: SchemaIR) -> None:
    again = load_compiled(compiled_bytes(), PROVENANCE)
    assert again == ir
    assert export_ir(again) == export_ir(ir)


def test_digest_is_sha256_of_the_bytes(ir: SchemaIR) -> None:
    assert digest(ir) == hashlib.sha256(export_ir(ir)).hexdigest()


def test_provenance_changes_the_digest(ir: SchemaIR) -> None:
    moved = replace(ir, provenance=replace(ir.provenance, ref="v9.9.9"))
    assert digest(moved) != digest(ir)


def test_synthetic_round_trip(ir: SchemaIR) -> None:
    assert load_ir(export_ir(ir)) == ir


def test_load_ir_from_path(ir: SchemaIR, tmp_path: Path) -> None:
    path = tmp_path / "ir.json"
    path.write_bytes(export_ir(ir))
    assert load_ir(path) == load_ir(str(path)) == ir


# --- ir_version policy (architecture §3.4) ---------------------------------------------------


def _with(ir: SchemaIR, version: str, **extra: object) -> bytes:
    doc = json.loads(export_ir(ir))
    doc["ir_version"] = version
    for path, value in extra.items():
        target = doc
        *parents, last = path.split("__")
        for key in parents:
            target = target[key]
        target[last] = value
    return json.dumps(doc).encode()


def test_other_major_is_refused(ir: SchemaIR) -> None:
    with pytest.raises(ValueError, match=r"2\.0 is not supported; this reader reads 1\.x"):
        load_ir(_with(ir, "2.0"))


def test_unknown_field_at_this_minor_is_an_error(ir: SchemaIR) -> None:
    with pytest.raises(ValueError, match=r"\$\.schema\.types\.port_t: unknown fields \['unit'\]"):
        load_ir(_with(ir, IR_VERSION, schema__types__port_t__unit="ms"))


def test_newer_minor_is_read_with_a_warning(ir: SchemaIR) -> None:
    data = _with(ir, "1.7", schema__types__port_t__unit="ms", future="x")
    with pytest.warns(UserWarning, match=r"ignored fields: \$\.future, \$\.schema\.types\.port_t\.unit"):
        loaded = load_ir(data)
    assert loaded == ir


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"not json", "IR is not JSON"),
        (b"[]", "missing; not an IR JSON document"),
        (b'{"ir_version": "one"}', "malformed 'one'"),
    ],
)
def test_garbage_is_refused(data: bytes, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        load_ir(data)


def test_bad_field_type_names_the_path(ir: SchemaIR) -> None:
    with pytest.raises(ValueError, match=r"\$\.schema\.classes\.finding\.uid: expected an integer"):
        load_ir(_with(ir, IR_VERSION, schema__classes__finding__uid="2001"))


@pytest.mark.parametrize(("value", "constant"), [(math.nan, "NaN"), (math.inf, "Infinity"), (-math.inf, "-Infinity")])
def test_non_finite_numbers_are_refused(ir: SchemaIR, value: float, constant: str) -> None:
    with pytest.raises(ValueError, match=f"IR holds {constant}"):
        load_ir(_with(ir, IR_VERSION, schema__classes__finding__uid=value))


def test_nesting_too_deep_is_a_value_error() -> None:
    with pytest.raises(ValueError, match="nested too deeply"):
        load_ir(b"[" * 100_000)


def test_a_table_of_the_wrong_kind_is_refused(ir: SchemaIR) -> None:
    with pytest.raises(ValueError, match=r"\$\.schema\.classes: expected an object, got bool"):
        load_ir(_with(ir, IR_VERSION, schema__classes=False))


def test_an_enum_on_a_non_scalar_type_is_refused(ir: SchemaIR) -> None:
    doc = json.loads(export_ir(ir))
    attribute = next(iter(doc["schema"]["classes"]["finding"]["attributes"].values()))
    attribute.update(type="boolean_t", object_type=None, enum={"1": {"caption": "one"}})
    with pytest.raises(ValueError, match=r"\.enum: enum on a boolean_t type"):
        load_ir(json.dumps(doc).encode())
