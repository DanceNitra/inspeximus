"""The cross-session loop: SessionEnd writes a digest, SessionStart injects it, and NO LLM is involved.

The feature is easy to fake. An implementation that injects everything scores 100% on "did the decision
carry over" and has reinvented "paste the whole log"; an implementation that injects nothing passes every
test that only asserts "did not raise". So the tests here come in pairs -- for each guarantee there is a
test that the guard HOLDS and a test that the guard is what is holding it:

  * a decision carries over          <-> shell commands and chit-chat do NOT (and at threshold=0 they DO,
                                         which is what proves the threshold is load-bearing rather than
                                         the fixture being empty of noise)
  * the block respects its bound     <-> a block built from oversized entries is still under the bound
  * the off switch suppresses        <-> with it ON the same call writes and injects
  * a reversed decision is dropped   <-> its replacement is present, and the stale entry was genuinely in
                                         the candidate pool first (a control aimed outside the injection
                                         window would pass no matter what the code did)

Plus the claim that makes this zero-LLM rather than merely cheap: two independently-built stores replaying
the same event log must render a BYTE-IDENTICAL digest.
"""
import io
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus                      # noqa: E402
import inspeximus.claude_code as cc                    # noqa: E402


def _m():
    return Inspeximus(path=None)


def _session(m, sid, decisions=(), facts=(), noise=(), threads=(), close=True, **kw):
    """One scripted session: open, write, close. Returns the close_session report."""
    m.open_session(sid)
    for text, topic in decisions:
        m.remember_decision(text, because="because " + text, topic=topic, session_id=sid)
    for text, key, obj in facts:
        m.remember(text, key=key, object=obj, mtype="semantic", session_id=sid)
    for text in threads:
        m.remember(text, tags=["open"], mtype="semantic", session_id=sid)
    for text in noise:
        m.remember("ran: " + text, key="cmd:" + text[:20], object=text[:40],
                   mtype="episodic", tags=["bash"], session_id=sid)
    return m.close_session(sid, **kw) if close else None


# ── salience: the admission bar, and that it cannot be bought ────────────────────────────────────────
def test_a_decision_clears_the_bar_and_raw_mechanics_do_not():
    m = _m()
    dec = m.items[0] if m.items else None
    m.remember_decision("use Postgres", because="sqlite locks", topic="db")
    m.remember("ran: pytest -q", key="cmd:x", object="pytest", mtype="episodic", tags=["bash"])
    m.remember("the coffee machine is broken", mtype="episodic")
    by = {r["text"][:12]: r for r in m.items}
    assert m.session_salience(by["DECISION: us"]) >= Inspeximus.SESSION_SALIENCE_THRESHOLD
    assert m.session_salience(by["ran: pytest "]) < Inspeximus.SESSION_SALIENCE_THRESHOLD
    assert m.session_salience(by["the coffee m"]) < Inspeximus.SESSION_SALIENCE_THRESHOLD
    assert dec is None


def test_accrued_value_cannot_buy_a_shell_command_past_the_bar():
    """The mechanics CAP, tested the only way that discriminates: a bash record whose value has been
    driven to the ceiling. Without the cap its score would be 0.5(key) + 0.2*5 = 1.5 and climbing with
    every recall, so 'raw tool exhaust never crosses the boundary' would be true only until a command was
    recalled often enough -- exactly the kind of guarantee that holds in a test and fails in a real store."""
    m = _m()
    m.remember("ran: pytest -q", key="cmd:x", object="pytest", mtype="episodic",
               tags=["bash"], value=5.0)
    m.remember("src/app.py :: current state -> x", key="file:src/app.py", object="x",
               mtype="semantic", tags=["file", "edit"], value=5.0)
    for r in m.items:
        assert m.session_salience(r) <= Inspeximus.SESSION_MECHANICS_CAP
        assert m.session_salience(r) < Inspeximus.SESSION_SALIENCE_THRESHOLD


def test_the_session_bookkeeping_never_digests_itself():
    m = _m()
    _session(m, "s1", decisions=[("use Postgres", "db")])
    for r in m.items:
        if set(r.get("tags") or []) & set(Inspeximus.SESSION_TAGS):
            assert m.session_salience(r) == 0.0


