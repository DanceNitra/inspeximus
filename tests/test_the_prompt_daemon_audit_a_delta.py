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
    _ev, _req, _today, daemon, project, switch_on, switched_on)


# ── E-1: relative path variables ───────────────────────────────────────────────────────────────────────────────
def test_a_relative_decision_store_is_never_asked_for(project, daemon, monkeypatch):
    """The hook resolves a relative path against the repository and the daemon against its own folder, so an answer
    from the daemon left the decisions out and still read `served`. A relative path variable means today's path."""
    proj, sp = project
    monkeypatch.chdir(proj)
    e = Inspeximus(os.path.join(proj, "dec.json"))
    e.remember("we decided QUOKKARULE governs the deploy window", key="decision::q", tags=["decision"])
    e.flush()
    monkeypatch.setenv("INSPEXIMUS_DECISION_STORE", "dec.json")
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
}


def test_every_path_variable_is_listed():
    pat = re.compile(r"environ(?:\.get)?\(?\[?[\"'](INSPEXIMUS_[A-Z0-9_]+)")
    seen = set()
    for f in glob.glob(os.path.join(ROOT, "inspeximus", "*.py")):
        seen |= set(pat.findall(open(f, encoding="utf-8").read()))
    assert "INSPEXIMUS_DECISION_STORE" in seen, "CONTROL: the scan finds the variables"
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
NOT_PROCESS_STATE = {("claude_code", "_LAST_STORES"), ("claude_code", "_FAST_EXIT")}


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
            got = hookd.ask(_ev(proj), sp)                      # the hook's own bound
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
