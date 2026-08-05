"""Session continuity conformance -- the contract any cross-session memory must satisfy.

"Cross-session memory" is about to become a headline claim. This file is the instrument that can
falsify it. It is written as the CONTRACT, not as a description of the current implementation: every
property is asserted against the public surface, so a later implementation (project scope, a
SessionEnd digest, agent-to-agent grants) is judged by the same test it is judged by today.

MEASURED BASELINE, 1.89.0, 2026-08-01/02, on this repository at `main` (ba7a3d4). Every number was produced
by the fixture in this file (or, where stated, by the same probe against a real dogfood store) and
re-runs on every invocation -- none of them is quoted from a note.

  P1  DURABILITY .............. HOLDS
      Two genuine processes: writer subprocess `remember()` + `flush()`, reader subprocess opens the
      same path cold. The record comes back at rank 1 of k=3. (The failure this closes is recorded in
      `Inspeximus.__init__`: a literal "~" and a missing parent directory once made every documented
      install path write nothing, in-process recall work, and a NEW process see an empty store.)

  P2a RECENCY REACHABILITY, within one handle ..... HOLDS
      A record written moments ago ranks **1** at k=5. Measured here on a 600-record synthetic store
      shaped like the real dogfood capture (81% `ran: ...` mechanics / 19% file states -- the observed
      mix in server/.inspeximus/coding_memory.json, n=884). The same probe, run out-of-band:
        * against that REAL 884-record store, 5 fresh records, 5 queries: in top-5 5/5, deep rank 1/1;
        * against a synthetic store at n=2,550 (the size in the original report), with and without
          400 rounds of background reinforcement: rank 1 in 6/6 conditions.
      So the reported "written today, not at k=5 on ~2,550 records" is NOT a ranking failure. P2b is.

  P2b RECENCY REACHABILITY, across a live session boundary ..... FAILS
      A handle opened in session 1 never sees session 2's write. Measured: writer subprocess adds a
      second record; the session-1 handle's `recall()` returns 0 hits for it and `len(items)` stays
      1 of 2, SILENTLY. Only `reload()` surfaces it (then 2 of 2), and only a WRITE detects the
      divergence (`StoreChangedOnDisk`). A long-lived reader -- which is exactly what an MCP server
      is -- serves a stale store for as long as it stays up, and reports nothing.

  P3  SESSION BOUNDARY ..... FAILS (a scoping gap, not a ranking one)
      There is no session boundary in the data model. The whole feature is `remember(session_id=)`
      stamping `meta.sid` (core.py:1361) and `recall(session_id=)` hard-filtering on it
      (core.py:5336). Measured: that filter DOES isolate peer sessions (1 hit each, 0 cross-talk --
      asserted below and green). What does not exist is any way to ask the store WHICH sessions it
      holds: `[n for n in dir(Inspeximus) if "session" in n.lower()]` == `['supersession_report']`.
      A resuming agent that does not already know the prior sid cannot find it. Our own 3-of-5 miss
      was a scoping question asked of a ranking system.

  P4  PROJECT / WORKSPACE SCOPE ..... FAILS, on all three of read, supersede and save
      `mcp_server.py:99` reads `_PATH = os.environ.get("INSPEXIMUS_PATH", "inspeximus_memory.json")`
      once at import, relative to the client's CWD -- so several projects on one configured path share
      one store with no project dimension.
        * READ: project B's DEFAULT recall returns project A's record. CONTROL, same fixture with the
          hard `tenant=` binding: 0 of A's records reach B, so the probe can see isolation when it
          exists.
        * SUPERSEDE: supersession keys are GLOBAL. Two projects writing `release::cadence` (or any
          `decision::<topic>` -- the key shape the product tells people to use) collide: B's write
          RETIRES A's record. Afterwards A sees 0 of its own facts and 1 of B's; add a `scope=` read
          filter and A sees 0 and 0, i.e. its own answer is silently blank. A read-side scope filter
          over a global key space turns a leak into data loss.
        * SAVE: the only hard isolation on offer DESTROYS DATA. `items` is a tenant-filtered VIEW
          (core.py:3288) and `_save` serialises `self.items` (core.py:7809), so a tenant-bound handle's
          save writes only its own rows. Measured: **0 of 3** of project A's records survive project
          B's first `flush()`; two UNBOUND handles on the same file keep both. `StoreChangedOnDisk`
          does not fire, because B loaded after A's flush -- a legitimate sequential handoff. The
          `items` SETTER already refuses exactly this move; the persist path reads the same view and
          was missed.

  P5  READ PURITY ..... FAILS
      A documented read is a persisted write. Measured on a 20-record store:
        * `recall("deployment pipeline", k=6)` changes **6 of 20** `value` fields and their
          `last_access`, and sets `_dirty=True`; the change reaches disk on the next save (after
          `flush()` the persisted values differ from the baseline);
        * `memory_report()` -- docstring "Read-only" -- changes **2 of 20**, because it calls
          `self.recall(...)` at the default `reinforce=True`. The sibling inspector `why_recalled()`
          passes `reinforce=False` with a comment naming this exact hazard (core.py:7021), so the fix
          exists in the codebase at one call site and was not applied at the other;
        * `state_digest()` is BLIND to all of it -- byte-identical before and after. A purity gate
          built on the digest reports SAFE, so this suite asserts on the VALUE VECTOR and pins the
          blindness separately.
      THE CONSEQUENCE, which is what the session claim is actually about: with the shipped default,
      **45/180 = 0.2500** of answers change when only the ORDER of the earlier questions changes
      (60 records, 30 queries, 6 shuffled orders). The control is exactly **0/180 = 0.0000** with
      `reinforce=False`, so the probe is not reading its own noise and order-independence is
      REACHABLE. Sibling unit A1 measured 0.318-0.605 on a different corpus, with the same zero
      control, and that reinforcement costs accuracy (20/20 held-out comparisons negative).
      Two neighbouring properties HOLD and are asserted as green, so that a fix is not allowed to
      break them:
        * run-to-run determinism: `reinforce=False` replay is identical;
        * the DECLARED tie-break: equal relevance -> the more recently inserted record first
          (core.py:5584, deliberate; `tie_recent` is built on it). Permutation invariance over the
          WRITE order is NOT asserted -- it is deliberately false, and is a different thing from the
          query-order independence above.
      The tie-break is asserted per RECALL MODE with a deterministic embedder, because a mode
      parametrization without one silently runs the lexical path in all four (core.py:5385-5392); each
      case asserts `_last_mode`. Measured on a 22-record store: lexical 1.0/1.0 tied, hybrid 1.0/1.0
      tied, auto 1.0/1.0 tied (routes to hybrid), semantic 0.94/0.938 NOT tied -- so in the semantic
      channel the policy never fires. Sibling A5 measured the tie dissolved in hybrid/auto on their
      corpus; mine does not reproduce that, which is why the assertion is conditional ("if tied, the
      newer wins") rather than "the newer record is top-1".

  P6  OFF SWITCH ..... HOLDS since 2026-08-05 (INSPEXIMUS_NO_INJECT)
      Two shipped mechanisms inject cross-session state into an agent's context: `claude_code`'s
      SessionStart handler (prints the project's known files) and its UserPromptSubmit handler (prints
      recalled memory). Measured: neither is silenced by ANY `INSPEXIMUS_*` variable the package
      declares. The only switches that exist are `INSPEXIMUS_NO_NUDGE` (the star ask) and
      `INSPEXIMUS_NO_UPDATE_CHECK` (the version line) -- both for lines that ride ALONGSIDE the
      injection, neither for the injection.

  P7a DIGEST DETERMINISM ..... HOLDS
      The same event log replayed into two different directories yields a byte-identical SessionStart
      output (measured, 363 chars, identical in-process, across a process boundary, and across
      directories). This is the zero-LLM differentiator made testable: an LLM-summarised write path
      cannot pass it.

  P7b SESSION DIGEST ..... HOLDS as of 1.91.0 (was FAILS at 1.89.0)
      MEASURED 1.89.0: there was no SessionEnd hook and no digest primitive -- `claude_code.install()`
      wrote exactly `("PostToolUse", "UserPromptSubmit", "SessionStart")`, and the SessionStart output
      was a list of up to 8 known files, not a digest of the session that produced them.
      1.91.0 adds `open_session`/`close_session`/`session_context` and installs a fourth hook,
      `SessionEnd`. The strict xfail here went XPASS and the marker was REMOVED, which is this suite
      working as designed; the property is now asserted end to end (the digest is written, and the next
      session's SessionStart output contains the decision the previous one recorded).

HOW THIS SUITE IS ALLOWED TO FAIL. A conformance suite that is green before the fixes has measured
nothing, so:
  * every FALSE property is `xfail(strict=True)` -- when it starts passing the suite goes RED and the
    marker must be removed, rather than rotting into a silent pass;
  * `INSPEXIMUS_CONFORMANCE_STRICT=1` removes the markers, which is the release-gate mode: on this
    commit that run FAILS on **8** properties (9 at 1.89.0; P7b was fixed), and the last test in this
    file runs exactly that child process and requires it to fail on EXACTLY the number of properties
    the file still marks `broken()` -- a count DERIVED at import, not typed in, because the typed one
    started lying the first time a sibling landed. "This suite can fail" is a runnable fact here;
  * every broken property carries a CONTROL that would fail if the fixture stopped reproducing the
    defect, and the file runs POSITIVE CONTROLS proving each comparator can see a deliberately broken
    implementation. An instrument that cannot fail has measured nothing either.

This file does not implement anything. C1 (project scope), C2 (SessionEnd digest -> SessionStart
injection) and C3 (agent-to-agent grants) own the implementations; this is what judges them. Their
branches are deliberately NOT depended on -- the contract is written against `main`, where at the time
of writing all nine sibling PRs were still open, so every verdict above is a verdict on shipped code.
"""
import hashlib
import io
import itertools
import json
import os
import random
import re
import subprocess
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import inspeximus.claude_code as cc                     # noqa: E402
from inspeximus import Inspeximus                       # noqa: E402
from inspeximus.core import StoreChangedOnDisk          # noqa: E402


