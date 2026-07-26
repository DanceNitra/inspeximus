"""A probe the docs cite as evidence has to still run against today's code.

`docs/API.md` and the README cite 53 scripts under `probes/` as the receipts behind measured claims. Nothing
ran them. Measured when this was written: 37 ran clean, 16 did not, and the reasons were not the same kind
of thing:

  * 2 imported `_PREFER_GAIN` from the `inspeximus` package, where it is not re-exported -- it lives in
    `inspeximus.core`. Stale evidence: cited, and could not execute. Fixed.
  * 3 import a sibling module that was NEVER committed (`echo_attack_probe.py`,
    `agentpoison_multiretriever_check.py`). The citation points at something nobody can run.
  * the rest need an external dataset (LoCoMo, benchmark output) that this repository does not and should
    not ship.

The distinction is the point. "Needs a dataset we cannot redistribute" is a documented precondition; "the
import broke two releases ago" is a claim quietly resting on nothing. This file runs the standalone ones
and requires every other cited probe to be DECLARED with a reason.
"""
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBES = os.path.join(ROOT, "probes")

#: Cited probes that cannot run standalone, each with the reason. A probe here is still expected to EXIST
#: and to be importable-looking; it is the execution that is excused, and only for a stated cause.
NOT_STANDALONE = {
    "locomo_composed_soft_filters.py": "needs agora_output/lab/data/locomo10.json (LoCoMo, not redistributable)",
    "locomo_correlated_cue_composition.py": "needs the LoCoMo dataset",
    "locomo_metadata_prefilter.py": "needs locomo10.json",
    "locomo_retrieval_map.py": "needs benchmark output under agora_output/",
    "locomo_soft_prefer_filter.py": "needs benchmark output under agora_output/",
    "membench_recall_probe_v2.py": "needs MemBench output under agora_output/",
    "membench_recency_tiebreak_probe.py": "needs MemBench output under agora_output/",
    "reversibility_gate_frontier.py": "needs benchmark output under agora_output/",
    "evidence_grade_ratchet.py": "needs a prepared store outside the repo",
    "route_probe.py": "needs a prepared store outside the repo",
    "operating_point_memory.py": "needs a prepared store outside the repo",
    "outcome_propagation_probe.py": "long-running; exceeds the suite's per-probe budget",
    "forget_subject_tombstone_probe.py": "needs a prepared store outside the repo",
    # These three cite a sibling module that is not in this repository at all. Recorded as the defect it
    # is rather than hidden: the citation currently points at something nobody can execute.
    "echo_attack_probe_v2.py": "MISSING DEPENDENCY: imports echo_attack_probe.py, never committed",
    "agentpoison_influence_gate.py":
        "MISSING DEPENDENCY: imports agentpoison_multiretriever_check.py, never committed",
    "agentpoison_influence_gate_validation.py":
        "MISSING DEPENDENCY: imports agentpoison_multiretriever_check.py, never committed",
}


def _cited():
    """Every probe filename the docs or README point at."""
    files = ["README.md"]
    docs = os.path.join(ROOT, "docs")
    if os.path.isdir(docs):
        files += [os.path.join("docs", f) for f in sorted(os.listdir(docs)) if f.endswith(".md")]
    names = set()
    for rel in files:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            names |= set(re.findall(r"probes/([a-z_0-9]+\.py)", fh.read()))
    return sorted(names)


def _standalone():
    return [n for n in _cited() if n not in NOT_STANDALONE]


def test_the_docs_actually_cite_probes():
    """If the extraction breaks, every parametrised test below silently becomes zero cases."""
    assert len(_cited()) >= 30, _cited()


@pytest.mark.parametrize("probe", _cited())
def test_every_cited_probe_exists(probe):
    assert os.path.exists(os.path.join(PROBES, probe)), \
        f"docs cite probes/{probe} as evidence and the file is not in the repository"


@pytest.mark.parametrize("probe", _standalone())
def test_a_standalone_cited_probe_still_runs(probe):
    """Against THIS repository, not whatever pip installed -- the same mistake that made the examples
    suite pass while never exercising the working tree."""
    r = subprocess.run([sys.executable, os.path.join("probes", probe)],
                       cwd=ROOT, capture_output=True, text=True, timeout=180,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8",
                            "PYTHONPATH": ROOT + os.pathsep + PROBES + os.pathsep
                            + os.environ.get("PYTHONPATH", "")})
    assert r.returncode == 0, (
        f"probes/{probe} is cited as evidence and exits {r.returncode}. If it needs a dataset or a "
        f"prepared store, add it to NOT_STANDALONE with the reason instead of leaving the citation "
        f"resting on a script that does not run.\n--- stderr tail ---\n{r.stderr[-1200:]}")


def test_no_stale_entries_in_the_exclusion_list():
    """An exclusion for a probe nobody cites any more is dead weight that hides the next real one."""
    stale = sorted(set(NOT_STANDALONE) - set(_cited()))
    assert not stale, f"NOT_STANDALONE names probes the docs no longer cite: {stale}"


def test_the_missing_dependency_probes_are_recorded_as_such():
    """Three citations point at scripts whose sibling module was never committed. That is a defect, not a
    precondition, and it should read as one until it is fixed or the citation is dropped."""
    broken = {k: v for k, v in NOT_STANDALONE.items() if v.startswith("MISSING DEPENDENCY")}
    assert broken, "if these were fixed, remove them from NOT_STANDALONE rather than leaving the note"
    for name in broken:
        assert os.path.exists(os.path.join(PROBES, name))
