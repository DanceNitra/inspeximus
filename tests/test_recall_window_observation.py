"""The store watches recall -> write and throws one end away; `observe_recall` keeps it.

WHY THIS EXISTS. `_last_recall` has always held what the last recall served, and `remember()` has always
been able to see it — but only in memory, consumed and gone with the process. So the question "on writes
where the store observed a recall, how much of the true parent set does that free window already capture?"
is unanswerable on any store ever written. Measured on our own 8-agent deployment: `derived_from` filled on
0 of 181,523 records across eight live stores (2026-08-07), and not one carrying a window field of any
kind. There is nothing to replay, which makes this a BUILD before it can be a measurement.

THE DISTINCTION THE WHOLE THING RESTS ON. `derived_from` is a CLAIM of parentage and earns consequences —
taint inheritance, the orphan rule, the influence gate. `recall_window` is an OBSERVATION: the store served
these ids, at this time, before this write. True by construction, no threshold, no embedding, no model. It
must feed nothing. The moment a gate reads it, it stops being evidence and becomes a claim, and the
measurement it exists to enable would be measuring its own stamp. `test_the_observation_is_inert` is the
control for that and is the most important test in this file.

WHAT IS DELIBERATELY *NOT* DECIDED AT WRITE TIME. No age cutoff, no relevance filter, no classification of
which writes are "real". Each would be a parameter the later analysis could never reach past — a window
stamped only when it is under 60s old cannot answer what the window captures at 300s. That is the failure
we retracted a published Crucible verdict over (a detector whose threshold was clamped to [0,1] while its
score's discriminative region sat at -0.111). So the raw observation goes down and every cutoff stays in
the analysis, where it can be varied. `w` carries the one thing a write-time classifier would have been
used for, as data rather than as a decision.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus


def _store(**kw):
    return Inspeximus(path=os.path.join(tempfile.mkdtemp(), "m.json"), **kw)


def _rec(m, rid):
    return next(r for r in m.items if r["id"] == rid)


# ── the stamp fires, and carries what the measurement needs ──────────────────────────────────────

def test_a_write_after_a_recall_carries_the_window_it_was_served():
    m = _store(observe_recall=True)
    a = m.remember("postgres connection pooling uses pgbouncer")
    b = m.remember("pgbouncer runs in transaction mode here")

    served = [h["id"] for h in m.recall("pgbouncer", k=4)]
    assert a in served and b in served, "fixture no longer reproduces: the recall served neither record"

    w = m.remember("summary: pooling is pgbouncer in transaction mode")
    obs = _rec(m, w)["recall_window"]
    assert obs["ids"] == served, "the window must be the ids AS SERVED, in rank order"
    assert obs["at"] > 0 and obs["w"] == 0
    assert len(obs["q"]) == 12


def test_the_window_is_recorded_even_though_no_lineage_was_claimed():
    """The point of the field: coverage without a per-write declaration. `derived_from` stays empty."""
    m = _store(observe_recall=True)
    m.remember("kafka retention is 7 days")
    m.recall("kafka", k=4)
    w = m.remember("note: retention came up again")

    r = _rec(m, w)
    assert r.get("recall_window"), "the observation is the whole point"
    assert not r.get("derived_from"), "nothing was CLAIMED; observing must not manufacture a claim"
    assert not r.get("taint") and not r.get("orphan")


def test_w_separates_the_write_that_followed_the_recall_from_the_burst_after_it():
    """`w` is what a write-time classifier would have been used for, kept as data instead.

    One recall followed by four writes stamps w=0..3, so an analysis can restrict to w=0 without
    anyone having decided at write time which writes were derived.
    """
    m = _store(observe_recall=True)
    m.remember("redis evicts with allkeys-lru")
    m.recall("redis", k=4)
    ws = [m.remember(f"unrelated note {i}") for i in range(4)]

    assert [_rec(m, x)["recall_window"]["w"] for x in ws] == [0, 1, 2, 3]
    # ...and a fresh recall resets the counter, or w would measure store age instead of write position.
    m.recall("redis", k=4)
    assert _rec(m, m.remember("after the second recall"))["recall_window"]["w"] == 0


def test_age_is_recoverable_because_no_cutoff_was_applied_at_write_time():
    """A stale window is STAMPED, not dropped. Dropping it is the clamped-parameter mistake: the
    analysis could then never ask what the window captures at any horizon wider than the built-in one."""
    m = _store(observe_recall=True)
    m.remember("terraform state lives in s3")
    m.recall("terraform", k=4)
    m._last_recall_at -= 8 * 3600.0                  # the recall was eight hours ago

    r = _rec(m, m.remember("much later, an unrelated write"))
    assert r.get("recall_window"), "a stale window must still be recorded"
    age = r["ts"] - r["recall_window"]["at"]
    assert age > 7 * 3600.0, "the age must be recoverable by the analysis, which is why `at` is stored raw"


def test_no_recall_means_no_stamp_so_the_denominator_is_honest():
    m = _store(observe_recall=True)
    assert "recall_window" not in _rec(m, m.remember("a write with no recall before it"))


# ── reads that are not part of a write flow ──────────────────────────────────────────────────────

def test_a_non_observing_read_leaves_the_next_write_unstamped():
    """`observe=False` is for reads that are ABOUT the store, not FOR the writer: a scoring pass, a
    maintenance sweep, or one agent reading a colleague's store. The last is why this exists — it is
    unfilterable after the fact, because a foreign read resets both the window and the counter, so the
    colleague's next write is indistinguishable from one that followed its own recall."""
    m = _store(observe_recall=True)
    m.remember("a fact worth scoring")
    m.recall("scoring", k=4, observe=False)
    assert "recall_window" not in _rec(m, m.remember("a write that followed a foreign read"))


