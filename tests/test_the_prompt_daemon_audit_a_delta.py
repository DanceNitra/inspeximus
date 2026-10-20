"""3.18 prototype: the prompt daemon against AUDIT-A's delta review of d7c9f2bb, findings E-1 to E-5.

E-1 relative path variables, E-2 once-per-process notices, E-3 the daemon count, E-4 a silent connection, E-5 records
without a start time. The fixtures are the daemon suite's.
"""
from __future__ import annotations

import ast
import glob
import io
import json
import os
import re
import sqlite3
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from inspeximus import claude_code as cc  # noqa: E402
from inspeximus import hookd  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402
from test_the_prompt_daemon_audit_a_findings import _start, _stop, _write  # noqa: E402
from test_the_prompt_daemon_is_an_accelerator_never_a_dependency import (  # noqa: E402,F401
    _ev, _req, _stop_started_daemons, _today, daemon, project, switch_on, switched_on)


# ── E-1: relative path variables ───────────────────────────────────────────────────────────────────────────────
def test_a_relative_decision_store_is_never_asked_for(project, daemon, monkeypatch):
    """The hook resolves a relative path against the repository and the daemon against its own folder, so an answer
    from the daemon left the decisions out and still read `served`. A relative path variable means today's path."""
    proj, sp = project
    monkeypatch.chdir(proj)
    # Outside the project: since 3.18 (EC-6) a decision store the environment names inside the project is ignored, so a
    # relative name that stays in the project would test that rule and not this one.
    outside = os.path.join(os.path.dirname(proj), "outside")
    os.makedirs(outside, exist_ok=True)
    e = Inspeximus(os.path.join(outside, "dec.json"))
    e.remember("we decided QUOKKARULE governs the deploy window", key="decision::q", tags=["decision"])
    e.flush()
    monkeypatch.setenv("INSPEXIMUS_DECISION_STORE", os.path.join("..", "outside", "dec.json"))
    assert "QUOKKARULE" in _today(proj), "CONTROL: today's path reads the relative decision store"
    assert hookd.ask(_ev(proj), sp, timeout=2.0) is None and hookd.LAST["outcome"] == "relative"


def test_the_daemon_refuses_a_relative_path_of_its_own(project, daemon, monkeypatch):
    proj, sp = project
    req = _req(daemon, proj)
    monkeypatch.setenv("INSPEXIMUS_DECISION_STORE", "dec.json")
    assert daemon.answer(req)["reason"] == "relative-path"


def test_the_daemon_refuses_other_absolute_paths(project, daemon, tmp_path):
    proj, sp = project
    other = {"INSPEXIMUS_DECISION_STORE": hookd._real(str(tmp_path / "elsewhere.json"))}
    assert daemon.answer(_req(daemon, proj, paths=other))["reason"] == "paths"
    assert daemon.answer(_req(daemon, proj))["ok"], "CONTROL: the same paths are answered"


def test_serve_starts_no_daemon_under_a_relative_path(project, monkeypatch):
    proj, sp = project
    monkeypatch.setenv("INSPEXIMUS_DECISION_STORE", "dec.json")
    assert hookd.serve_main(["x", "--serve", "--expect-store", sp, "--project", proj]) == 2
    assert hookd.live_daemon(sp) is None


def test_a_hex_receipt_key_is_not_a_path():
    assert hookd.path_values({"INSPEXIMUS_RECEIPT_KEY": "ab" * 32}) == {}
    assert hookd.path_values({"INSPEXIMUS_RECEIPT_KEY": "keys/receipt.key"}) is None


