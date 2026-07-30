"""The MCP extra must not resolve to an SDK whose API we do not use.

`pyproject.toml` declared `mcp = ["mcp[cli]>=1.0"]` with no upper bound. mcp 2.0 removed
`mcp.server.fastmcp`, which is exactly what `inspeximus/mcp_server.py` imports, so a fresh
`pip install "inspeximus[mcp]"` today resolved to 2.0.0 and the server raised ImportError on import --
every tool unusable. MEASURED in a clean venv on 2026-07-30: `pip install "mcp[cli]"` -> mcp 2.0.0,
`import mcp` SUCCEEDS, `from mcp.server.fastmcp import FastMCP` does not. CI never saw it: the dev image
already had 1.28.1.

And the message the failure printed was `pip install "mcp[cli]"` -- the very command that produces the
broken state. A remedy nobody ran is worse than saying less. Both messages now carry the bound, and
`pip install "mcp[cli]<2"` was verified end to end in that venv: it installed 1.29.0 and the module
imported.

These tests run where mcp 1.x IS installed, so they check the two things that are checkable there: the
declared bound, and that each failure branch names a remedy that would actually work.
"""
import os
import re
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


def test_the_declared_extra_excludes_the_sdk_major_that_removed_our_entry_point():
    # Parsed, not regexed: the value contains "mcp[cli]", whose "]" ends a naive [^\]]* capture before the
    # version bound is ever seen -- my first version of this test passed on a spec it had not read.
    try:
        import tomllib
    except ImportError:                                   # 3.8-3.10
        tomllib = pytest.importorskip("tomli", reason="needs tomllib/tomli to parse pyproject")
    with open(PYPROJECT, "rb") as f:
        spec = tomllib.load(f)["project"]["optional-dependencies"]["mcp"]
    joined = " ".join(spec)
    assert "<2" in joined or "~=1." in joined or "==1." in joined, (
        f"the mcp extra is {spec} -- unbounded above, so a fresh install resolves to mcp 2.0, where "
        f"mcp.server.fastmcp does not exist and every MCP tool is unusable")


def test_control_the_server_imports_when_the_sdk_is_present():
    """Without this, the two refusals below are satisfied by a module that never imports at all."""
    pytest.importorskip("mcp.server.fastmcp", reason="needs mcp 1.x")
    r = _run_with_blocked("nothing_is_blocked_here")
    assert "IMPORTED" in r.stdout, (r.stdout[-300:], r.stderr[-300:])


def test_a_reorganised_sdk_is_reported_as_a_version_problem_not_a_missing_package(tmp_path):
    """mcp 2.x: the package IS installed, so "install the SDK" sends the reader somewhere useless.

    SIMULATED WITH A STUB, and the reason matters. My first attempt blocked `mcp.server.fastmcp` with a
    meta-path finder -- but mcp 1.x's own `__init__` imports that submodule, so blocking it also broke
    `import mcp`, and the harness produced the ABSENT-SDK shape while claiming to test the REORGANISED
    one. It failed, and the failure was the test's, not the code's; the real branch was already verified
    against genuine mcp 2.0.0 in a clean venv. A stub package -- importable `mcp`, no `fastmcp` -- is the
    2.x shape faithfully.
    """
    stub = tmp_path / "stub"
    (stub / "mcp" / "server").mkdir(parents=True)
    (stub / "mcp" / "__init__.py").write_text("__version__ = '2.0.0-stub'\n", encoding="utf-8")
    (stub / "mcp" / "server" / "__init__.py").write_text("", encoding="utf-8")

    env = dict(os.environ, PYTHONPATH=str(stub) + os.pathsep + ROOT)
    r = subprocess.run([sys.executable, "-c",
                        "import mcp\n"
                        "assert not hasattr(mcp, 'server') or not hasattr(mcp.server, 'fastmcp')\n"
                        "try:\n"
                        "    import inspeximus.mcp_server\n"
                        "    print('IMPORTED')\n"
                        "except ImportError as e:\n"
                        "    print('IMPORTERROR:' + str(e))\n"],
                       cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=180,
                       encoding="utf-8", errors="replace")

    assert "IMPORTERROR:" in r.stdout, (r.stdout[-400:], r.stderr[-400:])
    msg = r.stdout.split("IMPORTERROR:", 1)[1]
    assert "mcp[cli]<2" in msg, f"the remedy has no version bound, so it reinstalls the broken SDK: {msg}"
    assert "1.x" in msg or "2.0" in msg, f"nothing in the message says this is a VERSION problem: {msg}"


def test_a_genuinely_absent_sdk_still_says_install_it_and_still_bounds_the_version():
    r = _run_with_blocked("mcp,mcp.server,mcp.server.fastmcp")
    assert "IMPORTERROR:" in r.stdout, (r.stdout[-300:], r.stderr[-300:])
    msg = r.stdout.split("IMPORTERROR:", 1)[1]
    assert "mcp[cli]<2" in msg, f"the install advice would fetch mcp 2.0 and fail again: {msg}"
