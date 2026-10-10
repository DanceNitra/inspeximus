"""3.18 prototype: the prompt daemon against AUDIT-A's review of a03da444 (audit_a_318_daemon_review.md).

One test per rule the review found without a test, and one per finding D-1 to D-10. The fixtures are the daemon suite's.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from inspeximus import claude_code as cc  # noqa: E402
from inspeximus import hookd  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402
from test_the_prompt_daemon_is_an_accelerator_never_a_dependency import (  # noqa: E402,F401
    _ev, _req, _stop_started_daemons, _today, daemon, project, switch_on, switched_on)


def _start(sp, proj, **kw):
    kw.setdefault("idle_exit_s", 60)
    d = hookd.Daemon(sp, **kw)
    th = threading.Thread(target=d.serve, kwargs={"warm_cwd": proj}, daemon=True)
    th.start()
    for _ in range(200):
        if hookd.live_daemon(sp) and hookd._read_token(d.kh, d.tag):
            break
        time.sleep(0.05)
    return d, th


def _stop(d, th):
    d.stop = True
    d._wake()
    th.join(10)


def _handshake(d):
    """A raw client: the hello and the daemon's proof. Returns (connection, server nonce)."""
    conn = hookd._connect(d.addr, time.monotonic() + 1)
    hookd._send(conn, {"hello": hookd.PROTOCOL, "nc": os.urandom(16).hex()})
    hi = hookd._recv(conn, time.monotonic() + 2)
    return conn, hi["ns"]


def _write(p, data):
    from inspeximus._safewrite import write_atomic
    os.makedirs(os.path.dirname(p), exist_ok=True)
    write_atomic(p, data)


# ── the channel ────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_recorded_request_cannot_be_replayed_on_a_new_connection(project, daemon):
    """The server nonce is in the request MAC: a request and MAC captured on one connection fail on the next."""
    proj, sp = project
    conn, ns = _handshake(daemon)
    req = json.dumps(_req(daemon, proj, until=time.time() + 5))
    msg = {"req": req, "mac": hookd._mac(daemon.token, b"C|", ns, req)}
    hookd._send(conn, msg)
    assert json.loads(hookd._recv(conn, time.monotonic() + 3)["rep"])["ok"], "CONTROL: the original is answered"
    conn.close()
    conn, _ns = _handshake(daemon)
    try:
        hookd._send(conn, msg)
        with pytest.raises((TimeoutError, EOFError, OSError)):
            hookd._recv(conn, time.monotonic() + 1)
    finally:
        conn.close()


def test_a_reply_mac_that_does_not_cover_the_request_is_refused(project, daemon, monkeypatch):
    """D-1: the reply MAC binds the request. A token holder's reply MAC'd over the client nonce alone is refused."""
    proj, sp = project
    nc = [None]
    real_send, real_recv = hookd._send, hookd._recv

    def spy(conn, deadline):
        m = real_recv(conn, deadline)
        if isinstance(m, dict) and "nc" in m:
            nc[0] = m["nc"]
        return m

    def unbound(conn, obj):
        if "rep" in obj:
            obj = dict(obj, mac=hookd._mac(daemon.token, b"R|", nc[0], obj["rep"]))
        real_send(conn, obj)
    monkeypatch.setattr(hookd, "_recv", spy)
    monkeypatch.setattr(hookd, "_send", unbound)
    assert hookd.ask(_ev(proj), sp, timeout=2.0) is None and hookd.LAST["outcome"] == "bad-reply"


def test_oversized_and_deeply_nested_input_does_not_stop_the_daemon(project, daemon):
    proj, sp = project
    for payload in (b"x" * ((1 << 24) + 10), b"[" * 100_000):
        conn = hookd._connect(daemon.addr, time.monotonic() + 1)
        try:
            try:
                conn.send_bytes(payload)
                hookd._recv(conn, time.monotonic() + 1)
            except Exception:                                   # noqa: BLE001 -- refused, or the pipe closed under it
                pass
        finally:
            conn.close()
    assert hookd.ask(_ev(proj), sp, timeout=2.0) is not None and hookd.LAST["outcome"] == "served"


