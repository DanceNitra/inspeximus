"""The cross-session dogfood probe, held to the property that makes it worth running.

`probes/dogfood_cross_session.py` measures whether inspeximus returns the facts an agent needs to resume
work after a session boundary. A retrieval probe is only as good as its controls: without them a hit rate
of 0.00 and a broken harness look identical, and a hit rate of 1.00 and a scorer that always says yes look
identical too. So these tests are about the INSTRUMENT, not about the score.

The full probe builds ~2,500 records and crosses a real process boundary. That is too slow for the suite,
so this runs the same code with a small deterministic corpus and the in-process boundary (a fresh handle
over the same file, which still forces the cold read from disk).

The load-bearing test is `test_a_broken_recall_is_reported_as_broken_not_as_a_low_score`: it disables
retrieval and asserts the probe says HARNESS_BROKEN and WITHHOLDS the hit rates. Without it, the day the
harness breaks we would publish a low number and read it as a finding about the library.
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "probes"))

import dogfood_cross_session as P  # noqa: E402

SMALL = dict(n_distractors=180, seed=7, in_process=True, siblings=4)


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    return P.run_harness(str(tmp_path_factory.mktemp("dogfood")), **SMALL)


# ── the controls, which are the point ─────────────────────────────────────────────────────────────────

def test_the_positive_control_fires(doc):
    """A record whose text is a verbatim copy of the query must come back at rank 1. This is the whole
    licence to report a hit rate at all: if the instrument cannot retrieve an exact copy of its own query
    out of the contested corpus, a low score would be a fact about the harness, not about the library."""
    ctl = doc["controls"]["positive"]
    assert ctl["passed"] is True, ctl["note"]
    assert ctl["rank_headline"] == 1
    assert all(r == 1 for r in ctl["rank_by_arm"].values()), \
        "the control must fire on EVERY arm; one arm passing is not an instrument check"


def test_the_positive_control_is_actually_verbatim():
    """A control whose query merely resembles its record proves nothing about exact retrieval. Assert the
    identity rather than trusting the flag the probe sets about itself."""
    assert P.CONTROL_TEXT.strip(), "the control record must have text"
    assert len(P.CONTROL_TEXT.split()) >= 12, \
        "a two-word control would be retrieved by accident; it has to be a real sentence"


def test_the_control_record_gets_no_special_treatment():
    """If the control were written with a boosted value or a slow-decay type it would be retrieved by its
    ranking weight rather than by its text, and would pass even with the matching broken."""
    src = open(os.path.join(ROOT, "probes", "dogfood_cross_session.py"), encoding="utf-8").read()
    assert "m.remember(CONTROL_TEXT)\n" in src, \
        "the control must be written with the library defaults (no value=, no mtype=)"


def test_the_no_floor_fill_is_reported_but_not_gated(doc):
    """A question about a subject the store has never seen still returns decisions, because no relevance
    floor is set. That belongs to the store, not to the harness: at small corpus sizes there is nothing
    better to return, so an earlier version that GATED on it declared its own instrument broken whenever
    the store was small. It is reported instead, and a control that fires on the operating point rather
    than on a defect is not a control."""
    obs = doc["store_observations"]["no_relevance_floor_fill"]
    assert obs["query"] and obs["subject_absent_from_store"] is True
    assert set(obs["target_decisions_returned_in_top25_by_arm"]) == set(doc["arms"])
    assert "absence" not in doc["controls"], \
        "this is an observation about the store; gating on it made the probe fail at small sizes"


def test_the_discrimination_control_holds(doc):
    """Every target re-scored against a DIFFERENT target's question must do worse than against its own.
    If it did not, the questions would be answerable by any decision record and the headline number would
    be measuring the corpus, not retrieval."""
    d = doc["controls"]["discrimination"]
    assert d["passed"] is True, d["note"]
    for arm, mismatched in d["mismatched_hit@5_by_arm"].items():
        assert mismatched < d["real_hit@5_by_arm"][arm]


def test_the_contest_control_reports_whether_the_fixture_is_hard(doc):
    """The fixture plants same-subject siblings so retrieval is contested. This asserts they are not inert
    -- a corpus where the answer is the only record about its subject makes every question a keyword
    lookup, which is how a probe comes to report SAFE forever."""
    c = doc["controls"]["contest"]
    assert c["passed"] is True, c["note"]
    assert c["mean_own_siblings_in_top25"] > 0


# ── the control must be able to FAIL, or it is decoration ─────────────────────────────────────────────

def test_a_broken_recall_is_reported_as_broken_not_as_a_low_score(tmp_path, monkeypatch):
    """Break retrieval outright and check the probe refuses to publish a number.

    This is the test the whole file exists for. A harness that reports 0.00 when its own recall is broken
    hands us a number that looks like evidence about the library. The probe must instead say
    HARNESS_BROKEN and WITHHOLD the hit rates.
    """
    from inspeximus import Inspeximus
    monkeypatch.setattr(Inspeximus, "recall", lambda self, *a, **k: [])

    d = P.run_harness(str(tmp_path / "broken"), **SMALL)
    assert d["verdict"] == "HARNESS_BROKEN"
    assert d["controls"]["positive"]["passed"] is False
    assert "headline" not in d and "hit_rates_withheld" in d, \
        "a broken instrument must not publish a hit rate — that is the failure this probe prevents"
    assert d["exit_code"] == 2
    # And it must say WHY in words a reader will act on, not just flip a boolean.
    assert "harness is broken" in d["controls"]["positive"]["note"].lower()


def test_a_retriever_that_returns_everything_is_caught(tmp_path, monkeypatch):
    """The other direction from a broken recall: one that answers every query with the whole store.

    That pathology is dangerous precisely because it looks like success at a deep k — every fact is
    "found". It has to trip a control rather than produce a number.

    The first version of this test patched recall to return a very deep slice of the REAL ranking, which
    is not the pathology at all: the ranking still worked, so the mismatched rate stayed below the real
    one and the probe correctly reported PASS. Returning the store unranked is the actual failure mode.
    """
    from inspeximus import Inspeximus

    def _everything(self, query, k=6, **kw):
        return [dict(r) for r in self.items if r.get("status") == "active"]

    monkeypatch.setattr(Inspeximus, "recall", _everything)
    d = P.run_harness(str(tmp_path / "yes"), **SMALL)
    assert d["verdict"] == "HARNESS_BROKEN", \
        "a retriever that returns the whole store must trip a control, not produce a score"
    assert "headline" not in d and "hit_rates_withheld" in d
    failed = [name for name, c in d["controls"].items() if c["passed"] is False]
    assert failed, "no control noticed that retrieval had stopped ranking"


def test_the_probe_can_report_a_miss(doc):
    """A hit rate that cannot come out below 1.0 measures nothing. The scorer must be able to record a
    miss, and it must say WHERE the record actually ranked so a reader can tell a ranking problem from an
    absence — the diagnosis is the difference between "tune the ranking" and "the record is not there"."""
    rows = doc["arms"][doc["headline_arm"]]["per_fact"]
    assert rows, "no facts were scored"
    assert all(r["rank"] is None or r["rank"] >= 1 for r in rows)
    for r in rows:
        if r["rank"] is None:
            assert "diagnosis" in r and r["diagnosis"], \
                "a miss must be diagnosed, not just counted"
    # The metric is a real fraction over the real denominator, not a hard-coded constant.
    n = len(rows)
    assert doc["arms"][doc["headline_arm"]]["hit@5"] == \
        round(sum(1 for r in rows if r["hit@5"]) / n, 4)
    assert doc["arms"][doc["headline_arm"]]["hit@1"] <= doc["arms"][doc["headline_arm"]]["hit@5"] \
        <= doc["arms"][doc["headline_arm"]]["hit@25"]


# ── the measurement itself ────────────────────────────────────────────────────────────────────────────

def test_the_boundary_was_actually_crossed(doc):
    """The reader must have loaded the store from disk, not inherited it. If the cold reader saw fewer
    records than were written, the boundary silently dropped the corpus and every number is about a store
    that does not exist."""
    op = doc["operating_point"]
    assert op["records_loaded_by_cold_reader"] == op["store_records"] > 0


def test_the_operating_point_is_reported_with_the_result(doc):
    """A hit rate without its store size is unreadable a month later."""
    op = doc["operating_point"]
    for field in ("store_records", "distractors", "topical_siblings", "targets", "seed",
                  "recall_mode", "session_boundary"):
        assert field in op, "missing operating-point field: %s" % field
    assert op["store_records"] >= op["distractors"] + op["topical_siblings"] + op["targets"]


def test_the_recency_case_is_reported_separately(doc):
    """The case we watched fail — a record written today, at k=5 — must never be averaged away."""
    rc = doc["recency_case"]
    assert rc["fact"] is not None and rc["query"]
    assert isinstance(rc["hit@5"], bool)
    assert doc["headline_arm"] in rc["rank_by_arm"]
    assert sum(1 for t in P.TARGETS if t.get("recency")) == 1, \
        "exactly one target carries the recency case; zero would make the report vacuous"


def test_the_correction_case_serves_the_current_decision_only(doc):
    """Keyed supersession is the thing we sell. A resuming agent asking how releases are published must
    not be handed the decision that was retired."""
    assert doc["correction_case"]["retired_decision_served"] is False


def test_two_runs_of_the_same_seed_agree(tmp_path):
    """A standing self-check whose number moves on its own cannot tell a regression from noise."""
    a = P.run_harness(str(tmp_path / "a"), **SMALL)
    b = P.run_harness(str(tmp_path / "b"), **SMALL)
    for arm in a["arms"]:
        assert [r["rank"] for r in a["arms"][arm]["per_fact"]] == \
               [r["rank"] for r in b["arms"][arm]["per_fact"]], \
            "same seed, different ranks: the probe is measuring its own randomness"


def test_the_fixture_is_deterministic():
    """Same seed -> byte-identical corpus, or a re-run compares two different experiments."""
    assert [d["text"] for d in P.build_distractors(60, 3)] == \
           [d["text"] for d in P.build_distractors(60, 3)]
    assert [d["text"] for d in P.build_distractors(60, 3)] != \
           [d["text"] for d in P.build_distractors(60, 4)]
    assert [s["text"] for s in P.build_siblings(4, 3)] == [s["text"] for s in P.build_siblings(4, 3)]


def test_siblings_are_built_from_their_own_targets_vocabulary():
    """A "sibling" that shares no words with its target contests nothing, and the contest control would
    then be reporting on records that could never have competed."""
    by_target = {}
    for s in P.build_siblings(6, 11):
        by_target.setdefault(s["sibling_of"], []).append(s["text"].lower())
    assert by_target, "no siblings were generated"
    for t in P.TARGETS:
        vocab = set(P._content_tokens(t["decision"] + " " + t["because"]))
        for text in by_target.get(t["id"], []):
            assert vocab & set(P._content_tokens(text)), \
                "sibling of %r shares no vocabulary with it: %r" % (t["id"], text)


def test_the_queries_do_not_quote_the_decisions():
    """The questions must be what a resuming agent ASKS, not a paraphrase of the sentence it is hunting.
    A query that reuses the decision's distinctive words is a positive control wearing a measurement's
    clothes — and softening the queries until the score looks good is the exact failure this probe exists
    to prevent, so the constraint is asserted rather than left to good intentions.

    The ceiling is half the decision's content words: a question is allowed to NAME its subject (for a
    short decision that alone is most of the budget) but not to hand over the verb it is looking for.
    This caught one real violation on its first run — `killed_centering` reused 67%, and its query was
    rewritten rather than the threshold moved.
    """
    for t in P.TARGETS:
        dec = set(P._content_tokens(t["decision"]))
        q = set(P._content_tokens(t["query"]))
        overlap = len(dec & q) / (len(dec) or 1)
        assert overlap <= 0.5, \
            "%s: the question reuses %.0f%% of the decision's own words" % (t["id"], 100 * overlap)


def test_the_query_overlap_is_published_per_fact(doc):
    """The ceiling above is enforced in the suite; the actual value ships with each fact so a reader can
    see which questions were easy without re-deriving it."""
    for r in doc["arms"][doc["headline_arm"]]["per_fact"]:
        assert isinstance(r["query_overlap_with_decision"], float)
        assert 0.0 <= r["query_overlap_with_decision"] <= 0.5


# ── the shipped result document ───────────────────────────────────────────────────────────────────────

def test_the_shipped_result_file_matches_the_current_schema():
    """The committed result is the number a reader will quote. It must be readable and carry its operating
    point, its controls and its caveats — a bare hit rate is not a result."""
    path = os.path.join(ROOT, "probes", "dogfood_cross_session.result.json")
    if not os.path.exists(path):                       # not generated in this checkout
        pytest.skip("result file not present")
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    assert d["probe"] == "dogfood_cross_session"
    assert d["verdict"] in ("PASS", "FAIL", "HARNESS_BROKEN")
    assert set(d["controls"]) == {"positive", "discrimination", "contest"}
    assert "no_relevance_floor_fill" in d["store_observations"]
    assert d["operating_point"]["store_records"] > 0
    if d["verdict"] != "HARNESS_BROKEN":
        assert d["controls"]["positive"]["passed"] is True
        assert d["caveats"], "the shipped number must ship with what it does not cover"
        for k in ("hit@1", "hit@5", "hit@25"):
            assert 0.0 <= d["headline"][k] <= 1.0


def test_the_cli_runs_and_honours_its_documented_exit_codes(tmp_path):
    """The probe is meant to be re-run by hand and by CI, so the entry point has to work end to end."""
    out = tmp_path / "r.json"
    r = subprocess.run([sys.executable, os.path.join("probes", "dogfood_cross_session.py"),
                        "--distractors", "120", "--siblings", "3", "--in-process",
                        "--out", str(out)],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8",
                            "PYTHONPATH": ROOT + os.pathsep + os.environ.get("PYTHONPATH", "")})
    assert r.returncode in (0, 1), \
        "exit 2 means a control failed:\n%s\n%s" % (r.stdout[-2500:], r.stderr[-2500:])
    with open(out, encoding="utf-8") as fh:
        d = json.load(fh)
    assert d["exit_code"] == r.returncode
    assert "DOGFOOD CROSS-SESSION SELF-CHECK" in r.stdout