def test_a_non_observing_read_invalidates_rather_than_freezing_the_window():
    """The failure this avoids: keeping the previous recall's at/q/w while `_last_recall` moves on
    stamps one recall's ids with another recall's timestamp — a record that looks complete and is
    internally false, which is worse than no record at all."""
    m = _store(observe_recall=True)
    own = m.remember("the writer's own context")
    other = m.remember("something else entirely")
    m.recall("own context", k=1)
    stale_at = m._last_recall_at
    assert m._last_recall == [own], "fixture no longer reproduces: the first recall served the wrong record"
    m.recall("something else", k=1, observe=False)
    assert m._last_recall == [other], "fixture no longer reproduces: _last_recall did not move on"

    assert stale_at > 0.0 and m._last_recall_at == 0.0, "the window must be invalidated, not frozen"
    assert "recall_window" not in _rec(m, m.remember("a write after the foreign read")), (
        "a stamp here would pair the foreign recall's ids with the earlier recall's timestamp")


def test_a_non_observing_read_does_not_disturb_the_legacy_lineage_paths():
    """Control: `observe=False` must be an observability switch, not a behaviour change in disguise.
    `_last_recall` still drives derived=True exactly as before."""
    m = _store(observe_recall=True)
    p = m.remember("the parent record")
    m.recall("parent", k=4, observe=False)
    child = m.remember("a declared derivative", derived=True)
    assert _rec(m, child).get("derived_from") == [p], "derived=True must be untouched by observe="


def test_observation_resumes_after_a_non_observing_read():
    """Control for the three above: if invalidation were sticky, they would pass on a store where the
    feature had simply stopped working."""
    m = _store(observe_recall=True)
    m.remember("a fact")
    m.recall("fact", k=4, observe=False)
    m.recall("fact", k=4)
    assert _rec(m, m.remember("a write after a real recall"))["recall_window"]["w"] == 0


# ── the observation must stay an observation ─────────────────────────────────────────────────────

