"""The prompt daemon's process start time on macOS (3.19 item 4): written, switched off, untested on a Mac.

The daemon recognises a live daemon by its pid and the process's start time (AUDIT-A D-5). 3.18 read that time on
Windows and Linux only, so on macOS no daemon started and the hook took its normal path. 3.19 adds a sysctl(3)
reader (`_darwin_kinfo_proc`, `_parse_kinfo_proc`) behind `hookd.DARWIN_START_TIME`, which is False: no CI job runs these
tests on a Mac. The tests below run everywhere on the parser and the dispatch, with a fake sysctl; the last ones need a
Mac and are what must pass there before the switch is turned on.
"""
from __future__ import annotations

import os
import struct
import subprocess
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from inspeximus import hookd  # noqa: E402

KINFO_PROC_SIZE = 648                                    # sizeof(struct kinfo_proc) on 64-bit macOS


def _kinfo(pid, sec, usec, size=KINFO_PROC_SIZE):
    raw = bytearray(size)
    if size >= 12:
        struct.pack_into("<qi", raw, 0, sec, usec)
    if size >= hookd._KP_PID_OFFSET + 4:
        struct.pack_into("<i", raw, hookd._KP_PID_OFFSET, pid)
    return bytes(raw)


def test_the_parser_reads_the_start_time_of_the_named_pid():
    assert hookd._parse_kinfo_proc(_kinfo(4242, 1760000000, 123456), 4242) == 1760000000123456


@pytest.mark.parametrize("raw,why", [
    (b"", "no such process: sysctl writes nothing"),
    (_kinfo(4242, 1760000000, 1, size=40), "short"),
    (_kinfo(4243, 1760000000, 1), "another pid"),
    (_kinfo(4242, 0, 0), "no time"),
    (_kinfo(4242, 1760000000, 1000000), "microseconds out of range"),
], ids=["empty", "short", "other-pid", "no-time", "usec-range"])
def test_the_parser_names_no_time_it_cannot_vouch_for(raw, why):
    assert hookd._parse_kinfo_proc(raw, 4242) is None, why


def test_the_mac_reader_is_switched_off():
    """Released off: the switch is turned on only after a macOS run of this file passes (the CHANGELOG says so)."""
    assert hookd.DARWIN_START_TIME is False


def test_a_mac_reads_no_start_time_while_switched_off(monkeypatch):
    monkeypatch.setattr(hookd.sys, "platform", "darwin")
    monkeypatch.setattr(hookd, "_darwin_kinfo_proc", lambda pid: _kinfo(pid, 1760000000, 5))
    assert hookd._proc_start(4242) is None, "a Mac read a start time with the reader switched off"


def test_switched_on_a_reused_pid_is_not_the_daemon(monkeypatch):
    """With the switch on, a pid that a new process took has another start time, so its record is not live."""
    monkeypatch.setattr(hookd.sys, "platform", "darwin")
    monkeypatch.setattr(hookd, "DARWIN_START_TIME", True)
    started = {"t": 1760000000}
    monkeypatch.setattr(hookd, "_darwin_kinfo_proc", lambda pid: _kinfo(pid, started["t"], 7))
    import inspeximus.claude_code as cc
    monkeypatch.setattr(cc, "_pid_alive", lambda pid: True)
    rec = {"pid": 4242, "proc_start": hookd._proc_start(4242)}
    assert rec["proc_start"] == 1760000000000007
    assert hookd._record_is_live(rec), "CONTROL: the same process is live"
    started["t"] += 30                                   # the pid now names a process started later
    assert not hookd._record_is_live(rec), "a reused pid was taken for the daemon"


# ── on a Mac only: what must pass before DARWIN_START_TIME is turned on ─────────────────────────────────────────────
mac = pytest.mark.skipif(sys.platform != "darwin", reason="needs macOS: the reader has never run on a Mac")


@mac
def test_on_a_mac_the_struct_has_the_expected_size():
    assert len(hookd._darwin_kinfo_proc(os.getpid())) == KINFO_PROC_SIZE


@mac
def test_on_a_mac_this_process_has_a_plausible_stable_start_time(monkeypatch):
    monkeypatch.setattr(hookd, "DARWIN_START_TIME", True)
    a, b = hookd._proc_start(os.getpid()), hookd._proc_start(os.getpid())
    assert a is not None and a == b
    assert time.time() - 86400 * 365 < a / 1e6 <= time.time() + 1


@mac
def test_on_a_mac_a_child_has_a_later_start_time_and_none_after_it_exits(monkeypatch):
    monkeypatch.setattr(hookd, "DARWIN_START_TIME", True)
    me = hookd._proc_start(os.getpid())
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
    try:
        time.sleep(0.2)
        child = hookd._proc_start(p.pid)
        assert child is not None and child > me
    finally:
        p.wait()
    assert hookd._proc_start(p.pid) is None
