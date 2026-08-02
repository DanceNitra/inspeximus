"""The two-phase `recall_iterative` surface: it reaches the bridge, it stays bounded, and it does not lie.

WHY THIS FILE EXISTS. `recall_iterative()` is the one retrieval lever our lab measured to move multi-hop
recall, and it took a Python CALLABLE — so it was reachable from the library and from none of the 56 MCP
tools or 25 CLI subcommands, which is where every user and every agent actually stands. The surface added
alongside these tests inverts it into two stateless calls. This file asserts the three things that make that
inversion worth shipping:

  1. IT REACHES THE BRIDGE, with the control that makes the claim falsifiable: the fixture is rejected if a
     single recall(k=6) already retrieves the bridge record, because then the measurement cannot see the
     lever at all. And the reverse — on a single-hop question the iterative path must not do worse.
  2. IT IS BOUNDED. `mcp_server.py` carries one surface at O(n^2) / 162 s (n=8,000) and another that
     serialises ~150 MB of JSON-RPC at n=2,000. The bound here is asserted, not described: exact recall-call
     counts, an exact record ceiling, and payload size measured at two store sizes an order of magnitude
     apart to show it does not move with n.
  3. IT IS FAITHFUL. The two-call sequence must return the SAME merged set as the in-process
     recall_iterative() given the same follow-ups. An inversion that quietly retrieves something else would
     pass every other test in this file.

The fixture and the gold-blind mechanical reader come from `probes/recall_iterative_surface_multihop.py` —
ONE definition, imported here rather than restated, because two copies of a fixture is how a control ends up
unable to see a defect that lives in both.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from inspeximus import Inspeximus  # noqa: E402


def _probe():
    """The probe module, loaded by path — `probes/` is not a package."""
    p = os.path.join(ROOT, "probes", "recall_iterative_surface_multihop.py")
    spec = importlib.util.spec_from_file_location("ri_probe", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PROBE = _probe()
K, MF = 6, 3


@pytest.fixture(scope="module")
def fixture():
    m, items = PROBE.build_synthetic(n_items=8, n_filler=60)
    return m, items


# ── 1. the measurement, and the control that makes it able to fail ───────────────────────────────────
def test_a_single_recall_does_NOT_already_reach_the_bridge(fixture):
    """THE CONTROL. If plain recall(k=6) already returns the bridge record, the fixture is too easy and
    every "the iterative path found it" assertion below is vacuous — it would pass against a surface that
    did nothing at all. This must fail loudly rather than be quietly excluded: the fixture is ours, so a
    leak here is a defect to fix, not a row to drop from a denominator."""
    m, items = fixture
    leaked = [it["question"] for it in items
              if it["bridge_text"] in {h.get("text", "") for h in
                                       (m.recall(it["question"], k=K, reinforce=False) or [])}]
    assert not leaked, ("the fixture is too easy — a single recall already returns the bridge record for "
                        f"{len(leaked)}/{len(items)} questions, so this file cannot detect the lever: {leaked}")


def test_the_two_call_sequence_reaches_a_record_a_single_recall_misses(fixture):
    """THE MEASUREMENT. Rate, not anecdote, and the reader is gold-blind (see the probe's docstring): it
    sees the question and round-1 only, exactly what a caller's model sees."""
    m, items = fixture
    bridged = 0
    for it in items:
        start = m.recall_iterative_start(it["question"], k=K, max_followups=MF, reinforce=False)
        fups = PROBE.mechanical_reader(it["question"], start["hits"], MF)
        nxt = m.recall_iterative_followup(it["question"], followups=fups,
                                          prior_ids=start["prior_ids"], k=K, max_followups=MF,
                                          reinforce=False)
        bridged += int(it["bridge_text"] in {h.get("text", "") for h in nxt["new_hits"]})
    assert bridged == len(items), f"the two-call sequence reached the bridge on only {bridged}/{len(items)}"


def test_a_single_hop_question_does_not_regress(fixture):
    """THE REVERSE CONTROL. The lever must not cost anything where it is not needed: the records the caller
    ends up holding must be a SUPERSET of plain recall's, and the answer record must still be among them.

    Asserted as a set relation rather than by re-listing round-1, because that is the property that stays
    true if the merge is ever rewritten — and it is the property a caller relies on when they turn this on
    by default."""
    m, items = fixture
    for it in items:
        q = it["single_hop_question"]
        plain = m.recall(q, k=K, reinforce=False) or []
        start = m.recall_iterative_start(q, k=K, max_followups=MF, reinforce=False)
        nxt = m.recall_iterative_followup(q, followups=PROBE.mechanical_reader(q, start["hits"], MF),
                                          prior_ids=start["prior_ids"], k=K, max_followups=MF,
                                          reinforce=False)
        merged = set(nxt["merged_ids"])
        assert {h["id"] for h in plain} <= merged, f"the iterative path LOST a plain-recall hit for {q!r}"
        by_id = {r["id"]: r.get("text", "") for r in m.items}
        assert it["single_hop_text"] in {by_id[i] for i in merged if i in by_id}, \
            f"the single-hop answer is no longer reachable for {q!r}"


# ── 2. the bounds, asserted rather than described ────────────────────────────────────────────────────
def _counted(m):
    """Wrap recall() so the WORK is counted exactly. Run-to-run wall clock on this box moves 15-40%, so a
    timing gate on a difference this size is unbuildable; the call count is exact at any store size and is
    what determines the runtime, so it is what the bound is asserted on."""
    calls = {"n": 0}
    raw = m.recall

    def counted(*a, **kw):
        calls["n"] += 1
        return raw(*a, **kw)

    m.recall = counted
    return calls


def test_phase_1_costs_exactly_one_retrieval_and_at_most_k_records(fixture):
    m, items = fixture
    calls = _counted(m)
    try:
        res = m.recall_iterative_start(items[0]["question"], k=K, max_followups=MF, reinforce=False)
    finally:
        del m.recall
    assert calls["n"] == 1, f"phase 1 made {calls['n']} retrievals, not 1"
    assert len(res["hits"]) <= K and len(res["prior_ids"]) == len(res["hits"])
    assert res["bounds"] == {"recall_calls": 1, "max_records": K, "independent_of_store_size": True}


def test_phase_2_never_exceeds_max_followups_retrievals(fixture):
    """The cost ceiling is `max_followups`, and a caller (or a model) that emits more must not be able to
    buy more retrievals by asking for them. The surplus is DROPPED and REPORTED — a silently truncated list
    is the failure mode this codebase already wrote up on `contradictions`."""
    m, items = fixture
    calls = _counted(m)
    try:
        res = m.recall_iterative_followup(items[0]["question"],
                                          followups=[f"query {i}" for i in range(25)],
                                          prior_ids=[], k=K, max_followups=MF, reinforce=False)
    finally:
        del m.recall
    assert calls["n"] == MF, f"phase 2 made {calls['n']} retrievals with max_followups={MF}"
    assert len(res["followups_used"]) == MF and res["followups_dropped"] == 22
    assert len(res["new_hits"]) <= K * MF


def test_max_followups_is_capped_even_when_the_caller_asks_for_more():
    """CONTROL for the test above: a ceiling the caller can raise is not a ceiling. 8 is the hard cap."""
    m = Inspeximus(path=os.path.join(os.environ.get("TEMP", "/tmp"), f"ri_cap_{time.time_ns()}.json"))
    for i in range(40):
        m.remember(f"record number {i} about topic {i % 5}")
    calls = _counted(m)
    try:
        res = m.recall_iterative_followup("topic", followups=[f"topic {i}" for i in range(50)],
                                          prior_ids=[], k=K, max_followups=999, reinforce=False)
    finally:
        del m.recall
    assert Inspeximus.MAX_FOLLOWUPS == 8
    assert calls["n"] == 8, f"a caller raised the retrieval ceiling to {calls['n']}"
    assert res["bounds"]["max_recall_calls"] == 8


def test_the_response_does_not_grow_with_the_store():
    """THE BOUND THAT MATTERS, and the one the two surfaces this file cites did not have: response size is
    a function of (k, max_followups) and NOT of n. Measured at two store sizes 16x apart.

    A wall-clock assertion is deliberately absent as a *performance* gate and present only as a BLOWUP
    detector: the ceiling below sits ~2 orders of magnitude above the measured time, so it cannot flake on a
    loaded box, but an O(n^2) regression of the kind documented at mcp_server.py:952 (162 s at n=8,000)
    would still trip it."""
    sizes, payloads = (250, 4000), {}
    for n in sizes:
        m, items = PROBE.build_synthetic(n_items=8, n_filler=n)
        q = items[0]["question"]
        t0 = time.perf_counter()
        start = m.recall_iterative_start(q, k=K, max_followups=MF, reinforce=False)
        nxt = m.recall_iterative_followup(q, followups=PROBE.mechanical_reader(q, start["hits"], MF),
                                          prior_ids=start["prior_ids"], k=K, max_followups=MF,
                                          reinforce=False)
        payloads[n] = {"bytes": len(json.dumps(start, default=str)) + len(json.dumps(nxt, default=str)),
                       "records": len(start["hits"]) + len(nxt["new_hits"]),
                       "seconds": time.perf_counter() - t0}
    small, big = payloads[sizes[0]], payloads[sizes[1]]
    assert big["records"] <= K * (1 + MF), f"record ceiling breached: {big['records']} > {K * (1 + MF)}"
    assert big["bytes"] <= 1.10 * small["bytes"], (
        f"the payload grew with the store: {small['bytes']} bytes at n={sizes[0]} -> "
        f"{big['bytes']} at n={sizes[1]}")
    assert big["bytes"] < 64_000, f"payload {big['bytes']} bytes exceeds the stated 64 KB ceiling"
    assert big["seconds"] < 20.0, f"two-phase pair took {big['seconds']:.2f}s — suspect a complexity regression"


# ── 3. fidelity: the inversion must retrieve what the in-process lever retrieves ──────────────────────
@pytest.mark.parametrize("reinforce", [False, True], ids=["reinforce off", "reinforce on"])
def test_the_two_phase_pair_matches_the_in_process_recall_iterative(reinforce):
    """The whole justification for the surface is that it IS `recall_iterative`, reachable from a client
    that cannot pass a callable. So the merged set must match exactly, given the same follow-ups.

    Run on two stores built identically, because recall() reinforces by default and each arm would otherwise
    re-rank the other; the reinforce=True arm is the one that would catch an inversion whose extra or
    reordered retrievals move value as a side effect."""
    fups = ["Priya Raman", "Tomas Vlk"]
    a, items = PROBE.build_synthetic(n_items=8, n_filler=60)
    b, _ = PROBE.build_synthetic(n_items=8, n_filler=60)
    q = items[0]["question"]

    in_process = a.recall_iterative(q, lambda _q, _r: fups, k=K, rounds=1, reinforce=reinforce)
    start = b.recall_iterative_start(q, k=K, max_followups=MF, reinforce=reinforce)
    nxt = b.recall_iterative_followup(q, followups=fups, prior_ids=start["prior_ids"], k=K,
                                      max_followups=MF, reinforce=reinforce)

    assert {r["text"] for r in in_process} == {h["text"] for h in start["hits"]} | \
                                              {h["text"] for h in nxt["new_hits"]}
    assert len(nxt["merged_ids"]) == len(in_process)


def test_omitting_prior_ids_is_not_silently_compensated_for(fixture):
    """The documented caller error, pinned. Without `prior_ids` every follow-up hit is reported as new —
    including records round 1 already returned. The alternative (a hidden extra round-1 retrieval inside
    phase 2) would silently double the work bound this file asserts, so the honest behaviour is the one
    that ships and this test stops it drifting into the convenient one."""
    m, items = fixture
    calls = _counted(m)
    try:
        res = m.recall_iterative_followup(items[0]["question"], followups=[items[0]["entity"]],
                                          prior_ids=None, k=K, max_followups=MF, reinforce=False)
    finally:
        del m.recall
    assert calls["n"] == 1, "phase 2 ran a hidden round-1 retrieval"
    assert res["merged_ids"] == [h["id"] for h in res["new_hits"]]


def test_blank_and_non_string_followups_are_dropped_not_retrieved(fixture):
    """A model emits junk. Each junk entry must cost ZERO retrievals, not one that matches everything."""
    m, items = fixture
    calls = _counted(m)
    try:
        res = m.recall_iterative_followup(items[0]["question"],
                                          followups=["", "   ", None, 7, items[0]["entity"]],
                                          prior_ids=[], k=K, max_followups=MF, reinforce=False)
    finally:
        del m.recall
    assert calls["n"] == 1 and res["followups_used"] == [items[0]["entity"]]


# ── the surfaces themselves ──────────────────────────────────────────────────────────────────────────
def test_the_mcp_tools_exist_and_carry_the_loop(monkeypatch, tmp_path):
    """Through the MCP module, not the library — the whole point of this unit is the surface. Asserts the
    two-phase contract a client depends on: round 1 hands back a continuation token and an instruction, and
    phase 2 turns the client's answer into the record round 1 did not have."""
    pytest.importorskip("mcp")
    import importlib
    monkeypatch.setenv("INSPEXIMUS_PATH", str(tmp_path / "mcp.json"))
    monkeypatch.delenv("INSPEXIMUS_RECEIPTS", raising=False)
    m = importlib.reload(importlib.import_module("inspeximus.mcp_server"))

    m.remember("Priya Raman signed off on the marketing spend review")
    m.remember("Priya Raman relocated to Lisbon in April")
    for i in range(30):
        m.remember(f"the marketing spend review note {i} was filed by the finance desk")

    q = "which city does the person who signed off on the marketing spend review work from"
    plain = m.recall(q, k=6)
    assert not any("Lisbon" in h["text"] for h in plain), \
        "CONTROL: plain recall already has the bridge, so this test proves nothing"

    start = m.recall_iterative(q, k=6)
    assert start["prior_ids"] and start["ask"] and start["round"] == 1
    assert len(start["hits"]) <= 6
    nxt = m.recall_followup(q, followups=["Priya Raman"], prior_ids=start["prior_ids"], k=6)
    assert any("Lisbon" in h["text"] for h in nxt["new_hits"]), nxt
    assert nxt["bridged"] >= 1 and nxt["recall_calls"] == 1
    json.dumps(start, default=str), json.dumps(nxt, default=str)      # must cross JSON-RPC


def test_the_cli_runs_both_phases(tmp_path):
    """Through `python -m inspeximus.cli`, as a user has it."""
    s = tmp_path / "s.json"

    def cli(*args):
        env = {**os.environ, "PYTHONPATH": ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""),
               "PYTHONIOENCODING": "utf-8"}
        r = subprocess.run([sys.executable, "-m", "inspeximus.cli", "--path", str(s), *args],
                           cwd=ROOT, capture_output=True, text=True, env=env, timeout=300)
        assert r.returncode == 0, f"CLI {args} failed: {r.stderr[-500:]}"
        return r.stdout

    cli("remember", "Priya Raman signed off on the marketing spend review")
    cli("remember", "Priya Raman relocated to Lisbon in April")
    for i in range(20):
        cli("remember", f"the marketing spend review note {i} was filed by the finance desk")

    q = "which city does the person who signed off on the marketing spend review work from"
    assert "Lisbon" not in cli("recall", q, "-k", "6"), "CONTROL: plain recall already reaches the bridge"

    phase1 = cli("recall-iterative", q, "-k", "6")
    assert "ask your model:" in phase1 and "--prior-id" in phase1
    assert "Lisbon" not in phase1

    both = cli("recall-iterative", q, "-k", "6", "--followup", "Priya Raman")
    assert "+ Priya Raman relocated to Lisbon in April" in both