def test_a_message_over_the_size_bound_is_refused_before_it_is_parsed():
    """The bound applies before authentication: an unauthenticated peer cannot make the daemon read and parse more."""
    from multiprocessing import Pipe
    a, b = Pipe()
    big = json.dumps({"hello": 1, "pad": "x" * (1 << 24)}).encode("utf-8")
    # From another thread: a pipe write of this size waits for the reader, and the reader is this thread.
    th = threading.Thread(target=lambda: _quiet(a.send_bytes, big), daemon=True)
    th.start()
    try:
        with pytest.raises(OSError):
            hookd._recv(b, time.monotonic() + 5)
    finally:
        b.close()
        th.join(5)
        a.close()


def _quiet(f, *a):
    try:
        f(*a)
    except Exception:                                           # noqa: BLE001 -- the reader refused and closed
        pass


def test_a_silent_connection_holds_the_daemon_for_at_most_the_read_timeout(project, daemon):
    """D-8: a connection that said nothing held the single accept loop for 2 s."""
    proj, sp = project
    silent = hookd._connect(daemon.addr, time.monotonic() + 1)
    try:
        time.sleep(0.05)
        t = time.monotonic()
        got = hookd.ask(_ev(proj), sp, timeout=1.2)
        assert got is not None and hookd.LAST["outcome"] == "served", hookd.LAST
        assert time.monotonic() - t < hookd.SERVER_READ_TIMEOUT_S + 0.6
    finally:
        silent.close()


@pytest.mark.skipif(os.name != "nt", reason="the Windows pipe descriptor")
def test_the_pipe_grants_this_user_and_system_only(project, daemon):
    """D-1: the default pipe descriptor also lets Everyone and Anonymous read."""
    import ctypes
    from ctypes import wintypes
    adv = ctypes.WinDLL("advapi32")
    conn = hookd._connect(daemon.addr, time.monotonic() + 1)
    try:
        psd, dacl = ctypes.c_void_p(), ctypes.c_void_p()
        assert adv.GetSecurityInfo(wintypes.HANDLE(conn._handle), 6, 4, None, None, ctypes.byref(dacl), None,
                                   ctypes.byref(psd)) == 0
        s = wintypes.LPWSTR()
        assert adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(psd, 1, 4, ctypes.byref(s), None)
        sddl = s.value
    finally:
        conn.close()
    aces = sddl.split("(")[1:]
    assert sddl.startswith("D:P") and len(aces) == 2, sddl
    assert sddl.endswith(";;;SY)") or ";;;SY)(" in sddl, sddl
    assert ";;;WD)" not in sddl and ";;;AN)" not in sddl, sddl


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
def test_state_files_and_socket_are_private(project, daemon):
    import stat
    assert stat.S_IMODE(os.stat(hookd.state_dir(daemon.kh)).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(hookd._token_path(daemon.kh, daemon.tag)).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(daemon.addr).st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="the POSIX socket directory")
def test_a_shared_socket_directory_is_refused(tmp_path, monkeypatch):
    import tempfile
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    d = tmp_path / ("inspeximus-hookd-%d" % os.getuid())
    d.mkdir()
    os.chmod(str(d), 0o777)
    with pytest.raises(PermissionError):
        hookd.address(str(tmp_path / ("k" * 120)), "0" * 16)


# ── stale state ────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_token_without_a_live_record_is_never_used(project, monkeypatch):
    """D-1: a token left by a crash let whoever took the endpoint name read the prompt. No live record, no connection."""
    proj, sp = project
    kh = hookd.key_home_for(sp)
    t = hookd.tag(sp, kh)
    token = os.urandom(32)
    _write(hookd._token_path(kh, t), token)
    _write(hookd._pid_path(kh, t), json.dumps(
        {"pid": 2 ** 22 + 17, "proc_start": 1, "token_sha": hashlib.sha256(token).hexdigest()[:32]}))
    connected = []
    real = hookd._connect
    monkeypatch.setattr(hookd, "_connect", lambda *a: (connected.append(a), real(*a))[1])
    try:
        assert hookd.ask(_ev(proj), sp, timeout=1.0) is None and hookd.LAST["outcome"] == "absent"
        assert not connected, "the client connected on a token no live daemon owns"
    finally:
        assert hookd._clear_stale(kh, t) is True
    assert hookd._read_token(kh, t) is None and not os.path.exists(hookd._pid_path(kh, t)), \
        "a starting daemon left a token it did not write"