# ── how a false property is marked ──────────────────────────────────────────────────────────────────
_STRICT = os.environ.get("INSPEXIMUS_CONFORMANCE_STRICT", "").strip().lower() in ("1", "true", "yes")
_CHILD_FLAG = "INSPEXIMUS_CONFORMANCE_CHILD"
# The properties the module docstring records as FALSE on this commit -- DERIVED, not hardcoded.
#
# It was `_BROKEN_PROPERTIES = 9`, and it started lying the first time a sibling landed: 1.91.0 made P7b
# true, its strict xfail went XPASS (which is this suite working -- a false property that starts passing
# turns the run RED so the claim is re-measured instead of rotting into a silent pass), and the honest
# response was to edit the 9 down by hand. A number maintained by hand is a number that drifts: nothing
# would have caught it staying at 9 while the file held 8 markers, or sitting at 8 while a later unit
# added two more false properties -- and in that second case the floor silently stops covering them.
#
# So count what is actually MARKED, at the moment it is marked. This cannot disagree with the file.
_BROKEN_MARKERS: list = []


def broken(reason):
    """Mark a property that is FALSE on this commit.

    Default: `xfail(strict=True)` -- the run stays green with a visible XFAIL (`pytest -rxX`), and goes
    RED the moment the property starts passing, which forces the marker to be deleted instead of
    quietly inverting into "tested".

    `INSPEXIMUS_CONFORMANCE_STRICT=1` drops the marker, so the suite genuinely fails on today's gaps.
    That is the mode a release gate runs, and it is how this file demonstrates it can fail at all.

    Records the reason BEFORE the strict branch, so the count is the same in both modes -- the parent
    process counts markers while the strict child counts failures, and they are only comparable if
    dropping the marker still counts it.
    """
    _BROKEN_MARKERS.append(reason)
    if _STRICT:
        return lambda fn: fn
    return pytest.mark.xfail(strict=True, reason=reason)


# ── fixtures ────────────────────────────────────────────────────────────────────────────────────────
def _path(name="m.json"):
    return os.path.join(tempfile.mkdtemp(), name)


def _read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _child(body):
    """Run `body` in a GENUINE separate process against this repo. Returns CompletedProcess."""
    src = f"import sys\nsys.path.insert(0, {REPO!r})\n" + body
    return subprocess.run([sys.executable, "-c", src], capture_output=True, text=True,
                          env={**os.environ, "INSPEXIMUS_NO_UPDATE_CHECK": "1", "PYTHONIOENCODING": "utf-8"})


# The observed shape of a real coding-memory store: server/.inspeximus/coding_memory.json held 884
# records, 720 tagged `bash` and 164 tagged `file`+`edit` -- 81% command mechanics. A recency probe run
# on a store of curated prose is not the store the claim is about.
_MECHANICS_FRAC = 0.81
_FILES = ["core.py", "cli.py", "mcp_server.py", "claude_code.py", "audit_bundle.py",
          "compliance.py", "erasure_auditor.py", "install.py", "browser.py", "code_guard.py"]
_CMDS = ["pytest tests/ -q", "git status", "git commit -m wip", "python -m pytest tests/test_core.py",
         "ruff check inspeximus", "git diff --stat", "python -c import inspeximus",
         "git log --oneline -5", "pip install -e .", "python build.py"]
# 600, not the 2,550 of the original report: the write path is O(n) per write, so 2,550 costs ~44 s to
# build against ~4 s here. The 2,550 run was made out-of-band with THIS generator and gave the same
# verdict (rank 1, with and without background reinforcement) -- recorded in the module docstring.
_N_BACKGROUND = int(os.environ.get("INSPEXIMUS_CONFORMANCE_N", "600"))


@pytest.fixture(autouse=True)
def _no_update_check(monkeypatch):
    """The "a newer version exists" line rides ALONGSIDE the SessionStart injection, so leaving it on
    would turn every byte-equality assertion below into a network test.

    Through `monkeypatch`, not `os.environ[...] = "1"`: an env var one test leaves behind is how a
    later test passes for a reason nobody wrote down -- and this one suppresses a network call, which
    is exactly the kind of silent difference that is never noticed until it matters.
    """
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")


@pytest.fixture(scope="module")
def dogfood_store():
    """A store shaped like the real capture: mostly `ran: ...` mechanics, some file states."""
    rnd = random.Random(7)
    m = Inspeximus(path=_path("coding_memory.json"))
    for i in range(_N_BACKGROUND):
        if rnd.random() < _MECHANICS_FRAC:
            cmd = f"{rnd.choice(_CMDS)} run {i}"
            m.remember(f"ran: {cmd}", key=f"cmd:{i}", object=cmd[:60], mtype="episodic", tags=["bash"])
        else:
            f = rnd.choice(_FILES)
            m.remember(f"inspeximus/{f} :: current state -> edited block {i} in {f}",
                       key=f"file:{f}:{i}", object=f"block {i}", mtype="semantic", tags=["file", "edit"])
    m.flush()
    return m


