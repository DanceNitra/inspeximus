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
    # ── probes no doc cites. They are not evidence, but they ROT: identity_gate_supersession_probe.py had
    # been crashing on its first line and nobody knew, and two others were failing on real assertions about
    # remember_decision -- which turned out to be a genuine product defect, not a stale probe. Measured
    # sweep of all 47: 36 ran clean, 0 missing modules, 5 too slow, 6 failing. These are those.
    "conflict_depth_compounding.py": "exceeds the suite's per-probe budget (>75s)",
    "generative_agents_agent_stress.py": "exceeds the suite's per-probe budget (>75s)",
    "integrity_cost_axis.py": "exceeds the suite's per-probe budget (>75s)",
    "memory_tipping_ews.py": "exceeds the suite's per-probe budget (>75s)",
    "supersession_replication.py": "exceeds the suite's per-probe budget (>75s)",
    # These three read server/.env -- a file in the PRIVATE research repo, holding model credentials. In
    # this repository they can never run, and no install fixes that. Named as the cross-repo coupling it
    # is rather than left to fail as if it were a bug.
    "integrity_bench_echo.py": "reads server/.env from the private research repo (live model credentials)",
    "integrity_bench_revert.py": "reads server/.env from the private research repo (live model credentials)",
    "reversion_classifier_probe.py": "reads server/.env from the private research repo (live model creds)",
    "echo_attack_probe.py": "needs the MemBench knowledge_update fixture and a local embedder",
    # It refuses with an explicit message rather than a ModuleNotFoundError, so the skip above cannot see
    # it -- and it downloads three dense retrievers, which is more than a pip install anyway.
    "agentpoison_multiretriever_check.py": "needs torch + transformers AND downloads three dense retrievers",
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
    # torch/transformers ARE installable and DO make these probes runnable, so a missing one is a
    # precondition, not a defect. They were left out at first on the reasoning that applied to `openai` --
    # where no install helps, because it also needs a live endpoint -- and that reasoning does not
    # transfer. CI caught it: three probes that run on this box (torch is installed) failed there.
    names |= {"torch", "transformers"}
    return names


OPTIONAL_THIRD_PARTY = _optional_third_party()

#: Two different questions, which I collapsed into one and CI caught within the hour:
#:   OPTIONAL_THIRD_PARTY -- "may a probe be SKIPPED because this is absent?"  `openai` must NOT be here:
#:       the probe that needs it also needs a live endpoint, so `pip install openai` would not make it
#:       runnable, and listing it would say otherwise.
#:   KNOWN_THIRD_PARTY    -- "is this somebody else's package, or an uncommitted module of OURS?"  `openai`
#:       obviously belongs here.
#: The first version answered the second question with "can I import it?", which is a property of the
#: machine: green here, red in CI, for the third time today.
#: Environment-independent on 3.10+; the CI matrix still runs 3.9, where the attribute does not exist, so
#: the fallback lists what these probes actually import. Deliberately not `__import__`: that answers "is it
#: installed HERE", which is the question that produced the false failure.
def _stdlib_names():
    """The standard library, DERIVED, never hand-listed.

    3.10+ has `sys.stdlib_module_names`. Below it the first version of this carried a literal set of "what
    these probes actually import" -- and the 3.9 leg failed within the hour on `asyncio` and `concurrent`,
    reported as modules of OURS that were never committed. A hand-maintained list of the standard library
    is wrong the moment somebody imports a different part of it, and it fails in the direction that accuses
    the repository of a defect it does not have.

    So: read the interpreter's own stdlib directory. That is a property of the interpreter, not of this
    machine -- unlike `__import__`, which answers "is it INSTALLED here" and is what put a false failure in
    CI twice already.
    """
    names = set(getattr(sys, "stdlib_module_names", ()))
    if names:
        return names
    import sysconfig

    names = set(sys.builtin_module_names)
    stdlib = sysconfig.get_paths().get("stdlib")
    if stdlib and os.path.isdir(stdlib):
        for entry in os.listdir(stdlib):
            if entry.endswith(".py"):
                names.add(entry[:-3])
            elif os.path.isdir(os.path.join(stdlib, entry)) and entry != "site-packages":
                names.add(entry)
    return names


_STDLIB = _stdlib_names()

