"""Every `pip install` we print must name a distribution we actually publish.

Found in the wild, not by review: our public comment on openclaw/openclaw#7707 offers a runnable repro
as the evidence for a claim — `pip install agora-mnemo`, then `examples/trust_is_not_truth.py`. The
package name was the PREVIOUS one (frozen at 1.24.4, providing module `mnemo`) and the script it
points at imports `inspeximus`, so following our own instructions produces an ImportError. The file's
own header then said `pip install agora-inspeximus`, which does not exist on PyPI at all — that is a
`No matching distribution found` for anyone who opened the link we published as our credibility.

Nine files carried it, including three docstrings inside SHIPPED modules. The instrument that should
have caught it (`inspeximus/_update.py`) had already been fixed for exactly this — it used to poll
`pypi.org/pypi/agora-inspeximus/json`, which 404s, so the update notice could never fire — and the fix
landed at that one instance while the class went on living in the examples.

So this walks what is on disk rather than a list: every install line in every shipped file and doc must
name the distribution in pyproject.toml. Offline by design — a test that needs PyPI to be reachable is
a test that gets skipped.
"""
from __future__ import annotations

import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: `pip install X`, `pip install "X[extra]"`, `pip install -U X`
_PIP = re.compile(r"pip install\s+(?:-U\s+|--upgrade\s+)?[\"']?([A-Za-z0-9][A-Za-z0-9._-]*)")

#: Names that are legitimately not us: third-party extras an example asks for alongside the package.
_THIRD_PARTY = {
    "cryptography", "langgraph", "langchain", "langchain-core", "llama-index", "haystack-ai",
    "crewai", "autogen-agentchat", "google-adk", "pydantic-ai", "openai-agents", "mcp", "numpy",
    "pytest", "uv", "pipx", "ollama", "sentence-transformers", "agents",
}


def _dist_name() -> str:
    text = open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8").read()
    m = re.search(r'^name = "([^"]+)"', text, re.M)
    assert m, "pyproject.toml has no name"
    return m.group(1)


def _files():
    out = []
    for sub in ("examples", "inspeximus", "docs"):
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, sub)):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "build", "node_modules")]
            for f in filenames:
                if f.endswith((".py", ".md")):
                    out.append(os.path.join(dirpath, f))
    out.append(os.path.join(ROOT, "README.md"))
    return sorted(p for p in out if os.path.exists(p))


@pytest.mark.parametrize("path", _files(), ids=lambda p: os.path.relpath(p, ROOT).replace("\\", "/"))
def test_every_pip_install_names_a_distribution_we_publish(path):
    dist = _dist_name()
    text = open(path, encoding="utf-8").read()
    bad = []
    for line_no, line in enumerate(text.splitlines(), 1):
        for name in _PIP.findall(line):
            base = name.split("[")[0].strip("\"'")
            if base == dist or base in _THIRD_PARTY:
                continue
            # `_update.py` documents the dead name on purpose, as the defect it fixed.
            if os.path.basename(path) == "_update.py":
                continue
            bad.append(f"{line_no}: pip install {name}")
    assert not bad, (f"{os.path.relpath(path, ROOT)} tells the reader to install something other than "
                     f"`{dist}`:\n  " + "\n  ".join(bad))


def test_the_guard_can_actually_fire(tmp_path):
    """The control. A file carrying a dead name must be rejected, or the sweep above proves nothing."""
    p = tmp_path / "bad_example.py"
    p.write_text('"""    pip install agora-inspeximus\n"""\n', encoding="utf-8")
    found = _PIP.findall(p.read_text(encoding="utf-8"))
    assert found == ["agora-inspeximus"], found
    assert found[0] != _dist_name()
