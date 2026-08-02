"""Tests for the parity harness.

These are tests of the MEASURING INSTRUMENT, not of inspeximus. Each one pins a property that, when it
was absent, produced a wrong number in this benchmark:

* the corpus digest — a generator that drifts silently invalidates every published cell;
* the paraphrase invariant — a probe sharing a word with its fact turns the retrieval axis into a
  keyword-match axis and hands us a row we should lose;
* the reader's `unclear` verdict — folding "both values present" into a win is how a keep-all store
  gets credited with a capability it does not have;
* the validity precondition — without it the `naive` arm scored revert_success 1.00 with no revert
  channel, because its ranker preferred the older sentence and the correction never took effect;
* NOT-MEASURED — an arm that fails its positive control must never be scored as a zero.

Every check carries a control that fails if the fixture stops reproducing the defect it guards against.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
PARITY = REPO / "benchmarks" / "parity"
sys.path.insert(0, str(PARITY))

# Plain imports, NOT importorskip: these are repo-local modules that are always present. Guarding them
# would hide all sixteen instrument tests from the base CI job -- which is the exact failure
# tests/test_skip_census.py exists to catch, and it caught it. Only the genuinely optional
# benchmark-only dependency (rank_bm25) is guarded, and only on the tests that need it.
import adapters  # noqa: E402
import corpus  # noqa: E402
import run  # noqa: E402

needs_bm25 = pytest.mark.skipif(
    importlib.util.find_spec("rank_bm25") is None,
    reason="rank_bm25 is a benchmark-only dependency; the library itself stays zero-dependency")


# ---------------------------------------------------------------- corpus
def test_corpus_is_deterministic_and_matches_the_committed_manifest():
    """The corpus is generated, not stored; the manifest digest is what makes that reproducible."""
    manifest = json.loads((PARITY / "corpus_manifest.json").read_text(encoding="utf-8"))
    for subset, meta in manifest["subsets"].items():
        threads, distractors = corpus.SUBSETS[subset]
        built = corpus.build(threads, distractors, seed=manifest["seed"])
        assert corpus.digest(built) == meta["sha256"], (
            f"{subset}: generator drifted from the pinned digest — every published number for this "
            f"subset was measured on a different fixture")
        # control: a different seed MUST produce a different digest, else the digest proves nothing
        assert corpus.digest(corpus.build(threads, distractors, seed=manifest["seed"] + 1)) \
            != meta["sha256"]


def test_probes_share_no_content_word_with_the_fact_they_target():
    """The retrieval axis is only honest if the probes are genuine paraphrases."""
    c = corpus.build(*corpus.SUBSETS["small"])
    checked = 0
    for th in c["items"]:
        for d, p in zip(th["distractors"], th["probes"]):
            fw = {w.strip("?.,") for w in d["text"].lower().split()}
            pw = {w.strip("?.,") for w in p["question"].lower().split()}
            assert not (fw & pw) - corpus._STOP - {th["tag"]}
            checked += 1
    assert checked > 0, "vacuous: no probe/fact pairs were examined"


def test_corpus_validator_rejects_a_value_collision():
    """Control for the validator itself: hand it the defect it exists to catch."""
    c = corpus.build(*corpus.SUBSETS["small"])
    bad = c["items"][0]
    bad["value_b"] = bad["value_a"] + "x"          # b now contains a
    with pytest.raises(AssertionError):
        corpus._validate([bad])


# ---------------------------------------------------------------- the reader
@pytest.mark.parametrize("surface,expected", [
    (["the region is osaka"], "A"),
    (["the region is now malmo"], "B"),
    (["osaka was corrected to malmo"], "unclear"),          # both in one item
    (["nothing relevant"], "unclear"),                      # neither anywhere
    ([], "unclear"),
    (["the region is now malmo", "the region is osaka"], "B"),   # rank order decides
    (["the region is osaka", "the region is now malmo"], "A"),
])
def test_reader_verdicts(surface, expected):
    assert run.top_value(surface, "osaka", "malmo") == expected


def test_reader_never_credits_a_store_that_serves_both_values():
    """A bitemporal-style surface that returns the invalidated edge next to the valid one is a
    read-contract difference, not a win for anyone. It must land `unclear`."""
    assert run.top_value(["osaka (invalidated) / malmo (valid)"], "osaka", "malmo") == "unclear"


# ---------------------------------------------------------------- arms
@needs_bm25
def test_positive_control_passes_for_local_arms():
    for name in adapters.LOCAL_ARMS:
        arm = adapters.build_arm(name)
        pc = arm.positive_control()
        arm.close()
        assert pc["passes"], f"{name}: {pc['reason']}"


@needs_bm25
def test_positive_control_fails_loudly_for_a_broken_arm():
    """Control: an arm whose write path silently drops everything must FAIL the gate, not score zero."""
    class Broken(adapters.Bm25Arm):
        name = "broken"

        def write(self, s, text, key=None, object=None):
            return None                                   # stores nothing

    pc = Broken().positive_control()
    assert not pc["passes"] and pc["reason"]


@needs_bm25
def test_driver_reports_not_measured_instead_of_a_zero(monkeypatch):
    """The rule 'a competitor scoring 0.000 is our bug' has to live in the driver, not in the author.

    A broken arm is routed through `run_arm` exactly as a competitor would be; the contract is that it
    comes back NOT-MEASURED with a reason and carries **no numeric axis**.
    """
    class Broken(adapters.Bm25Arm):
        name = "broken"

        def write(self, s, text, key=None, object=None):
            return None                                   # silently stores nothing

    monkeypatch.setattr(adapters, "build_arm", lambda name, **cfg: Broken(**cfg))
    c = corpus.build(2, 3)
    out = run.run_arm("broken", c["items"], k=4, cfg={})
    assert out["status"] == "NOT-MEASURED"
    assert "positive control failed" in out["reason"]
    assert not any(ax in out for ax in run.AXES), "a failed arm must carry no scored axis"

    # control: the same driver on a WORKING arm must produce scored axes, else this test would pass
    # even if run_arm refused everything.
    monkeypatch.undo()
    ok = run.run_arm("bm25", c["items"], k=4, cfg={})
    assert ok["status"] == "measured" and all(ax in ok for ax in run.AXES)


# ---------------------------------------------------------------- validity precondition
def test_attack_axes_require_the_correction_to_have_taken_effect():
    """The precondition that removed a false 1.00.

    `naive` (keep-all, no supersession) serves the ORIGINAL value at rank 1 even after the correction,
    so it must be reported NOT-MEASURED on revert rather than credited with reverting perfectly.
    """
    c = corpus.build(4, 3)
    naive = adapters.build_arm("naive")
    res = run.axis_revert(naive, c["items"], k=4)
    naive.close()
    assert "not_measured" in res, f"expected the precondition to void this arm, got {res}"
    assert res["valid_cases"] == 0
    assert "B" not in res["pre_attack_verdicts"] or res["pre_attack_verdicts"].get("B", 0) == 0

    # control: the arm that DOES apply the correction must produce valid cases, else the precondition
    # is simply voiding everything and would hide a real result.
    keyed = adapters.build_arm("inspeximus")
    ok = run.axis_revert(keyed, c["items"], k=4)
    keyed.close()
    assert "not_measured" not in ok and ok["valid_cases"] == len(c["items"])


def test_erasure_over_forget_is_a_before_after_difference():
    """An absolute post-deletion count would mostly measure how well a templated query retrieves."""
    c = corpus.build(3, 4)
    arm = adapters.build_arm("inspeximus")
    res = run.axis_erasure(arm, c["items"], k=6)
    arm.close()
    assert res["over_forget_denominator"] > 0, "vacuous: nothing was reachable before deletion"
    assert res["retrieval_leakage"]["rate"] == 0.0
    assert res["over_forget"] == 0.0
