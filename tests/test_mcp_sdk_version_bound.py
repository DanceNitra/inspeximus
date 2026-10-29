"""The MCP extra resolves only to SDK majors this code runs on, and each import failure names a working remedy.

History. `pyproject.toml` once declared `mcp = ["mcp[cli]>=1.0"]` with no upper bound. mcp 2.0 removed
`mcp.server.fastmcp`, so a fresh install on 2026-07-30 resolved to 2.0.0 and the server raised ImportError on
import. The fix then was a `<2` cap. The cap later cost hosts that pin mcp 2.x: installing into Hermes Agent
(mcp==2.3.0) downgraded its SDK to 1.30.0 (measured 2026-10-28). The server now resolves either layout in one
place (inspeximus/mcp_server.py), and the cap is `<3`.

What these tests hold: the declared range has the measured floor and stops before a major nobody has run;
the server imports under whichever supported SDK is installed; an SDK that is present but has neither layout
is reported as a version problem; and an absent SDK gets install advice that carries the tested range. The
full two-major check (every tool registered, a write and a recall through a client) is tools/mcp_sdk_check.py,
run by the CI job mcp-sdk on 1.28.1 and 2.3.0.
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYPROJECT = os.path.join(ROOT, "pyproject.toml")

# find_spec, not find_module: the latter was removed in 3.12 and blocks nothing (unit 18's lesson).
BLOCKER = '''import sys
from importlib.abc import MetaPathFinder


class Blocker(MetaPathFinder):
    def __init__(self, names):
        self.names = names

    def find_spec(self, name, path=None, target=None):
        if name in self.names:
            raise ImportError("blocked for test: " + name)
        return None


sys.meta_path.insert(0, Blocker(sys.argv[1].split(",")))
try:
    import inspeximus.mcp_server            # noqa: F401
    print("IMPORTED")
except ImportError as e:
    print("IMPORTERROR:" + str(e))
'''


def _run_with_blocked(blocked):
    return subprocess.run([sys.executable, "-c", BLOCKER, blocked], cwd=ROOT,
                          capture_output=True, text=True, timeout=180,
                          encoding="utf-8", errors="replace")


def test_the_declared_extra_has_the_tested_floor_and_stops_before_an_untested_major():
    # Parsed, not regexed: the value contains "mcp[cli]", whose "]" ends a naive [^\]]* capture before the
    # version bound is ever seen -- an earlier version of this test passed on a spec it had not read.
    try:
        import tomllib
    except ImportError:                                   # 3.8-3.10
        tomllib = pytest.importorskip("tomli", reason="needs tomllib/tomli to parse pyproject")
    with open(PYPROJECT, "rb") as f:
        spec = tomllib.load(f)["project"]["optional-dependencies"]["mcp"]
    assert spec == ["mcp[cli]>=1.28,<3"], (
        f"the mcp extra is {spec}: the server is tested on 1.28.1, 1.29.0 and 2.3.0, so the range is >=1.28 "
        f"(1.0 has FastMCP but not the API around it) and <3 (a major nobody has run)")


def test_control_the_server_imports_when_the_sdk_is_present():
    """Without this, the two refusals below are satisfied by a module that never imports at all."""
    pytest.importorskip("mcp", reason="needs the MCP SDK")
    r = _run_with_blocked("nothing_is_blocked_here")
    assert "IMPORTED" in r.stdout, (r.stdout[-300:], r.stderr[-300:])


def test_an_sdk_with_neither_layout_is_reported_as_a_version_problem_not_a_missing_package(tmp_path):
    """An mcp older than 1.28 or a future major: the package IS installed, so "install the SDK" is useless.

    SIMULATED WITH A STUB: an importable `mcp` with neither `mcp.server.fastmcp` nor `mcp.server.mcpserver`.
    Blocking the submodules with a meta-path finder does not work, because mcp 1.x's own `__init__` imports
    `fastmcp`, so the block breaks `import mcp` too and produces the ABSENT shape instead.
    """
    stub = tmp_path / "stub"
    (stub / "mcp" / "server").mkdir(parents=True)
    (stub / "mcp" / "__init__.py").write_text("__version__ = '3.0.0-stub'\n", encoding="utf-8")
    (stub / "mcp" / "server" / "__init__.py").write_text("", encoding="utf-8")

    env = dict(os.environ, PYTHONPATH=str(stub) + os.pathsep + ROOT)
    r = subprocess.run([sys.executable, "-c",
                        "import mcp, mcp.server\n"
                        "assert mcp.__version__ == '3.0.0-stub', mcp.__file__\n"
                        "assert not hasattr(mcp.server, 'fastmcp') and not hasattr(mcp.server, 'mcpserver')\n"
                        "try:\n"
                        "    import inspeximus.mcp_server\n"
                        "    print('IMPORTED')\n"
                        "except ImportError as e:\n"
                        "    print('IMPORTERROR:' + str(e))\n"],
                       cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=180,
                       encoding="utf-8", errors="replace")

    assert "IMPORTERROR:" in r.stdout, (r.stdout[-400:], r.stderr[-400:])
    msg = r.stdout.split("IMPORTERROR:", 1)[1]
    assert "mcp[cli]>=1.28,<3" in msg, f"the remedy does not carry the tested range: {msg}"
    assert "1.28+ or 2.x" in msg, f"nothing in the message says this is a VERSION problem: {msg}"


def test_a_genuinely_absent_sdk_still_says_install_it_and_still_bounds_the_version():
    r = _run_with_blocked("mcp,mcp.server,mcp.server.fastmcp,mcp.server.mcpserver")
    assert "IMPORTERROR:" in r.stdout, (r.stdout[-300:], r.stderr[-300:])
    msg = r.stdout.split("IMPORTERROR:", 1)[1]
    assert "mcp[cli]>=1.28,<3" in msg, f"the install advice does not carry the tested range: {msg}"
    assert "needs the MCP SDK" in msg, f"an absent SDK was reported as some other problem: {msg}"
