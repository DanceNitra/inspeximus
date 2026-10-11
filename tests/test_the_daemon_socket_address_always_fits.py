"""The daemon's socket address always fits AF_UNIX, and two daemons in one process leave the process's state as it was.

The 3.18.0 release run (2026-10-11, ci.yml 38110545558) failed on Linux, the first CI run of the daemon's tests:

- The test sandbox points `tempfile` at pytest's per-worker basetemp, so `address()`'s fallback under the temp directory
  was as long as the key home it replaced: `/tmp/inspeximus-run-*/pytest-of-runner/pytest-0/popen-gw1/tmp/
  inspeximus-hookd-1001/<tag>.sock` is over 107 bytes, the bind raised "AF_UNIX path too long", and the client later
  met a None connection. `address()` now checks every candidate, adds /tmp, and returns None when none fits; then the
  daemon does not start, `ask` says "no-address", and the hook takes its normal path with one notice.
- Two daemons serving in one process (the tests run several) interleaved their swaps of sys.stdout, sys.stderr and the
  umask, and could leave the umask at 0177. Every folder the process made after that had no execute bit, which is the
  "Permission denied" on 40 tests' own tmp_path. One lock now covers those swaps.

The tests force the interleaving with a slow warm-up and a slow bind, so they fail without the lock every time.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from inspeximus import claude_code as cc  # noqa: E402
from inspeximus import hookd  # noqa: E402
from inspeximus._surface import coding_store_path  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402
from test_the_prompt_daemon_audit_a_findings import _stop  # noqa: E402
from test_the_prompt_daemon_is_an_accelerator_never_a_dependency import (  # noqa: E402,F401
    _stop_started_daemons, project, switch_on)

POSIX = pytest.mark.skipif(os.name == "nt", reason="AF_UNIX paths and the umask: the Windows endpoint is a named pipe")
TAG = "0123456789abcdef"


def _long(tmp_path, name):
    d = tmp_path / name / ("x" * 60) / ("y" * 60)
    d.mkdir(parents=True)
    return str(d)


@POSIX
def test_a_long_key_home_and_a_long_temp_directory_still_give_a_socket_that_fits(tmp_path, monkeypatch):
    long_tmp = _long(tmp_path, "tmp")
    monkeypatch.setattr(tempfile, "gettempdir", lambda: long_tmp)
    kh = _long(tmp_path, "kh")
    assert len(os.path.join(hookd.state_dir(kh), TAG + ".sock").encode()) > hookd.SOCK_PATH_MAX, \
        "CONTROL: the key home's socket path is too long"
    assert len(os.path.join(long_tmp, "inspeximus-hookd-%d" % os.getuid(), TAG + ".sock").encode()) > \
        hookd.SOCK_PATH_MAX, "CONTROL: the temp directory's socket path is too long"
    a = hookd.address(kh, TAG)
    assert a is not None and len(a.encode()) <= hookd.SOCK_PATH_MAX, a
    assert a == os.path.join("/tmp", "inspeximus-hookd-%d" % os.getuid(), TAG + ".sock")


@POSIX
def test_every_address_returned_fits(tmp_path, monkeypatch):
    for n in range(0, 140, 7):
        t = _long(tmp_path, "t%d" % n) if n > 70 else str(tmp_path / ("t%d" % n))
        os.makedirs(t, exist_ok=True)
        monkeypatch.setattr(tempfile, "gettempdir", lambda t=t: t)
        kh = str(tmp_path / ("k" * n))
        a = hookd.address(kh, TAG)
        assert a is None or len(a.encode()) <= hookd.SOCK_PATH_MAX, (n, a)


@POSIX
def test_when_nothing_fits_there_is_no_address(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "gettempdir", lambda: _long(tmp_path, "tmp"))
    assert hookd.address(_long(tmp_path, "kh"), "f" * 90) is None


@POSIX
def test_with_no_address_the_daemon_does_not_start_and_the_hook_says_so_once(project, monkeypatch, capsys):
    proj, sp = project
    monkeypatch.setattr(hookd, "address", lambda kh, t: None)
    d = hookd.Daemon(sp, idle_exit_s=60)
    assert d.serve(warm_cwd=proj) == "no-address"
    assert hookd.live_daemon_count(d.kh) == 0, "a daemon without an address claimed a slot"
    assert hookd.ask({"prompt": "deploy"}, sp) is None
    started = []
    monkeypatch.setattr(cc, "_start_detached", lambda *a: started.append(a))
    monkeypatch.setattr(hookd, "_NO_ADDRESS_SAID", [])
    with switch_on():
        assert hookd.maybe_start(proj, sp) == "no-address"
        hookd.maybe_start(proj, sp)                             # rate-limited or not, never a second notice
    assert not started, "a daemon was started with nowhere to listen"
    err = capsys.readouterr().err
    assert err.count("no socket path of at most") == 1, err


def _key_home_of_length(n):
    """A key home whose socket path is exactly n bytes long. Nothing is created: this candidate is only a string."""
    pad = n - len(os.path.join(hookd.state_dir("/k"), TAG + ".sock").encode())
    kh = "/k" + "k" * pad
    assert len(os.path.join(hookd.state_dir(kh), TAG + ".sock").encode()) == n, "CONTROL: %d bytes" % n
    return kh


@POSIX
def test_a_key_home_socket_path_of_exactly_the_limit_fits_and_one_byte_more_does_not(tmp_path, monkeypatch):
    """The approved CHANGELOG says "a socket path of at most 100 bytes": 100 is used, 101 is not."""
    monkeypatch.setattr(tempfile, "gettempdir", lambda: _long(tmp_path, "tmp"))
    at = _key_home_of_length(hookd.SOCK_PATH_MAX)
    assert hookd.address(at, TAG) == os.path.join(hookd.state_dir(at), TAG + ".sock"), \
        "a key home socket path of exactly the limit was not used"
    over = _key_home_of_length(hookd.SOCK_PATH_MAX + 1)
    assert hookd.address(over, TAG) != os.path.join(hookd.state_dir(over), TAG + ".sock"), \
        "a key home socket path one byte over the limit was used"


@POSIX
def test_a_temp_socket_path_of_exactly_the_limit_fits_and_one_byte_more_does_not(monkeypatch):
    """The same boundary for the temp-directory candidate. makedirs and stat are stubbed with a private folder of this
    user, so nothing is created and the test reaches the length check wherever pytest puts its own folders."""
    kh = _key_home_of_length(hookd.SOCK_PATH_MAX + 1)
    tail = os.path.join("inspeximus-hookd-%d" % os.getuid(), TAG + ".sock")
    made = []
    real_stat = os.stat
    monkeypatch.setattr(hookd.os, "makedirs", lambda d, mode=0o777, exist_ok=False: made.append(d))
    monkeypatch.setattr(hookd.os, "stat", lambda d, *a, **k: os.stat_result(
        (0o40700, 0, 0, 0, os.getuid(), 0, 0, 0, 0, 0)) if d in made else real_stat(d, *a, **k))
    for n, fits in ((hookd.SOCK_PATH_MAX, True), (hookd.SOCK_PATH_MAX + 1, False)):
        root = "/t" + "t" * (n - len(os.path.join("/t", tail).encode()))
        assert len(os.path.join(root, tail).encode()) == n, "CONTROL: the temp socket path is %d bytes" % n
        monkeypatch.setattr(tempfile, "gettempdir", lambda root=root: root)
        a = hookd.address(kh, TAG)
        if fits:
            assert a == os.path.join(root, tail), "a temp socket path of exactly the limit was not used: %r" % a
        else:
            assert a == os.path.join("/tmp", tail), "a temp socket path one byte over the limit was used: %r" % a


def _second_project(tmp_path):
    proj = tmp_path / "proj2"
    proj.mkdir()
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    sp = coding_store_path(str(proj))
    os.makedirs(os.path.dirname(sp), exist_ok=True)
    m = Inspeximus(sp)
    m.remember("we decided the release train leaves on Thursday", key="decision::train", tags=["decision"])
    m.flush()
    return str(proj), sp


def _two_overlapping(project, tmp_path, monkeypatch, slow):
    """Start daemon A, then daemon B 0.1 s later, with `slow` making A's step take 0.5 s and B's 1.0 s."""
    (pa, sa), (pb, sb) = project, _second_project(tmp_path)
    da, db = hookd.Daemon(sa, idle_exit_s=60), hookd.Daemon(sb, idle_exit_s=60)
    slow(da, db)
    ta = threading.Thread(target=da.serve, kwargs={"warm_cwd": pa}, daemon=True)
    tb = threading.Thread(target=db.serve, kwargs={"warm_cwd": pb}, daemon=True)
    ta.start()
    time.sleep(0.1)
    tb.start()
    for _ in range(200):
        if hookd._read_token(da.kh, da.tag) and hookd._read_token(db.kh, db.tag):
            break
        time.sleep(0.05)
    return (da, ta), (db, tb)


def test_two_daemons_warming_up_together_give_back_the_streams(project, tmp_path, monkeypatch):
    real_out, real_err = sys.stdout, sys.stderr

    def slow(da, db):
        for d, s in ((da, 0.5), (db, 1.0)):
            d.warm = (lambda cwd, s=s: time.sleep(s))
    a, b = _two_overlapping(project, tmp_path, monkeypatch, slow)
    try:
        assert hookd._read_token(a[0].kh, a[0].tag) and hookd._read_token(b[0].kh, b[0].tag), "CONTROL: both serve"
        assert sys.stdout is real_out and sys.stderr is real_err, \
            "two daemons warming up in one process left sys.stdout as %r" % type(sys.stdout).__name__
    finally:
        sys.stdout, sys.stderr = real_out, real_err
        _stop(*a)
        _stop(*b)


@POSIX
def test_two_daemons_binding_together_give_back_the_umask(project, tmp_path, monkeypatch):
    from multiprocessing import connection
    real = connection.Listener
    delays = {}

    def listener(addr, *a, **kw):
        time.sleep(delays.get(addr, 0))
        return real(addr, *a, **kw)
    monkeypatch.setattr(connection, "Listener", listener)

    def slow(da, db):
        delays[da.addr], delays[db.addr] = 0.5, 1.0
        for d in (da, db):
            d.warm = lambda cwd: None
    before = os.umask(0o022)
    a, b = _two_overlapping(project, tmp_path, monkeypatch, slow)
    try:
        assert hookd._read_token(a[0].kh, a[0].tag) and hookd._read_token(b[0].kh, b[0].tag), "CONTROL: both serve"
        now = os.umask(0o022)
        assert now == 0o022, "two daemons binding in one process left the umask at %o" % now
    finally:
        os.umask(before)
        _stop(*a)
        _stop(*b)


def test_two_daemons_answering_together_give_back_the_streams(project, tmp_path, monkeypatch):
    """The answer redirects the streams too. A and B answer at once; A's answer takes 0.5 s, B's 1.0 s."""
    from test_the_prompt_daemon_is_an_accelerator_never_a_dependency import _req
    (pa, sa), (pb, sb) = project, _second_project(tmp_path)
    da, db = hookd.Daemon(sa, idle_exit_s=60), hookd.Daemon(sb, idle_exit_s=60)
    real_reset = hookd.reset_process_state
    delays = {"A": 0.5, "B": 1.0}

    def slow_reset():
        real_reset()
        time.sleep(delays.get(threading.current_thread().name, 0))
    monkeypatch.setattr(hookd, "reset_process_state", slow_reset)
    real_out, real_err = sys.stdout, sys.stderr
    got = {}
    ta = threading.Thread(target=lambda: got.setdefault("A", da.answer(_req(da, pa))), name="A")
    tb = threading.Thread(target=lambda: got.setdefault("B", db.answer(_req(db, pb))), name="B")
    try:
        ta.start()
        time.sleep(0.1)
        tb.start()
        ta.join(30)
        tb.join(30)
        assert got.get("A", {}).get("ok") and got.get("B", {}).get("ok"), "CONTROL: both answered: %r" % got
        assert sys.stdout is real_out and sys.stderr is real_err, \
            "two daemons answering in one process left sys.stdout as %r" % type(sys.stdout).__name__
    finally:
        sys.stdout, sys.stderr = real_out, real_err


def test_three_hook_runs_with_no_address_print_one_line(project, monkeypatch, capsys):
    """AUDIT-A V-1: maybe_start returned before it wrote its attempt record, so every hook run, a new process each
    time, printed the line again. Each run here starts with a fresh once-per-process flag, as a new process would."""
    proj, sp = project
    monkeypatch.setattr(hookd, "address", lambda kh, t: None)
    started = []
    monkeypatch.setattr(cc, "_start_detached", lambda *a: started.append(a))
    outcomes = []
    with switch_on():
        for _ in range(3):
            monkeypatch.setattr(hookd, "_NO_ADDRESS_SAID", [])
            outcomes.append(hookd.maybe_start(proj, sp))
    err = capsys.readouterr().err
    assert outcomes[0] == "no-address", outcomes
    assert err.count("no socket path of at most") == 1, "3 hook runs printed %d lines" % err.count("no socket path")
    assert not started


def _private(d):
    os.makedirs(d, exist_ok=True)
    os.chmod(d, 0o700)


@POSIX
def test_a_foreign_last_folder_is_not_used_when_an_earlier_one_is_usable_or_at_all(project, tmp_path, monkeypatch, capsys):
    """AUDIT-A V-2: a candidate folder that is not this user's, or not 0700, is skipped and never changed."""
    proj, sp = project
    uid = "inspeximus-hookd-%d" % os.getuid()
    t_root, last_root = str(tmp_path / "t"), str(tmp_path / "last")
    monkeypatch.setattr(tempfile, "gettempdir", lambda: t_root)
    monkeypatch.setattr(hookd, "_LAST_RESORT_ROOT", last_root)
    monkeypatch.setattr(hookd, "SOCK_PATH_MAX", 4000)          # this test's paths fit, wherever pytest puts tmp_path
    kh = str(tmp_path / "/".join(["k" * 250] * 16))
    monkeypatch.setattr(hookd, "key_home_for", lambda s: kh)
    assert len(os.path.join(hookd.state_dir(kh), TAG + ".sock").encode()) > hookd.SOCK_PATH_MAX, \
        "CONTROL: the key home's path does not fit"
    shared_last = os.path.join(last_root, uid)
    os.makedirs(shared_last)
    os.chmod(shared_last, 0o777)
    # An earlier candidate that is usable: it is used, and the shared last folder is left as it is.
    assert hookd.address(kh, TAG) == os.path.join(t_root, uid, TAG + ".sock")
    # The earlier folder now belongs to someone else (stat says another uid): nothing is usable.
    foreign = os.path.join(t_root, uid)
    real_stat = os.stat
    monkeypatch.setattr(hookd.os, "stat", lambda d, *a, **k: os.stat_result(
        (0o40700, 0, 0, 0, os.getuid() + 1, 0, 0, 0, 0, 0)) if d == foreign else real_stat(d, *a, **k))
    assert hookd.address(kh, TAG) is None, "a folder of another user, or a shared one, was used"
    assert stat_mode(shared_last) == 0o777, "the shared folder was changed"
    started = []
    monkeypatch.setattr(cc, "_start_detached", lambda *a: started.append(a))
    monkeypatch.setattr(hookd, "_NO_ADDRESS_SAID", [])
    with switch_on():
        assert hookd.maybe_start(proj, sp) == "no-address"
    assert capsys.readouterr().err.count("no socket path of at most") == 1
    assert not started


def stat_mode(p):
    import stat
    return stat.S_IMODE(os.stat(p).st_mode)


@POSIX
def test_a_candidate_that_cannot_be_made_is_skipped(tmp_path, monkeypatch):
    """A file where the candidate folder would be: makedirs fails, and the next candidate is used, not an exception."""
    uid = "inspeximus-hookd-%d" % os.getuid()
    t_root, last_root = str(tmp_path / "t"), str(tmp_path / "last")
    os.makedirs(t_root)
    with open(os.path.join(t_root, uid), "w") as fh:            # a file, not a folder
        fh.write("x")
    monkeypatch.setattr(tempfile, "gettempdir", lambda: t_root)
    monkeypatch.setattr(hookd, "_LAST_RESORT_ROOT", last_root)
    monkeypatch.setattr(hookd, "SOCK_PATH_MAX", 4000)
    kh = str(tmp_path / "/".join(["k" * 250] * 16))
    assert hookd.address(kh, TAG) == os.path.join(last_root, uid, TAG + ".sock")