def _replay_session_one(project_dir, n=6):
    """A realistic session-1 transcript, captured through the SHIPPED hook -- not hand-written records.

    This is the honest fixture for anything that claims to resume a session: what a resuming agent can
    have is what the capture path actually stored, and the capture path stores Edit/Write/Bash only.
    """
    for i in range(n):
        cc.capture({"hook_event_name": "PostToolUse", "cwd": project_dir, "tool_name": "Write",
                    "tool_input": {"file_path": os.path.join(project_dir, f"mod{i}.py"),
                                   "content": f"def f{i}(): return {i}"}})
        cc.capture({"hook_event_name": "PostToolUse", "cwd": project_dir, "tool_name": "Bash",
                    "tool_input": {"command": f"pytest tests/test_{i}.py -q"}})


def _capture_stdout(fn):
    buf, old = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        fn()
    finally:
        sys.stdout = old
    return buf.getvalue().replace("\r\n", "\n")


# ════════════════════════════════════════════════════════════════════════════════════════════════════
# P1 -- DURABILITY: a record written in session 1 is readable in session 2 across a PROCESS boundary
# ════════════════════════════════════════════════════════════════════════════════════════════════════
_ROTATION_QUERY = "how often does the deploy key rotate"
# json.dumps, not repr(): a text with an apostrophe would break a repr-then-replace round trip, and the
# test would report a durability failure that is really a parsing failure in the probe.
_COLD_READ = ("from inspeximus import Inspeximus\nimport json\n"
              "m = Inspeximus(path={path!r})\n"
              "print(json.dumps([h['text'] for h in m.recall({q!r}, k=3, reinforce=False)]))\n")


def _cold_read(path):
    r = _child(_COLD_READ.format(path=path, q=_ROTATION_QUERY))
    assert r.returncode == 0, f"reader process failed: {r.stderr[-400:]}"
    return json.loads(r.stdout.strip())


def test_p1_a_record_survives_a_genuine_process_boundary():
    """A fresh object in the same process proves nothing -- the store may never have reached disk."""
    p = _path()
    w = _child(f"from inspeximus import Inspeximus\n"
               f"m = Inspeximus(path={p!r})\n"
               f"m.remember('the deploy key rotates every 90 days', key='ops:key-rotation')\n"
               f"m.flush()\nprint('WROTE')\n")
    assert w.returncode == 0 and "WROTE" in w.stdout, f"writer process failed: {w.stderr[-400:]}"
    assert os.path.exists(p), "the write never reached disk, so no later session could ever read it"

    hits = _cold_read(p)
    assert hits, "session 2 read an EMPTY store on the path session 1 wrote to"
    assert "90 days" in hits[0], f"session 1's record is not the top hit in session 2: {hits}"


def test_p1_control_the_probe_would_notice_an_empty_store():
    """Falsification control: the same reader against a path nobody wrote to must come back empty."""
    assert _cold_read(_path()) == [], "the durability probe cannot tell a written store from an empty one"


# ════════════════════════════════════════════════════════════════════════════════════════════════════
# P2 -- RECENCY REACHABILITY: a record written moments ago is findable at small k
# ════════════════════════════════════════════════════════════════════════════════════════════════════
_FRESH_TEXT = "decided to pin the retry budget at 3 attempts for the uploader"
_FRESH_QUERY = "what did we decide about the retry budget for the uploader"


def _write_the_fresh_record(store):
    """Idempotent (one supersession key), so each test can establish its own precondition.

    It has to be idempotent: the two tests below share a module-scoped store, and a control whose
    precondition is set up by the test it is controlling passes VACUOUSLY when run on its own.
    """
    return store.remember(_FRESH_TEXT, key="decision:retry-budget", mtype="semantic", tags=["decision"])


def test_p2a_a_record_written_moments_ago_is_in_the_top_k(dogfood_store):
    """HOLDS. Pinned so a ranking change cannot quietly bury the newest record."""
    m = dogfood_store
    fresh = _write_the_fresh_record(m)
    ids = [h["id"] for h in m.recall(_FRESH_QUERY, k=5, reinforce=False)]
    assert fresh in ids, (f"a record written moments ago is not in the top 5 of {len(m.items)} records; "
                          f"got {[h['text'][:60] for h in m.recall(_FRESH_QUERY, k=5, reinforce=False)]}")
    assert ids[0] == fresh, "the freshest, best-matching record must rank first, not merely appear"


def test_p2a_control_the_probe_can_see_a_record_that_is_genuinely_missing(dogfood_store):
    """If the k=5 assertion above passes for everything it is worthless -- so ask for something absent."""
    fresh = _write_the_fresh_record(dogfood_store)
    assert any(r["id"] == fresh for r in dogfood_store.items), \
        "the target is not in the store, so 'it was not returned' would mean nothing"
    hits = dogfood_store.recall("the quarterly revenue of a company nobody here has mentioned",
                                k=5, reinforce=False)
    assert not any(h["id"] == fresh for h in hits), \
        "the recency probe returns the target for an unrelated query, so it is not measuring retrieval"


@broken("MEASURED 1.89.0: a handle opened in session 1 never sees session 2's write -- recall() returns "
        "0 hits and len(items) stays 1 of 2, silently. Only reload() surfaces it; only a WRITE detects "
        "the divergence (StoreChangedOnDisk). A long-lived MCP reader serves a stale store indefinitely.")
def test_p2b_a_live_handle_converges_on_another_sessions_write():
    """The failure the dogfood 'written today, not at k=5' report was really made of.

    The contract is not "reload() can be called". It is that a session which is still open when
    another session writes does not go on answering from a store that no longer exists.
    """
    p = _path()
    session1 = Inspeximus(path=p)
    session1.remember("session one wrote the alpha fact", key="k:alpha")
    session1.flush()

    w = _child(f"from inspeximus import Inspeximus\n"
               f"w = Inspeximus(path={p!r})\n"
               f"w.remember('session two wrote the bravo fact about the sequencer', key='k:bravo')\n"
               f"w.flush()\n")
    assert w.returncode == 0, f"writer process failed: {w.stderr[-400:]}"

    hits = session1.recall("bravo fact about the sequencer", k=5, reinforce=False)
    assert any("bravo" in h["text"] for h in hits), \
        f"the still-open session cannot see the other session's write ({len(session1.items)} records visible)"


def test_p2b_control_reload_is_what_closes_the_gap_today():
    """Control for the xfail above: prove the write DID land and the probe can see it once reloaded.

    Without this, `test_p2b` failing would be indistinguishable from a broken writer subprocess.
    """
    p = _path()
    session1 = Inspeximus(path=p)
    session1.remember("session one wrote the alpha fact", key="k:alpha")
    session1.flush()
    w = _child(f"from inspeximus import Inspeximus\n"
               f"w = Inspeximus(path={p!r})\n"
               f"w.remember('session two wrote the bravo fact about the sequencer', key='k:bravo')\n"
               f"w.flush()\n")
    assert w.returncode == 0, w.stderr[-400:]

    session1.reload()
    assert any("bravo" in h["text"]
               for h in session1.recall("bravo fact about the sequencer", k=5, reinforce=False)), \
        "even reload() does not surface the other session's write -- the fixture is broken, not the store"


