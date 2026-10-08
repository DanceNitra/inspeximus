"""3.18 prototype: the prompt daemon (inspeximus/hookd.py), one test per risk in design_hook_daemon_318.md.

The daemon answers the UserPromptSubmit hook from held stores. Every test here checks one rule the design names:
the channel, which store it serves, version skew, freshness, lifetime, the launch, and above all fail-open: the hook's
output is today's whenever the daemon is absent, slow, of another code, or wrong.
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
from inspeximus import claude_code as cc  # noqa: E402
from inspeximus import hookd  # noqa: E402
from inspeximus._surface import coding_store_path  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

PROMPT = "when is the deploy window"


@pytest.fixture
def project(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    sp = coding_store_path(str(proj))
    os.makedirs(os.path.dirname(sp), exist_ok=True)
    m = Inspeximus(sp)
    for i in range(20):
        m.remember("we decided the deploy window is Tuesday %d" % i, key="decision::deploy%d" % i, tags=["decision"])
    m.flush()
    return str(proj), sp


@pytest.fixture(autouse=True)
def _stop_started_daemons():
    """Every daemon process a test started, by a hook that ran maybe_start or by a direct --serve, is stopped when the
    test ends (AUDIT-A, 2026-10-08: a daemon from a mutated hook test, pid 54996, outlived the run). Only a process
    named by a record or claim in the sandboxed key home, whose pid AND start time still match, and which is not this
    process; never by name, and never a pid that may have been reused."""
    yield
    import glob
    import signal
    from inspeximus._keyhome import key_home
    try:
        d = hookd.state_dir(key_home())
    except Exception:                                           # noqa: BLE001
        return
    for p in glob.glob(os.path.join(d, "*.json")) + glob.glob(os.path.join(d, "*.claim")):
        rec = hookd._claim_owner(p)
        if not rec or rec.get("pid") in (None, os.getpid()) or not hookd._record_is_live(rec):
            continue
        try:
            os.kill(int(rec["pid"]), signal.SIGTERM)
        except OSError:
            pass


@contextlib.contextmanager
def switch_on():
    """`{"hook": {"daemon": true}}` in the user's config inside the block; the previous file is restored after it."""
    from inspeximus import _userconfig
    p = _userconfig.path()
    old = open(p, "rb").read() if os.path.exists(p) else None
    cfg = dict(_userconfig.read())
    cfg["hook"] = dict(cfg.get("hook") or {}, daemon=True)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh)
    _userconfig._CACHE.clear()
    try:
        yield p
    finally:
        if old is None:
            os.unlink(p)
        else:
            with open(p, "wb") as fh:
                fh.write(old)
        _userconfig._CACHE.clear()


@pytest.fixture
def switched_on():
    with switch_on() as p:
        yield p


def _ev(proj):
    return {"hook_event_name": "UserPromptSubmit", "prompt": PROMPT, "cwd": proj, "session_id": "s"}


def _today(proj):
    buf, so = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        cc.recall(_ev(proj))
    finally:
        sys.stdout = so
    return buf.getvalue()


@pytest.fixture
def daemon(project):
    proj, sp = project
    d = hookd.Daemon(sp, idle_exit_s=60)
    th = threading.Thread(target=d.serve, kwargs={"warm_cwd": proj}, daemon=True)
    th.start()
    for _ in range(100):
        if hookd._read_token(d.kh, d.tag):
            break
        time.sleep(0.05)
    yield d
    d.stop = True
    d._wake()
    th.join(10)


def _req(d, proj, **over):
    r = {"ev": _ev(proj), "cwd": proj, "store": d.store, "code": hookd.code_identity(), "env": hookd.env_fingerprint(),
         "paths": hookd.path_values()}
    r.update(over)
    return r


# ── the answer is today's answer ───────────────────────────────────────────────────────────────────────────────
def test_a_served_answer_is_byte_identical_to_todays_path(project, daemon):
    proj, sp = project
    today = _today(proj)
    got = hookd.ask(_ev(proj), sp, timeout=2.0)
    assert hookd.LAST["outcome"] == "served", hookd.LAST
    assert got == today and "deploy window" in got


