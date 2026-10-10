"""`inspeximus hookd stop` reaches a daemon that was being launched when the request was made (3.18, EM).

A daemon counted stop requests from its own `__init__`. Between the launcher's start and that moment the interpreter
loads, a second or more on Windows, and no daemon holds a slot yet: a stop request then found nothing running, and the
daemon came up after it and ignored it. The launcher now passes `--launched-at`, its own clock at the start, and the
daemon counts requests from there. A launch time that is too old or in the future is not believed, so an old request
left in the folder cannot stop every later daemon.
"""
from __future__ import annotations

import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from inspeximus import hookd  # noqa: E402
from test_the_prompt_daemon_audit_a_findings import _stop  # noqa: E402
from test_the_prompt_daemon_is_an_accelerator_never_a_dependency import (  # noqa: E402,F401
    _stop_started_daemons, project, switch_on)


def _run(target, *a, **kw):
    out = {}
    th = threading.Thread(target=lambda: out.setdefault("r", target(*a, **kw)), daemon=True)
    th.start()
    return th, out


def _kh(sp):
    from inspeximus._keyhome import key_home
    return key_home(sp)


def test_the_launcher_passes_the_time_it_recorded(project, monkeypatch):
    proj, sp = project
    from inspeximus import claude_code as cc
    seen = []
    monkeypatch.setattr(cc, "_start_detached", lambda args, cwd, log: seen.append(list(args)) or None)
    with switch_on():
        assert hookd.maybe_start(proj, sp) == "started", "CONTROL: the launcher ran"
    import json
    with open(cc._state_path(sp, "hookd"), encoding="utf-8") as fh:
        recorded = json.load(fh)["last_attempt"]
    args = seen[0]
    assert "--launched-at" in args, "the launcher did not pass its start time"
    assert float(args[args.index("--launched-at") + 1]) == recorded


def test_a_stop_made_while_the_daemon_loads_ends_it_before_the_warm_up(project):
    proj, sp = project
    launched = time.time()
    time.sleep(0.02)                                            # the interpreter loading
    assert hookd.request_stop(_kh(sp), wait_s=0)["running_before"] == 0, "CONTROL: no daemon held a slot yet"
    time.sleep(0.02)
    d = hookd.Daemon(sp, idle_exit_s=60, launched_at=repr(launched))
    warmed = []
    real_warm = d.warm
    d.warm = lambda cwd: warmed.append(cwd) or real_warm(cwd)
    th, out = _run(d.serve, warm_cwd=proj)
    try:
        th.join(5)
        assert not th.is_alive(), "a stop request made during the launch did not reach the daemon"
        assert out.get("r") == "stopped", out
        assert not warmed, "the daemon opened the stores before it read the stop request"
        assert hookd.live_daemon(sp) is None and not hookd._read_token(d.kh, d.tag)
    finally:
        _stop(d, th)


def test_the_same_stop_without_the_launch_time_is_lost(project):
    """CONTROL: the window is real. Counted from its own start, the daemon ignores the request and serves."""
    proj, sp = project
    hookd.request_stop(_kh(sp), wait_s=0)
    time.sleep(0.02)
    d = hookd.Daemon(sp, idle_exit_s=60)
    th, _ = _run(d.serve, warm_cwd=proj)
    try:
        for _ in range(200):
            if hookd.live_daemon(sp):
                break
            time.sleep(0.05)
        time.sleep(2.5)                                         # two watchdog rounds
        assert th.is_alive() and hookd.live_daemon(sp), "CONTROL: without the launch time the request is lost"
    finally:
        _stop(d, th)


def test_the_serve_command_reads_the_launch_time(project):
    proj, sp = project
    launched = time.time()
    time.sleep(0.02)
    hookd.request_stop(_kh(sp), wait_s=0)
    argv = ["x", "--serve", "--expect-store", sp, "--project", proj, "--launched-at", repr(launched)]
    th, out = _run(hookd.serve_main, argv)
    try:
        th.join(5)
        assert not th.is_alive(), "--launched-at on the command line did not reach the daemon"
        assert out.get("r") == 0 and hookd.live_daemon(sp) is None
    finally:
        if th.is_alive():
            hookd.request_stop(_kh(sp), wait_s=10)              # a daemon counted from now: a new request ends it
            th.join(10)


def test_a_launch_time_that_cannot_be_right_is_not_believed():
    now = time.time()
    for bad in (None, "", "abc", "nan", "inf", "-inf", repr(now + 60), repr(now - hookd.LAUNCH_MAX_AGE_S - 5), "0"):
        got = hookd._launch_time(bad)
        assert now <= got <= time.time(), "launch time %r was believed: %r" % (bad, got)
    ok = now - hookd.LAUNCH_MAX_AGE_S + 5
    assert hookd._launch_time(repr(ok)) == ok, "CONTROL: a launch time inside the window is used"
    assert hookd._launch_time(repr(now - 1.5)) == now - 1.5


def test_an_old_request_with_a_forged_old_launch_time_does_not_stop_a_new_daemon(project):
    proj, sp = project
    hookd.request_stop(_kh(sp), wait_s=0)
    time.sleep(0.02)
    d = hookd.Daemon(sp, idle_exit_s=60, launched_at=repr(time.time() - 10 * hookd.LAUNCH_MAX_AGE_S))
    th, _ = _run(d.serve, warm_cwd=proj)
    try:
        for _ in range(200):
            if hookd.live_daemon(sp):
                break
            time.sleep(0.05)
        time.sleep(2.5)
        assert th.is_alive() and hookd.live_daemon(sp), "an old stop request ended a daemon with a forged launch time"
    finally:
        _stop(d, th)