#: Every INSPEXIMUS_* variable the package reads that does NOT name a file or folder. A new variable must be added to
#: hookd.PATH_VARS or here, so a path variable cannot be added without the daemon resolving it.
NOT_PATHS = {
    "INSPEXIMUS_ACTIONS", "INSPEXIMUS_ACTOR", "INSPEXIMUS_AGENT_ID", "INSPEXIMUS_ARCHIVE_AUTO",
    "INSPEXIMUS_BUSY_TIMEOUT_S", "INSPEXIMUS_DECISION_STORE_MAX_MB", "INSPEXIMUS_ECHO_GUARD", "INSPEXIMUS_EMBED_HOOKS",
    "INSPEXIMUS_EMBED_MODEL", "INSPEXIMUS_HEADS", "INSPEXIMUS_HOOK_DAEMON", "INSPEXIMUS_HOOK_DAEMON_NOSTART",
    "INSPEXIMUS_HOOK_DAEMON_TRACE", "INSPEXIMUS_HOOK_FAST_EXIT", "INSPEXIMUS_KEEP_CONVERSION_BACKUP",
    "INSPEXIMUS_LLM_MODEL", "INSPEXIMUS_MAX_K", "INSPEXIMUS_NO_INJECT", "INSPEXIMUS_NO_NUDGE",
    "INSPEXIMUS_NO_UPDATE_CHECK", "INSPEXIMUS_NOMIC_PREFIX", "INSPEXIMUS_READ_GUARDS", "INSPEXIMUS_READ_RESOLVER",
    "INSPEXIMUS_REALIGN_MAX", "INSPEXIMUS_RECEIPT_MAX_POINTERS", "INSPEXIMUS_RECEIPT_PUBKEY",
    "INSPEXIMUS_RECEIPTS_TAIL", "INSPEXIMUS_SAVE_RETRIES", "INSPEXIMUS_SCOPE", "INSPEXIMUS_SERVICE_SECRET",
    "INSPEXIMUS_SESSION_DIGEST", "INSPEXIMUS_SNIPPET_CHARS", "INSPEXIMUS_STAMP_AUTO", "INSPEXIMUS_STORE_FORMAT",
    "INSPEXIMUS_SUPERSESSION", "INSPEXIMUS_TRUST_SEEDS", "INSPEXIMUS_WITNESS_SECRET", "INSPEXIMUS_WRITER_KEY",
    "INSPEXIMUS_RECALL_INDEX",
    "INSPEXIMUS_TRACEBACK",                                  # 3.21 F-2: a flag for the command line
    "INSPEXIMUS_HOOK_DEDUPE",                                # 3.22: an off switch
    # found by the AST scan (F-3), which the old regex missed: keys, URLs, flags, names and numbers
    "INSPEXIMUS_EMBED_KEY", "INSPEXIMUS_EMBED_URL", "INSPEXIMUS_LLM_KEY", "INSPEXIMUS_LLM_URL",
    "INSPEXIMUS_OBSERVE_RECALL", "INSPEXIMUS_PERSIST_VECTORS", "INSPEXIMUS_PII_DETECT", "INSPEXIMUS_PROJECT",
    "INSPEXIMUS_RECEIPTS", "INSPEXIMUS_SESSION_MAX_CHARS", "INSPEXIMUS_SESSION_MAX_ITEMS",
    "INSPEXIMUS_SESSION_MAX_SESSIONS", "INSPEXIMUS_SESSION_SALIENCE",
}