def test_the_cli_emits_ONE_json_envelope_for_all_three_modes(tmp_path):
    """A script consuming `--json` must not have to guess which of three shapes arrived. Phase 1 used to
    return a flat dict and the other two a wrapper, so "phase 2 only" and a malformed payload looked alike."""
    s = tmp_path / "s.json"

    def cli(*args, expect=0):
        env = {**os.environ, "PYTHONPATH": ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""),
               "PYTHONIOENCODING": "utf-8"}
        r = subprocess.run([sys.executable, "-m", "inspeximus.cli", "--path", str(s), *args],
                           cwd=ROOT, capture_output=True, text=True, env=env, timeout=300)
        assert r.returncode == expect, f"{args} -> {r.returncode}: {r.stderr[-400:]}"
        return r

    cli("remember", "Priya Raman signed off on the marketing spend review")
    cli("remember", "Priya Raman relocated to Lisbon in April")
    q = "which city does the person who signed off on the marketing spend review work from"

    one = json.loads(cli("--json", "recall-iterative", q).stdout)
    both = json.loads(cli("--json", "recall-iterative", q, "--followup", "Priya Raman").stdout)
    only2 = json.loads(cli("--json", "recall-iterative", q, "--followup", "Priya Raman",
                           "--prior-id", one["round_1"]["prior_ids"][0]).stdout)
    for payload in (one, both, only2):
        assert set(payload) == {"mode", "round_1", "followup"}, payload
    assert (one["mode"], both["mode"], only2["mode"]) == ("phase1", "both", "phase2")
    assert one["followup"] is None and only2["round_1"] is None


