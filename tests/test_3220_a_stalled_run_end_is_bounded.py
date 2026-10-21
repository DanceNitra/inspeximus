"""A test run that stalls after its tests ends red with stacks, and leaves no child alive (3.22).

Measured 2026-10-21: ci.yml's test (3.12) leg sat after its "[ 99%]" line for 4 h in one run and over an hour in
another, and pytest-timeout's 900 s never fired. Each test here builds the stall in a real subprocess and checks
the guard in tests/_exit_guard.py ends it, so a guard that stops working fails here and not on a 6 h CI job.
"""
import os
import subprocess
import sys
import textwrap
import time

import _exit_guard

TESTS = os.path.dirname(os.path.abspath(__file__))


def _py(code, tmp_path, env=None, timeout=90):
    script = tmp_path / "stall.py"
    script.write_text("import sys; sys.path.insert(0, %r)\n" % TESTS + textwrap.dedent(code), encoding="utf-8")
    e = dict(os.environ, **(env or {}))
    t0 = time.time()
    p = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=timeout,
                       cwd=str(tmp_path), env=e, stdin=subprocess.DEVNULL)
    return p, time.time() - t0


STALLED_EXIT = """
    import threading, time, _exit_guard
    def the_stall_that_keeps_this_process_alive():
        time.sleep(3600)
    threading.Thread(target=the_stall_that_keeps_this_process_alive).start()   # not a daemon: exit waits on it
    _exit_guard.arm(%d, delay=2)
"""


def test_a_stalled_exit_ends_with_every_stack_and_never_reports_green(tmp_path):
    p, took = _py(STALLED_EXIT % 0, tmp_path)
    assert p.returncode == 1, (p.returncode, p.stderr[-2000:])
    assert took < 60
    assert "[exit guard]" in p.stderr and "still alive 2 s after its session finished" in p.stderr
    assert "the_stall_that_keeps_this_process_alive" in p.stderr       # the stack names the stalled thread
    assert "live child processes:" in p.stderr


def test_a_stalled_exit_keeps_a_failing_runs_status(tmp_path):
    p, _ = _py(STALLED_EXIT % 5, tmp_path)
    assert p.returncode == 5, (p.returncode, p.stderr[-2000:])


def test_a_stall_that_holds_the_gil_is_ended_on_ci(tmp_path):
    # A regex that backtracks holds the GIL for its whole run, so no Python thread (pytest-timeout's included)
    # gets to run. The session watchdog is a C thread and ends it anyway.
    code = """
        import re, _exit_guard
        assert _exit_guard.arm_session(2)
        re.match(r"(a+)+$", "a" * 60 + "b")
    """
    p, took = _py(code, tmp_path, env={"CI": "true"})
    assert p.returncode != 0 and took < 60, (p.returncode, took)
    assert "Timeout" in p.stderr, p.stderr[-2000:]


def test_the_session_watchdog_is_off_outside_ci(tmp_path):
    p, _ = _py("import _exit_guard\nprint(_exit_guard.arm_session(1))\n", tmp_path, env={"CI": ""})
    assert p.returncode == 0 and p.stdout.strip() == "False", (p.stdout, p.stderr)


def test_a_child_is_listed_and_reaped_by_its_pid(tmp_path):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], stdin=subprocess.DEVNULL)
    try:
        time.sleep(0.3)
        assert child.pid in [pid for pid, _ in _exit_guard.descendants()]
        assert _exit_guard.reap([child.pid], grace=5) == {child.pid: "terminated"}
        assert child.wait(timeout=10) is not None
    finally:
        if child.poll() is None:
            child.kill()


INNER_CONFTEST = """
    import sys
    sys.path.insert(0, %r)
    from _exit_guard import Plugin

    def pytest_configure(config):
        config.pluginmanager.register(Plugin(), "inspeximus-exit-guard")
"""

INNER_TEST = """
    import os, subprocess, sys, threading, time

    def test_leaks_a_child():
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"], stdin=subprocess.DEVNULL)
        open(os.environ["LEAK_PID_FILE"], "w").write(str(p.pid))

    def test_leaves_a_thread_that_stalls_the_exit():
        if os.environ.get("STALL"):
            threading.Thread(target=time.sleep, args=(3600,)).start()
"""


def _inner_pytest(tmp_path, stall):
    d = tmp_path / "inner"
    d.mkdir()
    (d / "conftest.py").write_text(textwrap.dedent(INNER_CONFTEST % TESTS), encoding="utf-8")
    (d / "test_inner.py").write_text(textwrap.dedent(INNER_TEST), encoding="utf-8")
    (d / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    pid_file = tmp_path / "leak.pid"
    env = dict(os.environ, LEAK_PID_FILE=str(pid_file), EXIT_GUARD_LIMIT_S="3", STALL="1" if stall else "",
               CI="")
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n", "0", str(d)],
                       capture_output=True, text=True, timeout=120, cwd=str(d), env=env, stdin=subprocess.DEVNULL)
    return p, int(pid_file.read_text())


def test_a_run_stops_the_child_a_test_leaked(tmp_path):
    p, pid = _inner_pytest(tmp_path, stall=False)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-2000:]
    assert "[exit guard] stopped 1 child process(es) this run left alive" in p.stderr, p.stderr[-2000:]
    assert str(pid) in p.stderr
    deadline = time.time() + 10
    while time.time() < deadline and _exit_guard._alive(pid):
        time.sleep(0.1)
    assert not _exit_guard._alive(pid)


def test_a_green_run_that_stalls_at_exit_ends_red(tmp_path):
    p, pid = _inner_pytest(tmp_path, stall=True)
    try:
        assert "2 passed" in p.stdout, p.stdout[-2000:]
        assert p.returncode == 1, (p.returncode, p.stderr[-2000:])
        assert "[exit guard] pid" in p.stderr and "still alive 3 s" in p.stderr
    finally:
        if _exit_guard._alive(pid):
            _exit_guard.reap([pid])


def test_this_suite_runs_with_the_guard(request):
    assert isinstance(request.config.pluginmanager.get_plugin("inspeximus-exit-guard"), _exit_guard.Plugin)


def test_without_psutil_children_are_read_from_proc(tmp_path, monkeypatch):
    # The zero-dependency CI leg has no psutil, so this is the path that runs there.
    if not os.path.isdir("/proc"):
        import pytest
        pytest.skip("no /proc on this platform; the psutil path covers it")
    monkeypatch.setitem(sys.modules, "psutil", None)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], stdin=subprocess.DEVNULL)
    try:
        time.sleep(0.3)
        found = dict(_exit_guard.descendants())
        assert child.pid in found and "time.sleep(120)" in found[child.pid]
        assert _exit_guard.reap([child.pid], grace=5) == {child.pid: "terminated"}
    finally:
        if child.poll() is None:
            child.kill()