def test_the_observation_is_inert():
    """THE control for this feature. Turning observation on must change nothing a consumer can see
    except the new field: not recall order, not scores, not standing, not supersession. If this ever
    fails, `recall_window` has become a claim and the measurement is measuring its own stamp."""
    def run(**kw):
        m = _store(**kw)
        ids = [m.remember(f"service {i} owns queue {i}", key=f"svc::{i}", object=f"q{i}") for i in range(6)]
        m.recall("service queue", k=4)
        ids.append(m.remember("service 2 owns queue 9", key="svc::2", object="q9"))
        hits = m.recall("service queue", k=6)
        # Compared by WRITE POSITION, never by id: ids are random per store, so comparing them would
        # fail on every run and the control would be testing uuid4 rather than inertness.
        at = {rid: i for i, rid in enumerate(ids)}
        return ([(at[h["id"]], round(h["score"], 9)) for h in hits],
                [(at[r["id"]], r["status"], r.get("taint"), r.get("orphan"),
                  [at[x] for x in (r.get("links") or [])],
                  [at[x] for x in (r.get("derived_from") or [])]) for r in m.items])

    on, off = run(observe_recall=True), run(observe_recall=False)
    assert on == off, "observation changed recall order, scores, status, taint or lineage"
    # ...and the control: this comparison must be capable of failing. A run whose recall genuinely
    # differs has to come out unequal, or the assertion above proves nothing.
    assert run(observe_recall=True) != run(observe_recall=True, capacity=3)


def test_default_is_off_and_leaves_the_record_byte_identical():
    m = _store()
    m.remember("a")
    m.recall("a", k=4)
    assert "recall_window" not in _rec(m, m.remember("b"))
    assert m.observe_recall is False


def test_the_window_survives_a_round_trip_to_disk():
    """It is only evidence if it is still there tomorrow — the entire reason the field exists."""
    path = os.path.join(tempfile.mkdtemp(), "m.json")
    m = Inspeximus(path=path, observe_recall=True)
    m.remember("elasticsearch shards are sized at 30gb")
    m.recall("shards", k=4)
    w = m.remember("summary of the shard sizing")
    m.flush()

    obs = _rec(Inspeximus(path=path, observe_recall=True), w)["recall_window"]
    assert obs["ids"] and obs["at"] > 0 and obs["w"] == 0


# ── a multi-hop recall is ONE retrieval, so it gets ONE window ───────────────────────────────────

def test_an_iterative_recall_stamps_the_union_not_the_last_hop():
    """`recall_iterative` returns the union of every hop, but each internal recall() overwrites the
    window. Stamping the last hop would not merely lose ids — it would understate what the free window
    captures, silently and in a known direction, in the one metric this field exists to produce."""
    m = _store(observe_recall=True)
    a = m.remember("the cache is redis")
    m.remember("redis is deployed in cluster mode")
    c = m.remember("cluster mode needs a quorum of three")

    got = m.recall_iterative("the cache", ask_followup=lambda q, hits: ["quorum"], k=2, rounds=1)
    union = {h["id"] for h in got}
    assert {a, c} <= union and len(union) >= 2, "fixture no longer reproduces: no bridge happened"
    # the control that makes this test mean something: the last hop must be NARROWER than the union,
    # or last-hop and union would be the same answer and the assertion below could not fail.
    last_hop = {h["id"] for h in m.recall("quorum", k=2)}
    assert last_hop < union, "fixture no longer reproduces: the last hop already equals the union"

    m.recall_iterative("the cache", ask_followup=lambda q, hits: ["quorum"], k=2, rounds=1)
    obs = _rec(m, m.remember("summary built from the whole bridge"))["recall_window"]
    assert set(obs["ids"]) == union
    assert obs["w"] == 0, "the follow-up hops are not writes and must not advance the counter"


def test_the_two_phase_surface_stamps_round_one_plus_the_bridge():
    """The MCP/CLI surface. `merged` is round-1 plus what the bridge added — and round-1 is what the
    caller is most likely writing from, so dropping it would be the worst version of the same bug."""
    m = _store(observe_recall=True)
    m.remember("the queue is kafka")
    m.remember("kafka retention is seven days")
    m.remember("seven days exceeds the audit window")

    start = m.recall_iterative_start("the queue", k=2)
    prior = list(start["prior_ids"])
    assert prior, "fixture no longer reproduces: round 1 returned nothing"
    out = m.recall_iterative_followup("the queue", followups=["audit window"],
                                      prior_ids=prior, k=2)
    assert out["bridged"] > 0, "fixture no longer reproduces: the follow-up added nothing"

    obs = _rec(m, m.remember("summary across both rounds"))["recall_window"]
    assert set(obs["ids"]) == set(out["merged_ids"])
    assert set(prior) <= set(obs["ids"]), "round-1 records must survive into the window"


