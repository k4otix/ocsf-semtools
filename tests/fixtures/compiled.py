"""A small compiler-format schema exercising every loader quirk, for tests that need no build/."""

import copy
import json
from typing import Any

from ocsf_semtools.ir import Provenance

PROVENANCE = Provenance(
    schema_repo="https://github.com/ocsf/ocsf-schema",
    ref="9.9.9",
    commit="0" * 40,
    compiler_version="1.1.1",
    compiler_output_sha256="1" * 64,
    extensions=(),
)

_DEPRECATED = {"message": "Use <code>new</code>.", "since": "9.8.0", "superseded_by": ["new"]}

_COMPILED: dict[str, Any] = {
    "compile_version": 1,
    "version": "9.9.9",
    "categories": {
        "caption": "Categories",
        "description": "ignored",
        "name": "category",
        "attributes": {"findings": {"caption": "Findings", "description": "x", "uid": 2}},
    },
    "dictionary": {
        "caption": "Dictionary",
        "description": "ignored",
        "name": "dictionary",
        "types": {
            "caption": "Types",
            "description": "ignored",
            "attributes": {
                "string_t": {"caption": "String"},
                "integer_t": {"caption": "Integer"},
                "long_t": {"caption": "Long"},
                "boolean_t": {"caption": "Boolean", "values": [False, True]},
                "port_t": {
                    "caption": "Port",
                    "type": "integer_t",
                    "type_name": "Integer",
                    "range": [0, 65535],
                },
                "ip_t": {
                    "caption": "IP",
                    "type": "string_t",
                    "type_name": "String",
                    "max_len": 40,
                    "regex": "^x$",
                    "observable": 2,
                },
                "ipv4_t": {"caption": "IPv4", "type": "ip_t", "type_name": "IP"},  # 2-level chain
            },
        },
        "attributes": {
            "severity_id": {
                "caption": "Severity ID",
                "type": "integer_t",
                "type_name": "Integer",
                "sibling": "severity",
                "enum": {"0": {"caption": "Unknown"}, "99": {"caption": "Other"}},
            },
            "flag_ids": {"caption": "Flags", "type": "integer_t", "is_array": True, "enum": {}},
            "win/hosted": {"caption": "Hosted", "type": "string_t", "extension": "win"},
        },
    },
    "classes": {
        "base_event": {
            "name": "base_event",
            "caption": "Base Event",
            "uid": 0,
            "category": "other",
            "category_uid": 0,
            "profiles": [],
            "attributes": {},
        },
        "finding": {
            "name": "finding",
            "caption": "Finding",
            "description": "ignored",
            "extends": "base_event",
            "uid": 2001,
            "category": "findings",
            "category_uid": 2,
            "category_name": "Findings",
            "profiles": ["cloud"],
            "associations": {"actor.user": ["device"]},
            "constraints": {"at_least_one": ["severity", "severity_id"]},
            "attributes": {
                "category_uid": {
                    "caption": "Category ID",
                    "type": "integer_t",
                    "requirement": "required",
                    "group": "classification",
                    "profiles": None,
                    "enum": {"2": {"caption": "Findings", "uid": 2}},
                },
                "severity_id": {
                    "caption": "Severity ID",
                    "type": "integer_t",
                    "requirement": "required",
                    "sibling": "severity",
                    "suppress_checks": ["enum_convention"],
                    "enum": {
                        "0": {"caption": "Unknown", "description": "dropped"},
                        "3": {"caption": "High", "@deprecated": _DEPRECATED},
                        "99": {"caption": "Other"},
                    },
                },
                "cloud": {
                    "caption": "Cloud",
                    "type": "object_t",
                    "object_type": "cloud",
                    "object_name": "Cloud",
                    "requirement": "required",
                    "profiles": ["cloud"],
                },
                "method": {
                    "caption": "Method",
                    "type": "string_t",
                    "enum": {"GET": {"caption": "Get"}, "PUT": {"caption": "Put"}},
                },
                "old": {"caption": "Old", "type": "string_t", "@deprecated": _DEPRECATED},
                "port": {"caption": "Port", "type": "port_t", "default": 443},
            },
        },
    },
    "objects": {
        "cloud": {
            "name": "cloud",
            "caption": "Cloud",
            "observable": 30,
            "profiles": [],
            "attributes": {
                "ip": {"caption": "IP", "type": "ip_t", "observable": 2, "source": "free text"},
                "hosted": {"caption": "Hosted", "type": "string_t", "extension": "win"},
            },
        },
        "win/reg_key": {
            "name": "reg_key",
            "caption": "Registry Key",
            "extension": "win",
            "extension_id": 2,
            "profiles": [],
            "attributes": {},
        },
    },
    "profiles": {
        "cloud": {
            "name": "cloud",
            "caption": "Cloud",
            "meta": "profile",
            "annotations": {"group": "primary"},
        },
        "linux/linux_users": {
            "name": "linux_users",
            "caption": "Linux Users",
            "extension": "linux",
        },
    },
    "extensions": {
        "win": {
            "name": "win",
            "uid": 2,
            "caption": "Windows",
            "version": "9.9.9",
            "platform_extension?": True,
        }
    },
}


def compiled() -> dict[str, Any]:
    """A fresh deep copy, safe to mutate in a test."""
    return copy.deepcopy(_COMPILED)


def compiled_bytes(data: dict[str, Any] | None = None) -> bytes:
    return json.dumps(compiled() if data is None else data).encode()