def test_p2b_the_divergence_is_detected_on_the_WRITE_path_only():
    """Pins the asymmetry that makes P2b silent: the store already knows, it just never says so on a read."""
    p = _path()
    session1 = Inspeximus(path=p)
    session1.remember("alpha", key="k:a")
    session1.flush()
    assert _child(f"from inspeximus import Inspeximus\n"
                  f"w = Inspeximus(path={p!r})\nw.remember('bravo about the sequencer', key='k:b')\n"
                  f"w.flush()\n").returncode == 0

    session1.recall("bravo sequencer", k=5, reinforce=False)     # a read: silent, no signal of any kind
    with pytest.raises(StoreChangedOnDisk):
        session1.remember("charlie", key="k:c")
        session1.flush()


# ════════════════════════════════════════════════════════════════════════════════════════════════════
# P3 -- SESSION BOUNDARY: a scoping primitive must exist and be queryable
# ════════════════════════════════════════════════════════════════════════════════════════════════════
def _two_session_store():
    m = Inspeximus(path=_path())
    m.remember("session one decided to use the queue", key="d:1", session_id="s1")
    m.remember("session two decided to use the stream", key="d:2", session_id="s2")
    m.remember("a project-wide fact that belongs to no session", key="d:3")
    m.flush()
    return m


def test_p3_the_session_filter_isolates_peer_sessions():
    """HOLDS today, and is the half of the feature that exists: `meta.sid` + a hard recall filter."""
    m = _two_session_store()
    s1 = [h["text"] for h in m.recall("decided", k=5, session_id="s1", reinforce=False)]
    s2 = [h["text"] for h in m.recall("decided", k=5, session_id="s2", reinforce=False)]
    assert any("queue" in t for t in s1) and not any("stream" in t for t in s1), s1
    assert any("stream" in t for t in s2) and not any("queue" in t for t in s2), s2


def _session_id_collections(value, depth=0):
    """Every set of strings `value` presents AS A COLLECTION: list/tuple/set members, or dict keys.

    The tightening matters. An earlier version of this probe json-dumped each public attribute and
    grepped for the ids -- and it PASSED on today's store, because `items` returns the raw records and
    every record carries `meta.sid`. That is not discoverability: the ids were sitting inside a record
    dump that the caller would have to know to go looking through. A primitive presents sessions AS
    sessions, so only collections of ids count.
    """
    out = []
    if isinstance(value, dict):
        out.append({str(k) for k in value})
        if depth < 2:
            for v in value.values():
                out.extend(_session_id_collections(v, depth + 1))
    elif isinstance(value, (list, tuple, set, frozenset)):
        members = list(value)
        if members and all(isinstance(x, str) for x in members):
            out.append(set(members))
        elif depth < 2:
            for v in members[:50]:
                out.extend(_session_id_collections(v, depth + 1))
    return out


def _sessions_discoverable_from(store):
    """Which session ids can a resuming agent obtain WITHOUT already knowing them?

    Implementation-independent on purpose: it walks the public surface and accepts ANY zero-argument
    callable or property that presents the session ids as a collection -- `sessions()` returning a
    list, a `session_report()` keyed by id, either works. What does not work is `meta.sid`, which only
    answers when you already hold the key, and that is exactly the gap.
    """
    found = set()
    for name in dir(store):
        if name.startswith("_"):
            continue
        try:
            attr = getattr(store, name)
            out = attr() if callable(attr) else attr
        except Exception:
            continue
        for collection in _session_id_collections(out):
            found |= {x for x in collection if re.fullmatch(r"s\d+", x)}
    return found


def test_p3_control_the_discovery_probe_finds_the_primitive_when_it_exists():
    """Falsification control: attach a boundary primitive and the probe must see it.

    Without this, the xfail below would be satisfied by a probe that can never find anything.
    """
    m = _two_session_store()

    class _WithBoundary:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, n):
            return getattr(self._inner, n)

        def sessions(self):
            return sorted({(r.get("meta") or {}).get("sid") for r in self._inner.items
                           if (r.get("meta") or {}).get("sid")})

    assert _sessions_discoverable_from(_WithBoundary(m)) == {"s1", "s2"}, \
        "the discovery probe cannot see a session boundary even when one is present"


@broken("MEASURED 1.89.0: there is no session boundary in the data model. The entire feature is "
        "remember(session_id=) stamping meta.sid (core.py:1361) + recall(session_id=) hard-filtering "
        "(core.py:5336). [n for n in dir(Inspeximus) if 'session' in n] == ['supersession_report'] -- "
        "no enumeration, no boundary object, no digest. Our 3-of-5 resume miss was a SCOPING question "
        "asked of a ranking system, so no ranking change can fix it.")
def test_p3_a_resuming_agent_can_discover_which_sessions_the_store_holds():
    """A resuming agent does not know the prior session id -- that is what "resuming" means."""
    m = _two_session_store()
    assert _sessions_discoverable_from(m) == {"s1", "s2"}, \
        "the store cannot say which sessions it holds, so session 1 is unreachable without prior knowledge"


# ════════════════════════════════════════════════════════════════════════════════════════════════════
# P4 -- SCOPE ISOLATION: memories do not leak between projects / workspaces
# ════════════════════════════════════════════════════════════════════════════════════════════════════
_A_FACT = "project alpha uses the postgres connection pool"
_A_QUERY = "postgres connection pool"


@broken("MEASURED 1.89.0: mcp_server.py:99 reads INSPEXIMUS_PATH once at import, relative to the "
        "client CWD, and there is no project dimension anywhere in the record. Two projects sharing "
        "one configured path share one store: project B's DEFAULT recall returns project A's record. "
        "The `tenant=` binding does isolate (control below), but it is opt-in and per-handle, so a "
        "caller who does not know to pass it gets the leak.")
def test_p4_a_second_project_cannot_read_the_first_projects_memory():
    """The default must be safe. An isolation you have to remember to switch on is not isolation."""
    p = _path("shared.json")
    a = Inspeximus(path=p)
    a.remember(_A_FACT, key="a:db")
    a.flush()

    b = Inspeximus(path=p)                       # a different project, same configured store path
    leaked = [h["text"] for h in b.recall(_A_QUERY, k=5, reinforce=False) if "alpha" in h["text"]]
    assert not leaked, f"project B's default recall returned project A's memory: {leaked}"


def test_p4_control_the_leak_probe_sees_isolation_when_it_exists():
    """The same fixture with a hard tenant binding must come back clean, or the probe proves nothing.

    This control says ONLY that the read direction can be isolated and that the probe can see it. It is
    not an endorsement of `tenant=` as the project mechanism -- the two tests below measure what the
    same binding does to the WRITE path.
    """
    p = _path("shared.json")
    a = Inspeximus(path=p, tenant="proj-a")
    a.remember(_A_FACT, key="a:db")
    a.flush()

    b = Inspeximus(path=p, tenant="proj-b")
    assert not [h for h in b.recall(_A_QUERY, k=5, reinforce=False) if "alpha" in h["text"]], \
        "the leak probe reports a leak even under hard tenant isolation -- it cannot see isolation at all"