def test_a_token_its_live_record_does_not_name_is_never_used(project, daemon, monkeypatch):
    proj, sp = project
    _write(hookd._token_path(daemon.kh, daemon.tag), os.urandom(32))    # replaced under the live daemon
    connected = []
    real = hookd._connect
    monkeypatch.setattr(hookd, "_connect", lambda *a: (connected.append(a), real(*a))[1])
    assert hookd.ask(_ev(proj), sp, timeout=1.0) is None and hookd.LAST["outcome"] == "absent" and not connected


def test_a_record_whose_pid_now_names_another_process_is_not_a_daemon(project):
    """D-5: a crash record whose pid was reused by a live process stopped any daemon from starting for good."""
    proj, sp = project
    kh = hookd.key_home_for(sp)
    p = hookd._pid_path(kh, hookd.tag(sp, kh))
    mine = hookd._proc_start(os.getpid())
    assert mine is not None, "CONTROL: this platform reads a start time"
    _write(p, json.dumps({"pid": os.getpid(), "proc_start": mine}))
    try:
        assert hookd.live_daemon(sp) is not None, "CONTROL: the same process by pid and start time is live"
        _write(p, json.dumps({"pid": os.getpid(), "proc_start": mine - 1}))
        assert hookd.live_daemon(sp) is None
    finally:
        os.unlink(p)


def test_a_daemon_that_lost_its_files_to_another_leaves_them(project):
    proj, sp = project
    d, th = _start(sp, proj)
    _write(hookd._pid_path(d.kh, d.tag), json.dumps({"pid": os.getpid() + 1}))
    _stop(d, th)
    try:
        assert hookd._read_token(d.kh, d.tag) is not None, "a daemon removed files another daemon owns"
    finally:
        for p in (hookd._token_path(d.kh, d.tag), hookd._pid_path(d.kh, d.tag)):
            if os.path.exists(p):
                os.unlink(p)


