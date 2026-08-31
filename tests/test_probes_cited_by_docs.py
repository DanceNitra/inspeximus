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
#: These tests each spawn a subprocess with a wall-clock budget, so running several at once is
#: load the suite created and then measured. One worker keeps the budget meaningful.
pytestmark = pytest.mark.xdist_group("cited_probes")

PROBES = os.path.join(ROOT, "probes")

#: Cited probes that cannot run standalone, each with the reason. A probe here is still expected to EXIST
#: and to be importable-looking; it is the execution that is excused, and only for a stated cause.
NOT_STANDALONE = {
    # Reads every judge through the paid OpenAI API, which is the point: it measures how much
    # of our published 0.75 is gpt-4o-mini rather than inspeximus. It exits 2 with the reason
    # when no key is set, which is the behaviour the benchmark entrypoints were given after
    # #1, so on a CI runner it declines correctly rather than failing.
    # Same shape, same reason, and it was left out of this list when it landed -- CI caught it
    # on main. Thirty judge calls per run against the paid API; exits 2 with the reason when
    # no key is set, which is the post-#1 refusal behaviour rather than a failure.
    "the_judge_is_not_deterministic_at_temperature_zero.py":
        "needs OPENAI_API_KEY -- it replays one fixture through the judge 30 times",
    "does_the_headline_number_depend_on_who_judges_it.py":
        "needs OPENAI_API_KEY -- it replays the fixture through several paid judges",
    "locomo_composed_soft_filters.py": "needs agora_output/lab/data/locomo10.json (LoCoMo, not redistributable)",
    "locomo_correlated_cue_composition.py": "needs the LoCoMo dataset",
    "locomo_metadata_prefilter.py": "needs locomo10.json",
    # Exits 2 with a message naming --locomo when the dataset is absent. Recorded rather than made to
    # pass on a synthetic corpus: the movement it measures does NOT reproduce on any synthetic store
    # tried (120-600 records, ages spread over 2-10 s, exact-tie and near-tie text -> 0 answers move),
    # so a self-contained version of this probe would print zeros and quietly retire the README
    # paragraph it exists to support.
    "recall_over_a_time_gap.py": "needs locomo10.json (LoCoMo, not redistributable)",
    "locomo_retrieval_map.py": "needs benchmark output under agora_output/",
    "locomo_soft_prefer_filter.py": "needs benchmark output under agora_output/",
    "membench_recall_probe_v2.py": "needs MemBench output under agora_output/",
    "membench_recency_tiebreak_probe.py": "needs MemBench output under agora_output/",
    "reversibility_gate_frontier.py": "needs benchmark output under agora_output/",
    # Scans the stores THIS deployment writes, and asserts it found some: a zero from a scan that
    # reached nothing would look identical to "no echoes exist", which is the exact confusion the
    # probe was written to prevent. On a CI runner there are no stores, so the assertion fires and
    # the probe exits 1 -- correctly. Its numbers are ours to REPORT, not anyone else's to re-run;
    # the mechanism half is in corroboration_counts_an_echo_as_two_witnesses.py, which is
    # self-contained and runs everywhere.
    "would_a_lineage_rail_reach_anything_here.py": "measures OUR deployment's stores; refuses to report a zero it did not earn",
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
    # Cited by the CHANGELOG as the receipt for retrieval-recall@25 = 0.783/0.648 and never committed --
    # found the moment CHANGELOG.md entered this file's scope. It (and its sibling locomo_qa.py) are in the
    # repository now; what remains is the dataset, which we cannot redistribute.
    "retrieval_recall_locomo.py": "needs agora_output/lab/data/locomo10.json (LoCoMo, not redistributable)",
    "locomo_qa.py": "needs the LoCoMo dataset; also the shared loader retrieval_recall_locomo.py imports",
    # It refuses with an explicit message rather than a ModuleNotFoundError, so the skip above cannot see
    # it -- and it downloads three dense retrievers, which is more than a pip install anyway.
    "agentpoison_multiretriever_check.py": "needs torch + transformers AND downloads three dense retrievers",
}


_URL = re.compile(r"https?://\S+")
_PROBE_PATH = re.compile(r"probes/([a-z_0-9]+\.py)")
#: The path segment that identifies THIS repository inside a github URL.
_THIS_REPO = "/inspeximus/"


def _local_probe_citations(text):
    """Probe filenames the text claims are in THIS repository.

    A bare `probes/x.py` is exactly that claim, and stays fully checked. A FULLY-QUALIFIED URL into a
    DIFFERENT repository is not: it says where the file actually lives, which is what a reader following
    it needs. That distinction is what lets CHANGELOG credit a probe that lives in the agora repo --
    because it measures that deployment and could not run here -- without this guard reporting it as
    missing evidence.

    The hole this exemption could open is a URL into our OWN repo naming a probe we never committed, so
    that case is deliberately NOT exempt and `test_the_foreign_url_exemption_still_has_teeth` holds it
    down. An exemption without a control is how a guard stops seeing its target while still reporting
    green.
    """
    for url in _URL.findall(text):
        if _PROBE_PATH.search(url) and _THIS_REPO not in url:
            text = text.replace(url, " ")
    return set(_PROBE_PATH.findall(text))