def test_p4_control_the_record_is_actually_present_in_the_shared_store():
    """Guards the reverse failure: an empty store would satisfy the isolation assertion for free."""
    p = _path("shared.json")
    a = Inspeximus(path=p)
    a.remember(_A_FACT, key="a:db")
    a.flush()
    b = Inspeximus(path=p)
    assert len(b.items) == 1, "the shared store is empty, so any isolation verdict on it is vacuous"


# Isolation is not a read-side filter. These two put the WRITE path under the same property, because a
# scope that only filters reads leaves both of the failures below intact -- and both are silent.
_CADENCE_KEY = "release::cadence"


@broken("MEASURED 1.89.0: supersession keys are GLOBAL, not namespaced by project. Two projects on one "
        "store writing the same key -- and `release::cadence` or `decision::<topic>` is exactly the key "
        "shape the product tells people to use -- collide: B's write SUPERSEDES A's record. Measured on "
        "a plain shared store, project A afterwards sees 0 of its own facts and 1 of B's; with a "
        "meta scope= read filter A sees 0 and 0, i.e. its own answer is silently blank. A read-side "
        "scope filter over a global key space converts a leak into data loss, which is worse.")
def test_p4_a_second_projects_write_cannot_supersede_the_firsts():
    """Project A's answer must survive project B using the same supersession key."""
    p = _path("shared.json")
    a = Inspeximus(path=p)
    a.remember("project A ships on Fridays", key=_CADENCE_KEY, meta={"scope": "proj-a"})
    a.flush()

    b = Inspeximus(path=p)
    b.remember("project B ships on Mondays", key=_CADENCE_KEY, meta={"scope": "proj-b"})
    b.flush()

    a2 = Inspeximus(path=p)
    mine = [h["text"] for h in a2.recall("when does the project ship", k=5, scope="proj-a", reinforce=False)]
    assert any("Fridays" in t for t in mine), \
        f"project A's own fact is gone after project B wrote the same key; A now sees {mine}"


@broken("MEASURED 1.89.0, and this one DESTROYS DATA: the only hard isolation on offer (`tenant=`) makes "
        "`items` a tenant-filtered VIEW (core.py:3288) while `_save` serialises `self.items` "
        "(core.py:7809) -- so a tenant-bound handle's save writes ONLY its own rows and the other "
        "tenant's records are gone from the file. Measured: 0 of 3 of project A's rows survive project "
        "B's first flush(); two UNBOUND handles on the same file keep both. The single-writer guard does "
        "not fire, because B loaded after A's flush -- a legitimate sequential handoff. The `items` "
        "SETTER already refuses this exact move ('a whole-list write from a tenant-bound store would "
        "drop every other tenant's records'); the persist path reads the same view and was missed.")
def test_p4_a_second_projects_save_cannot_destroy_the_firsts_records():
    """The severest form of a scope failure: not a leak, an erasure."""
    p = _path("shared.json")
    a = Inspeximus(path=p, tenant="proj-a")
    for i in range(3):
        a.remember(f"project A fact {i} about the release cadence", key=f"a::{i}")
    a.flush()

    b = Inspeximus(path=p, tenant="proj-b")
    b.remember("project B fact about the release cadence", key="b::0")
    b.flush()

    on_disk = _read_json(p)
    survived = [r for r in on_disk if r.get("tenant") == "proj-a"]
    assert len(survived) == 3, \
        f"project B's save destroyed project A's records: {len(survived)} of 3 left on disk"


def test_p4_control_two_unbound_handles_do_not_destroy_each_others_records():
    """Falsification control: the destruction is specific to the binding, not to sharing a file.

    Without this, the test above would be satisfied by any store that loses records for any reason, and
    the finding would be 'shared files are unsafe' rather than the much narrower, fixable thing it is.
    """
    p = _path("shared.json")
    a = Inspeximus(path=p)
    a.remember("project A fact about the release cadence", key="a::0")
    a.flush()
    b = Inspeximus(path=p)
    b.remember("project B fact about the release cadence", key="b::0")
    b.flush()

    on_disk = _read_json(p)
    assert len(on_disk) == 2, f"two unbound handles already lose records ({len(on_disk)} of 2 on disk)"


# ════════════════════════════════════════════════════════════════════════════════════════════════════
# P5 -- READ PURITY: query N+1 must not depend on queries 1..N
#
# Three separable things live here and only one is a defect. Run-to-run determinism HOLDS. Permutation
# invariance is DELIBERATELY FALSE (core.py:5584 sorts on (-score, -insertion_position): equal
# relevance -> the more recent record first, and `tie_recent` is built on that policy) -- so the
# declared policy is asserted instead of invariance. Read purity is the defect.
# ════════════════════════════════════════════════════════════════════════════════════════════════════
def _twenty_record_store():
    m = Inspeximus(path=_path())
    for i in range(20):
        m.remember(f"fact {i} about the deployment pipeline stage {i}", key=f"k{i}", mtype="episodic")
    m.flush()
    return m


def _value_vector(store):
    return [(r["id"], r["value"], r.get("last_access")) for r in store.items]


# Every one of these is documented read-only. `items` is included because a property that mutated
# would be the quietest defect of the lot.
def _read_only_surfaces(store):
    return [
        ("recall", lambda: store.recall("deployment pipeline", k=6)),
        ("recall(other query)", lambda: store.recall("stage 7", k=3)),
        ("why_recalled", lambda: store.why_recalled("deployment pipeline")),
        ("memory_report", lambda: store.memory_report()),
        ("selection_integrity", lambda: store.selection_integrity("deployment pipeline")),
        ("contradictions", lambda: store.contradictions()),
        ("history", lambda: store.history("k1")),
        ("provenance", lambda: store.provenance("k1")),
        ("graph", lambda: store.graph()),
        ("supersession_report", lambda: store.supersession_report()),
        ("items", lambda: store.items),
    ]


def test_p5_replay_of_the_same_query_is_deterministic():
    """HOLDS. The floor the purity property is measured against -- without it nothing else is readable."""
    m = _twenty_record_store()
    first = [h["id"] for h in m.recall("deployment pipeline stage", k=5, reinforce=False)]
    for _ in range(4):
        assert [h["id"] for h in m.recall("deployment pipeline stage", k=5, reinforce=False)] == first


def _deterministic_embedder(dim=64):
    """A hashed bag-of-tokens embedder: real vectors, no model, no network, no dependency.

    It exists because of a measured trap. A test parametrized over recall modes with NO embedder runs
    the LEXICAL path in every one of them -- `mode in ('semantic','hybrid')` falls back to lexical when
    `self.embed` is None (core.py:5385-5392) -- so a four-mode suite reports four passes for one path.
    Every mode test below therefore asserts `_last_mode`, which is the store's own record of the path
    it actually took.
    """
    def embed(text):
        v = [0.0] * dim
        for tok in text.lower().split():
            v[int(hashlib.sha256(tok.encode()).hexdigest()[:16], 16) % dim] += 1.0
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / norm for x in v]
    return embed


