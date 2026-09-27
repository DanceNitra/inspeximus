"""AUDIT-B B-10: a hook event that does nothing must not import numpy.

`core.py` imported numpy at module load, and every hook event is a new process that imports the
package: `python -m inspeximus.claude_code`. numpy only accelerates semantic recall ("OPTIONAL: numpy
only ACCELERATES semantic recall at scale"), yet a PreToolUse event for `ls` paid for it. Measured
2026-09-27 with -X importtime: numpy was about 0.17 s of the 0.32 s package import, on a hook whose
floor was 0.58 s against 0.16 to 0.20 s for bare Python.

The test puts a stand-in `numpy` package first on PYTHONPATH that writes a marker file when it is
imported, so it measures the same thing whether or not the real numpy is installed. The control
imports the stand-in directly and requires the marker, so a stand-in the child cannot find cannot pass
as "not imported".
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env(tmp_path, marker):
    fake = tmp_path / "fake"
    (fake / "numpy").mkdir(parents=True)
    (fake / "numpy" / "__init__.py").write_text(
        "import os\nopen(os.environ['NUMPY_MARKER'], 'w').write('imported')\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    env.update(PYTHONPATH=os.pathsep.join([str(fake), ROOT]), NUMPY_MARKER=str(marker),
               HOME=str(tmp_path), USERPROFILE=str(tmp_path), INSPEXIMUS_NO_UPDATE_CHECK="1")
    return env


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="B-10: core imports numpy at module load, so every hook process pays for it")
def test_a_pre_tool_use_event_that_matches_nothing_does_not_import_numpy(tmp_path):
    marker = tmp_path / "numpy-was-imported"
    env = _env(tmp_path, marker)
    # CONTROL: the stand-in is found and writes its marker.
    subprocess.run([sys.executable, "-c", "import numpy"], env=env, cwd=str(tmp_path), check=True)
    if not marker.exists():
        pytest.fail("control: the stand-in numpy was not importable, so its absence would prove nothing")
    marker.unlink()

    (tmp_path / ".git").mkdir()
    ev = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"},
          "cwd": str(tmp_path).replace("\\", "/"), "session_id": "b10"}
    p = subprocess.run([sys.executable, "-m", "inspeximus.claude_code"], input=json.dumps(ev).encode(),
                       env=env, cwd=str(tmp_path), capture_output=True)
    if p.returncode != 0 or p.stderr.strip():
        pytest.fail(f"the hook failed: exit {p.returncode}, stderr {p.stderr[-300:]!r}")
    assert not marker.exists(), "a PreToolUse event for `ls` imported numpy"
