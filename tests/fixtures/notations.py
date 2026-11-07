"""The five spellings of one observable path that upstream uses."""

NOTATIONS = (
    "resources.uid",
    "resources[].uid",
    "resources[0].uid",
    "resources[*].uid",
    "$.resources[*].uid",
)
CANONICAL = "resources.uid"
