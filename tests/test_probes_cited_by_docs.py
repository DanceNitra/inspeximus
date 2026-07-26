"""A probe the docs cite as evidence has to still run against today's code.

`docs/API.md` and the README cite 53 scripts under `probes/` as the receipts behind measured claims. Nothing
ran them. Measured when this was written: 37 ran clean, 16 did not, and the reasons were not the same kind
of thing:

  * 2 imported `_PREFER_GAIN` from the `inspeximus` package, where it is not re-exported -- it lives in
    `inspeximus.core`. Stale evidence: cited, and could not execute. Fixed.
  * 3 imported a sibling module that was NEVER committed (`echo_attack_probe.py`,
    `agentpoison_multiretriever_check.py`) -- the citation pointed at something nobody could run. FIXED:
    both modules existed in the research tree all along and had never been copied across. Two of the three
    probes now run standalone with a deterministic embedder; the third still needs a 71 MB embedding cache,
    and the claim it carried is re-measured by `echo_policy_panel.py`.
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
    # Not a missing pip package: it calls a live OpenAI-compatible endpoint, so no amount of installing
    # makes it runnable in CI. Recorded as the precondition it is rather than added to the optional-
    # dependency allow-list, which would have implied `pip install openai` was enough.
    "memory_defense_layer_probe.py": "needs a live LLM endpoint (OPENAI_API_KEY + OPENAI_BASE_URL)",
    # These three imported a sibling module that was not in this repository at all -- recorded here for a
    # day as "MISSING DEPENDENCY", which is a defect wearing the costume of a precondition. The modules
    # existed the whole time in the research tree and had simply never been copied across; they are now
    # committed, the imports resolve, and what remains is an ordinary data/runtime precondition like the
    # LoCoMo entries above. Fixing beat re-describing.
    "echo_attack_probe_v2.py": "needs the MemBench knowledge_update fixture and a local embedder "
                               "(the 71 MB embedding cache is not redistributable); the policy numbers it "
                               "produced are re-measured standalone by echo_policy_panel.py",
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


#: Third-party modules a probe may legitimately need and a bare environment may legitimately lack. Read
#: from the declared extras, plus the scientific packages the analysis probes use. DECLARED, not inferred:
#: an unrecognised missing module is a defect, because `echo_attack_probe.py` -- a sibling that was never
#: committed -- fails with exactly the same ModuleNotFoundError as an absent pip package. Skipping on the
#: shape of the error would have quietly swallowed the one finding this file exists to surface.
def _optional_third_party():
    import re as _re

    names = {"numpy", "scipy", "pandas", "matplotlib", "sklearn", "yaml"}
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as fh:
        block = fh.read().split("[project.optional-dependencies]", 1)
    if len(block) == 2:
        for dep in _re.findall(r'"([A-Za-z0-9_.\[\]-]+)', block[1].split("\n[", 1)[0]):
            names.add(dep.split("[")[0].split(">")[0].split("=")[0].replace("-", "_"))
    # distribution name -> import name, where they differ
    names |= {"langchain_core", "llama_index", "autogen_core", "autogen_agentchat",
              "google", "agents", "pydantic_ai", "crewai", "pydantic", "mcp", "langgraph"}
    return names


OPTIONAL_THIRD_PARTY = _optional_third_party()


def _missing_module(stderr):
    # No `str | None` annotation: the CI matrix includes Python 3.9, where that is a runtime TypeError at
    # import unless the module opts into postponed evaluation. Local 3.13 can never show it.
    m = re.search(r"No module named '([\w.]+)'", stderr or "")
    return m.group(1).split(".")[0] if m else None


@pytest.mark.parametrize("probe", _standalone())
def test_a_standalone_cited_probe_still_runs(probe):
    """Against THIS repository, not whatever pip installed -- the same mistake that made the examples
    suite pass while never exercising the working tree.

    Locally every one of these passed; in CI eight failed, because this box has langgraph, autogen,
    pydantic-ai and numpy installed and the CI base environment has none of them. Local green is not CI
    green whenever an optional dependency is in reach -- a lesson this repository had already written
    down and I repeated anyway. The skip below is therefore narrow and declared."""
    r = subprocess.run([sys.executable, os.path.join("probes", probe)],
                       cwd=ROOT, capture_output=True, text=True, timeout=180,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8",
                            "PYTHONPATH": ROOT + os.pathsep + PROBES + os.pathsep
                            + os.environ.get("PYTHONPATH", "")})

    missing = _missing_module(r.stderr) if r.returncode != 0 else None
    if missing and missing in OPTIONAL_THIRD_PARTY:
        pytest.skip(f"{probe} needs the optional third-party module {missing!r}, "
                    f"which this environment does not have")
    assert not (missing and missing not in OPTIONAL_THIRD_PARTY), (
        f"probes/{probe} cannot import {missing!r}, and that is not a declared optional dependency. If it "
        f"is one, add it to the extras; if it is a sibling module of ours that was never committed, that "
        f"is the defect -- do not skip it away.")
    assert r.returncode == 0, (
        f"probes/{probe} is cited as evidence and exits {r.returncode}. If it needs a dataset or a "
        f"prepared store, add it to NOT_STANDALONE with the reason instead of leaving the citation "
        f"resting on a script that does not run.\n--- stderr tail ---\n{r.stderr[-1200:]}")


def test_no_stale_entries_in_the_exclusion_list():
    """An exclusion for a probe nobody cites any more is dead weight that hides the next real one."""
    stale = sorted(set(NOT_STANDALONE) - set(_cited()))
    assert not stale, f"NOT_STANDALONE names probes the docs no longer cite: {stale}"


def test_no_citation_rests_on_a_module_we_never_committed():
    """This test used to assert the OPPOSITE -- that three `MISSING DEPENDENCY` entries were present -- and
    it failed the moment they were fixed, which is what it was written to do.

    The two sibling modules (`echo_attack_probe.py`, `agentpoison_multiretriever_check.py`) had existed in
    the research tree the whole time and were simply never copied across. They are committed now. The rule
    they leave behind: a dependency of OURS that is not in the repository is a defect, and describing it in
    an exclusion list is not a fix. An exclusion may only ever name something outside our control -- a
    dataset we cannot redistribute, a model download, a live endpoint."""
    ours = []
    for name in sorted(NOT_STANDALONE):
        with open(os.path.join(PROBES, name), encoding="utf-8") as fh:
            src = fh.read()
        for sibling in re.findall(r"^\s*(?:import|from)\s+([a-z_][a-z_0-9]*)", src, re.M):
            if sibling in OPTIONAL_THIRD_PARTY or sibling == "inspeximus":
                continue
            if os.path.exists(os.path.join(PROBES, sibling + ".py")):
                continue
            try:                                  # stdlib and installed packages are fine
                __import__(sibling)
            except ImportError:
                ours.append(f"{name} imports {sibling!r}, which is not in probes/ and not installable")
    assert not ours, ("a cited probe depends on something of ours that was never committed; commit it "
                      "rather than describing it: " + "; ".join(ours))
    assert not [v for v in NOT_STANDALONE.values() if v.startswith("MISSING DEPENDENCY")], \
        "MISSING DEPENDENCY is a defect wearing the costume of a precondition; fix it instead"


# ── the skip above must not become a way to not look ────────────────────────────────────────────────
def test_the_optional_list_is_declared_not_guessed():
    """It is built from pyproject's extras. If those are renamed and this silently empties, every probe
    failure would turn back into a hard failure -- noisy, but honest. The dangerous direction is the
    other one, so the assertion is on the content, not merely the size."""
    assert {"langgraph", "numpy", "pydantic_ai", "autogen_core"} <= OPTIONAL_THIRD_PARTY
    assert "echo_attack_probe" not in OPTIONAL_THIRD_PARTY


def test_a_missing_sibling_of_ours_is_not_skipped_away():
    """THE discriminator. A never-committed sibling module raises exactly the same ModuleNotFoundError as
    an absent pip package, so skipping on the shape of the error would have swallowed the finding this
    file exists to report -- three cited probes import `echo_attack_probe.py` /
    `agentpoison_multiretriever_check.py`, which are not in this repository at all."""
    for ours in ("echo_attack_probe", "agentpoison_multiretriever_check", "inspeximus"):
        stderr = f"ModuleNotFoundError: No module named '{ours}'"
        assert _missing_module(stderr) == ours
        assert _missing_module(stderr) not in OPTIONAL_THIRD_PARTY, \
            f"a missing {ours} must fail the suite, never skip it"


def test_a_missing_optional_dependency_is_recognised():
    assert _missing_module("ModuleNotFoundError: No module named 'langgraph.store.base'") == "langgraph"
    assert _missing_module("ModuleNotFoundError: No module named 'langgraph.store.base'") \
        in OPTIONAL_THIRD_PARTY


def test_a_probe_that_fails_for_any_other_reason_still_fails():
    """A crash, a bad assertion, a missing dataset -- none of those name a module, so none of them can
    reach the skip. If this ever returned a module name, every failure would become skippable."""
    assert _missing_module("ZeroDivisionError: division by zero") is None
    assert _missing_module("FileNotFoundError: agora_output/lab/data/locomo10.json") is None
    assert _missing_module("") is None
