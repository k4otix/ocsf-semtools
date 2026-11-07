"""The README's typed-model example runs as written against the 1.9.0 dist.

The README's dict-level examples run as doctests in every test run; this one needs the built dist.
"""

import os
import re
import subprocess
import sys

from tests.conftest import ROOT
from tests.generated.conftest import Dist, require


def test_typed_example_runs() -> None:
    d = require(Dist.of("1.9.0"))
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Typed models", 1)[1]
    found = re.search(r"```python\n(.*?)```", section, re.DOTALL)
    assert found is not None
    assert "ocsf_toolkit_v1_9_0" in found[1]
    env = {"PYTHONPATH": os.pathsep.join([str(d.root), str(ROOT / "src")]), "PATH": ""}
    result = subprocess.run([sys.executable, "-c", found[1]], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