def test_the_foreign_url_exemption_still_has_teeth():
    """A control for the exemption in _local_probe_citations: it must exempt foreign repos and nothing else."""
    missing = "definitely_not_in_this_repo.py"
    assert missing in _local_probe_citations("see probes/%s for the receipt" % missing), \
        "a BARE path is a claim about this repo and must still be checked"
    ours = "https://github.com/DanceNitra/inspeximus/blob/main/probes/" + missing
    assert missing in _local_probe_citations(ours), \
        "a URL into our OWN repo must still be checked -- the exemption is for foreign repos only"
    foreign = "https://github.com/DanceNitra/agora/blob/main/research/probes/" + missing
    assert _local_probe_citations(foreign) == set(), \
        "a fully-qualified URL into another repo makes no claim about this one"


def _cited():
    """Every probe filename the docs or README point at."""
    # CHANGELOG.md is IN SCOPE. It was not, and it was never declared out of scope either -- so probes
    # cited only there were checked by nothing, and two of them do not exist in the repository at all.
    # It is also the file a user reads before upgrading, which makes a broken "run this" there worse than
    # one in the docs, not better.
    files = ["README.md", "CHANGELOG.md"]
    docs = os.path.join(ROOT, "docs")
    if os.path.isdir(docs):
        files += [os.path.join("docs", f) for f in sorted(os.listdir(docs)) if f.endswith(".md")]
    names = set()
    for rel in files:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as fh:
            names |= _local_probe_citations(fh.read())
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
        section = block[1].split("\n[", 1)[0]
        # COMMENTS ARE NOT DEPENDENCIES. This regexes a TOML section, and it used to regex the comments
        # too -- so a comment that merely QUOTED a package spec silently added it to the skip allowlist.
        # It happened: a note explaining the mcp bound mentioned `pip install "inspeximus[mcp]"`, and
        # `inspeximus` -- OUR OWN package -- landed in OPTIONAL_THIRD_PARTY, which is the exact set that
        # decides "may a probe be SKIPPED because this is absent?". A missing sibling of ours would then
        # have been skipped away instead of failing, which is the finding
        # test_a_missing_sibling_of_ours_is_not_skipped_away exists to prevent. That test caught it.
        # Prose near a dependency list must not be able to widen it.
        lines = []
        for ln in section.split("\n"):
            if ln.lstrip().startswith("#"):
                continue
            lines.append(ln.split(" #", 1)[0])
        for dep in _re.findall(r'"([A-Za-z0-9_.\[\]-]+)', "\n".join(lines)):
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

    import importlib.machinery

    names = set(sys.builtin_module_names)
    paths = sysconfig.get_paths()
    roots = [paths.get("stdlib"), paths.get("platstdlib")]
    # POSIX keeps C extensions in lib-dynload; Windows keeps them in a DLLs directory beside Lib.
    # Missing the second is how `unicodedata` slipped through -- found by running this branch, not by
    # reasoning about it.
    roots += [os.path.join(r, "lib-dynload") for r in list(roots) if r]
    roots += [os.path.join(os.path.dirname(r), "DLLs") for r in list(roots) if r]
    # C extensions (unicodedata, _socket, ...) are .pyd/.so, not .py, and live in lib-dynload on POSIX.
    # A scan for *.py alone missed `unicodedata` -- caught by exercising this branch directly rather than
    # by shipping it and waiting for the 3.9 leg to complain again.
    suffixes = [".py"] + list(importlib.machinery.EXTENSION_SUFFIXES)
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for entry in os.listdir(root):
            full = os.path.join(root, entry)
            if os.path.isdir(full) and entry not in ("site-packages", "__pycache__", "lib-dynload"):
                names.add(entry)
                continue
            for suf in suffixes:
                if entry.endswith(suf):
                    names.add(entry[:-len(suf)].split(".")[0])
                    break
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


#: Seconds a probe gets before the runner calls it hung. DECLARED per probe, not inferred, because the
#: default is a HANG detector and a probe that legitimately takes minutes turns it into a performance
#: test that fails whenever the box is busy.
#:
#: Measured 2026-08-10: `reinforce_accuracy_ablation.py` takes 173.8s on an idle machine against a flat
#: 180s limit -- 3% of headroom. It passed alone and FAILED in the release run, where ten mutation
#: workers had the CPU. Nothing was broken; the guard was reporting the load average. Raising the flat
#: limit for everything would have been the wrong fix: it would blind the hang detector for the other
#: 150 probes to buy margin for one.
_SLOW_PROBES = {
    "reinforce_accuracy_ablation.py": 600,   # 173.8s idle; LoCoMo corpora x 2, bootstrap CIs
    # 2.10.1 made every store save durable (unique temp + fsync + inter-process lock), which is what
    # stops six concurrent writers blending one store into an unparseable file. These two probes each
    # perform ~14,000 full-store writes, so they pay the guarantee 14,000 times: measured on an idle
    # box, 46.2s -> 66.1s and 83.6s -> 91.7s, with 20.4s of that squarely in nt.fsync. Both passed
    # alone and both failed the release run at 24-way parallelism -- the load-average failure this
    # table already exists to prevent. The cost is the feature; the probes are outliers in how often
    # they trigger it.
    #
    # (A first pass blamed the lock and cached its file handle for 2.6 ms/save. The profile then
    # showed 19.3s in importlib: `import msvcrt` was inside the lock's __init__ and ran on every
    # save. Both are fixed, and the residue below is fsync alone.)
    "identity_gate_supersession_probe.py": 400,     # 66.1s idle
    "recall_iterative_surface_multihop.py": 400,    # 91.7s idle
}
_DEFAULT_PROBE_TIMEOUT = 180


