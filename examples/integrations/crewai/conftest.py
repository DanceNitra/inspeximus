"""Test the checkout this example sits in, not whichever inspeximus pip installed.

The repository's own example sweep learned this the hard way (tests/test_examples_run.py): without the
path set, the example passed against a published release while the working tree was never exercised.
"""
from __future__ import annotations

import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]

# Off before anything imports crewai, in this process and in every subprocess a test starts.
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")

if (REPO / "inspeximus" / "__init__.py").exists():
    sys.path.insert(0, str(REPO))
    os.environ["PYTHONPATH"] = os.pathsep.join(
        [str(REPO)] + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p])
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