# ── the channel ────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_daemon_without_the_token_is_not_believed(project, daemon, monkeypatch):
    """A squatted endpoint: whatever answers must prove it holds the token in the key home."""
    proj, sp = project
    fake = b"x" * 32
    monkeypatch.setattr(hookd, "_read_token", lambda kh, t: fake)
    real = hookd._read_record
    monkeypatch.setattr(hookd, "_read_record", lambda kh, t: dict(
        real(kh, t), token_sha=hashlib.sha256(fake).hexdigest()[:32]))   # past the record check, onto the proof
    assert hookd.ask(_ev(proj), sp, timeout=2.0) is None and hookd.LAST["outcome"] == "bad-reply"


def test_a_caller_without_the_token_gets_no_answer(project, daemon):
    proj, sp = project
    conn = hookd._connect(daemon.addr, time.monotonic() + 1)
    try:
        nc = os.urandom(16).hex()
        hookd._send(conn, {"hello": hookd.PROTOCOL, "nc": nc})
        hi = hookd._recv(conn, time.monotonic() + 2)
        req = json.dumps(_req(daemon, proj))
        hookd._send(conn, {"req": req, "mac": hookd._mac(b"y" * 32, b"C|", hi["ns"], req)})
        with pytest.raises((TimeoutError, EOFError, OSError)):
            hookd._recv(conn, time.monotonic() + 1)
    finally:
        conn.close()


def test_a_reply_altered_in_transit_is_not_used(project, daemon, monkeypatch):
    proj, sp = project
    real_send = hookd._send

    def tamper(conn, obj):
        if "rep" in obj:
            obj = dict(obj, rep=obj["rep"].replace("Tuesday", "Friday"))
        real_send(conn, obj)
    monkeypatch.setattr(hookd, "_send", tamper)
    assert hookd.ask(_ev(proj), sp, timeout=2.0) is None and hookd.LAST["outcome"] == "bad-reply"


# ── which store, which code, which environment ─────────────────────────────────────────────────────────────────
def test_the_daemon_answers_only_for_the_store_it_resolves_itself(project, daemon, tmp_path):
    proj, sp = project
    assert daemon.answer(_req(daemon, proj, store=str(tmp_path / "other.json")))["reason"] == "store"
    other = tmp_path / "other_proj"
    other.mkdir()
    subprocess.run(["git", "init", "-q", str(other)], check=True)
    assert daemon.answer(_req(daemon, str(other)))["reason"] == "store", "a cwd that resolves elsewhere was answered"
    assert daemon.answer(_req(daemon, proj))["ok"] is True, "control: its own store is answered"


def test_a_newer_version_is_refused_and_the_daemon_exits(project, daemon):
    proj, sp = project
    code = dict(hookd.code_identity(), version="999.0.0")
    assert daemon.answer(_req(daemon, proj, code=code))["reason"] == "code"
    assert daemon.stop is True


def test_an_older_or_other_build_is_refused_without_an_exit(project, daemon):
    """AUDIT-A D-6: two sessions pinning different builds of one store took turns stopping each other's daemon."""
    proj, sp = project
    for code in (dict(hookd.code_identity(), version="0.0.0"), dict(hookd.code_identity(), python="elsewhere"),
                 dict(hookd.code_identity(), guard_set="other")):
        assert daemon.answer(_req(daemon, proj, code=code))["reason"] == "code"
        assert daemon.stop is False, code


def test_another_environment_is_refused_without_an_exit(project, daemon):
    proj, sp = project
    assert daemon.answer(_req(daemon, proj, env="0" * 16))["reason"] == "env"
    assert daemon.stop is False


def test_one_daemon_per_store_and_key_home(tmp_path):
    a, b = str(tmp_path / "a.json"), str(tmp_path / "b.json")
    assert hookd.tag(a, str(tmp_path / "k1")) != hookd.tag(b, str(tmp_path / "k1"))
    assert hookd.tag(a, str(tmp_path / "k1")) != hookd.tag(a, str(tmp_path / "k2"))
    assert hookd.tag(a, str(tmp_path / "k1")) == hookd.tag(a, str(tmp_path / "k1"))


