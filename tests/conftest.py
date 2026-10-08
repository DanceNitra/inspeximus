"""Shared fixtures for the witness/audit-bundle tests.

`fork_of` lives here because three separate modules got it wrong in the same way, which makes it a
class of defect rather than three mistakes. Each of them built its "rewritten history" by creating a
SECOND store from scratch and handing the witness the victim's `store_id="prod"` label. That worked
only while the witness keyed its fork-memory on that caller-supplied label -- the very defect the
2.10.6 round fixed, because it let a rolled-back store be `cp`-ed elsewhere and re-witnessed as a
first contact. Once the witness keyed on the genesis receipt hash instead, those fixtures stopped
reaching the victim's history at all, and every one of them reported a pass.

A fork is a chain that SHARES A GENESIS and diverges after it. Two stores built independently are
two stores, and a witness reporting them as a fork of each other would be raising a false alarm.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile

import pytest

from inspeximus import Inspeximus

from _store_io import load_receipts, load_store, save_receipts, save_store


@pytest.fixture(scope="session", autouse=True)
def _every_temp_file_lands_under_pytests_basetemp(tmp_path_factory):
    """Point `tempfile` and the TMP/TEMP/TMPDIR environment at pytest's own basetemp for the session.

    MEASURED 2026-09-20 on the machine that runs this suite: 1,027,150 entries in the user's Temp
    directory were ours. 596,291 were `inspeximus-<hex>.lock` files, one per store PATH, written by
    `_StoreLock` into `tempfile.gettempdir()` and never removed (by design for a real store; a test
    store is a fresh path every time). 422,798 were `tmp*` directories from the 354 bare
    `tempfile.mkdtemp()` calls across 149 test files, none of which cleans up. The rest were the
    example runners' `inspeximus_example_*` and `inspeximus_base_*` directories.

    Fixing the two example call sites would have removed 0.7% of it. The class is "a test asked
    the system for a temporary path and never gave it back", and the one place that covers every
    caller, including the lock file and every subprocess a test spawns, is the directory those
    calls resolve to. pytest keeps its basetemp to the last three runs and prunes older ones, so
    everything written here disappears on its own three sessions later. The environment is set
    too, because a child interpreter reads TMP/TEMP before it reads anything of ours.
    """
    base = tmp_path_factory.getbasetemp() / "tmp"
    base.mkdir(parents=True, exist_ok=True)
    before = tempfile.tempdir
    env_before = {k: os.environ.get(k) for k in ("TMP", "TEMP", "TMPDIR")}
    tempfile.tempdir = str(base)
    for k in env_before:
        os.environ[k] = str(base)
    try:
        yield
    finally:
        tempfile.tempdir = before
        for k, v in env_before.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture(scope="session", autouse=True)
def _a_probe_run_by_the_suite_never_rewrites_a_tracked_file(tmp_path_factory):
    """Every Python the suite starts loads tests/probe_shadow/sitecustomize.py, which sends a probe's
    writes to tracked files into a shadow directory. The reason and the exact scope are in that file.

    MEASURED 2026-09-27: CI's Python 3.9 leg failed test_running_the_examples_does_not_dirty_the_repository
    because a probe on another worker rewrote agora_output/lab/data/forget_verification_bench.json, and
    one full local run left two tracked probe receipts modified."""
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    listing = tmp_path_factory.getbasetemp() / "tracked-files.txt"
    shadow = tmp_path_factory.getbasetemp() / "probe-shadow"
    if not listing.exists():
        out = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True).stdout or b""
        listing.write_text("\n".join(p for p in out.decode("utf-8", "replace").split("\0") if p) + "\n",
                           encoding="utf-8")
    shim = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_shadow")
    before = {k: os.environ.get(k) for k in ("PYTHONPATH", "INSPEXIMUS_PROBE_ROOT", "INSPEXIMUS_PROBE_SHADOW",
                                             "INSPEXIMUS_PROBE_TRACKED")}
    os.environ["PYTHONPATH"] = os.pathsep.join(p for p in (shim, before["PYTHONPATH"]) if p)
    os.environ["INSPEXIMUS_PROBE_ROOT"] = root
    os.environ["INSPEXIMUS_PROBE_SHADOW"] = str(shadow)
    os.environ["INSPEXIMUS_PROBE_TRACKED"] = str(listing)
    try:
        yield
    finally:
        for k, v in before.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def fork_of(ix, dest, records, receipt_key=None, keep=1):
    """A real fork of `ix` at `dest`: same genesis receipt, divergent history from `keep` onwards.

    `records` is a list of (text, key, object) written after the rollback. Returns the forked store,
    whose derived store id -- what the witness keys on -- equals the original's.
    """
    shutil.copytree(os.path.dirname(str(ix.path)), dest)
    p = os.path.join(dest, os.path.basename(str(ix.path)))
    rows = load_receipts(p)
    kept = {r["memory_id"] for r in rows[:keep]}
    del rows[keep:]
    save_receipts(p, rows)
    save_store(p, [r for r in load_store(p) if r["id"] in kept])

    f = Inspeximus(path=p, receipts=True, receipt_key=receipt_key)
    for text, key, obj in records:
        f.remember(text, key=key, object=obj)
    f.flush()
    return f


# -- process-global state must be put back --------------------------------------------------------
#
# WHY THIS EXISTS. On 2026-09-10 one line in a new test file -- `module.urllib.request.urlopen =
# fake` -- replaced urlopen for the WHOLE process, because `urllib.request` is the shared module and
# not a private copy. Seventeen tests in four unrelated files then reached that fake, several test
# files later, and failed with `AttributeError: 'str' object has no attribute 'full_url'` and
# `assert 401 == 400`. Nothing caught it until CI.
#
# The local runs were worse than useless: the failure SET moved between runs, because test order
# moved, and that was read as flakiness. A failing set that changes per run is the signature of a
# shared-state leak, not noise, and two 17-minute runs were spent learning that.
#
# monkeypatch already restores what it patches. This catches the bare assignment that does not, and
# it names the test that did it instead of the test that tripped over it.
import builtins as _builtins
import socket as _socket
import subprocess as _subprocess
import time as _time
import urllib.request as _urllib_request

import pytest as _pytest

#: (module, attribute) pairs a test may legitimately want to fake, and must therefore put back.
#: Deliberately short: each entry costs one identity comparison per test, and a long list of things
#: nobody patches would be cost without cover.
_GLOBALS_THAT_MUST_SURVIVE_A_TEST = (
    (_urllib_request, "urlopen"),
    (_socket, "socket"),
    (_socket, "create_connection"),
    (_subprocess, "run"),
    (_subprocess, "Popen"),
    (_time, "sleep"),
    (_builtins, "open"),
)


@_pytest.fixture(autouse=True)
def _no_test_leaves_a_global_patched():
    before = [getattr(mod, attr) for mod, attr in _GLOBALS_THAT_MUST_SURVIVE_A_TEST]
    yield
    leaked = []
    for (mod, attr), was in zip(_GLOBALS_THAT_MUST_SURVIVE_A_TEST, before):
        now = getattr(mod, attr)
        if now is not was:
            leaked.append(("%s.%s" % (mod.__name__, attr), was, now))
            # PUT IT BACK, not only report it. Without this the guard names the culprit and the
            # cascade still happens: measured on the incident this was written for, one leak took
            # 17 tests in four later files with it. Restoring turns that into one error on the test
            # that did it, which is the only place the fix belongs.
            setattr(mod, attr, was)
    if leaked:
        raise AssertionError(
            "this test left process-global state patched, so every test that runs after it in this "
            "worker sees the fake:\n"
            + "".join("  %s is now %r, was %r\n" % (name, now, was) for name, was, now in leaked)
            + "Use monkeypatch.setattr, which restores it. A bare assignment to a module attribute "
              "is a process-wide change, and the test that BREAKS is never the test that did it.")


def _redirect_home(config):
    """Every test, and every process a test starts, gets a temporary home.

    MEASURED 2026-09-27: test_docs_examples_are_runnable.py runs every documented command, README.md
    documents `inspeximus install --ide claude`, and the subprocess inherited the real HOME and
    USERPROFILE. Each suite run installed this tree's version into the owner's ~/.claude.json and
    ~/.claude/settings.json; a tree at 3.14.2 pinned a version not yet on PyPI, and every new Claude
    Code session on that machine then launched an MCP server that could not start. A sandboxed run
    found the same class elsewhere: chain heads under %APPDATA%\\inspeximus\\heads from subprocesses
    that strip INSPEXIMUS_KEY_HOME, ~/.inspeximus/.update_check.json from a probe that starts the MCP
    server (an update check is a network call), and third-party configs (~/.mem0, ~/.haystack).

    APPLIED IN pytest_configure, BEFORE COLLECTION, not in a fixture. Four test modules copy
    os.environ into a module-level ENV when they are imported, which is during collection, before
    any fixture runs; the first version of this redirect was a session fixture, and the run-end guard
    caught one of those modules' children writing a chain head into the real %APPDATA%. Not in the
    xdist controller: it runs no tests, and a worker STARTED with a redirected USERPROFILE breaks the
    Windows known-folder API (appdirs, used by crewai). Changed inside the worker it is not.

    Set in os.environ, so a child inherits it however it builds its environment from ours. On
    Windows expanduser() and Path.home() read USERPROFILE and ignore HOME, which is why a
    HOME-only redirect would not have stopped this.

    NO AppData\\Local IN THE TEMPORARY HOME, deliberately. With the Microsoft Store Python,
    sys.executable is an alias under the profile's AppData\\Local\\Microsoft\\WindowsApps; measured
    here, a process started with USERPROFILE pointing at a home that HAS AppData\\Local derives its
    sys.executable from there and cannot start a child Python at all. LOCALAPPDATA itself is left
    alone for the same reason.

    RESIDUAL, stated: a lookup through the Windows known-folder API (appdirs, used by crewai)
    resolves the real profile inside a process that changed its environment after it started. That
    is third-party data, never a host configuration; `_home_guard` checks the configurations.

    HF_HOME keeps the real Hugging Face cache, so a test that loads a local model does not download
    it again into every temporary home. INSPEXIMUS_NO_UPDATE_CHECK=1: tests do not call PyPI."""
    config._real_home = os.path.expanduser("~")
    if not hasattr(config, "workerinput") and config.getoption("numprocesses", default=None):
        return                                    # the xdist controller: its workers redirect themselves
    import tempfile as _tempfile
    home = _tempfile.mkdtemp(prefix="inspeximus-test-home-")
    for sub in (("AppData", "Roaming"), (".config",)):
        os.makedirs(os.path.join(home, *sub), exist_ok=True)
    new = {"HOME": home, "USERPROFILE": home, "APPDATA": os.path.join(home, "AppData", "Roaming"),
           "XDG_CONFIG_HOME": os.path.join(home, ".config"), "INSPEXIMUS_NO_UPDATE_CHECK": "1"}
    hf = os.path.join(config._real_home, ".cache", "huggingface")
    if not os.environ.get("HF_HOME") and os.path.isdir(hf):
        new["HF_HOME"] = hf
    # PLAYWRIGHT_BROWSERS_PATH keeps the real browser cache. CI installs Chromium once into the real
    # home; with HOME redirected, Playwright looked for it under the temporary home, and the browser
    # verifier job failed 144 tests at setup (run 36330567561). On Windows the cache is under
    # LOCALAPPDATA, which is not redirected, so no candidate below exists there.
    if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        xdg_cache = os.environ.get("XDG_CACHE_HOME")
        for pw in ([os.path.join(xdg_cache, "ms-playwright")] if xdg_cache else []) + [
                os.path.join(config._real_home, ".cache", "ms-playwright"),
                os.path.join(config._real_home, "Library", "Caches", "ms-playwright")]:
            if os.path.isdir(pw):
                new["PLAYWRIGHT_BROWSERS_PATH"] = pw
                break
    config._home_env_before = {k: os.environ.get(k) for k in new}
    os.environ.update(new)
    config._test_home = home


def _restore_home(config):
    before = getattr(config, "_home_env_before", None)
    if before is None:
        return
    for k, v in before.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    shutil.rmtree(config._test_home, ignore_errors=True)


@pytest.fixture(scope="session")
def _no_test_writes_the_real_home(request):
    """{"home": the temporary home every test runs in, "real": the home the run started with}.

    The redirect itself happens in pytest_configure (see `_redirect_home`); a test asks for this
    only to compare against the two paths, and it fails loudly if this process was not redirected."""
    home = getattr(request.config, "_test_home", None)
    if home is None:
        pytest.fail("the conftest did not redirect the home in this process")
    return {"home": home, "real": request.config._real_home}


@pytest.fixture(autouse=True, scope="session")
def _heads_and_keys_in_a_temporary_config_home(tmp_path_factory, request):
    """Every store with receipts writes its chain head to the config home. One suite run left 6,102
    heads in the real one (measured 2026-09-16); the suite gets its own. Tests that need a specific
    home set INSPEXIMUS_KEY_HOME themselves and override this."""
    import os
    if not os.environ.get("INSPEXIMUS_KEY_HOME"):
        os.environ["INSPEXIMUS_KEY_HOME"] = str(tmp_path_factory.mktemp("config-home"))
    if request.config.getoption("--receipts-tail"):
        tail_config(True)                       # the tail-on gate: every test starts with the tail switched on
    yield


def tail_config(on=True):
    """Switch the receipt tail on or off in the user's config, the only place that switches it (3.17.0).

    The environment variable INSPEXIMUS_RECEIPTS_TAIL is ignored: a project's settings can set it. The change lasts for
    the test; `_user_config_is_restored` puts the file back."""
    import json
    from inspeximus import _userconfig
    path = _userconfig.path()
    cfg = dict(_userconfig.read())
    rc = dict(cfg.get("receipts") or {})
    if on:
        rc["tail"] = True
    else:
        rc.pop("tail", None)
    cfg["receipts"] = rc
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh)


@pytest.fixture(autouse=True)
def _user_config_is_restored():
    """A test that writes the user's config (the receipt tail, the store format) does not leave it for the next one."""
    from inspeximus import _userconfig
    path = _userconfig.path()
    try:
        with open(path, "rb") as fh:
            before = fh.read()
    except OSError:
        before = None
    yield
    try:
        if before is None:
            if os.path.exists(path):
                os.remove(path)
        else:
            with open(path, "wb") as fh:
                fh.write(before)
    except OSError:
        pass


