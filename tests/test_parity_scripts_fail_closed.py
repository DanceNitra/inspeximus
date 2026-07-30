"""A parity check must not report success when it could not run.

The five framework-parity scripts are our evidence for the drop-in claim, and CI runs them. The failure
mode that would quietly void that evidence is "the framework is not installed, so nothing was checked,
so nothing failed, so exit 0" — a green build that verified nothing.

MEASURED: all five fail closed. With their framework's import blocked, each exits 1 with an ImportError
rather than passing. This file pins that, because it is the kind of property a later refactor
(a defensive try/except ImportError around the import, added to "make CI more robust") would silently
destroy while looking like an improvement.

NOTE ON THE INSTRUMENT, which is the reason this file exists at all. The first version of the blocker
below used find_module/load_module — the import-hook API removed in Python 3.12. It blocked nothing, so
every script imported its framework normally and exited 0, and that reads exactly like the defect being
present. Believing it would have produced a confident, wrong report that all five scripts pass when
their framework is missing. The control in test_the_blocker_actually_blocks() exists so the next reader
does not have to take the harness on trust.
"""
import os
import subprocess
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BLOCKER = '''import sys
from importlib.abc import MetaPathFinder


class Blocker(MetaPathFinder):
    def __init__(self, names):
        self.names = names

    def find_spec(self, name, path=None, target=None):
        if any(name == n or name.startswith(n + ".") for n in self.names):
            raise ImportError("blocked for test: " + name)
        return None


sys.meta_path.insert(0, Blocker(sys.argv[1].split(",")))
target = sys.argv[2]
sys.argv = sys.argv[2:]
exec(compile(open(target, encoding="utf-8").read(), target, "exec"),
     {"__name__": "__main__", "__file__": target})
'''

# (script, the import it depends on)
SCRIPTS = [
    ("checkpointer_conformance.py", "langgraph"),
    ("store_audit.py", "langgraph"),
    ("adk_audit.py", "google"),
    ("haystack_audit.py", "haystack"),
    ("session_audit.py", "agents"),
]


@pytest.fixture(scope="module")
def blocker():
    p = os.path.join(tempfile.mkdtemp(), "blocker.py")
    with open(p, "w", encoding="utf-8") as f:
        f.write(BLOCKER)
    return p


def _run(blocker_path, blocked, target, cwd=REPO, timeout=180):
    return subprocess.run([sys.executable, blocker_path, blocked, target],
                          cwd=cwd, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")


def test_the_blocker_actually_blocks(blocker, tmp_path):
    """THE CONTROL. Without it, a broken harness makes every script look like it passes."""
    probe = tmp_path / "probe.py"
    probe.write_text("import langgraph\nprint('IMPORT SUCCEEDED')\n", encoding="utf-8")
    r = _run(blocker, "langgraph", str(probe), cwd=str(tmp_path), timeout=60)
    assert r.returncode != 0, "the blocker did not block — every result below would be meaningless"
    assert "blocked for test: langgraph" in (r.stderr or ""), r.stderr[-300:]


@pytest.mark.parametrize("script,dep", SCRIPTS, ids=[s for s, _ in SCRIPTS])
def test_parity_script_fails_closed_when_its_framework_is_missing(blocker, script, dep):
    """Absent framework must be an ERROR, never a pass. CI sees only the exit code."""
    if not os.path.exists(os.path.join(REPO, script)):
        pytest.skip(f"{script} not present")
    r = _run(blocker, dep, script)
    assert r.returncode != 0, (
        f"{script} exited 0 with {dep} unavailable. A parity check that cannot run must not report "
        f"success — that is a green build proving nothing.\n{(r.stdout or '')[-400:]}")