def _env_names():
    """Every string constant in the package that is exactly an INSPEXIMUS_* name: however the value is read
    (os.environ.get, env.get on a copy, os.getenv), the name appears as such a constant (AUDIT-A F-3: a regex on
    `environ.get(` missed INSPEXIMUS_CODING_STORE, read through `env.get`)."""
    seen = set()
    for f in glob.glob(os.path.join(ROOT, "inspeximus", "*.py")):
        for node in ast.walk(ast.parse(open(f, encoding="utf-8").read())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.fullmatch(
                    r"INSPEXIMUS_[A-Z0-9_]+", node.value):
                seen.add(node.value)
    return seen


def test_every_path_variable_is_listed():
    seen = _env_names()
    assert {"INSPEXIMUS_DECISION_STORE", "INSPEXIMUS_CODING_STORE"} <= seen, "CONTROL: the scan finds the variables"
    assert "INSPEXIMUS_CODING_STORE" in hookd.PATH_VARS
    unknown = seen - set(hookd.PATH_VARS) - NOT_PATHS
    assert not unknown, "classify these as a path (hookd.PATH_VARS) or not (NOT_PATHS): %s" % sorted(unknown)


# ── E-2: once-per-process notices ──────────────────────────────────────────────────────────────────────────────
def _damage(sp, n=3):
    con = sqlite3.connect(sp)
    for rowid, doc in con.execute("SELECT rowid, doc FROM records LIMIT ?", (n,)).fetchall():
        j = json.loads(doc)
        j.setdefault("meta", {})["quarantined"] = "x"
        con.execute("UPDATE records SET doc = ? WHERE rowid = ?", (json.dumps(j), rowid))
    con.commit()
    con.close()


def test_every_answer_prints_the_notices_a_fresh_process_prints(project):
    proj, sp = project
    _damage(sp)
    hookd.reset_process_state()
    err, se = io.StringIO(), sys.stderr
    sys.stderr = err
    try:
        _today(proj)
    finally:
        sys.stderr = se
    today = err.getvalue()
    assert "could not be read" in today, "CONTROL: today's path prints the notice: %r" % today[:200]
    d, th = _start(sp, proj)
    try:
        for i in range(2):
            hookd.ask(_ev(proj), sp, timeout=2.0)
            assert hookd.LAST["outcome"] == "served", hookd.LAST
            assert "could not be read" in hookd.LAST["err"], "answer %d lost the notice" % (i + 1)
    finally:
        _stop(d, th)


#: Module-level containers that are not once-per-process state: caches keyed by their inputs, or state the daemon
#: handles itself. Anything else that is a module-level set or list must be in hookd.PROCESS_STATE.
#: hookd._NO_ADDRESS_SAID (3.18): its notice prints in the hook's own process, from maybe_start, never in an answer.
#: hookd._NO_LOCK_SAID (3.18): its notice prints from a claim, in serve() before any answer, never in an answer.
NOT_PROCESS_STATE = {("claude_code", "_LAST_STORES"), ("claude_code", "_FAST_EXIT"), ("hookd", "_NO_ADDRESS_SAID"),
                     ("hookd", "_NO_LOCK_SAID")}


def test_every_once_per_process_set_is_reset():
    found = set()
    for f in glob.glob(os.path.join(ROOT, "inspeximus", "*.py")):
        mod = os.path.splitext(os.path.basename(f))[0]
        for node in ast.parse(open(f, encoding="utf-8").read()).body:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(
                node, ast.AnnAssign) and node.value is not None else []
            value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
            empty = (isinstance(value, ast.Call) and getattr(value.func, "id", "") == "set" and not value.args) or \
                (isinstance(value, ast.List) and not value.elts)
            for t in targets:
                if empty and isinstance(t, ast.Name) and re.fullmatch(r"_[A-Z0-9_]+", t.id):
                    found.add((mod, t.id))
    assert ("_isolate", "_SAID") in found, "CONTROL: the scan finds the known set"
    missing = found - set(hookd.PROCESS_STATE) - NOT_PROCESS_STATE
    assert not missing, "reset per answer (hookd.PROCESS_STATE) or list as not process state: %s" % sorted(missing)


def test_the_reset_empties_each_container():
    import importlib
    for mod, name in hookd.PROCESS_STATE:
        c = getattr(importlib.import_module("inspeximus." + mod), name)
        c.add("x") if isinstance(c, set) else c.append("x")
    hookd.reset_process_state()
    for mod, name in hookd.PROCESS_STATE:
        assert not getattr(importlib.import_module("inspeximus." + mod), name), (mod, name)


# ── E-3: the count ─────────────────────────────────────────────────────────────────────────────────────────────
def test_an_attempt_record_is_not_a_daemon(project, tmp_path, monkeypatch):
    proj, sp = project
    kh = str(tmp_path / "count-key-home")
    monkeypatch.setattr(hookd, "key_home_for", lambda s: kh)
    d, th = _start(sp, proj)
    try:
        assert hookd.live_daemon_count(kh) == 1, "CONTROL: one live daemon"
        _write(os.path.join(hookd.state_dir(kh), "ab" * 8 + ".json"),
               json.dumps({"last_attempt": time.time(), "pid": os.getpid()}))   # maybe_start's record, same folder
        assert hookd.live_daemon_count(kh) == 1, "an attempt record was counted as a daemon"
    finally:
        _stop(d, th)


# ── E-4: a silent connection ───────────────────────────────────────────────────────────────────────────────────
def test_a_silent_connection_does_not_delay_the_next_ask(project, daemon):
    proj, sp = project
    hookd.ask(_ev(proj), sp, timeout=2.0)                       # warm
    silent = [hookd._connect(daemon.addr, time.monotonic() + 1) for _ in range(3)]
    try:
        time.sleep(0.05)
        for _ in range(3):
            # Under 3 x SERVER_READ_TIMEOUT_S: an accept loop that reads each silent connection in turn makes the first
            # ask wait 1.5 s; a thread per connection answers at once. Not the hook's 0.3 s, which load alone can miss.
            got = hookd.ask(_ev(proj), sp, timeout=3 * hookd.SERVER_READ_TIMEOUT_S - 0.3)
            assert got is not None and hookd.LAST["outcome"] == "served", hookd.LAST
    finally:
        for c in silent:
            if c is not None:
                c.close()


# ── E-5: a record without a start time ─────────────────────────────────────────────────────────────────────────
def test_a_record_without_a_start_time_is_stale(project):
    proj, sp = project
    kh = hookd.key_home_for(sp)
    p = hookd._pid_path(kh, hookd.tag(sp, kh))
    _write(p, json.dumps({"pid": os.getpid()}))
    try:
        assert hookd.live_daemon(sp) is None
        assert hookd._clear_stale(kh, hookd.tag(sp, kh)) is True
    finally:
        if os.path.exists(p):
            os.unlink(p)



# ── AUDIT-A d8: F-1 to F-3 and the slots ───────────────────────────────────────────────────────────────────────
def test_no_daemon_where_a_start_time_cannot_be_read(project, monkeypatch, switched_on):
    """F-1: on macOS and BSD no start time is read, so no record is ever live and every hook would start another."""
    proj, sp = project
    monkeypatch.setattr(hookd, "_proc_start", lambda pid: None)
    monkeypatch.setattr(cc, "_start_detached", lambda *a: pytest.fail("started where no daemon can be live"))
    assert hookd.maybe_start(proj, sp) == "unsupported"
    assert hookd.serve_main(["x", "--serve", "--expect-store", sp, "--project", proj]) == 2


def _projects(tmp_path, n):
    import subprocess
    from inspeximus._surface import coding_store_path
    out = []
    for i in range(n):
        proj = tmp_path / ("p%d" % i)
        proj.mkdir()
        subprocess.run(["git", "init", "-q", str(proj)], check=True)
        sp = coding_store_path(str(proj))
        os.makedirs(os.path.dirname(sp), exist_ok=True)
        m = Inspeximus(sp)
        m.remember("deploy window note %d" % i, key="k")
        m.flush()
        out.append((str(proj), sp))
    return out


def test_a_burst_of_starts_never_exceeds_the_limit(tmp_path, monkeypatch):
    """F-2: six starts before the first record was written made six daemons. The slot is claimed before the warm-up."""
    import threading
    kh = str(tmp_path / "burst-key-home")
    monkeypatch.setattr(hookd, "key_home_for", lambda s: kh)
    ps = _projects(tmp_path, hookd.MAX_LIVE_DAEMONS + 2)
    ds = [hookd.Daemon(sp, idle_exit_s=60) for _p, sp in ps]
    res = {}
    gate = threading.Barrier(len(ds))

    def run(i):
        gate.wait()
        res[i] = ds[i].serve(warm_cwd=ps[i][0])
    ths = [threading.Thread(target=run, args=(i,), daemon=True) for i in range(len(ds))]
    for t in ths:
        t.start()
    try:
        deadline = time.time() + 30
        while time.time() < deadline and sum(1 for i in range(len(ds)) if i in res) < 2:
            time.sleep(0.05)
        assert sorted(v for v in res.values()) == ["limit", "limit"], res
        assert hookd.live_daemon_count(kh) == hookd.MAX_LIVE_DAEMONS
    finally:
        for d in ds:
            d.stop = True
            d._wake()
        for t in ths:
            t.join(10)
    assert hookd.live_daemon_count(kh) == 0, "a stopped daemon kept its slot"


def test_two_starts_of_one_store_make_one_daemon(project, tmp_path, monkeypatch):
    import threading
    proj, sp = project
    kh = str(tmp_path / "one-store-key-home")
    monkeypatch.setattr(hookd, "key_home_for", lambda s: kh)
    a, b = hookd.Daemon(sp, idle_exit_s=60), hookd.Daemon(sp, idle_exit_s=60)
    res = {}
    ta = threading.Thread(target=lambda: res.__setitem__("a", a.serve(warm_cwd=proj)), daemon=True)
    ta.start()
    # READY MEANS SERVING, NOT A SLOT (3.18.0 release run, main CI, 2026-10-11). The slot count reached 1 when a held its
    # slot but not yet its store claim; b then took the store and served, a got "taken", and b's serve() ended
    # "stopped" at its idle limit. The record is written after both claims and the bind, so a live record is ready.
    for _ in range(200):
        if hookd.live_daemon(sp) and hookd._read_token(a.kh, a.tag):
            break
        time.sleep(0.05)
    assert hookd.live_daemon(sp), "CONTROL: a is serving before b starts"
    try:
        assert b.serve(warm_cwd=proj) == "taken"
        assert hookd.live_daemon_count(kh) == 1
    finally:
        a.stop = True
        a._wake()
        ta.join(10)


def test_a_claim_whose_process_is_gone_is_not_counted_and_is_taken_over(tmp_path):
    kh = str(tmp_path / "dead-claim-key-home")
    os.makedirs(hookd.state_dir(kh))
    for i in range(hookd.MAX_LIVE_DAEMONS):
        _write(hookd._slot_path(kh, i), json.dumps({"pid": 2 ** 22 + 17 + i, "proc_start": 1}))
    assert hookd.live_daemon_count(kh) == 0
    p = hookd.claim_slot(kh, "0" * 16)
    assert p is not None and hookd.live_daemon_count(kh) == 1
    hookd.release_slot(p)


def test_a_silent_connection_gives_its_slot_back_within_the_read_timeout(project, daemon):
    """D-8 after E-4: the read timeout no longer delays an ask; it bounds how long a silent connection holds one of the
    MAX_CONNECTIONS handshake slots."""
    silent = hookd._connect(daemon.addr, time.monotonic() + 1)
    try:
        time.sleep(0.1)
        assert daemon.conns._value == hookd.MAX_CONNECTIONS - 1, "CONTROL: the silent connection holds a slot"
        # An absolute bound, not one read from the constant: the documented 0.5 s plus 0.6 s of scheduling slack. A
        # bound computed from SERVER_READ_TIMEOUT_S grows with it, so a longer timeout would pass unseen.
        t0 = time.monotonic()
        while daemon.conns._value != hookd.MAX_CONNECTIONS and time.monotonic() - t0 < 1.1:
            time.sleep(0.02)
        assert daemon.conns._value == hookd.MAX_CONNECTIONS,             "a silent connection kept its slot for over 1.1 s (the read timeout is documented as 0.5 s)"
    finally:
        silent.close()



def _writing_the_claim_itself():
    """Whether the json.dump being made writes the claim file a reader can see, not a private temp file. The file
    object cannot say: os.fdopen() names it by its fd number. So the writer's frame decides: _claim_exclusive writes its
    temp file first (it has `tmp` and no `fd2`) and, in the no-link fallback, the claim itself (`fd2`); code without a
    temp file (3d6b0348) writes the claim directly."""
    import sys as _sys
    caller = _sys._getframe(2).f_locals
    return "fd2" in caller or "tmp" not in caller


class _SlowClaims:
    """hookd.json with a slow dump for one kind of claim, to hold open the gap between creating a claim file and writing
    it (3.18.0 release run, main CI, Python 3.9: a burst counted 3 daemons for 4). The wait is for a write into the claim
    file itself, where a reader can see it empty; a write into a private temp file does not wait, so a starter that
    writes there first does not come back late enough to hide the gap."""

    def __init__(self, real, slot):
        self._real, self._slot = real, slot

    def __getattr__(self, n):
        return getattr(self._real, n)

    def dump(self, obj, fh, *a, **k):
        if isinstance(obj, dict) and "pid" in obj and ("tag" in obj) == self._slot and _writing_the_claim_itself():
            time.sleep(0.3)
        return self._real.dump(obj, fh, *a, **k)


def test_a_burst_count_sees_every_claimed_slot(tmp_path, monkeypatch):
    """The count taken when the losers return "limit" sees all 4 winners, and every slot file holds a whole claim. With
    the claim created first and written after, all 4 slot files were empty at that moment: 0 counted, 5 runs of 5."""
    import threading
    kh = str(tmp_path / "burst-key-home")
    monkeypatch.setattr(hookd, "key_home_for", lambda s: kh)
    monkeypatch.setattr(hookd, "json", _SlowClaims(hookd.json, slot=True))
    ps = _projects(tmp_path, hookd.MAX_LIVE_DAEMONS + 2)
    ds = [hookd.Daemon(sp, idle_exit_s=60) for _p, sp in ps]
    res = {}
    gate = threading.Barrier(len(ds))

    def run(i):
        gate.wait()
        res[i] = ds[i].serve(warm_cwd=ps[i][0])
    ths = [threading.Thread(target=run, args=(i,), daemon=True) for i in range(len(ds))]
    for t in ths:
        t.start()
    try:
        deadline = time.time() + 30
        while time.time() < deadline and len(res) < 2:
            time.sleep(0.01)
        assert sorted(res.values()) == ["limit", "limit"], res
        slots = [hookd._claim_owner(hookd._slot_path(kh, i)) for i in range(hookd.MAX_LIVE_DAEMONS)]
        assert all(isinstance(s, dict) and s.get("tag") for s in slots), "a slot was seen without a whole claim: %r" % slots
        assert hookd.live_daemon_count(kh) == hookd.MAX_LIVE_DAEMONS
    finally:
        for d in ds:
            d.stop = True
            d._wake()
        for t in ths:
            t.join(10)
    assert hookd.live_daemon_count(kh) == 0, "a stopped daemon kept its slot"
    left = [f for f in os.listdir(hookd.state_dir(kh)) if f.endswith(".tmp")]
    assert not left, "claim temp files were left behind: %s" % left


def test_two_claims_of_one_store_at_once_give_one(tmp_path, monkeypatch):
    """With the gap held open, the second starter found the first one's claim empty, took it for a dead process's,
    removed it and claimed the store too: two daemons for one store."""
    import threading
    kh = str(tmp_path / "store-claim-key-home")
    os.makedirs(hookd.state_dir(kh))
    monkeypatch.setattr(hookd, "json", _SlowClaims(hookd.json, slot=False))
    got = []
    gate = threading.Barrier(2)

    def run():
        gate.wait()
        got.append(hookd._claim_store(kh, "0" * 16))
    ths = [threading.Thread(target=run, daemon=True) for _ in range(2)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(10)
    won = [p for p in got if p]
    try:
        assert len(got) == 2 and len(won) == 1, "two starts of one store both claimed it: %r" % got
    finally:
        for p in won:
            hookd.release_slot(p)



# ── a dead claim, reclaimed by several starters at once (AUDIT-A C-1 to C-3, 2026-10-11) ───────────────────────────
DEAD = {"pid": 2 ** 22 + 99, "proc_start": 1}


def _slow_reads(monkeypatch, delay=0.3):
    """The starter in thread "slow" takes `delay` over every claim read, so it decides "dead" on a read that another
    starter has made out of date by the time it acts: the window C-1 races in, held open. The others read at full speed."""
    import threading
    real = hookd._claim_owner

    def slow(p):
        rec = real(p)
        if threading.current_thread().name == "slow":
            time.sleep(delay)
        return rec
    monkeypatch.setattr(hookd, "_claim_owner", slow)


def _at_once(n, fn, stagger=False):
    """Run fn(0) .. fn(n-1) in n threads; thread 0 is "slow" (see _slow_reads). With `stagger`, thread 0 starts first
    and the others 0.05 s later, so it is inside its slow read of the dead claim while they replace it: the order that
    loses a claim on the old code, every time, not by chance. Without it, all start together at a barrier."""
    import threading
    out = []
    gate = threading.Barrier(n - 1 if stagger else n)

    def run(i):
        if not (stagger and i == 0):
            gate.wait()
        out.append(fn(i))
    ths = [threading.Thread(target=run, args=(i,), daemon=True, name="slow" if i == 0 else "s%d" % i) for i in range(n)]
    if stagger:
        ths[0].start()
        time.sleep(0.05)
        for t in ths[1:]:
            t.start()
    else:
        for t in ths:
            t.start()
    for t in ths:
        t.join(30)
    return out


def test_a_dead_store_claim_with_8_starters_gives_exactly_one_winner(tmp_path, monkeypatch):
    kh = str(tmp_path / "kh")
    os.makedirs(hookd.state_dir(kh))
    t = "0" * 16
    _write(os.path.join(hookd.state_dir(kh), t + ".claim"), json.dumps(DEAD))
    _slow_reads(monkeypatch)
    got = _at_once(8, lambda i: hookd._claim_store(kh, t), stagger=True)
    won = [g for g in got if g]
    assert len(got) == 8 and len(won) == 1, "%d starters won one dead store claim" % len(won)
    rec = hookd._claim_owner(won[0])
    assert rec and hookd._record_is_live(rec), "CONTROL: the winner's claim is the one on disk"


def test_four_dead_slots_with_8_starters_give_no_slot_twice(tmp_path, monkeypatch):
    kh = str(tmp_path / "kh")
    os.makedirs(hookd.state_dir(kh))
    for i in range(hookd.MAX_LIVE_DAEMONS):
        _write(hookd._slot_path(kh, i), json.dumps(dict(DEAD, pid=DEAD["pid"] + i)))
    _slow_reads(monkeypatch)
    got = _at_once(8, lambda i: hookd.claim_slot(kh, "%016x" % i), stagger=True)
    won = [g for g in got if g]
    assert len(won) == len(set(won)), "a slot was won twice: %r" % sorted(won)
    assert len(won) <= hookd.MAX_LIVE_DAEMONS
    assert hookd.live_daemon_count(kh) == len(won), "a winner's slot is not on disk"


def test_the_no_link_fallback_gives_one_winner_while_a_claim_is_being_written(tmp_path, monkeypatch):
    """Without hard links the claim is created, then written. A second starter must take a young empty claim as held."""
    kh = str(tmp_path / "kh")
    os.makedirs(hookd.state_dir(kh))
    monkeypatch.setattr(hookd.os, "link", lambda *a, **k: (_ for _ in ()).throw(OSError("no hard links")))
    monkeypatch.setattr(hookd, "json", _SlowClaims(hookd.json, slot=False))
    got = _at_once(4, lambda i: hookd._claim_store(kh, "0" * 16))
    won = [g for g in got if g]
    assert len(won) == 1, "%d starters won one store claim in the no-link fallback" % len(won)


@pytest.mark.parametrize("which", ["slot", "store"])
def test_an_old_empty_claim_is_reclaimed_and_a_fresh_one_is_not(tmp_path, which):
    kh = str(tmp_path / "kh")
    os.makedirs(hookd.state_dir(kh))
    t = "0" * 16
    paths = [hookd._slot_path(kh, i) for i in range(hookd.MAX_LIVE_DAEMONS)] if which == "slot" else         [os.path.join(hookd.state_dir(kh), t + ".claim")]
    for p in paths:
        open(p, "w").close()                                    # empty: a crash between the create and the write
    claim = (lambda: hookd.claim_slot(kh, t)) if which == "slot" else (lambda: hookd._claim_store(kh, t))
    assert claim() is None, "a fresh empty claim was taken for a dead one"
    old = time.time() - 3600                                 # far older than CLAIM_STALE_S
    for p in paths:
        os.utime(p, (old, old))
    got = claim()
    assert got in paths, "an empty claim older than CLAIM_STALE_S was never reclaimed"
    rec = hookd._claim_owner(got)
    assert rec and hookd._record_is_live(rec)


def test_an_old_stray_temp_file_of_ours_is_removed_and_nothing_else(tmp_path):
    kh = str(tmp_path / "kh")
    d = hookd.state_dir(kh)
    os.makedirs(d)
    ours = os.path.join(d, "slot-0.claim.4242.140001.0a1b2c3d.tmp")
    fresh = os.path.join(d, "slot-1.claim.4242.140001.0a1b2c3e.tmp")
    other = os.path.join(d, "notes.claim.4242.140001.0a1b2c3d.tmp")
    for p in (ours, fresh, other):
        open(p, "w").close()
    old = time.time() - 3600                                 # far older than CLAIM_STALE_S
    for p in (ours, other):
        os.utime(p, (old, old))
    p = hookd.claim_slot(kh, "0" * 16)
    try:
        assert not os.path.exists(ours), "an old temp file of ours was left"
        assert os.path.exists(fresh), "a fresh temp file, maybe a starter's in flight, was removed"
        assert os.path.exists(other), "a file of another name was removed"
    finally:
        hookd.release_slot(p)



def test_a_starter_that_cannot_get_the_reclaim_lock_removes_nothing(tmp_path, monkeypatch):
    """While another reclaim holds the lock past RECLAIM_WAIT_S, a dead claim is treated as held: never removed blind."""
    kh = str(tmp_path / "kh")
    os.makedirs(hookd.state_dir(kh))
    p = hookd._slot_path(kh, 0)
    _write(p, json.dumps(DEAD))
    monkeypatch.setattr(hookd, "RECLAIM_WAIT_S", 0.2)
    with hookd._reclaim_lock(hookd.state_dir(kh)) as held:
        assert held, "CONTROL: the lock is taken here"
        assert hookd._reclaim_if_dead(p) is False, "a reclaim went ahead without the lock"
        assert os.path.exists(p), "a claim was removed without the lock"
    assert hookd._reclaim_if_dead(p) is True and not os.path.exists(p), "CONTROL: with the lock free, the dead claim goes"



# ── the reclaim lock's edges (AUDIT-A E-1, E-2, E-4, 2026-10-11) ───────────────────────────────────────────────────
def _patch_platform_lock(monkeypatch, wrap):
    """Wrap the platform's lock call (fcntl.flock, or msvcrt.locking on Windows) with `wrap(real, fd, *args)`."""
    if os.name == "nt":
        import msvcrt as mod
        name = "locking"
    else:
        import fcntl as mod
        name = "flock"
    real = getattr(mod, name)
    monkeypatch.setattr(mod, name, lambda fd, *a: wrap(real, fd, *a))


def test_a_file_system_without_locks_takes_nothing_over_and_says_so_once(tmp_path, monkeypatch, capsys):
    """E-1: ENOLCK is not "busy". The starter does not retry it for RECLAIM_WAIT_S, removes nothing, and says why."""
    import errno

    def no_locks(real, fd, *a):
        raise OSError(errno.ENOLCK, "No locks available")
    kh = str(tmp_path / "kh")
    os.makedirs(hookd.state_dir(kh))
    p = hookd._slot_path(kh, 0)
    _write(p, json.dumps(DEAD))
    _patch_platform_lock(monkeypatch, no_locks)
    monkeypatch.setattr(hookd, "_NO_LOCK_SAID", [], raising=False)
    t0 = time.monotonic()
    assert hookd._reclaim_if_dead(p) is False
    took = time.monotonic() - t0
    assert os.path.exists(p), "a claim was removed without a lock"
    assert took < hookd.RECLAIM_WAIT_S / 2, "ENOLCK was retried as if another holder had the lock (%.2f s)" % took
    hookd._reclaim_if_dead(p)
    err = capsys.readouterr().err
    assert err.count("no file lock in") == 1, "the starter said %d times that it cannot lock: %r" % (
        err.count("no file lock in"), err)


def test_removing_reclaim_lock_while_it_is_held_does_not_let_a_second_starter_in(tmp_path, monkeypatch):
    """E-2: a lock file can be removed while it is held, and the next starter then made and locked a new one: with a
    0.5 ms deleter, two winners in 18 of 60 rounds. On POSIX the lock is on the state folder; on Windows a file another
    handle has open cannot be removed. Either way a second starter does not get the lock while the first holds it."""
    import threading
    kh = str(tmp_path / "kh")
    d = hookd.state_dir(kh)
    os.makedirs(d)
    monkeypatch.setattr(hookd, "RECLAIM_WAIT_S", 0.3)
    second = {}
    with hookd._reclaim_lock(d) as held:
        assert held, "CONTROL: the first starter holds the lock"
        try:
            os.unlink(os.path.join(d, "reclaim.lock"))         # a cleanup that removes the lock file while it is held
        except OSError:
            pass                                                # Windows: the open file cannot be removed

        def other():
            with hookd._reclaim_lock(d) as h:
                second["held"] = h
        t = threading.Thread(target=other, daemon=True)
        t.start()
        t.join(5)
    assert second.get("held") is False, "a second starter got the reclaim lock while the first held it"


@pytest.mark.skipif(os.name == "nt", reason="POSIX: the lock is on the state folder; Windows locks reclaim.lock")
def test_a_state_folder_replaced_before_the_lock_is_opened_again(tmp_path, monkeypatch):
    """The lock counts only on the folder at the path: one replaced between the open and the lock is let go and the
    path opened again, or a second starter could lock the new folder while the first held the old one."""
    import fcntl
    kh = str(tmp_path / "kh")
    d = hookd.state_dir(kh)
    os.makedirs(d)
    replaced = []

    def replace_first(real, fd, *a):
        if not replaced:
            replaced.append(1)
            os.rename(d, d + ".old")
            os.makedirs(d)                                      # another folder at the path, after this one was opened
        return real(fd, *a)
    _patch_platform_lock(monkeypatch, replace_first)
    with hookd._reclaim_lock(d) as held:
        assert held and replaced, "CONTROL: the lock was taken after the folder at the path was replaced"
        monkeypatch.undo()
        other = os.open(d, os.O_RDONLY)
        try:
            with pytest.raises(OSError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(other)


def test_a_fallback_writer_stalled_past_the_age_rule_does_not_also_win(tmp_path, monkeypatch):
    """E-4: without hard links, A creates the claim and stalls before writing it. Past CLAIM_STALE_S, B takes the empty
    claim for a crash's leftover and makes its own; A's write then lands in a file no longer at the path. One winner."""
    import threading
    kh = str(tmp_path / "kh")
    os.makedirs(hookd.state_dir(kh))
    monkeypatch.setattr(hookd.os, "link", lambda *a, **k: (_ for _ in ()).throw(OSError("no hard links")))
    monkeypatch.setattr(hookd, "CLAIM_STALE_S", 0.2)
    real = hookd.json

    class StallA:
        def __getattr__(self, n):
            return getattr(real, n)

        def dump(self, obj, fh, *a, **k):
            if threading.current_thread().name == "A" and _writing_the_claim_itself():
                time.sleep(0.6)
            return real.dump(obj, fh, *a, **k)
    monkeypatch.setattr(hookd, "json", StallA())
    got = {}
    ta = threading.Thread(target=lambda: got.__setitem__("A", hookd._claim_store(kh, "0" * 16)), name="A", daemon=True)
    tb = threading.Thread(target=lambda: got.__setitem__("B", hookd._claim_store(kh, "0" * 16)), name="B", daemon=True)
    ta.start()
    time.sleep(0.35)                                            # past CLAIM_STALE_S, while A is stalled
    tb.start()
    ta.join(10)
    tb.join(10)
    won = [k for k, v in got.items() if v]
    assert len(got) == 2 and len(won) == 1, "winners: %r" % got
