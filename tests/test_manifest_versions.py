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

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _pyproject_version():
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as fh:
        return re.search(r'^version = "([^"]+)"', fh.read(), re.M).group(1)


def _json(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return json.load(fh)


def _pinner(root):
    """The real pinner module, pointed at `root`. Never at the working tree."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "pinner_under_test", os.path.join(ROOT, "packages", "_pin_server_json.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.ROOT = root
    return mod


def test_the_package_version_is_the_one_source_of_truth():
    import inspeximus
    assert inspeximus.__version__ == _pyproject_version(), \
        "core.py and pyproject.toml disagree about what version this is"


def test_the_plugin_manifest_matches_the_package():
    assert _json(".claude-plugin/plugin.json")["version"] == _pyproject_version()


def test_the_marketplace_manifest_matches_the_package():
    for entry in _json(".claude-plugin/marketplace.json")["plugins"]:
        assert entry["version"] == _pyproject_version(), entry["name"]


def test_the_pinner_actually_pins_every_manifest(tmp_path):
    """RUN it, do not read it.

    This used to grep the pinner's SOURCE for "server.json", "plugin.json" and "marketplace.json" —
    and the pinner's own docstring contains all three while `main()` only ever wrote `server.json`.
    The guard was satisfied by the claim it was supposed to check, so both Claude Code manifests sat
    at 1.85.0 through the 1.86.0 release and CI went red on the release commit.

    Now: copy the real manifests into a temp tree, set every version to a wrong value, run the pinner
    against it, and require every one to come back pinned. A manifest added tomorrow is covered
    because the check walks what is on disk instead of a list someone has to maintain.
    """
    import shutil

    shutil.copy(os.path.join(ROOT, "pyproject.toml"), tmp_path / "pyproject.toml")
    shutil.copytree(os.path.join(ROOT, ".claude-plugin"), tmp_path / ".claude-plugin")
    shutil.copy(os.path.join(ROOT, "server.json"), tmp_path / "server.json")

    def carriers():
        """(path, jsonpath-ish setter/getter) for every version that must equal the package's."""
        out = []
        d = json.load(open(tmp_path / "server.json", encoding="utf-8"))
        out.append(("server.json", ["version"] + [f"packages.{i}.version" for i in
                                                  range(len(d.get("packages", [])))]))
        out.append((".claude-plugin/plugin.json", ["version"]))
        d = json.load(open(tmp_path / ".claude-plugin" / "marketplace.json", encoding="utf-8"))
        out.append((".claude-plugin/marketplace.json",
                    [f"plugins.{i}.version" for i in range(len(d.get("plugins", [])))]))
        return out

    def _get(doc, path):
        cur = doc
        for part in path.split("."):
            cur = cur[int(part)] if part.isdigit() else cur[part]
        return cur

    def _set(doc, path, val):
        parts = path.split(".")
        cur = doc
        for part in parts[:-1]:
            cur = cur[int(part)] if part.isdigit() else cur[part]
        cur[parts[-1]] = val

    for rel, paths in carriers():                       # falsify every one of them
        f = tmp_path / rel
        doc = json.load(open(f, encoding="utf-8"))
        for p in paths:
            _set(doc, p, "0.0.0-wrong")
        json.dump(doc, open(f, "w", encoding="utf-8"), indent=2)

    keys_before = {rel: set(json.load(open(tmp_path / rel, encoding="utf-8")))
                   for rel, _ in carriers()}
    assert _pinner(tmp_path).main(["_", _pyproject_version()]) == 0

    want = _pyproject_version()
    for rel, paths in carriers():
        doc = json.load(open(tmp_path / rel, encoding="utf-8"))
        for p in paths:
            assert _get(doc, p) == want, f"{rel}:{p} was not pinned by the release pinner"
        # It must not INVENT a version field either. `marketplace.json` has no top-level `version` at
        # all, and a pinner that adds one is writing a key the schema does not define — my own first
        # cut of this test asserted against a top-level version that never existed, which is the same
        # mistake in the other direction: checking a field nobody has instead of the shape that matters.
        assert set(json.load(open(tmp_path / rel, encoding="utf-8"))) == keys_before[rel], \
            f"{rel}: the pinner added or removed a top-level key"


def test_running_the_pinner_is_idempotent_and_changes_nothing_when_current(tmp_path):
    """It runs on every release; if it rewrote files gratuitously it would churn the diff each time.

    Against a COPY, not the working tree. This used to invoke the real pinner on the real repo, which
    makes a test a WRITER of tracked files — and under a mutation that made the pinner write a wrong
    field, this test dutifully carried the corruption into the actual manifest. The mutation harness
    only restores `probes/*.json`, on purpose, so nothing put it back: a mutant reached a committed
    file through a test that was helping. A test that verifies a tool must not run it where the tool's
    output is the repository.
    """
    import shutil
    shutil.copy(os.path.join(ROOT, "pyproject.toml"), tmp_path / "pyproject.toml")
    shutil.copy(os.path.join(ROOT, "server.json"), tmp_path / "server.json")
    shutil.copytree(os.path.join(ROOT, ".claude-plugin"), tmp_path / ".claude-plugin")

    rels = ("server.json", ".claude-plugin/plugin.json", ".claude-plugin/marketplace.json")
    before = {p: open(tmp_path / p, encoding="utf-8").read() for p in rels}
    # ROOT is derived from the pinner's own __file__, so a subprocess with cwd=tmp_path would still
    # write to the real repo. Import it and point ROOT at the copy — the only way to run the real code
    # somewhere it cannot do damage.
    assert _pinner(tmp_path).main(["_"]) == 0
    for p, text in before.items():
        assert open(tmp_path / p, encoding="utf-8").read() == text, f"{p} churned"


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


# ── the pinner missed the two places a USER actually reads (found on the 1.89.0 release) ───────────
def _seed(tmp_path):
    """A minimal ROOT the pinner can work on: the manifests plus the two files added in 1.89.0."""
    import shutil
    shutil.copy(os.path.join(ROOT, "pyproject.toml"), tmp_path / "pyproject.toml")
    shutil.copy(os.path.join(ROOT, "server.json"), tmp_path / "server.json")
    shutil.copytree(os.path.join(ROOT, ".claude-plugin"), tmp_path / ".claude-plugin")
    (tmp_path / "inspeximus").mkdir()
    shutil.copy(os.path.join(ROOT, "inspeximus", "core.py"), tmp_path / "inspeximus" / "core.py")
    shutil.copy(os.path.join(ROOT, "README.md"), tmp_path / "README.md")
    return tmp_path


def test_the_pinner_pins_the_version_a_user_reads(tmp_path):
    """`inspeximus.__version__` and the README badge, neither of which the pinner touched until 1.89.0.

    Bumping pyproject.toml and running the pinner left core.py at the PREVIOUS version -- and core.py is
    what `import inspeximus; inspeximus.__version__` returns, i.e. the number a user sees. Only
    `test_the_package_version_is_the_one_source_of_truth` caught it, at release time; without that test
    the wheel would have announced the version before it.

    This asserts the OUTCOME (the files now carry the new number), never the presence of a filename
    string in the pinner's source. That mistake is documented in `main()`: a guard that greps this file
    for "plugin.json" was satisfied by the docstring while the code covered nothing.
    """
    root = _seed(tmp_path)
    target = "99.98.97"                      # cannot collide with any real version in these files
    assert _pinner(root).main(["_", target]) == 0

    core = (root / "inspeximus" / "core.py").read_text(encoding="utf-8")
    assert f'__version__ = "{target}"' in core, "core.py __version__ was not pinned"
    assert core.count("__version__ = ") == 1, "more than one __version__ assignment; pinning is ambiguous"

    readme = (root / "README.md").read_text(encoding="utf-8")
    assert f"v{target}" in readme, "the README version badge was not pinned"
    assert not re.search(r'(?<![\w.])v\d+\.\d+\.\d+(?![\w.])',
                         readme.replace(f"v{target}", "")), "a vX.Y.Z token was left behind"


def test_the_pinner_refuses_a_core_that_lost_its_version_line(tmp_path):
    """THE FALSIFICATION CONTROL. If the assignment is renamed, the pinner must FAIL, not quietly pin
    nothing — silently covering zero files is the failure this whole module exists to prevent."""
    root = _seed(tmp_path)
    p = root / "inspeximus" / "core.py"
    p.write_text(p.read_text(encoding="utf-8").replace('__version__ = "', '__ver__ = "', 1),
                 encoding="utf-8")
    with pytest.raises(SystemExit):
        _pinner(root).main(["_", "99.98.97"])


def test_a_missing_core_is_skipped_rather_than_fatal(tmp_path):
    """The sibling case, kept distinct on purpose: a partial ROOT (a harness) is not a defect, so the
    pinner reports and continues. Without this the pre-existing idempotence test, which copies no
    core.py, would die on an unrelated change."""
    import shutil
    shutil.copy(os.path.join(ROOT, "pyproject.toml"), tmp_path / "pyproject.toml")
    shutil.copy(os.path.join(ROOT, "server.json"), tmp_path / "server.json")
    shutil.copytree(os.path.join(ROOT, ".claude-plugin"), tmp_path / ".claude-plugin")
    assert _pinner(tmp_path).main(["_", "99.98.97"]) == 0
