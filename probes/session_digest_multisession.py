#!/usr/bin/env python3
"""Does the cross-session loop actually carry a session's conclusions into the next session -- and does
it REFUSE to carry the rest? Measured on a realistic multi-session store, not a happy path.

WHY THIS SHAPE. A digest that injects everything scores 100% recall and has reinvented "paste the whole
log", which is the thing a context window already does badly. So every number here is reported with its
control:

  recall@+1            a salient item established in session i appears in session i+1's injection
  rejection            a BELOW-THRESHOLD item (shell commands, file states, chit-chat, plain facts)
                       does NOT appear
  rejection @ thr=0    the NEGATIVE CONTROL. With the salience bar removed the same noise MUST get in.
                       If it does not, the noise never reached the candidate pool and the rejection
                       number above measured nothing at all.
  stale injection      a decision REVERSED in a later session must not survive in the injected context
  bound                every injected block <= its declared max_chars
  off switch           disabled -> no injection AND the store's state_digest is unchanged by SessionEnd
  determinism          two independently-built stores replaying the SAME event log render a
                       BYTE-IDENTICAL digest. This is the zero-LLM claim in falsifiable form.
  findability          recall("what changed last session", k=1) returns the current digest, in a store
                       of >=2500 records (our own dogfood failed at ~2550, so a smaller fixture cannot
                       reproduce the problem this unit exists to fix)
  loop value           at least one carried-over fact is in the injection and NOT in a plain recall
                       top-5. If the injected set is a subset of the recall top-5, the loop adds
                       nothing and this unit has no reason to exist.

Run:  python probes/session_digest_multisession.py
Exit: 0 = every gate held; 1 = a gate failed (the gates are pre-registered below in GATES).
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inspeximus import Inspeximus  # noqa: E402

SESSIONS = 8
FILLER_PER_SESSION = 320          # -> ~2560 records, past the ~2550 where our own dogfood recall failed
MAX_CHARS = 1200
MAX_SESSIONS = 3                  # how many past sessions the injection draws from

# Pre-registered gates. Stated before the run so a disappointing number is a finding, not a knob.
GATES = {
    "recall_at_plus_1": 0.90,      # >= this fraction of a session's conclusions reach the next session
    "rejection": 0.99,             # >= this fraction of below-threshold items stay out
    "rejection_at_threshold_0": 0.50,   # MUST be BELOW this: the control has to break the guard
    "stale_injected": 0,           # exactly zero reversed decisions injected
}


# ── the scripted event log (replayable, so two stores can be built from the same script) ──────────────
def _plan():
    """One list of (session_index, kind, payload). Pure data: replaying it twice must give the same
    digest, which is what the determinism gate checks."""
    ev = []
    for s in range(1, SESSIONS + 1):
        ev.append((s, "boundary", None))
        # --- planted SALIENT items (the things a next session must know) ---
        if s == 1:
            ev.append((s, "decision", ("use Postgres for the ledger", "sqlite locks under concurrent "
                                       "writers marker-p01", "db::engine")))
            ev.append((s, "decision", ("authenticate with OIDC, not shared API keys",
                                       "key rotation is manual marker-p02", "auth::method")))
            ev.append((s, "thread", "whether to shard by tenant or by time marker-p03"))
        if s == 2:
            ev.append((s, "decision", ("pin the embedder recipe in the sidecar",
                                       "a silent recipe change unranks the index marker-p04",
                                       "index::recipe")))
        if s == 3:
            # a CORRECTION: retires the value planted as noise in session 1
            ev.append((s, "fact", ("retry budget is 3 attempts marker-p05", "cfg::retry", "3", None)))
            ev.append((s, "knowledge", "the integration suite needs a live embedder marker-p06"))
        if s == 4:
            ev.append((s, "decision", ("ship the CLI before the hosted tier",
                                       "distribution is the bottleneck marker-p07", "roadmap::order")))
        if s == 5:
            # REVERSES session 1's db decision -> the old one must stop being injected
            ev.append((s, "decision", ("use CockroachDB for the ledger",
                                       "we need multi-region writes marker-p08", "db::engine")))
        if s == 6:
            ev.append((s, "thread", "whether to keep the lexical fallback marker-p09"))
            ev.append((s, "decision", ("evict the cache LRU", "simplest thing that works marker-p12",
                                       "cache::policy")))
        if s == 7:
            ev.append((s, "knowledge", "the hub pass fires only above 50 active records marker-p10"))
            # REVERSES session 6, one session back -- inside the default 3-session injection window,
            # so the "no stale decision" control is aimed at something that is genuinely in the pool
            ev.append((s, "decision", ("evict the cache LFU", "hot keys were being dropped marker-p13",
                                       "cache::policy")))
        if s == 8:
            ev.append((s, "decision", ("cap the injected block at 1200 chars",
                                       "a bigger block is just the log again marker-p11",
                                       "digest::bound")))
        # --- planted BELOW-THRESHOLD items (must never be injected) ---
        if s == 1:
            # a plain keyed fact: documented as below the bar until something CORRECTS it
            ev.append((s, "fact", ("retry budget is 5 attempts marker-n1", "cfg::retry", "5", None)))
        ev.append((s, "chat", f"the coffee machine on floor 2 is broken again marker-n2-{s}"))
        ev.append((s, "chat", f"standup moved to 10:15 this week marker-n3-{s}"))
        # --- realistic session exhaust: file states + shell commands ---
        for i in range(FILLER_PER_SESSION):
            if i % 3 == 0:
                ev.append((s, "bash", f"pytest tests/test_mod{i}.py -q marker-n4-{s}-{i}"))
            else:
                ev.append((s, "file", (f"src/pkg/mod{i}.py",
                                       f"def f{i}(): return {i}  # marker-n5-{s}-{i}")))
        ev.append((s, "close", None))
    return ev


def _replay(store, plan, upto_session=None, threshold=None, close_bound=MAX_CHARS,
            close_items=12):
    """Apply the scripted log to a store. `upto_session` stops after that session's close, so the same
    script can be measured at every session boundary."""
    sid = None
    for s, kind, payload in plan:
        if upto_session is not None and s > upto_session:
            break
        sid = f"sess-{s}"
        if kind == "boundary":
            store.open_session(sid)
        elif kind == "close":
            store.close_session(sid, max_chars=close_bound, threshold=threshold,
                                max_items=close_items)
        elif kind == "decision":
            text, because, topic = payload
            store.remember_decision(text, because=because, topic=topic, session_id=sid)
        elif kind == "fact":
            text, key, obj, _ = payload
            store.remember(text, key=key, object=obj, mtype="semantic", session_id=sid)
        elif kind == "knowledge":
            store.remember(payload, tags=["knowledge"], mtype="semantic", session_id=sid)
        elif kind == "thread":
            store.remember(payload, tags=["open"], mtype="semantic", session_id=sid)
        elif kind == "chat":
            store.remember(payload, mtype="episodic", session_id=sid)
        elif kind == "bash":
            store.remember("ran: " + payload, key="cmd:" + payload[-24:], object=payload[:60],
                           mtype="episodic", tags=["bash"], session_id=sid)
        elif kind == "file":
            fp, content = payload
            store.remember(f"{fp} :: current state -> {content}", key="file:" + fp,
                           object=content[:80], mtype="semantic", tags=["file", "edit"],
                           session_id=sid)
    return sid


def _markers(plan, session, salient=True):
    """The distinctive tokens planted in `session`, split by whether they are supposed to cross the
    boundary. Matching on a planted token (not on prose) is what makes 'it appeared' checkable."""
    want = {"decision", "thread", "knowledge", "fact"} if salient else {"chat", "bash", "file", "fact"}
    out = []
    for s, kind, payload in plan:
        if s != session or kind not in want:
            continue
        blob = " ".join(str(x) for x in (payload if isinstance(payload, tuple) else (payload,)))
        for tok in blob.split():
            t = tok.strip("(),.")
            if not t.startswith("marker-"):
                continue
            is_noise = t.startswith("marker-n")
            if salient and not is_noise:
                out.append(t)
            elif not salient and is_noise:
                out.append(t)
    return sorted(set(out))


def _new_store():
    return Inspeximus(path=None)


def main() -> int:
    plan = _plan()
    lines, failures = [], []

    def gate(name, ok, detail):
        lines.append(f"{'PASS' if ok else 'FAIL'}  {name:34s} {detail}")
        if not ok:
            failures.append(name)

    # ── build once, measure at every boundary ────────────────────────────────────────────────────────
    t0 = time.time()
    store = _new_store()
    _replay(store, plan)
    build_s = time.time() - t0
    n_records = len(store.items)
    n_active = sum(1 for r in store.items if r.get("status") == "active")

    # recall@+1 / rejection / bound, evaluated at every session boundary
    hit = miss = 0
    rej_ok = rej_leak = 0
    bound_violations = []
    per_session = []
    for i in range(1, SESSIONS):
        s2 = _new_store()
        _replay(s2, plan, upto_session=i)
        ctx = s2.session_context(max_sessions=MAX_SESSIONS, max_chars=MAX_CHARS)
        text = ctx["text"]
        if len(text) > MAX_CHARS:
            bound_violations.append((i, len(text)))
        want = _markers(plan, i, salient=True)
        got = [t for t in want if t in text]
        hit += len(got)
        miss += len(want) - len(got)
        noise = []
        for j in range(max(1, i - MAX_SESSIONS + 1), i + 1):
            noise += _markers(plan, j, salient=False)
        leaked = [t for t in noise if t in text]
        rej_ok += len(noise) - len(leaked)
        rej_leak += len(leaked)
        per_session.append((i, len(got), len(want), len(leaked), len(noise), len(text)))

    recall1 = hit / (hit + miss) if (hit + miss) else 0.0
    rejection = rej_ok / (rej_ok + rej_leak) if (rej_ok + rej_leak) else 0.0

    lines.append(f"store: {n_records} records ({n_active} active) across {SESSIONS} sessions, "
                 f"built in {build_s:.1f}s")
    lines.append("per-session injection (session -> carried/expected, leaked/noise, chars):")
    for i, g, w, lk, nz, ch in per_session:
        lines.append(f"    s{i}->s{i + 1}: {g}/{w} carried, {lk}/{nz} leaked, {ch} chars")
    gate("recall@+1", recall1 >= GATES["recall_at_plus_1"],
         f"{recall1:.3f} ({hit}/{hit + miss} conclusions reached the next session), gate >= "
         f"{GATES['recall_at_plus_1']}")
    gate("rejection (below threshold)", rejection >= GATES["rejection"],
         f"{rejection:.4f} ({rej_ok}/{rej_ok + rej_leak} below-threshold items kept out), gate >= "
         f"{GATES['rejection']}")
    gate("size bound", not bound_violations,
         f"every block <= {MAX_CHARS} chars" if not bound_violations else f"violations: {bound_violations}")

    # ── NEGATIVE CONTROL: drop the salience bar and the same noise must flood in ─────────────────────
    # AIMED AT THE RIGHT END. The first version of this control lowered the threshold only at INJECTION
    # time and measured 0/967 noise admitted -- it looked like a pass for the guard and was in fact a
    # control that could never see its target: session_context() ranks the entries a digest ALREADY
    # holds, and those were selected at close time. Whatever the close-time bar excluded is not in the
    # pool to be re-admitted later. Lowering the bar at BOTH ends is what exposes the noise, and it also
    # documents which threshold is load-bearing: the one in close_session().
    noise3 = []
    for j in range(1, 4):
        noise3 += _markers(plan, j, salient=False)
    # BOTH limits lifted at the close end, not just the threshold. The second version of this control
    # still read 0.9845 because close_session's max_chars (1200) truncated the digest at ~15 entries
    # long before the salience bar was reached -- a control blocked by a DIFFERENT guard than the one it
    # is aimed at is still a control that measures nothing about its target.
    s0 = _new_store()
    _replay(s0, plan, upto_session=3, threshold=0.0, close_bound=400000, close_items=100000)
    ctx0 = s0.session_context(max_sessions=MAX_SESSIONS, max_chars=400000, max_items=100000,
                              threshold=0.0)
    leaked0 = [t for t in noise3 if t in ctx0["text"]]
    rej0 = (len(noise3) - len(leaked0)) / len(noise3) if noise3 else 1.0
    # ...and the SAME store shape at the default bar, so the two numbers differ only by the threshold
    s0d = _new_store()
    _replay(s0d, plan, upto_session=3)
    ctxd = s0d.session_context(max_sessions=MAX_SESSIONS, max_chars=400000, max_items=100000)
    leaked_d = [t for t in noise3 if t in ctxd["text"]]
    rej_d = (len(noise3) - len(leaked_d)) / len(noise3) if noise3 else 1.0
    gate("negative control (threshold=0)", rej0 < GATES["rejection_at_threshold_0"],
         f"rejection collapses {rej_d:.4f} -> {rej0:.4f} ({len(leaked0)}/{len(noise3)} noise items get "
         f"in with the bar removed, {len(leaked_d)} with it) -- the threshold is what does the work, "
         f"gate < {GATES['rejection_at_threshold_0']}")

    # ── a REVERSED decision must not survive in the injected context ─────────────────────────────────
    # THE CONTROL HAS TO BE INSIDE THE WINDOW. Reversing session 1's decision in session 5 and then
    # injecting with max_sessions=3 proves nothing: session 1's digest is out of the window regardless
    # of supersession, so the check passes whether or not the re-resolution works. The pair that
    # discriminates is one session apart (6 -> 7), where the stale entry IS in the pool and only the
    # live-status lookup can remove it. The PRE-CHECK below fails the run if the fixture ever stops
    # putting the stale entry in the pool -- i.e. if this control goes back to measuring nothing.
    s7 = _new_store()
    _replay(s7, plan, upto_session=7)
    in_window = any(("marker-p12" in (e.get("text") or ""))
                    for d in s7._session_digests()[-MAX_SESSIONS:]
                    for e in ((d.get("meta") or {}).get("entries") or []))
    ctx_rev = s7.session_context(max_sessions=MAX_SESSIONS, max_chars=MAX_CHARS)
    stale_n = 1 if "marker-p12" in ctx_rev["text"] else 0     # the REVERSED session-6 cache decision
    fresh = "marker-p13" in ctx_rev["text"]                   # its session-7 replacement
    gate("no stale decision injected", in_window and stale_n == GATES["stale_injected"] and fresh,
         f"session-6 cache decision reversed in session 7: in the injection pool={in_window} "
         f"(control aims at a live target), injected={stale_n}, replacement present={fresh}, "
         f"dropped_superseded={ctx_rev['dropped_superseded']} "
         f"substituted_current={ctx_rev['substituted_current']}")

    # ── the OFF SWITCH: return value, store state, and the report ────────────────────────────────────
    import inspeximus.claude_code as cc
    import tempfile
    off_dir = tempfile.mkdtemp()
    prev = os.environ.get("INSPEXIMUS_SESSION_DIGEST")
    os.environ["INSPEXIMUS_SESSION_DIGEST"] = "0"
    try:
        m_off = cc._store(off_dir)
        m_off.remember_decision("a decision made while the digest is off", topic="offswitch")
        m_off._save(force=True)
        before = m_off.state_digest()
        rep_off = cc.session_end({"hook_event_name": "SessionEnd", "cwd": off_dir, "session_id": "x"})
        after = cc._store(off_dir).state_digest()
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cc.session_start({"hook_event_name": "SessionStart", "cwd": off_dir, "session_id": "y"})
        printed = buf.getvalue()
        off_ok = (rep_off.get("enabled") is False and rep_off.get("written") is False
                  and before == after and "resuming from the previous session" not in printed)
        gate("off switch", off_ok,
             f"report enabled={rep_off.get('enabled')} written={rep_off.get('written')}, "
             f"store unchanged={before == after}, injected={'resuming' in printed}")
        # ...and ON must do the opposite, or the switch is not a switch, it is a broken feature
        os.environ["INSPEXIMUS_SESSION_DIGEST"] = "1"
        rep_on = cc.session_end({"hook_event_name": "SessionEnd", "cwd": off_dir, "session_id": "x"})
        after_on = cc._store(off_dir).state_digest()
        gate("off switch is a SWITCH (on writes)", rep_on.get("written") is True and after_on != after,
             f"enabled -> written={rep_on.get('written')}, store changed={after_on != after}")
    finally:
        if prev is None:
            os.environ.pop("INSPEXIMUS_SESSION_DIGEST", None)
        else:
            os.environ["INSPEXIMUS_SESSION_DIGEST"] = prev
        import shutil
        shutil.rmtree(off_dir, ignore_errors=True)

    # ── DETERMINISM: same event log, two independent stores, byte-identical digest ───────────────────
    a, b = _new_store(), _new_store()
    _replay(a, plan, upto_session=4)
    _replay(b, plan, upto_session=4)
    da = a.close_session("sess-4", write=False, max_chars=MAX_CHARS)["text"]
    db = b.close_session("sess-4", write=False, max_chars=MAX_CHARS)["text"]
    twice = a.close_session("sess-4", write=False, max_chars=MAX_CHARS)["text"]
    gate("determinism (byte-identical)", da == db and da == twice,
         f"{len(da)} chars, cross-store identical={da == db}, repeat identical={da == twice}")

    # ── FINDABILITY at scale: recall the digest with the query a resuming agent actually types ───────
    q = "what changed last session"
    hits = store.recall(q, k=10, reinforce=False)
    rank = next((i + 1 for i, h in enumerate(hits)
                 if h.get("id") == next((r["id"] for r in store.items
                                         if r.get("key") == store.SESSION_DIGEST_KEY
                                         and r.get("status") == "active"), None)), None)
    gate("findability rank<=3 at >=2500 records", rank is not None and rank <= 3,
         f"recall({q!r}) returns the current digest at rank {rank} in {n_records} records")

    # ── LOOP VALUE: the injection must carry something a plain recall top-5 does not ─────────────────
    s7 = _new_store()
    _replay(s7, plan, upto_session=7)
    ctx7 = s7.session_context(max_sessions=MAX_SESSIONS, max_chars=MAX_CHARS)
    resume_q = "what did we decide and what is still open"
    top5 = " ".join(h.get("text", "") for h in s7.recall(resume_q, k=5, reinforce=False))
    carried = []
    for j in range(5, 8):
        carried += _markers(plan, j, salient=True)
    only_injection = [t for t in carried if t in ctx7["text"] and t not in top5]
    gate("loop adds what recall@5 misses", len(only_injection) > 0,
         f"{len(only_injection)} carried fact(s) in the injection but NOT in recall(k=5): "
         f"{only_injection[:5]}")

    # ── cost, because SessionEnd runs on a 1.5s Claude Code budget ───────────────────────────────────
    t = time.time()
    store.close_session("sess-8", write=False, max_chars=MAX_CHARS)
    close_ms = (time.time() - t) * 1000
    t = time.time()
    store.session_context(max_sessions=MAX_SESSIONS, max_chars=MAX_CHARS)
    ctx_ms = (time.time() - t) * 1000
    lines.append(f"cost at {n_records} records: close_session {close_ms:.0f} ms, "
                 f"session_context {ctx_ms:.0f} ms (SessionEnd budget is 1500 ms)")

    print("\n".join(lines))
    print()
    print(f"HEADLINE  injection recall@+1 {recall1:.3f} | below-threshold rejection {rejection:.4f} | "
          f"rejection at threshold=0 {rej0:.4f} | digest bound {MAX_CHARS} chars")
    if failures:
        print("FAILED GATES: " + ", ".join(failures))
        return 1
    print("all gates held")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
