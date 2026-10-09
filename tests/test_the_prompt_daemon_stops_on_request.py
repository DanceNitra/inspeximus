"""`inspeximus hookd stop`: the user's way to stop the prompt daemons of a key home (AUDIT-B CL-7).

The 3.18 draft said a daemon exits "on request", and no request existed: `--hookd-stop` was named only in a docstring.
`hookd.request_stop` writes a request into the daemons' own folder; each daemon's watchdog reads it within a second and
exits. No process is signalled, and a daemon started after the request is not affected.
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from inspeximus import hookd  # noqa: E402
from test_the_prompt_daemon_audit_a_findings import _start, _stop  # noqa: E402
from test_the_prompt_daemon_is_an_accelerator_never_a_dependency import (  # noqa: E402,F401
    _stop_started_daemons, project)


def test_a_stop_request_ends_a_running_daemon(project):
    proj, sp = project
    d, th = _start(sp, proj)
    try:
        assert hookd.live_daemon_count(d.kh) == 1, "CONTROL: the daemon runs"
        res = hookd.request_stop(d.kh, wait_s=10)
        assert res["running_before"] == 1 and res["stopped"] == 1 and res["still_running"] == 0, res
        th.join(5)
        assert not th.is_alive(), "the daemon kept serving after the stop request"
        assert hookd.live_daemon(sp) is None and not hookd._read_token(d.kh, d.tag), "the daemon left its files"
    finally:
        _stop(d, th)


def test_a_daemon_started_after_the_request_keeps_running(project):
    proj, sp = project
    from inspeximus._keyhome import key_home
    assert hookd.request_stop(key_home(sp), wait_s=0)["running_before"] == 0, "CONTROL: nothing ran before"
    time.sleep(0.05)
    d, th = _start(sp, proj)
    try:
        time.sleep(2.5)                                         # two watchdog rounds
        assert th.is_alive() and hookd.live_daemon_count(d.kh) == 1, "an old stop request ended a new daemon"
    finally:
        _stop(d, th)


def test_the_command_stops_the_daemons_and_reports(project):
    proj, sp = project
    from inspeximus import cli
    d, th = _start(sp, proj)
    try:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["--path", sp, "--json", "hookd", "stop"])
        out = json.loads(buf.getvalue())
        assert rc == 0 and out["stopped"] == 1 and out["still_running"] == 0, (rc, out)
        th.join(5)
        assert not th.is_alive()
    finally:
        _stop(d, th)
