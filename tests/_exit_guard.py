"""The end of a test run is bounded: a run that stalls after its tests becomes a red run with stacks (3.22).

Measured 2026-10-21: ci.yml's test (3.12) leg stopped after its "[ 99%]" line in 2 of 3 runs, once for 4 h and
once for over an hour, and pytest-timeout's 900 s never fired. A stall outside a test body (session end, a worker
exit, a child that holds the step's output open) is out of that timeout's reach, and so is a test that holds the
GIL, because the thread method needs the GIL to run. The guards here close both:

- `arm(status)`, at session finish: a daemon thread that, if the process is still alive `delay` seconds later,
  writes every thread's stack and the live child processes, then ends the process with the run's status, or 1
  when the status was 0. `faulthandler.dump_traceback_later` backs it with a C thread that needs no GIL.
- `reap(pids)`: stops this run's own leftover child processes by pid, terminate first and kill after a grace.
  Never by name.
- `arm_session(limit)`, on CI only: the same C-thread watchdog over the whole session, for a stall inside one.

Children are listed with psutil when it is installed and from /proc otherwise, so the zero-dependency CI leg
has them too. Where neither exists the list is empty and only the stacks are written."""
import faulthandler
import os
import signal
import sys
import threading
import time

#: Seconds a process may live past its session finish before the guard ends it.
EXIT_LIMIT_S = 120
#: Seconds a whole pytest process may run on CI. ci.yml's slowest step measured 1,261 s (3.9, run 39564229511).
SESSION_LIMIT_S = 35 * 60


