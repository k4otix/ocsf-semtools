"""Events for the conformance tests: a conformant detection_finding and one fixture per error class.

Every caption and identity constant is read from
the shipped IR rather than written by hand, so a fixture cannot drift from the schema it targets.
"""

import copy
from typing import Any

from ocsf_toolkit.schema import ResolvedSchema

T0 = 1_762_000_000_000  # epoch ms, 2025-11-01T12:26:40Z
CLASS = "detection_finding"


def minimal_fields(**extra: Any) -> dict[str, Any]:
    """Return the fewest detection_finding fields build() needs, with ``extra`` on top."""
    return {
        "activity_id": 1,
        "severity_id": 4,
        "time": T0,
        "metadata": {"product": {"name": "p"}},
        "finding_info": {"uid": "f"},
        **extra,
    }


def caption(schema: ResolvedSchema, owner: str, attr: str, value: int) -> str:
    item = schema.ir.classes.get(owner) or schema.ir.objects[owner]  # unfiltered: any profile
    enum = item.attributes[attr].enum
    assert enum is not None
    return enum[value].caption


def base_detection_finding(schema: ResolvedSchema) -> dict[str, Any]:
    """A minimal conformant detection_finding for the schema's version, no profiles."""
    ident = schema.classes[CLASS].identity
    assert ident is not None
    type_uid = ident.class_uid * 100 + 1
    return {
        "activity_id": 1,
        "activity_name": caption(schema, CLASS, "activity_id", 1),
        "category_uid": ident.category_uid,
        "category_name": ident.category_name,
        "class_uid": ident.class_uid,
        "class_name": ident.class_name,
        "type_uid": type_uid,
        "type_name": caption(schema, CLASS, "type_uid", type_uid),
        "severity_id": 4,
        "severity": caption(schema, CLASS, "severity_id", 4),
        "time": T0,
        "metadata": {
            "version": schema.version,
            "product": {"name": "probe", "vendor_name": "probe"},
        },
        "finding_info": {"uid": "f-1", "title": "probe finding"},
        "status_id": 1,
        "status": caption(schema, CLASS, "status_id", 1),
    }


def profiled_detection_finding(schema: ResolvedSchema) -> dict[str, Any]:
    """detection_finding with the host and security_control profiles' attributes set."""
    e = base_detection_finding(schema)
    e["metadata"]["profiles"] = ["host", "security_control"]
    e["actor"] = {"user": {"name": "alice", "uid": "u-1"}}
    e["device"] = {
        "hostname": "ws-01",
        "type_id": 2,
        "type": caption(schema, "device", "type_id", 2),
    }
    e["action_id"] = 2
    e["action"] = caption(schema, CLASS, "action_id", 2)
    e["disposition_id"] = 2
    e["disposition"] = caption(schema, CLASS, "disposition_id", 2)
    return e


def error_fixtures(schema: ResolvedSchema) -> dict[str, dict[str, Any]]:
    """One fixture per error class, each a single defect on the conformant base."""
    b = base_detection_finding(schema)
    out: dict[str, dict[str, Any]] = {}

    e = copy.deepcopy(b)
    e["activity_name"] = "My Analytic"
    out["E1_sibling_violated"] = e  # activity_id 1 is "Create"

    e = copy.deepcopy(b)
    e["type_name"] = "Detection Finding: My Analytic"
    out["E2_derived_hand_built"] = e  # type_name follows type_uid

    e = copy.deepcopy(b)
    e["observables"] = [{"name": "actor.user.name", "type": "User Name", "type_id": "4", "value": "alice"}]
    out["E3_wrong_primitive"] = e  # observable.type_id is integer_t

    e = copy.deepcopy(b)
    e["metadata"]["profiles"] = ["cloud"]
    out["E4_profile_gated_absent"] = e  # cloud is required under the cloud profile

    e = copy.deepcopy(b)
    del e["metadata"]["version"]
    out["E5_missing_nested_required"] = e  # metadata.version is required

    e = copy.deepcopy(b)
    e["severity_id"] = 0
    e["severity"] = ""
    out["E6_semantically_empty_enum"] = e  # not invalid; report-worthy

    e = copy.deepcopy(b)
    e["severity_id"] = 42
    e["severity"] = "Bogus"
    out["E7_enum_out_of_range"] = e

    e = copy.deepcopy(b)
    e["totally_unknown_field"] = "x"
    out["E8_unknown_field"] = e

    e = copy.deepcopy(b)
    e["type_uid"] = 200499
    out["E9_wrong_type_uid"] = e  # 2004 * 100 + 1 = 200401
    return out


def multi_error_fixture(schema: ResolvedSchema) -> dict[str, Any]:
    """Four independent defects in one event: the check must report all of them."""
    e = base_detection_finding(schema)
    e["activity_name"] = "My Analytic"
    del e["metadata"]["version"]
    e["severity_id"] = 42
    e["time"] = "yesterday"
    return e
