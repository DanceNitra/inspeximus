"""A tool that creates stores of its own keeps their chain heads and keys out of the real key home.

MEASURED 2026-09-28 on PC1: %APPDATA%\\inspeximus\\heads held 2,910 head files, and 2,589 of them named a store
that no longer exists: 1,184 governance_audit.py temp stores, 50 claims_audit.py tamper stores, 40 README-block
stores, and hundreds of other temp stores. A concurrent `release_check --skip-tests` added 13 of them inside one
suite run, and the suite's real-home guard failed that run. Every store writes its head under
INSPEXIMUS_KEY_HOME, else APPDATA, so a tool that never set it wrote into the owner's real one.

`isolate(label)` points INSPEXIMUS_KEY_HOME at a fresh temporary directory for this process and every child it
starts, turns the PyPI update check off, and removes the directory when the process exits. A tool started by
another isolated tool reuses its parent's directory. A tool meant to act on a REAL store or key does not call
this, and INSPEXIMUS_TOOL_REAL_KEY_HOME=1 keeps the caller's key home on purpose.

Only the key home moves: APPDATA and the home stay, because git and gh keep their own configuration there.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile

MARK = "_INSPEXIMUS_TOOL_KEY_HOME"


def isolate(label: str) -> str:
    """Isolate the key home for this tool and its children; return the directory in use."""
    if os.environ.get("INSPEXIMUS_TOOL_REAL_KEY_HOME") == "1":
        return os.environ.get("INSPEXIMUS_KEY_HOME", "")
    inherited = os.environ.get(MARK)
    if inherited and os.environ.get("INSPEXIMUS_KEY_HOME") == inherited and os.path.isdir(inherited):
        return inherited
    d = tempfile.mkdtemp(prefix=f"inspeximus-{label}-keys-")
    os.environ["INSPEXIMUS_KEY_HOME"] = d
    os.environ[MARK] = d
    os.environ["INSPEXIMUS_NO_UPDATE_CHECK"] = "1"
    atexit.register(shutil.rmtree, d, True)
    return d