def _tie_fixture(mode):
    """Two equivalent records, plus enough background that centring is not degenerate.

    The background is load-bearing and was measured: on a TWO-record store `center_embeddings` (on by
    default) subtracts a mean computed from those two vectors, which annihilates the signal -- semantic
    recall returned 0 hits and hybrid split the pair 1.0 / 0.984 with the OLDER record first. That is a
    property of a 2-record corpus, not of the ranker, and building the contract on it would have pinned
    a fixture artefact as a finding.
    """
    m = Inspeximus(path=_path(), embed=_deterministic_embedder())
    if mode == "auto":
        m.semantic_threshold = 0            # 'auto' fuses only past the threshold; force the fused path
    for i in range(20):
        m.remember(f"unrelated note {i} about the shipping queue stage {i}", key=f"bg{i}")
    m.remember("the release checklist lives in docs alpha", key="k:a")
    later = m.remember("the release checklist lives in docs bravo", key="k:b")
    return m, later


@pytest.mark.parametrize("mode,expect_path", [("lexical", "lexical"), ("semantic", "semantic"),
                                              ("hybrid", "hybrid"), ("auto", "hybrid")])
def test_p5_the_declared_tie_break_is_recency_not_invariance(mode, expect_path):
    """core.py:5584, deliberate: equal relevance -> the more recently inserted record first.

    Asserted as the POLICY and CONDITIONALLY, because the policy can only fire where a tie exists.
    Measured here, 22-record store, deterministic embedder:
        lexical  relevance 1.0 / 1.0    tied     -> newer first
        hybrid   relevance 1.0 / 1.0    tied     -> newer first
        auto     relevance 1.0 / 1.0    tied     -> newer first  (routes to hybrid)
        semantic relevance 0.94 / 0.938 NOT tied -> the policy never fires
    Sibling unit A5 measured the tie DISSOLVED in hybrid and auto on their corpus (RRF assigning
    equivalent records distinct fused scores, older first). Mine does not reproduce that, and the two
    are not in conflict: whether RRF separates a pair is corpus-dependent, which is itself the reason
    this is asserted as "if tied, newer wins" rather than as "the top hit is the newer record".
    """
    m, later = _tie_fixture(mode)
    hits = m.recall("the release checklist lives in docs", k=2, mode=mode, reinforce=False)

    assert m._last_mode == expect_path, \
        f"mode={mode!r} actually ran the {m._last_mode!r} path, so this case is not testing what it names"
    assert len(hits) >= 2, f"the fixture returned {len(hits)} hits in {mode!r}; nothing to compare"
    if hits[0]["relevance"] == hits[1]["relevance"]:
        assert hits[0]["id"] == later, \
            f"{mode!r}: equal relevance must put the MORE RECENT record first (declared policy)"


def test_p5_control_the_mode_parametrization_is_not_four_copies_of_lexical():
    """The trap the sibling warned about, asserted directly rather than trusted.

    Without an embedder every mode falls back to lexical, and a four-mode suite measures one path four
    times while reporting four passes.
    """
    without = Inspeximus(path=_path())
    without.remember("a record about the shipping queue", key="k")
    for mode in ("semantic", "hybrid", "auto"):
        without.recall("shipping queue", k=2, mode=mode, reinforce=False)
        assert without._last_mode == "lexical", "the no-embedder fallback has changed; re-read this control"

    seen = set()
    for mode, expected in (("lexical", "lexical"), ("semantic", "semantic"),
                           ("hybrid", "hybrid"), ("auto", "hybrid")):
        m, _ = _tie_fixture(mode)
        m.recall("the release checklist lives in docs", k=2, mode=mode, reinforce=False)
        assert m._last_mode == expected
        seen.add(m._last_mode)
    assert seen == {"lexical", "semantic", "hybrid"}, \
        f"the parametrization exercises only {seen}, not three distinct ranking paths"


@broken("MEASURED 1.89.0: recall('deployment pipeline', k=6) changes 6 of 20 `value` fields + their "
        "last_access and sets _dirty=True; the change reaches disk on the next save. memory_report() -- "
        "docstring 'Read-only' -- changes 2 of 20 because it calls self.recall(...) at the default "
        "reinforce=True, while the sibling why_recalled() passes reinforce=False with a comment naming "
        "this exact hazard (core.py:7021). So query N+1 depends on queries 1..N, across sessions.")
def test_p5_a_sweep_of_every_read_only_surface_leaves_the_store_unchanged():
    """The strongest form: read everything the docs call read-only, and nothing may move."""
    m = _twenty_record_store()
    before_values, before_digest = _value_vector(m), m.state_digest()

    moved = []
    for name, call in _read_only_surfaces(m):
        v0 = _value_vector(m)
        call()
        if _value_vector(m) != v0:
            moved.append(name)

    assert not moved, f"documented read-only surfaces mutated the store: {moved}"
    assert _value_vector(m) == before_values, "the read sweep changed the value vector"
    assert m.state_digest() == before_digest, "the read sweep changed the state digest"
    assert not getattr(m, "_dirty", False), "a read left the store dirty, so the mutation will be persisted"


def test_p5_control_state_digest_alone_cannot_serve_as_the_purity_instrument():
    """A check that never sees its target reports SAFE.

    `state_digest()` does not cover `value`/`last_access`, so a purity gate written against the digest
    alone is green on a store that a read just rewrote. Conditional, so it does not false-alarm once
    read purity is fixed: it only fires if the digest starts covering what it currently cannot see.
    """
    m = _twenty_record_store()
    v0, d0 = _value_vector(m), m.state_digest()
    m.recall("deployment pipeline", k=6)
    if _value_vector(m) != v0:
        assert m.state_digest() == d0, \
            "state_digest now covers value/last_access -- it CAN serve as the purity instrument; simplify this suite"


def test_p5_control_the_purity_probe_catches_a_mutation_it_is_shown():
    """Falsification control: hand the comparator a mutation and it must report it."""
    m = _twenty_record_store()
    v0 = _value_vector(m)
    m.items[0]["value"] += 0.25
    assert _value_vector(m) != v0, "the purity comparator cannot see a value change put in front of it"


def test_p5_the_non_mutating_read_is_available_and_is_exactly_pure():
    """The control the defect is measured against: reinforce=False must move NOTHING."""
    m = _twenty_record_store()
    v0 = _value_vector(m)
    for _ in range(5):
        m.recall("deployment pipeline", k=6, reinforce=False)
    assert _value_vector(m) == v0, "even reinforce=False mutates -- there is no pure read at all"


# ── the consequence: does the ANSWER depend on the order the earlier questions were asked? ───────────
def _answer_divergence(reinforce, n_records=60, n_queries=30, orders=6, seed=3):
    """Ask one query set in several orders and count how many ANSWERS change. Returns (changed, total).

    Answers are compared by TEXT, not by id: each order gets a fresh store (it has to, since with
    reinforce=True the reads are writes) and ids are per-store. An earlier version compared ids and
    reported 1.0000 divergence for BOTH arms -- including the control that should be exactly zero,
    which is how the bug announced itself.
    """
    rnd = random.Random(seed)
    words = ["deploy", "cache", "index", "retry", "quota", "schema", "token", "shard", "queue", "audit"]
    corpus = [f"record {i} about the {rnd.choice(words)} {rnd.choice(words)} in stage {i % 7}"
              for i in range(n_records)]
    queries = [f"the {rnd.choice(words)} {rnd.choice(words)} in stage {rnd.randrange(7)}"
               for _ in range(n_queries)]

    def answers(order):
        m = Inspeximus(path=_path())
        for i, text in enumerate(corpus):
            m.remember(text, key=f"k{i}")
        out = {}
        for qi in order:
            hits = m.recall(queries[qi], k=1, reinforce=reinforce)
            out[qi] = hits[0]["text"] if hits else None
        return out

    canonical = answers(list(range(n_queries)))
    changed = total = 0
    for o in range(orders):
        order = list(range(n_queries))
        random.Random(100 + o).shuffle(order)
        got = answers(order)
        for qi in canonical:
            total += 1
            changed += got[qi] != canonical[qi]
    return changed, total


