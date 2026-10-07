"""3.16.6: the read-only arm of probes/three_reasons_a_hook_can_look_installed_and_never_run.py is judged on every
run that can set it up, and reported as not reachable only as root.

Root ignores file and directory modes, so chmod cannot deny the write there, and the probe failed on a defect its
fixture never produced (AUDIT-B, WSL as root, on eebae5de and rc-317). The probe now skips that arm as root. This
test keeps the skip from widening: a run that is not root must reach the read-only assertions, see the write
denied, and never print the not-reachable line. The probe is run from a copy, so its receipt is not rewritten.
"""
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = "three_reasons_a_hook_can_look_installed_and_never_run.py"


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="as root the arm cannot be set up")
def test_a_run_that_is_not_root_judges_the_read_only_arm(tmp_path):
    copy = tmp_path / PROBE
    shutil.copy(os.path.join(ROOT, "probes", PROBE), copy)
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    env.update(PYTHONPATH=ROOT, INSPEXIMUS_KEY_HOME=str(tmp_path / "keys"), INSPEXIMUS_NO_UPDATE_CHECK="1")
    r = subprocess.run([sys.executable, str(copy)], cwd=ROOT, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    assert r.returncode == 0, r.stdout[-400:] + r.stderr[-400:]
    assert "NOT REACHABLE" not in r.stdout, r.stdout[-400:]
    assert "write denied: True" in r.stdout and "all controls passed" in r.stdout, r.stdout[-400:]
