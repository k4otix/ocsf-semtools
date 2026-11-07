"""The library answers schema questions and holds no query, table-DDL or sink logic."""

import re
from pathlib import Path

import pytest

from tests.architecture.support import PACKAGE, SOURCES

# Query languages, analytics stores and DDL. "table" alone is not banned: a lookup table or a
# TOML table is ordinary vocabulary; a table *definition* is not.
OUT_OF_SCOPE = re.compile(
    r"\b(kql|kusto|sql|spl|splunk|sentinel|bigquery|snowflake|athena|elasticsearch|opensearch"
    r"|lucene|ddl|create\s+table|columns?)\b",
    re.IGNORECASE,
)


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(PACKAGE)))
def test_no_sink_specific_terms(path: Path) -> None:
    hits = [
        f"{n}: {line.strip()}"
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if OUT_OF_SCOPE.search(line)
    ]
    assert hits == []