def test_a_correction_scores_above_the_plain_fact_it_corrected():
    m = _m()
    m.remember("retry budget is 5", key="cfg::retry", object="5", mtype="semantic")
    m.remember("retry budget is 3", key="cfg::retry", object="3", mtype="semantic")
    corr = m._session_correctors()
    new = next(r for r in m.items if r["status"] == "active" and r.get("key") == "cfg::retry")
    old = next(r for r in m.items if r["status"] == "superseded" and r.get("key") == "cfg::retry")
    assert m.session_salience(new, corr) >= Inspeximus.SESSION_SALIENCE_THRESHOLD
    assert m.session_salience(old, corr) < Inspeximus.SESSION_SALIENCE_THRESHOLD


# ── the loop itself ──────────────────────────────────────────────────────────────────────────────────
def test_a_decision_recorded_in_session_one_is_injected_in_session_two():
    """THE headline behaviour of the unit."""
    m = _m()
    _session(m, "s1", decisions=[("use Postgres for the ledger", "db")],
             noise=["pytest -q", "git status", "ls -la"])
    m.open_session("s2")
    ctx = m.session_context()
    assert "Postgres" in ctx["text"]
    assert ctx["items"] == 1


def test_the_injected_block_excludes_the_mechanics_of_the_same_session():
    m = _m()
    _session(m, "s1", decisions=[("use Postgres", "db")],
             noise=["pytest -q", "git status", "ruff check ."])
    ctx = m.session_context()
    assert "Postgres" in ctx["text"]
    for cmd in ("pytest", "git status", "ruff"):
        assert cmd not in ctx["text"], f"raw mechanics leaked into the next session: {cmd}"


def test_the_noise_IS_reachable_when_the_bar_is_removed():
    """The negative control, in the suite. If this fails, the test above proves nothing -- it would mean
    the shell commands never reached the candidate pool and 'they were excluded' was measuring an empty
    set. (Both limits have to come off at the CLOSE end: the injection-time threshold only re-ranks the
    entries a digest already holds, and max_chars truncates before the salience bar is ever reached.)"""
    m = _m()
    _session(m, "s1", decisions=[("use Postgres", "db")],
             noise=["pytest -q", "git status", "ruff check ."],
             threshold=0.0, max_chars=100000, max_items=1000)
    ctx = m.session_context(threshold=0.0, max_chars=100000, max_items=1000)
    for cmd in ("pytest", "git status", "ruff"):
        assert cmd in ctx["text"], f"the control cannot see its target: {cmd} never entered the pool"


def test_a_reversed_decision_is_replaced_by_the_current_one():
    m = _m()
    _session(m, "s1", decisions=[("evict the cache LRU", "cache")])
    # the stale entry must genuinely be in the pool, or this test passes without exercising anything
    assert any("LRU" in (e.get("text") or "")
               for d in m._session_digests() for e in (d.get("meta") or {}).get("entries") or [])
    _session(m, "s2", decisions=[("evict the cache LFU", "cache")])
    ctx = m.session_context()
    assert "LFU" in ctx["text"]
    assert "LRU" not in ctx["text"], "a decision reversed in a later session must not be re-injected"
    assert ctx["substituted_current"] + ctx["dropped_superseded"] >= 1


def test_an_erased_record_leaves_the_injected_context():
    """A frozen summary cannot honour an erasure; a ledger diff re-resolved at injection time can."""
    m = _m()
    _session(m, "s1", decisions=[("use Postgres", "db")])
    assert "Postgres" in m.session_context()["text"]
    target = next(r["id"] for r in m.items if "Postgres" in (r.get("text") or "")
                  and "decision" in (r.get("tags") or []))
    m.forget(ids=[target])
    ctx = m.session_context()
    assert "Postgres" not in ctx["text"]
    assert ctx["dropped_erased"] >= 1


def test_a_session_with_nothing_salient_injects_nothing_rather_than_padding():
    m = _m()
    _session(m, "s1", noise=["pytest -q", "git status"])
    ctx = m.session_context()
    assert ctx["text"] == "" and ctx["items"] == 0


# ── the size bound ───────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("bound", [120, 300, 1200])
def test_the_injected_block_never_exceeds_its_bound(bound):
    m = _m()
    for s in range(1, 5):
        _session(m, f"s{s}", decisions=[(f"decision number {s} " + "x" * 400, f"t{s}")],
                 threads=["an open thread " + "y" * 400], max_chars=bound)
    ctx = m.session_context(max_chars=bound)
    assert len(ctx["text"]) <= bound, f"{len(ctx['text'])} > {bound}"
    assert ctx["chars"] == len(ctx["text"])