# ── the answer ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_an_exception_in_the_answer_restores_the_streams(project, daemon, monkeypatch):
    proj, sp = project
    so, se = sys.stdout, sys.stderr

    def boom(ev, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(cc, "recall", boom)
    with pytest.raises(RuntimeError):
        daemon.answer(_req(daemon, proj))
    assert sys.stdout is so and sys.stderr is se


def test_foreign_stamps_are_counted_once_per_held_handle(project, daemon, monkeypatch):
    proj, sp = project
    calls = []
    monkeypatch.setattr(cc, "foreign_stamp_count", lambda m: (calls.append(1), 3)[1])
    daemon.counts.clear()
    r1, r2 = daemon.answer(_req(daemon, proj)), daemon.answer(_req(daemon, proj))
    assert r1["foreign"] == r2["foreign"] == 3 and len(calls) == 1


def test_only_the_users_config_switches_the_daemon_on(project, monkeypatch, capsys):
    """D-3, EM's decision: a project's settings reach the hook's environment, and a repository holds its own config."""
    from inspeximus import _userconfig
    proj, sp = project
    monkeypatch.setattr(cc, "_start_detached", lambda *a: pytest.fail("started without the user's config"))
    monkeypatch.setenv("INSPEXIMUS_HOOK_DAEMON", "1")
    _userconfig._SAID.discard("INSPEXIMUS_HOOK_DAEMON")
    assert hookd.enabled() is False and hookd.maybe_start(proj, sp) == "off"
    err = capsys.readouterr().err
    assert "INSPEXIMUS_HOOK_DAEMON" in err and "hook.daemon" in err and _userconfig.path() in err, err
    monkeypatch.delenv("INSPEXIMUS_HOOK_DAEMON")
    repo_cfg = os.path.join(proj, ".inspeximus", "config.json")
    with open(repo_cfg, "w", encoding="utf-8") as fh:
        json.dump({"hook": {"daemon": True}}, fh)
    assert hookd.enabled() is False, "the repository's own config switched the daemon on"
    with switch_on():
        assert hookd.enabled() is True, "CONTROL: the user's config switches it on"


def test_the_daemon_switches_are_not_part_of_the_environment():
    base = {"INSPEXIMUS_X": "1"}
    assert hookd.env_fingerprint(base) == hookd.env_fingerprint(dict(base, INSPEXIMUS_HOOK_DAEMON="1",
                                                                      INSPEXIMUS_HOOK_DAEMON_NOSTART="1"))
    assert hookd.env_fingerprint(base) != hookd.env_fingerprint(dict(base, INSPEXIMUS_Y="1"))


def test_nostart_starts_nothing(project, monkeypatch, switched_on):
    proj, sp = project
    monkeypatch.setenv("INSPEXIMUS_HOOK_DAEMON_NOSTART", "1")
    monkeypatch.setattr(cc, "_start_detached", lambda *a: pytest.fail("started under NOSTART"))
    assert hookd.maybe_start(proj, sp) == "off"


def test_the_decision_store_is_held_and_served_as_today(project, tmp_path, monkeypatch):
    proj, sp = project
    ext = str(tmp_path / "decisions.json")
    e = Inspeximus(ext)
    e.remember("we decided the deploy window freeze lasts a week", key="decision::freeze", tags=["decision"])
    e.flush()
    monkeypatch.setenv("INSPEXIMUS_DECISION_STORE", ext)
    today = _today(proj)
    assert "freeze lasts a week" in today, "CONTROL: today's path reads the decision store"
    d, th = _start(sp, proj)
    try:
        from inspeximus import _surface
        opened = []
        real_open = _surface.open_store
        monkeypatch.setattr(_surface, "open_store", lambda p, **kw: (opened.append((p, kw)), real_open(p, **kw))[1])
        assert hookd.ask(_ev(proj), sp, timeout=2.0) == today
        assert hookd.ask(_ev(proj), sp, timeout=2.0) == today
        assert not [o for o in opened if o[1] == {"resolve": False}], "the decision store was opened again for an answer"
    finally:
        _stop(d, th)


def test_the_daemon_returns_what_todays_path_writes_to_stderr(project, tmp_path, monkeypatch):
    """D-7: stdout was identical, and the stderr notice went to the daemon's log."""
    proj, sp = project
    ext = str(tmp_path / "big.json")
    e = Inspeximus(ext)
    e.remember("a decision in an oversized store", key="decision::big", tags=["decision"])
    e.flush()
    monkeypatch.setenv("INSPEXIMUS_DECISION_STORE", ext)
    monkeypatch.setenv("INSPEXIMUS_DECISION_STORE_MAX_MB", "0.000001")
    err, se = io.StringIO(), sys.stderr
    sys.stderr = err
    try:
        _today(proj)
    finally:
        sys.stderr = se
    assert "over the" in err.getvalue(), "CONTROL: today's path writes the notice"
    d, th = _start(sp, proj)
    try:
        hookd.ask(_ev(proj), sp, timeout=2.0)
        assert hookd.LAST["outcome"] == "served" and hookd.LAST["err"] == err.getvalue()
        # and the hook prints it where today's hook does
        env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_HOOK_DAEMON")}
        runs = []
        for on in (False, True):                                # switched off is today's path
            with (switch_on() if on else contextlib.nullcontext()):
                r = subprocess.run([sys.executable, "-m", "inspeximus.claude_code"], input=json.dumps(_ev(proj)),
                                   cwd=ROOT, env=dict(env, INSPEXIMUS_HOOK_DAEMON_TRACE="1"), capture_output=True,
                                   text=True, encoding="utf-8", timeout=120)
            runs.append(r)
        today_err = runs[0].stderr
        lines = runs[1].stderr.splitlines(True)
        assert any('"outcome": "served"' in ln for ln in lines), "CONTROL: the second hook was served by the daemon"
        served_err = "".join(ln for ln in lines if "[inspeximus] hookd " not in ln)
        assert "over the" in today_err and served_err == today_err and runs[0].stdout == runs[1].stdout
    finally:
        _stop(d, th)


# ── freshness ──────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_raw_edit_that_keeps_size_and_mtime_moves_the_signature(project):
    """D-9: an UPDATE outside the library, with the modification time restored, was served stale."""
    import sqlite3
    proj, sp = project
    base = hookd.store_signature(sp)
    st = os.stat(sp)
    c = sqlite3.connect(sp)
    # A real change of the same length: SQLite skips writing an UPDATE that changes nothing, which is no edit at all.
    c.execute("UPDATE records SET doc = replace(doc, 'Tuesday', 'Fridayx') WHERE rowid = (SELECT min(rowid) FROM records)")
    c.commit()
    c.close()
    os.utime(sp, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert os.stat(sp).st_size == st.st_size, "CONTROL: the edit kept the size"
    assert hookd.store_signature(sp) != base, "a raw edit that kept size and mtime left the signature unchanged"


def test_a_journal_file_moves_the_signature(project):
    proj, sp = project
    base = hookd.store_signature(sp)
    j = sp + "-journal"
    open(j, "wb").close()
    try:
        assert hookd.store_signature(sp) != base, "a rollback journal is not in the signature"
    finally:
        os.unlink(j)


def test_a_rotated_read_guard_key_moves_the_signature(project):
    from inspeximus.core import _guard_key_file
    proj, sp = project
    kf = _guard_key_file(sp)
    existed = os.path.exists(kf)
    old = open(kf, "rb").read() if existed else None
    base = hookd.store_signature(sp)
    os.makedirs(os.path.dirname(kf), exist_ok=True)
    with open(kf, "w", encoding="utf-8") as fh:
        fh.write(os.urandom(32).hex() + ("" if existed else "\n"))
    try:
        assert hookd.store_signature(sp) != base, "a rotated read-guard key is not in the signature"
    finally:
        if existed:
            with open(kf, "wb") as fh:
                fh.write(old)
        else:
            os.unlink(kf)


# ── lifetime ───────────────────────────────────────────────────────────────────────────────────────────────────
def test_the_daemon_process_does_not_hold_the_project_directory(project):
    """D-4: started from the repository, the daemon kept it as its working directory."""
    proj, sp = project
    env = dict(os.environ, PYTHONPATH=ROOT)
    kh = hookd.key_home_for(sp)
    os.makedirs(hookd.state_dir(kh), exist_ok=True)
    p = subprocess.Popen([sys.executable, "-m", "inspeximus.claude_code", "--serve", "--expect-store", sp,
                          "--project", proj], cwd=hookd.state_dir(kh), env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)   # as maybe_start starts it
    try:
        for _ in range(300):
            if hookd.live_daemon(sp):
                break
            time.sleep(0.05)
        assert hookd.live_daemon(sp), "CONTROL: the daemon started"
        if os.name == "nt":
            moved = proj + "_moved"
            os.rename(proj, moved)                              # WinError 32 while it is a process's working directory
            os.rename(moved, proj)
        else:
            assert os.path.realpath("/proc/%d/cwd" % p.pid) != os.path.realpath(proj)
    finally:
        p.terminate()                                           # the process this test started, by its handle
        p.wait(10)
        kh = hookd.key_home_for(sp)
        for f in (hookd._token_path(kh, hookd.tag(sp, kh)), hookd._pid_path(kh, hookd.tag(sp, kh))):
            if os.path.exists(f):
                os.unlink(f)


def test_serve_creates_its_state_directory_before_it_binds(project, tmp_path, monkeypatch):
    """D-10: on POSIX the listener was bound before the state directory existed."""
    proj, sp = project
    fresh = str(tmp_path / "fresh-key-home")
    monkeypatch.setattr(hookd, "key_home_for", lambda s: fresh)
    d, th = _start(sp, proj)
    try:
        assert hookd._read_token(d.kh, d.tag) is not None and th.is_alive()
    finally:
        _stop(d, th)


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes: the Windows endpoint is a pipe with its own descriptor")
def test_a_state_directory_left_open_is_0700_before_the_listener_binds(project, tmp_path, monkeypatch):
    """D-10, the part the slot claim does not cover: `claim_slot` creates a missing state directory before the bind, but
    leaves one that already exists as it is. A directory left at 0755 must be 0700 before the socket is bound in it."""
    import stat
    from multiprocessing import connection
    proj, sp = project
    fresh = str(tmp_path / "open-key-home")
    monkeypatch.setattr(hookd, "key_home_for", lambda s: fresh)
    sd = hookd.state_dir(fresh)
    os.makedirs(sd)
    os.chmod(sd, 0o755)
    assert stat.S_IMODE(os.stat(sd).st_mode) == 0o755, "CONTROL: the state directory starts open"
    seen = []
    real = connection.Listener

    def listener(*a, **kw):
        seen.append(stat.S_IMODE(os.stat(sd).st_mode))
        return real(*a, **kw)
    monkeypatch.setattr(connection, "Listener", listener)
    d, th = _start(sp, proj)
    try:
        assert seen, "CONTROL: the listener was created"
        assert seen[0] == 0o700, "the socket was bound in a state directory at %o" % seen[0]
    finally:
        _stop(d, th)


def test_no_more_than_the_limit_of_daemons_per_key_home(project, tmp_path, monkeypatch, switched_on):
    """D-3: a daemon per store and no global bound. A key home of its own: another test's live record would count."""
    proj, sp = project
    kh = str(tmp_path / "limit-key-home")
    monkeypatch.setattr(hookd, "key_home_for", lambda s: kh)
    assert hookd.live_daemon_count(kh) == 0, "CONTROL: the key home starts empty"
    live = json.dumps({"pid": os.getpid(), "proc_start": hookd._proc_start(os.getpid()), "tag": "x"})
    made = [hookd._slot_path(kh, i) for i in range(hookd.MAX_LIVE_DAEMONS)]   # every slot held by a live process
    for p in made:
        _write(p, live)
    monkeypatch.setattr(cc, "_start_detached", lambda *a: pytest.fail("started past the limit"))
    try:
        assert hookd.live_daemon_count(kh) == hookd.MAX_LIVE_DAEMONS
        assert hookd.maybe_start(proj, sp) == "limit"
    finally:
        for p in made:
            os.unlink(p)


# ── fail-open over several prompts ─────────────────────────────────────────────────────────────────────────────
def test_a_slow_daemon_is_skipped_after_repeated_timeouts(project, daemon, monkeypatch):
    """D-2: with every answer late, every prompt paid the timeout on top of today's path, for good."""
    proj, sp = project
    monkeypatch.setattr(hookd.Daemon, "answer", lambda self, req: (time.sleep(0.4), {"ok": True, "out": "x"})[1])
    for _ in range(hookd.BACKOFF_AFTER):
        assert hookd.ask(_ev(proj), sp, timeout=0.15) is None and hookd.LAST["outcome"] == "timeout"
    t = time.monotonic()
    assert hookd.ask(_ev(proj), sp, timeout=0.15) is None and hookd.LAST["outcome"] == "backoff"
    assert time.monotonic() - t < 0.05
    time.sleep(0.6)                                             # the daemon finishes the abandoned answers
    # The back-off ran out: the count stays on disk, its window is over. Only a served answer may clear it.
    _write(hookd._fails_path(daemon.kh, daemon.tag), json.dumps({"n": hookd.BACKOFF_AFTER, "until": time.time() - 1}))
    monkeypatch.setattr(hookd.Daemon, "answer", lambda self, req: {"ok": True, "out": "fast"})
    assert hookd.ask(_ev(proj), sp, timeout=1.0) == "fast"
    assert not os.path.exists(hookd._fails_path(daemon.kh, daemon.tag)), "a served answer did not clear the count"


def test_a_request_reached_after_its_client_gave_up_is_not_worked(project, daemon, monkeypatch):
    proj, sp = project
    monkeypatch.setattr(cc, "recall", lambda ev, **kw: pytest.fail("worked a request whose client had gone"))
    assert daemon.answer(_req(daemon, proj, until=time.time() - 1))["reason"] == "late"


def test_with_the_daemon_off_the_hook_imports_no_daemon_code(project):
    """AUDIT-B, 3.18 speed check: with the switch off, the 3.18 hook was about 0.10 s slower than the 3.17 hook. It now
    imports hookd only when the user's config switches the daemon on (or INSPEXIMUS_HOOK_DAEMON is set, for its notice).
    `-X importtime` lists every module the hook process imports."""
    proj, sp = project
    env = {k: v for k, v in os.environ.items() if k not in ("INSPEXIMUS_HOOK_DAEMON", "INSPEXIMUS_HOOK_DAEMON_TRACE")}
    env["PYTHONPATH"] = ROOT

    def imported(extra=None):
        r = subprocess.run([sys.executable, "-X", "importtime", "-m", "inspeximus.claude_code"],
                           input=json.dumps(_ev(proj)), cwd=proj, env=dict(env, **(extra or {})), capture_output=True,
                           text=True, encoding="utf-8", timeout=120)
        assert r.returncode == 0, r.stderr[-400:]
        return r.stderr
    assert "inspeximus.hookd" not in imported(), "the hook imported the daemon with the daemon switched off"
    assert "inspeximus.hookd" not in imported({"INSPEXIMUS_HOOK_DAEMON": "1"}), \
        "the environment's switch, which is ignored, loaded the daemon"
    with switch_on():
        assert "inspeximus.hookd" in imported({"INSPEXIMUS_HOOK_DAEMON_NOSTART": "1"}), \
            "CONTROL: -X importtime shows hookd when the user's config switches the daemon on"


def test_a_replaced_held_handle_keeps_nothing_of_the_store(project, monkeypatch):
    """AUDIT-A R-2: when a store's signature moves, the daemon opens a new handle. The old one could stay in a reference
    cycle, with every record it held, until the collector ran. It is emptied on replacement."""
    proj, sp = project
    d = hookd.Daemon(sp, idle_exit_s=60)
    old = d.handle_for(sp, lambda: Inspeximus(sp))
    old.recall("deploy window", k=5)                            # warm the derived caches
    assert old._items and old._tok_cache, "CONTROL: the held handle holds records and caches"
    peer = Inspeximus(sp)
    peer.remember("a peer write moves the signature", key="peer")
    peer.flush()
    new = d.handle_for(sp, lambda: Inspeximus(sp))
    assert new is not old and new._items, "CONTROL: the moved signature opened a new handle"
    assert old._items == [] and not old._tok_cache and not old._sig_cache and not old._tc_cache, \
        "the replaced handle still holds records"


# -- AUDIT-A I-2: every file the answer depends on is in the signature ----------------------------------------------
def _answer_files(sp):
    from inspeximus.core import _answer_files as f
    return f(sp)


def test_every_answer_file_moves_the_signature(project):
    """The user's config and each key-home file of the store: a change to any of them reopens the held handle."""
    proj, sp = project
    files = _answer_files(sp)
    from inspeximus import _userconfig
    assert _userconfig.path() in files and len(files) >= 4, ("CONTROL: the config and the key-home files are named", files)
    for f in files:
        existed = os.path.exists(f)
        old = open(f, "rb").read() if existed else None
        base = hookd.store_signature(sp)
        os.makedirs(os.path.dirname(f), exist_ok=True)
        with open(f, "ab") as fh:
            fh.write(b" " if existed else b"{}")
        try:
            assert hookd.store_signature(sp) != base, "%s is not in the signature" % f
        finally:
            if existed:
                with open(f, "wb") as fh:
                    fh.write(old)
            else:
                os.remove(f)


def test_a_change_to_the_users_config_reopens_the_held_handle(project):
    """AUDIT-A's repro, at the handle: a daemon opened under one config served under it after the user changed it."""
    from inspeximus import _userconfig
    proj, sp = project
    d = hookd.Daemon(sp, idle_exit_s=60)
    first = d.handle_for(sp, lambda: Inspeximus(sp))
    p = _userconfig.path()
    old = open(p, "rb").read() if os.path.exists(p) else None
    cfg = dict(_userconfig.read())
    cfg["guards"] = dict(cfg.get("guards") or {}, read=True)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh)
    try:
        assert d.handle_for(sp, lambda: Inspeximus(sp)) is not first, "the held handle outlived a change to the config"
    finally:
        if old is None:
            os.remove(p)
        else:
            with open(p, "wb") as fh:
                fh.write(old)
        _userconfig._CACHE.clear()


def test_every_key_home_file_is_an_answer_file():
    """A function in core that names a file in the key home for a store (it calls key_home) is in _answer_files, so a
    new one cannot be read by a held handle without moving the daemon's signature."""
    import ast
    src = open(os.path.join(ROOT, "inspeximus", "core.py"), encoding="utf-8").read()
    t = ast.parse(src)
    named, listed = set(), set()
    for f in t.body:
        if isinstance(f, ast.FunctionDef):
            calls = {n.func.id for n in ast.walk(f) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
            args = [a.arg for a in f.args.args]
            if "key_home" in calls and args[:1] == ["store_path"] and f.name != "_answer_files":
                named.add(f.name)
            if f.name == "_answer_files":
                listed = {n.id for n in ast.walk(f) if isinstance(n, ast.Name)}
    assert {"_head_path", "_receipt_key_file", "_guard_key_file", "_stamp_env_file"} <= named, ("CONTROL", named)
    assert named <= listed, "key-home files a held handle reads but the daemon's signature does not: %s" % (
        sorted(named - listed))