# ── sharding: one suite, split across parallel CI jobs ───────────────────────────────────────────────
# `--shard i/n` keeps bucket i of n and deselects the rest. The buckets are balanced on MEASURED time:
# tests/shard_durations.json holds seconds per test from a CI run (tools/shard_durations.py writes it),
# and a greedy longest-first pass puts each test where the shard's estimated wall time grows least.
#
# The estimate is the larger of two things, because a shard is n_workers processes, not one queue:
#   * everything in the shard divided by the workers (the parallel part), and
#   * the largest xdist group in the shard, since a group runs whole on ONE worker (the serial part).
# That second term is why the old key failed: it kept the cited-probe group whole, so one shard ran
# its ~13 minutes serially while the others finished in 3 to 7. A group only has to share a worker
# WITHIN a shard (its budgets assume nothing else of its kind runs beside it), so it may be split
# ACROSS shards, which are separate machines, and each part still runs on one worker there.
#
# The pass is deterministic: every shard sorts the same collection the same way and computes the same
# partition. A test missing from the durations file is charged the median. The union of the n shards
# is the whole suite and no test is in two; tests/test_the_shards_partition_the_suite.py checks both.
def pytest_addoption(parser):
    parser.addoption("--shard", default=None, help="run bucket i of n, written i/n (0-based)")
    parser.addoption("--receipts-tail", action="store_true", default=False,
                     help="run the suite with receipts.tail switched on in the session's user config (the tail-on gate)")