def test_the_bound_is_reported_as_truncation_at_both_ends():
    """There are TWO bounds and they are enforced independently -- close_session's (how much of the
    session is written down) and session_context's (how much is injected). Asserting on one and reading
    the other is how "truncated: False" gets believed about a digest that was cut in half."""
    m = _m()
    dec = [(f"decision {i} " + "z" * 120, f"t{i}") for i in range(12)]
    rep = _session(m, "s1", decisions=dec, max_chars=400)
    assert rep["truncated"] is True and rep["chars"] <= 400
    assert rep["items"] < len(dec)

    m2 = _m()
    rep2 = _session(m2, "s1", decisions=dec, max_chars=100000)
    assert rep2["truncated"] is False and rep2["items"] == len(dec)
    ctx = m2.session_context(max_chars=400)
    assert len(ctx["text"]) <= 400 and ctx["truncated"] is True


# ── determinism: the zero-LLM claim, in falsifiable form ─────────────────────────────────────────────
def _script(m):
    _session(m, "s1", decisions=[("use Postgres for the ledger", "db"),
                                 ("authenticate with OIDC", "auth")],
             facts=[("retry budget is 5", "cfg::retry", "5")],
             threads=["whether to shard by tenant or by time"],
             noise=["pytest -q", "git status"])
    m.open_session("s2")
    m.remember("retry budget is 3", key="cfg::retry", object="3", mtype="semantic", session_id="s2")
    m.remember_decision("ship the CLI first", because="distribution", topic="roadmap", session_id="s2")


def test_two_independent_stores_render_a_byte_identical_digest():
    """No LLM anywhere on this path, stated as something that can fail: same event log, different
    process-time, different record ids -> the same bytes. A summariser cannot promise this."""
    a, b = _m(), _m()
    _script(a)
    _script(b)
    ta = a.close_session("s2", write=False)["text"]
    tb = b.close_session("s2", write=False)["text"]
    assert ta == tb, f"non-deterministic digest:\n{ta!r}\n{tb!r}"
    assert ta == a.close_session("s2", write=False)["text"], "not idempotent on the same store"
    assert ta, "an empty digest would make this assertion vacuous"


def test_the_digest_text_carries_no_timestamp_or_record_id():
    """Why the determinism above holds: the rendered text is content-only. Ids and timestamps live in
    meta, where they belong -- putting either in the text would break byte-identity silently."""
    m = _m()
    _script(m)
    rep = m.close_session("s2", write=False)
    ids = [r["id"] for r in m.items]
    assert not any(i in rep["text"] for i in ids)
    assert "19" not in rep["text"] and "20" not in rep["text"].replace("1200", "")


# ── the keyed ledger of digests ──────────────────────────────────────────────────────────────────────
def test_each_close_supersedes_the_previous_digest_so_exactly_one_is_active():
    m = _m()
    for s in range(1, 4):
        _session(m, f"s{s}", decisions=[(f"decision {s}", f"t{s}")])
    active = [r for r in m.items
              if r.get("key") == Inspeximus.SESSION_DIGEST_KEY and r["status"] == "active"]
    assert len(active) == 1, f"{len(active)} active digests; recall(k=1) would be a coin flip"
    assert len(m.history(Inspeximus.SESSION_DIGEST_KEY)) == 3, "the older digests are the timeline"


def test_closing_the_same_session_twice_still_leaves_one_active_digest():
    m = _m()
    _session(m, "s1", decisions=[("use Postgres", "db")])
    m.close_session("s1")
    active = [r for r in m.items
              if r.get("key") == Inspeximus.SESSION_DIGEST_KEY and r["status"] == "active"]
    assert len(active) == 1


def test_opening_a_session_retires_the_previous_open_marker():
    m = _m()
    m.open_session("s1")
    m.open_session("s2")
    open_markers = [r for r in m.items
                    if r.get("key") == Inspeximus.SESSION_OPEN_KEY and r["status"] == "active"]
    assert len(open_markers) == 1
    assert "s2" in open_markers[0]["text"]


def test_the_digest_is_findable_by_the_query_a_resuming_agent_types():
    m = _m()
    for s in range(1, 4):
        _session(m, f"s{s}", decisions=[(f"decision {s}", f"t{s}")],
                 noise=[f"pytest tests/test_{i}.py" for i in range(40)])
    hits = m.recall("what changed last session", k=3, reinforce=False)
    cur = next(r["id"] for r in m.items
               if r.get("key") == Inspeximus.SESSION_DIGEST_KEY and r["status"] == "active")
    assert hits and hits[0]["id"] == cur, "the digest must be rank 1 for the obvious resume query"