# ── freshness ──────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_write_made_before_the_prompt_is_in_the_answer(project, daemon):
    proj, sp = project
    assert "Thursday" not in hookd.ask(_ev(proj), sp, timeout=2.0)
    p = Inspeximus(sp)
    p.remember("we decided the deploy window moved to Thursday", key="decision::deploy0", tags=["decision"])
    p.flush()
    got = hookd.ask(_ev(proj), sp, timeout=2.0)
    assert hookd.LAST["outcome"] == "served" and "Thursday" in got


def test_an_erasure_reaches_the_daemon_before_its_next_answer(project, daemon):
    proj, sp = project
    p = Inspeximus(sp)
    rid = p.remember("we decided the deploy window moved to Thursday", key="decision::deploy0", tags=["decision"])
    p.flush()
    assert "Thursday" in hookd.ask(_ev(proj), sp, timeout=2.0), "CONTROL: the record is served before the erasure"
    q = Inspeximus(sp)
    q.forget(rid)
    q.flush()
    got = hookd.ask(_ev(proj), sp, timeout=2.0)
    assert hookd.LAST["outcome"] == "served" and "Thursday" not in got, "an erased record was served"


def test_an_unchanged_store_is_not_reopened(project, daemon):
    proj, sp = project
    hookd.ask(_ev(proj), sp, timeout=2.0)
    n = daemon.reopens
    for _ in range(3):
        hookd.ask(_ev(proj), sp, timeout=2.0)
    assert daemon.reopens == n, "the held handle is reopened on every request: there is nothing held"


# ── fail-open ──────────────────────────────────────────────────────────────────────────────────────────────────
def test_no_daemon_means_none_quickly(project):
    proj, sp = project
    t = time.monotonic()
    assert hookd.ask(_ev(proj), sp) is None and hookd.LAST["outcome"] == "absent"
    assert time.monotonic() - t < 0.5


def test_a_slow_daemon_is_given_up_on_within_the_bound(project, daemon, monkeypatch):
    proj, sp = project
    monkeypatch.setattr(hookd.Daemon, "answer", lambda self, req: (time.sleep(1.5), {"ok": True, "out": "late"})[1])
    t = time.monotonic()
    assert hookd.ask(_ev(proj), sp) is None and hookd.LAST["outcome"] == "timeout"
    assert time.monotonic() - t < hookd.REPLY_TIMEOUT_S + 0.3


