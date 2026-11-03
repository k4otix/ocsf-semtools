"""Typed use of the generated 1.9.0 package, checked by test_static.py with pyright strict.

Each ``pyright: ignore`` marks a line pyright must report an error on: the check runs with
reportUnnecessaryTypeIgnoreComment, so a mistake that stops being caught fails the test.
"""

from typing import Any, assert_type

from ocsf_toolkit_v1_9_0 import DetectionFinding
from ocsf_toolkit_v1_9_0.classes.detection_finding import DetectionFindingActivityId
from ocsf_toolkit_v1_9_0.enums import SeverityId
from ocsf_toolkit_v1_9_0.objects import FindingInfo, Metadata, Product

from ocsf_toolkit import build, parse


def built() -> DetectionFinding:
    finding = build(
        DetectionFinding,
        activity_id=DetectionFindingActivityId.CREATE,
        severity_id=SeverityId.HIGH,
        time=1_700_000_000_000,
        metadata=Metadata(product=Product(name="p")),
        finding_info=FindingInfo(uid="u", title="t"),
    )
    assert_type(finding, DetectionFinding)
    assert_type(finding.type_uid, int | None)
    return finding


def parsed(event: dict[str, Any]) -> DetectionFinding | None:
    found, report = parse(event, as_=DetectionFinding)
    assert_type(found, DetectionFinding | None)
    return found if report.ok() else None


def mistakes() -> None:
    a, s = DetectionFindingActivityId.CREATE, SeverityId.HIGH
    m, f = Metadata(product=Product(name="p")), FindingInfo(uid="u", title="t")
    build(DetectionFinding, activity_id=a, severity_id=s, time=1, metadata=m, finding_info=f)
    build(DetectionFinding, time=1)  # pyright: ignore  (required fields missing)
    build(DetectionFinding, activity_id=a, severity_id=s, time="1", metadata=m)  # pyright: ignore
    _ = SeverityId.HIGHEST  # pyright: ignore  (no such member)