def test_p5_control_query_order_independence_is_achievable_and_the_probe_reads_exactly_zero():
    """The control that makes the divergence number mean something: reinforce=False must be EXACTLY 0.

    A divergence probe that cannot reach zero is measuring its own noise, and every number it produces
    afterwards is unreadable.
    """
    changed, total = _answer_divergence(reinforce=False)
    assert total == 180, f"the fixture changed shape ({total} comparisons); the pinned numbers are stale"
    assert changed == 0, \
        f"the non-mutating read is NOT order-independent ({changed}/{total}); the probe measures noise"


@broken("MEASURED 1.89.0, this file's own fixture (60 records, 30 queries, 6 shuffled orders, 180 "
        "comparisons): with the SHIPPED default reinforce=True, 45/180 = 0.2500 of answers change when "
        "only the ORDER of the earlier questions changes. The control is exactly 0/180 = 0.0000, so the "
        "instrument is not reading its own noise. Sibling unit A1 measured 0.318-0.605 on a different "
        "corpus (same direction, same zero control) and that reinforcement COSTS accuracy, 20/20 "
        "held-out comparisons negative. Across a session boundary this is the sharp form: session 2's "
        "answer depends on what session 1 happened to ask.")
def test_p5_the_answer_does_not_depend_on_the_order_earlier_questions_were_asked():
    """Distinct from the tie-break above, and not in conflict with it.

    The tie-break is about the order records were WRITTEN (deliberate, and asserted as policy). This is
    about the order questions were ASKED, which nothing in the design intends to matter -- and it is
    reachable, because the same probe reads exactly 0.0000 with reinforce=False.
    """
    changed, total = _answer_divergence(reinforce=True)
    assert changed == 0, \
        f"{changed}/{total} = {changed / total:.4f} of answers changed on a reorder of the same queries"


# ════════════════════════════════════════════════════════════════════════════════════════════════════
# P6 -- OFF SWITCH: every cross-session injection mechanism can be disabled
# ════════════════════════════════════════════════════════════════════════════════════════════════════
def _injection_mechanisms(project_dir):
    """The shipped mechanisms whose stdout Claude Code injects into the agent's context.

    Derived from the installer rather than hand-listed, so a mechanism added by C2/C3 is picked up:
    `install()` writes the hook events the plugin claims, and PostToolUse is a capture (a write), not
    an injection. Asserted non-empty by the control below -- an empty enumeration would make the
    off-switch test pass for free.
    """
    handlers = {
        "SessionStart": lambda: cc.session_start({"hook_event_name": "SessionStart", "cwd": project_dir}),
        "UserPromptSubmit": lambda: cc.recall({"hook_event_name": "UserPromptSubmit",
                                               "cwd": project_dir, "prompt": "what is in mod3.py"}),
    }
    # SessionEnd is deliberately NOT here, for the same reason PostToolUse is not: Claude Code DISCARDS
    # SessionEnd stdout (the session is ending; there is no context left to inject into), so it is a
    # WRITE, not an injection. C2's SessionEnd writes the digest and prints nothing; its output reaches
    # the model one hook later, through SessionStart, which is enumerated above and is where the off
    # switch has to be provable. Listing it here asserted "this write injects something" and failed on a
    # handler behaving exactly as its contract requires.
    return [(e, handlers[e]) for e in sorted(_installed_hook_events() & set(handlers))]


def _installed_hook_events():
    """The hook events `install()` actually writes, read back out of the settings file it wrote."""
    d = tempfile.mkdtemp()
    _capture_stdout(lambda: cc.install(cwd=d))          # install() prints; keep it out of the test log
    return set(_read_json(os.path.join(d, ".claude", "settings.json")).get("hooks", {}))


def _declared_env_switches():
    """Every INSPEXIMUS_* name the package declares, minus the ones that merely redirect the store.

    PATH/URL/KEY/MODEL are excluded because setting them would silence a mechanism by pointing it at a
    different (empty) store -- that is a false positive, not an off switch.
    """
    names = set()
    for root, _dirs, files in os.walk(os.path.join(REPO, "inspeximus")):
        for fn in files:
            if fn.endswith(".py"):
                with open(os.path.join(root, fn), encoding="utf-8", errors="ignore") as fh:
                    names.update(re.findall(r"INSPEXIMUS_[A-Z0-9_]+", fh.read()))
    # STORE joined the list on 2026-08-05: INSPEXIMUS_CODING_STORE is a path, and setting it to "0"
    # silenced every mechanism by pointing the hook at an empty directory. That is the exact false
    # positive this filter was written to exclude; it just did not name the variable.
    return sorted(n for n in names
                  if not any(w in n for w in ("PATH", "URL", "KEY", "MODEL", "STORE")))


def _silenced_by(handler, var, value):
    old = os.environ.get(var)
    os.environ[var] = value
    try:
        return _capture_stdout(handler).strip() == ""
    finally:
        if old is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = old


def test_p6_control_the_injection_mechanisms_exist_and_actually_inject():
    """Assert the target EXISTS and RESOLVES before asserting anything about switching it off."""
    proj = tempfile.mkdtemp()
    _replay_session_one(proj)
    mechanisms = _injection_mechanisms(proj)
    assert mechanisms, "no cross-session injection mechanism found -- the off-switch test would be vacuous"
    silent = [name for name, handler in mechanisms if not _capture_stdout(handler).strip()]
    assert not silent, f"these mechanisms inject nothing even with a stocked store, so they cannot be tested: {silent}"


# FIXED 2026-08-05 by INSPEXIMUS_NO_INJECT. Was broken since 1.89.0: neither shipped injection
# mechanism was silenced by any declared variable -- the only switches were INSPEXIMUS_NO_NUDGE (the
# star ask) and INSPEXIMUS_NO_UPDATE_CHECK (the version line), both for lines riding alongside the
# injection rather than for the injection itself. INSPEXIMUS_SESSION_DIGEST gated the digest but not
# the file list beside it and not the recall block at all. The marker is removed rather than the
# assertion relaxed: this test now holds, and if it stops holding that is a regression, not a known gap.
def test_p6_every_injection_mechanism_has_an_off_switch_that_injects_nothing():
    """An injection an operator cannot turn off is not a feature they can adopt."""
    proj = tempfile.mkdtemp()
    _replay_session_one(proj)
    switches = _declared_env_switches()
    without = []
    for name, handler in _injection_mechanisms(proj):
        if not any(_silenced_by(handler, var, val) for var in switches for val in ("0", "1")):
            without.append(name)
    assert not without, \
        (f"no declared switch disables these injection mechanisms: {without}; "
         f"tried {len(switches)} variables x 2 values: {switches}")


# ════════════════════════════════════════════════════════════════════════════════════════════════════
# P7 -- DIGEST DETERMINISM: the same event log must produce a byte-identical digest
# ════════════════════════════════════════════════════════════════════════════════════════════════════
def _session_digest(project_dir):
    return _capture_stdout(lambda: cc.session_start({"hook_event_name": "SessionStart", "cwd": project_dir}))