def _budget(probe):
    return _SLOW_PROBES.get(probe, _DEFAULT_PROBE_TIMEOUT)


def test_the_slow_probe_budget_names_real_probes():
    """A budget for a probe that no longer exists is a silent no-op, and the next slow probe inherits the
    flat limit with nobody noticing. Same shape as the exemption control above: a declared list has to be
    held against reality or it decays into decoration."""
    on_disk = {f for f in os.listdir(PROBES) if f.endswith(".py")}
    unknown = sorted(set(_SLOW_PROBES) - on_disk)
    assert not unknown, "the slow-probe budget names probes that are not on disk: %r" % unknown


def _missing_module(stderr):
    # No `str | None` annotation: the CI matrix includes Python 3.9, where that is a runtime TypeError at
    # import unless the module opts into postponed evaluation. Local 3.13 can never show it.
    m = re.search(r"No module named '([\w.]+)'", stderr or "")
    return m.group(1).split(".")[0] if m else None


def _run_probe(probe, extra_env=None):
    """Run a probe as a subprocess, and tell a TIMEOUT apart from a FAILURE.

    The budget is wall clock, and wall clock is not a property of the probe alone: under pytest-xdist
    six of these run at once, each possibly parallel itself, so the box they are timed on is one the
    tests loaded. Three cited probes failed that way in a -n 6 run and all 182 passed serially.

    The module-level xdist_group keeps them on one worker, which is what makes the budget mean
    anything. This function exists for the case that remains: a timeout now says the budget was
    exceeded and names load as a cause, instead of surfacing as a bare exception that reads like a
    broken probe.
    """
    env = {**os.environ, "PYTHONIOENCODING": "utf-8",
           "PYTHONPATH": ROOT + os.pathsep + PROBES + os.pathsep + os.environ.get("PYTHONPATH", "")}
    env.update(extra_env or {})
    try:
        return subprocess.run([sys.executable, os.path.join("probes", probe)],
                              cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=_budget(probe), env=env)
    except subprocess.TimeoutExpired:
        raise AssertionError(
            "probes/%s did not finish inside its %s s budget. That is a statement about this "
            "machine as much as about the probe: if the suite is running in parallel, the budget "
            "was measured on a quieter box. Re-run it alone before treating this as a defect in "
            "the probe." % (probe, _budget(probe)))


@pytest.mark.parametrize("probe", _standalone())
def test_a_standalone_cited_probe_still_runs(probe):
    """Against THIS repository, not whatever pip installed -- the same mistake that made the examples
    suite pass while never exercising the working tree.

    Locally every one of these passed; in CI eight failed, because this box has langgraph, autogen,
    pydantic-ai and numpy installed and the CI base environment has none of them. Local green is not CI
    green whenever an optional dependency is in reach -- a lesson this repository had already written
    down and I repeated anyway. The skip below is therefore narrow and declared."""
    r = _run_probe(probe)

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


def test_every_probe_on_disk_is_covered_somehow():
    """The property that actually matters, and it cannot silently empty: every file under probes/ is
    either CITED (and run, or exempt with a reason), SWEPT as an uncited probe, or declared a shared
    MODULE. A count threshold could not express this -- bringing CHANGELOG.md into scope moved 18 probes
    from "uncited" to "cited" in one commit and tripped a floor of 30 that was measuring the wrong thing."""
    on_disk = {f for f in os.listdir(PROBES) if f.endswith(".py") and not f.startswith("_")}
    covered = set(_cited()) | set(_uncited()) | set(NOT_STANDALONE)
    assert not (on_disk - covered), f"probes covered by nothing: {sorted(on_disk - covered)}"
    assert len(_uncited()) + len(_cited()) >= 60, "the sweep must not collapse to a handful"


# SHARED ARTIFACT. This and tests/test_echo_policy_panel.py both execute
# probes/echo_policy_panel.py, which writes ONE probes/echo_policy_panel_result.json.
# Serial they queue; under xdist they raced and clobbered it (1 failed + 4 errors that
# do not occur serially). Same worker, so they cannot overlap.
@pytest.mark.xdist_group("echo_policy_panel")
@pytest.mark.parametrize("probe", _uncited())
def test_an_uncited_probe_still_runs(probe):
    """Same standard as the cited half, same skip rule: a declared optional dependency excuses the run,
    anything else is a defect. An uncited probe that fails is either rot or -- as it turned out twice --
    a live product bug wearing a rotten probe's clothes."""
    r = _run_probe(probe)
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