def _store_for_hub_pass():
    """Enough generic notes for the hub pass to fire (it needs >= 50 active)."""
    m = _m()
    for i in range(80):
        m.remember(f"note {i} about the project and the code and the tests", mtype="semantic")
    _session(m, "s1", decisions=[("use Postgres for the ledger and the code and the tests", "db")])
    return m


def _store_for_cluster_pass():
    """Notes engineered to land in the SAME cluster as the digest -- their tokens are a subset of its
    text, which is exactly the relationship a real digest has to the session it summarises."""
    m = _m()
    for i in range(24):
        m.remember(f"session digest changed the ledger decision record note {i}", mtype="semantic")
    _session(m, "s1", decisions=[("changed the ledger record decision", "db")])
    return m


# Each case is (builder, call) rather than a bare method name, because a parametrisation that reuses one
# fixture for every pass is not three tests -- measured, the 80-note fixture makes only `consolidate()`
# discriminate, and the other two parameters passed identically with the guard REMOVED. Every case below
# was checked to FAIL when `_is_session_bookkeeping` is stubbed to return False.
@pytest.mark.parametrize("build,call,label", [
    (_store_for_hub_pass, lambda m: m.consolidate(), "consolidate()"),
    (_store_for_cluster_pass, lambda m: m.consolidate_clusters(threshold=15, keep_per_cluster=1),
     "consolidate_clusters(keep_per_cluster=1)"),
    (_store_for_cluster_pass, lambda m: m.sleep(cluster_threshold=15, keep=2),
     "sleep(cluster_threshold=15, keep=2)"),
])
def test_no_consolidation_pass_retires_the_session_digest(build, call, label):
    """A cross-cutting summary is similar to everything it covers, so EVERY consolidation heuristic reads
    it as demotable: the hub pass sees a universal matcher, the near-duplicate pass sees a duplicate, the
    state-toggle pass sees a value clash against a note it summarises. Guarding one pass is not guarding
    the mechanism -- measured, the digest cleared the hub pass and was then retired by the next one
    (`superseded_by_policy: state_toggle`), which ends the cross-session loop silently: the following
    SessionStart injects nothing and reports a truthful, useless `items: 0`."""
    m = build()
    call(m)
    dig = [r for r in m.items if r.get("key") == Inspeximus.SESSION_DIGEST_KEY]
    assert len(dig) == 1 and dig[0]["status"] == "active", \
        f"{label} retired the session digest: " \
        f"{[(r['status'], (r.get('meta') or {}).get('superseded_by_policy')) for r in dig]}"
    assert m.session_context()["items"] >= 1, "the loop stopped carrying anything"
    assert m.recall("what changed last session", k=1, reinforce=False)[0]["id"] == dig[0]["id"]


# ── the window ───────────────────────────────────────────────────────────────────────────────────────
def test_the_window_prefers_the_session_stamp_over_a_time_guess():
    m = _m()
    m.open_session("s1")
    m.remember_decision("stamped decision", topic="a", session_id="s1")
    m.remember_decision("decision from another session", topic="b", session_id="other")
    rep = m.close_session("s1")
    assert rep["mode"] == "sid"
    assert "stamped decision" in rep["text"]
    assert "another session" not in rep["text"]


def test_remember_decision_passes_the_session_stamp_through():
    """It did not, which is why close_session could not tell which session recorded a decision -- the
    single most important record type the digest carries."""
    m = _m()
    mid = m.remember_decision("a decision", topic="t", session_id="s9")
    rec = next(r for r in m.items if r["id"] == mid)
    assert (rec.get("meta") or {}).get("sid") == "s9"


def test_a_tenants_digest_never_draws_on_another_tenants_records():
    m = _m()
    a, b = m.for_tenant("acme"), m.for_tenant("globex")
    _session(a, "s1", decisions=[("acme picks Postgres", "db")])
    _session(b, "s1", decisions=[("globex picks MySQL", "db")])
    ta, tb = a.session_context()["text"], b.session_context()["text"]
    assert "Postgres" in ta and "MySQL" not in ta
    assert "MySQL" in tb and "Postgres" not in tb