def _hook(proj, env_extra, keyhome):
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_HOOK_DAEMON")}
    env.update(env_extra)
    r = subprocess.run([sys.executable, "-m", "inspeximus.claude_code"], input=json.dumps(_ev(proj)), cwd=ROOT,
                       env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    return r.stdout, r.returncode


def test_the_hook_prints_todays_output_with_the_daemon_absent(project, monkeypatch, switched_on):
    proj, sp = project
    off, rc1 = _hook(proj, {}, None)
    on, rc2 = _hook(proj, {"INSPEXIMUS_HOOK_DAEMON_NOSTART": "1"}, None)
    assert rc1 == rc2 == 0 and off == on and "deploy window" in off


def test_the_hook_prints_the_daemons_answer_when_it_is_up(project, daemon):
    proj, sp = project
    off, _ = _hook(proj, {}, None)                              # switched off: today's path
    with switch_on():
        r = subprocess.run([sys.executable, "-m", "inspeximus.claude_code"], input=json.dumps(_ev(proj)), cwd=ROOT,
                           env=dict(os.environ, INSPEXIMUS_HOOK_DAEMON_TRACE="1"), capture_output=True, text=True,
                           encoding="utf-8", timeout=120)
    on, rc = r.stdout, r.returncode
    assert '"outcome": "served"' in r.stderr, "CONTROL: the daemon answered this hook"
    assert rc == 0 and on == off, "the hook through the daemon printed something other than today's output"


# ── lifetime and launch ────────────────────────────────────────────────────────────────────────────────────────
def test_an_idle_daemon_exits(project):
    proj, sp = project
    d = hookd.Daemon(sp, idle_exit_s=0.6)
    th = threading.Thread(target=d.serve, kwargs={"warm_cwd": proj}, daemon=True)
    th.start()
    th.join(15)
    assert not th.is_alive(), "the daemon outlived its idle time"
    assert hookd._read_token(d.kh, d.tag) is None, "an exited daemon left its token"


def test_the_start_follows_the_launch_rules_and_is_rate_limited(project, monkeypatch, switched_on):
    proj, sp = project
    seen = []

    class P:
        pid = 4242
    monkeypatch.setattr(cc, "_start_detached", lambda args, cwd, log: (seen.append((args, cwd, log)), P())[1])
    assert hookd.maybe_start(proj, sp) == "started"
    kh = hookd.key_home_for(sp)
    assert seen and seen[0][0] == ["--serve", "--expect-store", sp, "--project", os.path.abspath(proj)]
    assert seen[0][1] == hookd.state_dir(kh), "the daemon is started in the project folder (AUDIT-A D-4)"
    rec = json.load(open(cc._state_path(sp, "hookd"), encoding="utf-8"))
    assert rec["pid"] == 4242 and rec["last_attempt"] > 0, "the attempt is not recorded"
    assert hookd.maybe_start(proj, sp) == "recent", "a second start within the interval"


def test_the_start_is_off_unless_asked_for(project, monkeypatch):
    proj, sp = project
    monkeypatch.delenv("INSPEXIMUS_HOOK_DAEMON", raising=False)
    assert hookd.maybe_start(proj, sp) == "off"


def test_serve_refuses_a_store_its_directory_does_not_resolve(project, tmp_path, monkeypatch):
    proj, sp = project
    monkeypatch.chdir(str(tmp_path))
    assert hookd.serve_main(["x", "--serve", "--expect-store", sp]) == 2


def test_a_squatted_endpoint_never_receives_the_prompt(project):
    """The reply check alone would reject a fake daemon's answer, but by then the request -- with the user's prompt in
    it -- would have been sent. The client checks the daemon's proof first and sends nothing to an endpoint without it."""
    from multiprocessing.connection import Listener
    from inspeximus._safewrite import write_atomic
    proj, sp = project
    kh = hookd.key_home_for(sp)
    t = hookd.tag(sp, kh)
    os.makedirs(hookd.state_dir(kh), exist_ok=True)
    token = os.urandom(32)
    write_atomic(hookd._token_path(kh, t), token)               # the real token, which the squatter does not hold
    write_atomic(hookd._pid_path(kh, t), json.dumps({          # and a live record naming it: the endpoint is what is squatted
        "pid": os.getpid(), "proc_start": hookd._proc_start(os.getpid()),
        "token_sha": hashlib.sha256(token).hexdigest()[:32]}))
    addr = hookd.address(kh, t)
    lst = Listener(addr, family="AF_PIPE" if os.name == "nt" else "AF_UNIX")
    received = []

    def squat():
        conn = lst.accept()
        try:
            hello = json.loads(conn.recv_bytes().decode())
            hookd._send(conn, {"ns": os.urandom(16).hex(), "mac": "0" * 64})
            if conn.poll(1.0):
                received.append(conn.recv_bytes().decode())
        except Exception:                                       # noqa: BLE001
            pass
        finally:
            conn.close()
    th = threading.Thread(target=squat, daemon=True)
    th.start()
    try:
        assert hookd.ask(_ev(proj), sp, timeout=2.0) is None and hookd.LAST["outcome"] == "bad-reply"
        th.join(3)
        assert not any(PROMPT in r for r in received), "the prompt reached an endpoint that never proved the token"
    finally:
        lst.close()
        os.unlink(hookd._token_path(kh, t))
        os.unlink(hookd._pid_path(kh, t))