KNOWN_THIRD_PARTY = OPTIONAL_THIRD_PARTY | {
    "openai", "torch", "transformers", "sentence_transformers", "datasets", "tiktoken",
    # Competitors' libraries. Our benchmark probes import them to measure AGAINST, so they are as
    # third-party as anything here -- and mistaking one for an uncommitted module of ours would send
    # somebody hunting for a file that was never meant to exist.
    "mem0", "graphiti_core", "zep_python", "letta", "chromadb", "qdrant_client", "faiss",
    "requests", "httpx", "tqdm", "matplotlib", "seaborn", "sklearn", "scipy",
}


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
    """An exclusion for a probe that no longer EXISTS is dead weight that hides the next real one.

    This used to require every exclusion to be cited, which was right while the list only covered cited
    probes. It now covers uncited ones too -- they rot just as quietly -- so the anti-staleness property
    is existence on disk, not citation."""
    gone = sorted(f for f in NOT_STANDALONE if not os.path.exists(os.path.join(PROBES, f)))
    assert not gone, f"NOT_STANDALONE names probes that are not in the repository: {gone}"


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
            if sibling in KNOWN_THIRD_PARTY or sibling == "inspeximus" or sibling in _STDLIB:
                continue
            if os.path.exists(os.path.join(PROBES, sibling + ".py")):
                continue
            ours.append(f"{name} imports {sibling!r}: not in probes/, not stdlib, and not a declared "
                        f"third-party package")
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


def test_the_ownership_discriminator_does_not_depend_on_this_machine():
    """It answered "is it installed here?", which is green on the maintainer's box and red in CI -- the
    third time in one day that a check measured the environment instead of the repository."""
    assert _STDLIB and "json" in _STDLIB and "os" in _STDLIB
    assert "openai" in KNOWN_THIRD_PARTY, "a real PyPI package is not an uncommitted module of ours"
    assert "openai" not in OPTIONAL_THIRD_PARTY, \
        "but it must NOT excuse a probe run: that probe needs a live endpoint, which no install provides"
    assert "echo_attack_probe" not in KNOWN_THIRD_PARTY and \
           "agentpoison_multiretriever_check" not in KNOWN_THIRD_PARTY, \
        "the two modules this whole check exists for must never be declared third-party"

# ── and the ones no doc cites: not evidence, but they still have to run ─────────────────────────────
def _uncited():
    """Every probe on disk that no doc points at. 48 of 101 when this was written, executed by nothing.

    A probe nobody runs rots silently, and the rot is not always in the probe: two of these were failing
    on correct assertions about `remember_decision`, which was leaving every decision on a topic ACTIVE
    at once -- a defect in a flagship API, exposed over MCP, found only because nothing had run its probe.
    """
    cited = set(_cited())
    return [f for f in sorted(os.listdir(PROBES))
            if f.endswith(".py") and not f.startswith("_")
            and f not in cited and f not in NOT_STANDALONE]


def test_there_really_are_uncited_probes_to_sweep():
    """If this list silently empties, the sweep below becomes a green result over nothing."""
    assert len(_uncited()) >= 30, len(_uncited())


@pytest.mark.parametrize("probe", _uncited())
def test_an_uncited_probe_still_runs(probe):
    """Same standard as the cited half, same skip rule: a declared optional dependency excuses the run,
    anything else is a defect. An uncited probe that fails is either rot or -- as it turned out twice --
    a live product bug wearing a rotten probe's clothes."""
    r = subprocess.run([sys.executable, os.path.join("probes", probe)],
                       cwd=ROOT, capture_output=True, text=True, timeout=180,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8",
                            "PYTHONPATH": ROOT + os.pathsep + PROBES + os.pathsep
                            + os.environ.get("PYTHONPATH", "")})
    missing = _missing_module(r.stderr) if r.returncode != 0 else None
    if missing and missing in OPTIONAL_THIRD_PARTY:
        pytest.skip(f"{probe} needs the optional third-party module {missing!r}")
    assert r.returncode == 0, (
        f"probes/{probe} exits {r.returncode}. Nothing cites it, so nothing was checking it -- add it to "
        f"NOT_STANDALONE with the real reason, or fix what it found. "
        f"stderr tail: {r.stderr[-1200:]}")


def test_the_stdlib_fallback_used_on_39_is_itself_correct():
    """This box has `sys.stdlib_module_names`, so the <3.10 branch never runs here — and that is exactly
    how the hand-written list shipped with `asyncio` and `concurrent` missing and failed only on the 3.9
    matrix leg, accusing the repository of modules it had never committed.

    So the fallback is exercised directly, with the attribute hidden."""
    import sys as _sys

    real = getattr(_sys, "stdlib_module_names", None)
    try:
        if real is not None:
            del _sys.stdlib_module_names
        derived = _stdlib_names()
    finally:
        if real is not None:
            _sys.stdlib_module_names = real

    assert len(derived) > 100, len(derived)
    for name in ("asyncio", "concurrent", "json", "os", "re", "sqlite3", "unicodedata", "collections"):
        assert name in derived, f"{name} is standard library and the fallback missed it"
    for name in ("mem0", "torch", "echo_attack_probe", "agentpoison_multiretriever_check"):
        assert name not in derived, f"{name} is not standard library and the fallback claimed it was"