SHARD_DURATIONS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shard_durations.json")
#: GitHub's ubuntu-latest runners have 4 vCPUs, so `-n auto` starts 4 workers in each shard.
SHARD_WORKERS = 4
#: Key in the durations file for the serial mutation set, which shard 0 runs AFTER its tests. The plan
#: charges it to shard 0 up front, so that shard gets less of the suite.
MUTATION_KEY = "@mutation_set"


def _plain_id(nodeid):
    # xdist's loadgroup appends "@<group>" to the node id; durations are stored without it.
    return nodeid.split("@")[0]


def _group_of(item):
    group = item.get_closest_marker("xdist_group")
    if group is None:
        return None
    return str(group.args[0] if group.args else group.kwargs.get("name"))


def shard_plan(tests, n, durations, workers=SHARD_WORKERS):
    """Assign each test to one of n shards. `tests` is a list of (node id, group or None).

    Returns (bucket per test, estimated seconds per shard). Pure, so the balancing is testable without
    a collection."""
    tail = {0: durations.get(MUTATION_KEY, 0.0)} if n > 0 else {}
    durations = {k: v for k, v in durations.items() if k != MUTATION_KEY}
    known = sorted(durations.values())
    default = known[len(known) // 2] if known else 1.0
    cost = [durations.get(_plain_id(t), default) for t, _ in tests]
    order = sorted(range(len(tests)), key=lambda k: (-cost[k], _plain_id(tests[k][0])))
    total = [0.0] * n
    groups = [dict() for _ in range(n)]
    longest = [0.0] * n
    bucket = [0] * len(tests)

    def wall(j, extra=0.0, group=None):
        serial = max(groups[j].values(), default=0.0)
        if group is not None:
            serial = max(serial, groups[j].get(group, 0.0) + extra)
        return max((total[j] + extra) / workers, serial, longest[j], extra) + tail.get(j, 0.0)

    for k in order:
        group = tests[k][1]
        j = min(range(n), key=lambda b: (wall(b, cost[k], group), total[b], b))
        bucket[k] = j
        total[j] += cost[k]
        longest[j] = max(longest[j], cost[k])
        if group is not None:
            groups[j][group] = groups[j].get(group, 0.0) + cost[k]
    return bucket, [wall(j) for j in range(n)]


def _load_durations():
    import json
    try:
        with open(SHARD_DURATIONS, encoding="utf-8") as fh:
            return {k: float(v) for k, v in json.load(fh).items()}
    except FileNotFoundError:
        return {}


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    spec = config.getoption("--shard")
    if not spec:
        return
    i, n = (int(x) for x in spec.split("/"))
    if not (n > 0 and 0 <= i < n):
        raise ValueError("--shard must be i/n with 0 <= i < n, got %r" % spec)
    bucket, _ = shard_plan([(it.nodeid, _group_of(it)) for it in items], n, _load_durations())
    keep = [it for it, b in zip(items, bucket) if b == i]
    drop = [it for it, b in zip(items, bucket) if b != i]
    report = os.environ.pop("SHARD_REPORT", None)
    if report:
        # What tools/shard_total.py compares across shards: every shard must have collected the same
        # suite, and together they must have kept all of it, each test exactly once.
        import json
        with open(report, "w", encoding="utf-8") as fh:
            json.dump({"shard": spec, "all": sorted(it.nodeid for it in items),
                       "mine": sorted(it.nodeid for it in keep)}, fh)
    if drop:
        config.hook.pytest_deselected(items=drop)
    items[:] = keep


# ── measured time per test, the input for balancing the shards ─────────────────────────────────────
# With SHARD_TIMES set, the controlling process writes {node id: seconds} at the end of the run, setup +
# call + teardown summed. Under xdist every worker's report reaches the controller, so only it writes.
# Taken out of the environment at configure time for the same reason as SHARD_REPORT: a test that runs
# pytest in a subprocess must not overwrite the file with its own few tests.
#: The environment variable that names this run's own temporary root; read by `_home_guard` callers and tests.
RUN_TMP_ENV = "PYTEST_INSPEXIMUS_RUN_TMP"


def _own_temp_root(config):
    """One temporary root per run, for every test, worker and child process (3.16.3).

    The run-end guard attributed a new chain head by whether its store sat under the SYSTEM temp
    directory, so another session's temp store failed this run: measured 2026-10-05, a -m mutation run
    failed on 6 heads of temporary `s.json` stores, and each of the 4 mutation-marked files, run alone with a
    watcher on the heads directory, wrote none. TEMP, TMP and TMPDIR point at a directory this run
    creates, before collection and before xdist starts its workers, so they inherit it; the guard then
    counts a head as this run's only when its store is under that root."""
    if hasattr(config, "workerinput"):
        return                                    # a worker inherits the controller's root
    import tempfile as _tempfile
    root = _tempfile.mkdtemp(prefix="inspeximus-run-")
    names = ("TEMP", "TMP", "TMPDIR", RUN_TMP_ENV)
    config._run_tmp_env_before = {k: os.environ.get(k) for k in names}
    os.environ.update({k: root for k in names})
    _tempfile.tempdir = None                      # recomputed from the environment on next use
    config._run_tmp = root


def _restore_temp_root(config):
    before = getattr(config, "_run_tmp_env_before", None)
    if before is None:
        return
    for k, v in before.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    import tempfile as _tempfile
    _tempfile.tempdir = None
    shutil.rmtree(config._run_tmp, ignore_errors=True)


def pytest_configure(config):
    _own_temp_root(config)
    _redirect_home(config)
    # A mutation survivor is re-run against the full suite (tools/mutation_check.py). Tests that drive the
    # gate with a survivor on purpose would each start a full serial suite run, so the session turns that
    # off; the tests of the classification itself set MUTATION_FULL_SUITE to their own small suite.
    os.environ["MUTATION_FULL_SUITE"] = "off"
    if not hasattr(config, "workerinput"):
        config._shard_times_path = os.environ.pop("SHARD_TIMES", None)
        config._shard_times = {}


def pytest_runtest_logreport(report):
    times = getattr(pytest_runtest_logreport, "_sink", None)
    if times is not None:
        node = report.nodeid.split("@")[0]
        times[node] = times.get(node, 0.0) + float(getattr(report, "duration", 0.0) or 0.0)


def pytest_sessionstart(session):
    config = session.config
    if getattr(config, "_shard_times_path", None):
        pytest_runtest_logreport._sink = config._shard_times
    # THE RUN-END GUARD for `_no_test_writes_the_real_home`, in the controlling process only: under
    # xdist the workers change their own environment, and the controller still sees the real home.
    if not hasattr(config, "workerinput"):
        import _home_guard
        config._real_home_before = _home_guard.snapshot(config._real_home)


def pytest_sessionfinish(session):
    config = session.config
    before = getattr(config, "_real_home_before", None)
    if before is not None:
        import _home_guard
        # A GUARD THAT RAISES HERE ERASES THE RUN'S OWN REPORT: an exception in this hook stops pytest
        # before it prints the summary, so no failing test is named anywhere. Measured 2026-09-28 by
        # the mutation gate, whose mutant of this call survived the full suite for exactly that
        # reason. A crash of the guard is reported as a guard failure and fails the run instead.
        try:
            changed, live = _home_guard.classify(before, _home_guard.snapshot(config._real_home), config._real_home,
                                                 temp_roots=[config._run_tmp] if getattr(config, "_run_tmp", None)
                                                 else None)
        except Exception as exc:                            # noqa: BLE001
            changed, live = ["the run-end guard itself failed: %r" % (exc,)], []
        tr = config.pluginmanager.get_plugin("terminalreporter")
        for ln in (["The real home changed beside this run, not by it (information, not a failure):"]
                   + ["  " + c for c in live] if live else []):
            (tr.write_line(ln) if tr else print(ln))
        if changed:
            lines = ["THIS RUN CHANGED THE REAL HOME (%s). A test wrote outside its temporary home:"
                     % config._real_home] + ["  " + c for c in changed]
            for ln in lines:
                (tr.write_line(ln, red=True) if tr else print(ln))
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
    path = getattr(config, "_shard_times_path", None)
    if path:
        import json
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({k: round(v, 3) for k, v in sorted(config._shard_times.items())}, fh, indent=0)


def pytest_unconfigure(config):
    _restore_home(config)
    _restore_temp_root(config)


@pytest.fixture
def user_config(monkeypatch, tmp_path_factory):
    """The user's config for one test (3.18): a key home of its own, beside the test's temp folders, and a setter that
    writes the config key of an INSPEXIMUS_* variable (inspeximus/_envpolicy.POLICY) the way the user would. A project's
    environment no longer sets these, so a test that means "the user chose this" writes it here.

        user_config(INSPEXIMUS_ECHO_GUARD="0")      # guards.echo = false
    """
    import _userconfig_env

    def setter(**env):
        # The key home the test set itself (a `home` fixture); never the session's shared one, which every test in
        # the worker reads; else one of its own.
        kh = os.environ.get("INSPEXIMUS_KEY_HOME") or ""
        if not kh or os.path.basename(kh.rstrip("/\\")).startswith("config-home"):
            kh = str(tmp_path_factory.mktemp("user-config-key-home"))
            monkeypatch.setenv("INSPEXIMUS_KEY_HOME", kh)
        cfg, rest = _userconfig_env.config_settings(env)
        assert not [k for k in rest if k != "INSPEXIMUS_PROJECT"], "not a config setting: %s" % sorted(rest)
        _userconfig_env.write_user_config(kh, cfg)
        return kh
    return setter