# ── the cap, which must never delete evidence silently ───────────────────────────────────────────

def test_a_capped_window_reports_its_true_width():
    """A cap that quietly drops the tail deletes exactly the evidence half an audit would need, and a
    truncated window would read as a genuinely narrow recall. `n` is what makes the cut visible."""
    m = _store(observe_recall=True)
    m._recall_window_max = 5
    for i in range(20):
        m.remember(f"metric {i} is scraped by prometheus")
    served = [h["id"] for h in m.recall("prometheus scraped", k=20)]
    assert len(served) == 20, "fixture no longer reproduces: the recall did not exceed the cap"

    obs = _rec(m, m.remember("summary of scraping"))["recall_window"]
    assert obs["ids"] == served[:5]
    assert obs["n"] == 20, "the true width must survive the cap"


def test_an_uncapped_window_carries_no_width_field():
    """The control for the test above: `n` present always would make it meaningless as a cut signal."""
    m = _store(observe_recall=True)
    m._recall_window_max = 5
    m.remember("only one record")
    m.recall("record", k=20)
    assert "n" not in _rec(m, m.remember("summary"))["recall_window"]


# ── erasure: kept as history, and VISIBLE to the audit ───────────────────────────────────────────

def test_forget_keeps_the_window_because_scrubbing_history_hides_the_erasure():
    """Same policy forget() already applies to derived_from/taint: behaviour-gating pointers are
    dropped, history fields are kept, because scrubbing them makes erasure_audit read clean."""
    m = _store(observe_recall=True)
    doomed = m.remember("customer alice lives at 5 elm st")
    m.recall("alice", k=4)
    w = m.remember("summary about a customer")
    assert doomed in _rec(m, w)["recall_window"]["ids"], "fixture no longer reproduces"

    m.forget([doomed])
    assert doomed in _rec(m, w)["recall_window"]["ids"], "history must survive, or the audit sees nothing"


def test_erasure_audit_reports_a_window_pointing_at_an_erased_record():
    """The reason the field could not ship without this. `recall_window.ids` is a NEW pointer channel
    between records; erasure_audit walked only derived_from, so a dangling window id would have been
    residue that reports as no residue."""
    m = _store(observe_recall=True)
    doomed = m.remember("customer bob lives at 9 oak ave", source={"doc": "hr/bob"})
    m.recall("bob", k=4)
    w = m.remember("summary about another customer")
    m.forget([doomed], request_id="rtbf-1", basis="erasure request")

    audit = m.erasure_audit()
    found = [f for f in audit["residue"] if f["kind"] == "dangling_recall_window"]
    assert [f["id"] for f in found] == [w]
    assert doomed in found[0]["detail"]
    # An id that was DELIBERATELY erased is residue, not an eviction — so it must move the verdict,
    # not sit in the advisory bucket where an operator reads it as housekeeping.
    assert audit["verdict"] == "residue_found"
    assert not [f for f in audit["advisory"] if f["kind"] == "dangling_recall_window"]


def test_a_window_whose_ids_all_survive_raises_nothing():
    """Control for the test above: without it, a check that fires on every store would prove nothing."""
    m = _store(observe_recall=True)
    m.remember("nothing here gets erased")
    m.recall("erased", k=4)
    m.remember("a summary")
    audit = m.erasure_audit()
    assert not [f for f in audit["residue"] + audit["advisory"]
                if f["kind"] == "dangling_recall_window"]


def test_the_audit_counts_observation_apart_from_declared_lineage():
    """Folding the two together would report lineage the store never declared — and on a store with
    observe_recall on, the observation count is the larger number by orders of magnitude."""
    m = _store(observe_recall=True)
    m.remember("a fact")
    m.recall("fact", k=4)
    m.remember("a write that observed a recall but declared nothing")

    cov = m.erasure_audit()["coverage"]
    assert cov["with_recall_window"] == 1
    assert cov["with_declared_lineage"] == 0 and cov["declared_ratio"] == 0.0