def test_p7a_the_same_event_log_yields_a_byte_identical_digest():
    """HOLDS -- and it is the zero-LLM claim made falsifiable: a summariser cannot pass this.

    Two DIFFERENT directories, so the comparison also proves the output is a function of the event log
    and not of where it happened.
    """
    a, b = tempfile.mkdtemp(), tempfile.mkdtemp()
    _replay_session_one(a)
    _replay_session_one(b)

    first = _session_digest(a)
    assert first.strip(), "the digest is empty, so byte-equality below would be vacuous"
    assert _session_digest(a) == first, "the digest is not stable across two runs in one process"
    assert _session_digest(b) == first, "the digest depends on the directory, not on the event log"


def test_p7a_across_a_process_boundary():
    """The boundary that matters: session 2 is a NEW process, not a second call."""
    proj = tempfile.mkdtemp()
    _replay_session_one(proj)
    here = _session_digest(proj)

    there = _child(f"import inspeximus.claude_code as cc\ncc.session_start({{'cwd': {proj!r}}})\n")
    assert there.returncode == 0, there.stderr[-400:]
    assert there.stdout.replace("\r\n", "\n") == here, "the digest differs between processes"


def test_p7a_control_the_comparator_catches_a_non_deterministic_digest():
    """Positive control: a digest that is not a pure function of the event log must be rejected.

    A per-run token, not a clock: `time.time_ns()` returns the SAME value for two adjacent calls on
    this machine (Windows tick granularity), so a clock-based fixture made this control pass for the
    wrong reason -- it never produced two different strings to compare.
    """
    ticket = itertools.count()

    def impure_digest():
        return f"[inspeximus] digest, summariser pass {next(ticket)}"

    assert impure_digest() != impure_digest(), \
        "the determinism comparator cannot see a digest that changes between runs"


def test_p7b_a_session_end_digest_exists_and_is_installed():
    """C2's contract: a session must be able to close itself into something the next one can read.

    WAS `@broken` ("MEASURED 1.89.0: there is no SessionEnd hook and no digest primitive"). It went
    XPASS(strict) in 1.91.0, which is this suite working as designed -- a false property that starts
    passing turns the run RED so the claim gets re-measured instead of rotting into a silent pass. The
    marker is removed rather than relabelled, so from here the property is asserted, not merely expected
    to fail. Extended past the two original assertions to pin the behaviour, not just the wiring: a
    handler that exists and writes nothing would have satisfied the 1.89.0 contract."""
    d = tempfile.mkdtemp()
    cc.install(cwd=d)
    events = set(_read_json(os.path.join(d, ".claude", "settings.json")).get("hooks", {}))
    assert "SessionEnd" in events, f"no SessionEnd hook is installed; only {sorted(events)}"
    assert hasattr(cc, "session_end"), "no session_end handler exists to produce a digest"

    proj = tempfile.mkdtemp()
    m = cc._store(proj)
    m.open_session("s1")
    m.remember_decision("use Postgres for the ledger", because="sqlite locks", topic="db",
                        session_id="s1")
    m._save(force=True)
    rep = cc.session_end({"hook_event_name": "SessionEnd", "cwd": proj, "session_id": "s1"})
    assert rep.get("written") is True and rep.get("items", 0) >= 1, \
        f"SessionEnd is installed but closed the session into nothing: {rep}"
    assert "Postgres" in _capture_stdout(
        lambda: cc.session_start({"hook_event_name": "SessionStart", "cwd": proj,
                                  "session_id": "s2"})), \
        "the digest was written but the next session was not told about it"


# ════════════════════════════════════════════════════════════════════════════════════════════════════
# THE SUITE ITSELF -- can it fail?
#
# Every property above that is false is marked xfail(strict=True), which keeps the default run green.
# A green run is exactly what a conformance suite is not allowed to be mistaken for, so these two
# tests make "this suite can fail" a measured fact rather than a claim in a docstring.
# ════════════════════════════════════════════════════════════════════════════════════════════════════
def test_the_suite_marks_the_broken_properties_and_can_be_run_without_the_markers():
    """`INSPEXIMUS_CONFORMANCE_STRICT=1` must REMOVE the marker, not merely relabel it."""
    marker = broken("because")
    if _STRICT:
        def sample():
            return None
        assert marker(sample) is sample, "strict mode must leave the test undecorated, so it can fail"
    else:
        mark = getattr(marker, "mark", None)
        assert mark is not None and mark.name == "xfail", "broken() does not produce an xfail marker"
        assert mark.kwargs.get("strict") is True, \
            "the xfail is not strict, so a fixed property would rot into a silent pass"
        assert mark.kwargs.get("reason"), "an xfail without a reason records no measurement"


@pytest.mark.skipif(os.environ.get(_CHILD_FLAG) == "1",
                    reason="this test re-runs the file in a child process; it must not recurse")
def test_a_strict_run_on_this_commit_reports_the_broken_properties():
    """Run this file in strict mode, in a child process, and require that it FAILS.

    This is the check that stops the suite rotting into decoration. Every false property above is
    xfailed, so the default run is GREEN -- and a green conformance suite is indistinguishable from
    one that tests nothing. This makes "it can fail, and it does fail on this commit" a measured fact.

    Recursion is stopped by an environment flag, not by `--deselect`: a `--deselect` with an absolute
    path does not match the collected node id, so the child re-ran this test, spawned its own child,
    and the suite hung until the harness killed it at 2 minutes. Measured.
    """
    r = subprocess.run([sys.executable, "-m", "pytest", os.path.abspath(__file__), "-q",
                        "-p", "no:cacheprovider"],
                       capture_output=True, text=True, cwd=REPO,
                       env={**os.environ, "INSPEXIMUS_CONFORMANCE_STRICT": "1", _CHILD_FLAG: "1",
                            "INSPEXIMUS_NO_UPDATE_CHECK": "1", "PYTHONIOENCODING": "utf-8"})
    assert r.returncode != 0, \
        ("a strict run is GREEN on this commit: either every property has been fixed (delete the "
         "broken() markers and this test) or the suite has stopped being able to fail.\n" + r.stdout[-2000:])
    failed = re.search(r"(\d+) failed", r.stdout)
    expected = _BROKEN_AT_IMPORT
    assert expected, "no property is marked broken(), so this check would pass over nothing"
    # EXACT, not a floor. A floor only notices one of the two ways this drifts. Fewer failures than
    # markers means a marker names a property that is no longer false (delete it, and assert the
    # property instead) or that strict mode did not actually drop the markers; MORE failures than
    # markers means something is failing that nobody wrote down as false, which is the more interesting
    # direction and the one a floor swallows in silence.
    assert failed and int(failed.group(1)) == expected, \
        (f"a strict run should fail on exactly the {expected} properties this file marks broken(), "
         f"got {failed.group(1) if failed else 'no failure count'}:\n{r.stdout[-2000:]}")


# ── snapshot, taken after every decorator above has been applied ─────────────────────────────────────
# The count has to be frozen at IMPORT. `broken()` is also called at RUN time -- the marker meta-test
# above calls `broken("because")` on a throwaway to check the marker it produces -- and a live len() of
# the list therefore reads 9 markers in a file that has 8, but only once that test has run, i.e. the
# number depends on test ORDER. Freezing here counts exactly the decorator applications, which is the
# thing the strict child's failure count is comparable to.
_BROKEN_AT_IMPORT = len(_BROKEN_MARKERS)