def descendants(pid=None):
    """Every live descendant of `pid` (default: this process), as a list of (pid, command line)."""
    pid = os.getpid() if pid is None else pid
    try:
        import psutil
    except ImportError:
        psutil = None
    if psutil is not None:
        try:
            out = []
            for p in psutil.Process(pid).children(recursive=True):
                try:
                    out.append((p.pid, " ".join(p.cmdline())[:300]))
                except psutil.Error:
                    out.append((p.pid, "?"))
            return out
        except psutil.Error:
            return []
    if not os.path.isdir("/proc"):
        return []
    parent = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open("/proc/%s/stat" % name, "rb") as fh:
                stat = fh.read().decode("utf-8", "replace")
            # The command name sits in parentheses and may hold spaces; the ppid is the second field after it.
            parent[int(name)] = int(stat[stat.rindex(")") + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            continue
    found, frontier = [], [pid]
    while frontier:
        nxt = [c for c, pp in parent.items() if pp in frontier]
        found.extend(nxt)
        frontier = nxt
    out = []
    for c in found:
        try:
            with open("/proc/%d/cmdline" % c, "rb") as fh:
                out.append((c, fh.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()[:300]))
        except OSError:
            out.append((c, "?"))
    return out


def _alive(pid):
    try:
        import psutil
        try:
            return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
        except psutil.Error:
            return False
    except ImportError:
        pass
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        with open("/proc/%d/stat" % pid, "rb") as fh:
            stat = fh.read().decode("utf-8", "replace")
        return stat[stat.rindex(")") + 2:].split()[0] != "Z"
    except (OSError, ValueError, IndexError):
        return True


def _signal(pid, kill):
    try:
        import psutil
        try:
            p = psutil.Process(pid)
            p.kill() if kill else p.terminate()
        except psutil.Error:
            pass
        return
    except ImportError:
        pass
    try:
        os.kill(pid, signal.SIGKILL if kill and hasattr(signal, "SIGKILL") else signal.SIGTERM)
    except OSError:
        pass


def reap(pids, grace=5.0):
    """Terminate each pid, wait up to `grace` seconds, then kill what is left. Returns {pid: outcome}."""
    pids = [p for p in pids if p != os.getpid()]
    for p in pids:
        _signal(p, kill=False)
    end = time.time() + grace
    while time.time() < end and any(_alive(p) for p in pids):
        time.sleep(0.1)
    out = {}
    for p in pids:
        if _alive(p):
            _signal(p, kill=True)
            out[p] = "killed"
        else:
            out[p] = "terminated"
    return out


def _report_and_exit(status, fd, delay, _exit):
    try:
        out = os.fdopen(os.dup(fd), "w", encoding="utf-8", errors="replace")
        out.write("\n[exit guard] pid %d is still alive %d s after its session finished; its stacks follow, "
                  "and it now exits with status %d.\n" % (os.getpid(), delay, status or 1))
        out.flush()
        faulthandler.dump_traceback(file=out, all_threads=True)
        kids = descendants()
        out.write("[exit guard] live child processes: %d\n" % len(kids))
        for pid, cmd in kids:
            out.write("  %d %s\n" % (pid, cmd))
        out.flush()
    finally:
        _exit(status or 1)


def arm(status, delay=EXIT_LIMIT_S, fd=2, _exit=os._exit):
    """Start the exit guard. `status` is the session's exit status; a stalled exit never reports 0."""
    fd = os.dup(fd)                     # pytest closes its saved stderr fd when capture stops; this copy stays
    t = threading.Thread(target=lambda: (time.sleep(delay), _report_and_exit(status, fd, delay, _exit)),
                         name="inspeximus-exit-guard", daemon=True)
    t.start()
    try:
        # Backstop for a stall that holds the GIL, where the thread above cannot run: a C thread, exit code 1.
        faulthandler.dump_traceback_later(delay + 30, exit=True, file=_fdfile(fd))
    except (ValueError, OSError, RuntimeError):
        pass
    return t


_FILES = []


def _fdfile(fd):
    f = os.fdopen(os.dup(fd), "w")
    _FILES.append(f)                    # faulthandler keeps only the fd; the file object must outlive it
    return f


def arm_session(limit=SESSION_LIMIT_S, fd=2):
    """On CI, end any pytest process that runs past `limit` with every thread's stack, GIL held or not."""
    if not os.environ.get("CI"):
        return False
    try:
        faulthandler.dump_traceback_later(limit, exit=True, file=_fdfile(fd))
    except (ValueError, OSError, RuntimeError):
        return False
    return True


def original_stderr_fd(config):
    """The fd of the real stderr, before pytest's fd capture replaced fd 2. Falls back to 2."""
    try:
        capman = config.pluginmanager.getplugin("capturemanager")
        fd = capman._global_capturing.err.targetfd_save
        if isinstance(fd, int) and fd >= 0:
            return fd
    except AttributeError:
        pass
    return getattr(sys.__stderr__, "fileno", lambda: 2)() if sys.__stderr__ else 2


class Plugin:
    """Registered by tests/conftest.py: arms the exit guard at session finish and stops leaked children."""

    def pytest_sessionfinish(self, session):
        # Registered as tryfirst below: on the controller, xdist's own sessionfinish waits for the workers, and
        # a stalled worker exit is one of the stalls this guard is for. EXIT_GUARD_LIMIT_S shortens the limit for
        # the guard's own tests (test_3220_a_stalled_run_end_is_bounded).
        # Not on an xdist worker: after its session a worker waits for the controller until every sibling is
        # done, and that wait is not a stall. Measured 2026-10-21 in ci.yml runs 39642830294 and 39646443034:
        # the guard ended 3 idle workers in 4 legs while a fourth still ran tests. A stalled worker keeps the
        # controller in its test loop, where the session watchdog (arm_session) covers it.
        if hasattr(session.config, "workerinput"):
            return
        arm(int(session.exitstatus or 0), delay=float(os.environ.get("EXIT_GUARD_LIMIT_S") or EXIT_LIMIT_S),
            fd=original_stderr_fd(session.config))

    def pytest_unconfigure(self, config):
        # trylast: xdist has torn its workers down by now, so what is left is a child a test leaked.
        left = descendants()
        if left:
            done = reap([pid for pid, _ in left])
            sys.stderr.write("[exit guard] stopped %d child process(es) this run left alive: %s\n"
                             % (len(left), "; ".join("%d %s (%s)" % (p, c[:120], done.get(p)) for p, c in left)))


try:
    import pytest as _pytest
    Plugin.pytest_sessionfinish = _pytest.hookimpl(tryfirst=True)(Plugin.pytest_sessionfinish)
    Plugin.pytest_unconfigure = _pytest.hookimpl(trylast=True)(Plugin.pytest_unconfigure)
except ImportError:
    pass
