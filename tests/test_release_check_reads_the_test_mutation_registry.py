"""release_check checks the hard-coded mutation targets in `mutation`-marked tests, not only tools/mutations.json.

3.15.0's first tag failed CI's serial mutation step on a needle in tests/test_claims_audit_mutation_score.py:
the recall tenant filter had moved one indent level. That test is marked `mutation`, pytest.ini deselects the
marker from every default run, and release_check read only tools/mutations.json, so nothing before CI looked at
it. These tests run in the default suite and hold release_check to the second registry, in both directions.
"""
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import release_check  # noqa: E402

REGISTRY = "test_claims_audit_mutation_score.py"


class _Rep(release_check.Report):
    def add(self, name, status, detail):
        self.rows.append((name, status, detail))

    def row(self, name):
        return [r for r in self.rows if r[0] == name][0]


def _tree(tmp_path):
    """Every file the mutation-target check reads: the spec, the harness, each target, and the tests."""
    for rel in ("tools/mutations.json", "tools/mutation_check.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(os.path.join(ROOT, rel), tmp_path / rel)
    spec = json.loads((tmp_path / "tools" / "mutations.json").read_text(encoding="utf-8"))
    for rel in {m["file"] for m in spec} | {"inspeximus/core.py"}:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(os.path.join(ROOT, rel), tmp_path / rel)
    (tmp_path / "tests").mkdir(exist_ok=True)
    shutil.copy(os.path.join(ROOT, "tests", REGISTRY), tmp_path / "tests" / REGISTRY)
    return tmp_path


def test_the_real_tree_passes_and_counts_the_test_registry():
    rep = _Rep()
    release_check.check_mutation_targets(rep, root=Path(ROOT))
    name, status, detail = rep.row("mutation targets")
    assert status == release_check.PASS, detail
    assert "hard-coded target(s) in the mutation-marked tests" in detail, detail
    n = int(detail.split("; ")[1].split(" ")[0])
    assert n >= 5, f"the check read {n} needles; the registry holds more than that: {detail}"


def test_a_stale_needle_in_a_mutation_marked_test_fails_the_check(tmp_path):
    tree = _tree(tmp_path)
    rep = _Rep()
    release_check.check_mutation_targets(rep, root=tree)
    assert rep.row("mutation targets")[1] == release_check.PASS, ("control: the copied tree fails before the edit",
                                                                   rep.row("mutation targets"))

    reg = tree / "tests" / REGISTRY
    text = reg.read_text(encoding="utf-8")
    needle = "        if trusted_only:"
    assert text.count(repr(needle)[1:-1]) == 1, "fixture error: the trusted_only needle moved"
    reg.write_text(text.replace(repr(needle)[1:-1], "        if trusted_only_moved_away:"), encoding="utf-8")
    rep = _Rep()
    release_check.check_mutation_targets(rep, root=tree)
    name, status, detail = rep.row("mutation targets")
    assert status == release_check.FAIL, f"a needle that no longer occurs passed the check: {detail}"
    assert "trusted_only_stops_filtering_and_fails_open" in detail, detail


def test_the_3_15_0_incident_is_caught(tmp_path):
    """The exact defect: the tenant needle at its pre-3.15.0 indentation, against the 3.15.0 core.py."""
    tree = _tree(tmp_path)
    reg = tree / "tests" / REGISTRY
    text = reg.read_text(encoding="utf-8")
    # Both lines of the needle go back, because the first alone is still a substring of the deeper code:
    # "        if self.tenant..." occurs inside "            if self.tenant...". The second line is what moved.
    pairs = [("'            if self.tenant is not None:", "'        if self.tenant is not None:"),
             ("'                pool = [r for r in pool if r.get(\"tenant\")", "'            pool = [r for r in pool if r.get(\"tenant\")")]
    for new, old in pairs:
        assert text.count(new) == 1, f"fixture error: the tenant needle moved: {new!r}"
        text = text.replace(new, old)
    reg.write_text(text, encoding="utf-8")
    rep = _Rep()
    release_check.check_mutation_targets(rep, root=tree)
    name, status, detail = rep.row("mutation targets")
    assert status == release_check.FAIL, f"the 3.15.0 stale needle passed the check: {detail}"
    assert "both_tenant_guards_disabled" in detail, detail