# ── the off switch: the return value, the store, and the report ──────────────────────────────────────
@pytest.fixture()
def project(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.chdir(d)
    monkeypatch.delenv("INSPEXIMUS_SESSION_DIGEST", raising=False)
    return d


def _seed(d):
    m = cc._store(d)
    m.open_session("s1")
    m.remember_decision("use Postgres for the ledger", because="sqlite locks", topic="db",
                        session_id="s1")
    m._save(force=True)
    return m


def test_session_end_off_writes_nothing_at_all(project, monkeypatch, capsys):
    """Three assertions, because a flipped-default audit has to check all three: the RETURN VALUE says
    disabled, the STORE is byte-identical afterwards, and the REPORT does not claim work it did not do.
    A version that built the digest and then declined to print it would pass the first and fail the
    second."""
    _seed(project)
    monkeypatch.setenv("INSPEXIMUS_SESSION_DIGEST", "0")
    before = cc._store(project).state_digest()
    rep = cc.session_end({"hook_event_name": "SessionEnd", "cwd": project, "session_id": "s1"})
    capsys.readouterr()
    assert rep["enabled"] is False and rep["written"] is False
    assert rep.get("id") is None and rep.get("items", 0) == 0
    assert cc._store(project).state_digest() == before, "SessionEnd wrote while disabled"


def test_session_start_off_injects_nothing(project, monkeypatch, capsys):
    _seed(project)
    cc.session_end({"hook_event_name": "SessionEnd", "cwd": project, "session_id": "s1"})
    capsys.readouterr()
    monkeypatch.setenv("INSPEXIMUS_SESSION_DIGEST", "0")
    cc.session_start({"hook_event_name": "SessionStart", "cwd": project, "session_id": "s2"})
    out = capsys.readouterr().out
    assert "resuming from the previous session" not in out
    assert "Postgres" not in out


def test_the_off_switch_is_a_SWITCH(project, monkeypatch, capsys):
    """The other direction. Without this, an implementation that never works passes every off-switch
    test in the file."""
    _seed(project)
    monkeypatch.setenv("INSPEXIMUS_SESSION_DIGEST", "1")
    rep = cc.session_end({"hook_event_name": "SessionEnd", "cwd": project, "session_id": "s1"})
    cc.session_start({"hook_event_name": "SessionStart", "cwd": project, "session_id": "s2"})
    out = capsys.readouterr().out
    assert rep["written"] is True and rep["items"] >= 1
    assert "resuming from the previous session" in out and "Postgres" in out


def test_the_off_switch_also_lives_in_the_project_config(project, capsys):
    _seed(project)
    os.makedirs(os.path.join(project, ".inspeximus"), exist_ok=True)
    io.open(os.path.join(project, ".inspeximus", "config.json"), "w", encoding="utf-8").write(
        json.dumps({"session_digest": {"enabled": False}}))
    rep = cc.session_end({"hook_event_name": "SessionEnd", "cwd": project, "session_id": "s1"})
    capsys.readouterr()
    assert rep["written"] is False
    assert cc.session_digest_enabled(project) is False


def test_the_env_var_overrides_a_config_that_disables_it(project, monkeypatch):
    os.makedirs(os.path.join(project, ".inspeximus"), exist_ok=True)
    io.open(os.path.join(project, ".inspeximus", "config.json"), "w", encoding="utf-8").write(
        json.dumps({"session_digest": {"enabled": False}}))
    monkeypatch.setenv("INSPEXIMUS_SESSION_DIGEST", "1")
    assert cc.session_digest_enabled(project) is True


def test_a_typo_in_a_numeric_env_var_does_not_silently_disable_the_feature(project, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_SESSION_MAX_CHARS", "not-a-number")
    cfg = cc._session_cfg(project)
    assert cfg["enabled"] is True and cfg["max_chars"] == 1200


# ── the hook surface ─────────────────────────────────────────────────────────────────────────────────
def test_the_hooks_close_and_then_inject_across_two_sessions(project, capsys):
    """End to end through the hook entry points, the way Claude Code drives them."""
    cc.session_start({"hook_event_name": "SessionStart", "cwd": project, "session_id": "s1"})
    cc.capture({"hook_event_name": "PostToolUse", "cwd": project, "session_id": "s1",
                "tool_name": "Bash", "tool_input": {"command": "pytest -q"}})
    m = cc._store(project)
    m.remember_decision("use Postgres for the ledger", because="sqlite locks", topic="db",
                        session_id="s1")
    m._save(force=True)
    cc.session_end({"hook_event_name": "SessionEnd", "cwd": project, "session_id": "s1",
                    "reason": "exit"})
    capsys.readouterr()
    cc.session_start({"hook_event_name": "SessionStart", "cwd": project, "session_id": "s2"})
    out = capsys.readouterr().out
    assert "Postgres" in out, "the next session was not told what the last one decided"
    assert "ran: pytest" not in out.split("known files")[0], "mechanics leaked into the digest block"


def test_capture_stamps_the_session_id_so_the_window_is_exact(project, capsys):
    cc.capture({"hook_event_name": "PostToolUse", "cwd": project, "session_id": "abc123",
                "tool_name": "Write", "tool_input": {"file_path": "a.py", "content": "x = 1"}})
    capsys.readouterr()
    rec = next(r for r in cc._store(project).items if "a.py" in (r.get("text") or ""))
    assert (rec.get("meta") or {}).get("sid") == "abc123"


def test_a_compact_session_start_does_not_open_a_new_session(project, capsys):
    """SessionStart fires again after a context compaction; that is the SAME session continuing. Opening
    a boundary there would split one session into two digests and orphan the first half."""
    cc.session_start({"hook_event_name": "SessionStart", "cwd": project, "session_id": "s1"})
    cc.session_start({"hook_event_name": "SessionStart", "cwd": project, "session_id": "s1",
                      "source": "compact"})
    capsys.readouterr()
    markers = [r for r in cc._store(project).items
               if r.get("key") == Inspeximus.SESSION_OPEN_KEY]
    assert len(markers) == 1


@pytest.mark.parametrize("ev", [{}, {"hook_event_name": "SessionEnd"}, {"cwd": None},
                                {"hook_event_name": "SessionEnd", "session_id": None}])
def test_the_session_hooks_are_fail_open_on_malformed_events(project, ev, capsys):
    """Documented contract: a hook that raises breaks the user's session, not just its own feature."""
    cc.session_end(ev)
    cc.session_start(ev)
    capsys.readouterr()


def test_install_adds_the_session_end_hook_with_a_raised_timeout(capsys):
    """Claude Code gives every SessionEnd hook 1.5s together unless the settings raise it, and a hook
    killed mid-write writes nothing -- so the digest would be lost exactly on a big store."""
    d = tempfile.mkdtemp()
    assert cc.install(cwd=d) is True
    capsys.readouterr()
    cfg = json.load(io.open(os.path.join(d, ".claude", "settings.json"), encoding="utf-8"))
    assert "SessionEnd" in cfg["hooks"]
    blob = json.dumps(cfg["hooks"]["SessionEnd"])
    assert any(mark in blob for mark in cc._HOOK_MARKERS)
    assert '"timeout": 15' in blob or '"timeout":15' in blob


def test_uninstall_removes_the_session_end_hook_too(capsys):
    d = tempfile.mkdtemp()
    cc.install(cwd=d)
    cc.uninstall(cwd=d)
    capsys.readouterr()
    raw = io.open(os.path.join(d, ".claude", "settings.json"), encoding="utf-8").read()
    assert not any(mark in raw for mark in cc._HOOK_MARKERS)


def test_install_stays_idempotent_with_the_fourth_event(capsys):
    d = tempfile.mkdtemp()
    for _ in range(3):
        cc.install(cwd=d)
    capsys.readouterr()
    hooks = json.load(io.open(os.path.join(d, ".claude", "settings.json"), encoding="utf-8"))["hooks"]
    for evt in ("PostToolUse", "UserPromptSubmit", "SessionStart", "SessionEnd"):
        ours = [h for h in hooks[evt] if any(m in json.dumps(h) for m in cc._HOOK_MARKERS)]
        assert len(ours) == 1, f"{evt} got {len(ours)} copies of our hook"


def test_the_dispatcher_routes_a_session_end_event(project, monkeypatch, capsys):
    """main() reads stdin; a missing branch there means the hook silently does nothing in production
    while every direct-call test in this file still passes."""
    seen = {}
    monkeypatch.setattr(cc, "session_end", lambda ev: seen.update(ev))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"hook_event_name": "SessionEnd", "cwd": project, "session_id": "s1"})))
    cc.main()
    capsys.readouterr()
    assert seen.get("hook_event_name") == "SessionEnd"
