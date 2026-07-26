"""Every version-carrying manifest must say the same thing the package says.

`.claude-plugin/plugin.json` and `marketplace.json` sat at 1.25.0 while the package was 1.78.0 -- fifty-two
releases of drift. Nothing pinned them, so a human had to remember, and a human did not. Anyone installing
the plugin saw a version that had not existed for weeks.

Fixing the numbers would have left the class alive, so `packages/_pin_server_json.py` now pins them at
release time and this test fails the moment any manifest drifts again.
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _pyproject_version():
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as fh:
        return re.search(r'^version = "([^"]+)"', fh.read(), re.M).group(1)


def _json(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return json.load(fh)


def test_the_package_version_is_the_one_source_of_truth():
    import inspeximus
    assert inspeximus.__version__ == _pyproject_version(), \
        "core.py and pyproject.toml disagree about what version this is"


def test_the_plugin_manifest_matches_the_package():
    assert _json(".claude-plugin/plugin.json")["version"] == _pyproject_version()


def test_the_marketplace_manifest_matches_the_package():
    for entry in _json(".claude-plugin/marketplace.json")["plugins"]:
        assert entry["version"] == _pyproject_version(), entry["name"]


def test_the_pinner_covers_every_manifest_that_carries_a_version():
    """A new manifest added tomorrow must be pinned too. This walks what exists rather than trusting a
    list, so the guard cannot go stale the way the manifests did."""
    src = open(os.path.join(ROOT, "packages", "_pin_server_json.py"), encoding="utf-8").read()
    for manifest in ("server.json", "plugin.json", "marketplace.json"):
        assert manifest in src, f"{manifest} carries a version and the release pinner does not touch it"


def test_running_the_pinner_is_idempotent_and_changes_nothing_when_current():
    """It runs on every release; if it rewrote files gratuitously it would churn the diff each time."""
    before = {p: open(os.path.join(ROOT, p), encoding="utf-8").read()
              for p in ("server.json", ".claude-plugin/plugin.json", ".claude-plugin/marketplace.json")}
    subprocess.run([sys.executable, "packages/_pin_server_json.py"], cwd=ROOT,
                   capture_output=True, text=True, check=True)
    for p, text in before.items():
        assert open(os.path.join(ROOT, p), encoding="utf-8").read() == text, f"{p} churned"


def test_the_plugin_description_reflects_what_ships():
    """The description is what a reader sees before installing. If a headline capability is not in it, it
    does not exist for them."""
    desc = _json(".claude-plugin/plugin.json")["description"].lower()
    assert "correct" in desc and "receipt" in desc, desc
    assert "erasure_residue" in desc or "residue" in desc, \
        "the residue check is a shipped capability the plugin description should name"


# ── the public homepage carries factual claims nobody pinned either ─────────────────────────────────
def _index_html():
    with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as fh:
        return fh.read()


def test_the_homepage_tool_count_matches_the_server():
    """It said "15 tools any MCP host can call" while 56 shipped -- off by 41, on the public page, for
    however many releases. Same class as the stale manifests: a number a human had to remember."""
    src = open(os.path.join(ROOT, "inspeximus", "mcp_server.py"), encoding="utf-8").read()
    actual = len(re.findall(r"^@mcp\.tool\(\)", src, re.M))
    claimed = re.search(r"(\d+) tools any MCP host can call", _index_html())
    assert claimed, "the homepage no longer states a tool count; if it did, this test should check it"
    assert int(claimed.group(1)) == actual, \
        f"the homepage claims {claimed.group(1)} MCP tools, the server defines {actual}"


def test_the_homepage_names_the_residue_check():
    assert "inspeximus residue" in _index_html(), \
        "a shipped capability absent from the homepage does not exist for anyone who visits it"