def test_the_cli_refuses_a_negative_followup_cap(tmp_path):
    """CONTROL on the clamp. A negative cap used to be accepted, silently clamped to 0, and then reported
    back as if it were the ceiling in force — an invalid request answered with a coherent-looking number."""
    s = tmp_path / "s.json"
    env = {**os.environ, "PYTHONPATH": ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""),
           "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, "-m", "inspeximus.cli", "--path", str(s),
                        "recall-iterative", "q", "--max-followups", "-5"],
                       cwd=ROOT, capture_output=True, text=True, env=env, timeout=300)
    assert r.returncode == 2 and "must be >= 0" in r.stderr, (r.returncode, r.stderr[-300:])


def test_a_tenant_view_cannot_reach_another_tenants_bridge():
    """The inversion runs the same recall() the rest of the surface does, so it must be tenant-scoped like
    it. Reached through _TenantView.__getattr__ instead of being bound, these would run PARENT-bound and
    read every tenant's records — the exact defect the structural isolation suite was written for."""
    p = os.path.join(os.environ.get("TEMP", "/tmp"), f"ri_tenant_{time.time_ns()}.json")
    s = Inspeximus(path=p)
    a, b = s.for_tenant("acme"), s.for_tenant("globex")
    a.remember("acme owns the blue warehouse")
    b.remember("GLOBEX_SECRET sk-globex-999 lives in the vault")

    start = a.recall_iterative_start("GLOBEX_SECRET vault", k=6)
    nxt = a.recall_iterative_followup("GLOBEX_SECRET vault", followups=["GLOBEX_SECRET", "vault"],
                                      prior_ids=start["prior_ids"], k=6)
    assert "sk-globex-999" not in json.dumps([start, nxt], default=str)
    assert "sk-globex-999" in json.dumps(b.recall_iterative_start("GLOBEX_SECRET vault", k=6), default=str)
