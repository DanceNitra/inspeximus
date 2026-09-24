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

from _store_io import load_store, save_store


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


def fork_of(ix, dest, records, receipt_key=None, keep=1):
    """A real fork of `ix` at `dest`: same genesis receipt, divergent history from `keep` onwards.

    `records` is a list of (text, key, object) written after the rollback. Returns the forked store,
    whose derived store id -- what the witness keys on -- equals the original's.
    """
    shutil.copytree(os.path.dirname(str(ix.path)), dest)
    p = os.path.join(dest, os.path.basename(str(ix.path)))
    rp = p + ".receipts.json"
    rec = json.load(open(rp, encoding="utf-8"))
    rows = rec if isinstance(rec, list) else rec["receipts"]
    kept = {r["memory_id"] for r in rows[:keep]}
    del rows[keep:]
    json.dump(rec, open(rp, "w", encoding="utf-8"))
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


@pytest.fixture(autouse=True, scope="session")
def _heads_and_keys_in_a_temporary_config_home(tmp_path_factory):
    """Every store with receipts writes its chain head to the config home. One suite run left 6,102
    heads in the real one (measured 2026-09-16); the suite gets its own. Tests that need a specific
    home set INSPEXIMUS_KEY_HOME themselves and override this."""
    import os
    if not os.environ.get("INSPEXIMUS_KEY_HOME"):
        os.environ["INSPEXIMUS_KEY_HOME"] = str(tmp_path_factory.mktemp("config-home"))
    yield


# ── sharding: one suite, split across parallel CI jobs ───────────────────────────────────────────────
# `--shard i/n` keeps the tests whose key lands in bucket i of n and deselects the rest. The key is the
# xdist group when a test declares one, so tests that must share a worker also share a shard, and the
# node id otherwise. crc32 rather than hash(): Python randomises str hashes per process, and every
# shard must compute the same partition. The union of the n shards is the whole suite and no test is
# in two; tests/test_the_shards_partition_the_suite.py checks both.
def pytest_addoption(parser):
    parser.addoption("--shard", default=None, help="run bucket i of n, written i/n (0-based)")


LONG_GROUP = "group:cited_probes"


def _shard_key(item):
    group = item.get_closest_marker("xdist_group")
    if group is not None:
        return "group:" + str(group.args[0] if group.args else group.kwargs.get("name"))
    return item.nodeid


def pytest_collection_modifyitems(config, items):
    spec = config.getoption("--shard")
    if not spec:
        return
    import zlib
    i, n = (int(x) for x in spec.split("/"))
    if not (n > 0 and 0 <= i < n):
        raise ValueError("--shard must be i/n with 0 <= i < n, got %r" % spec)
    keep, drop = [], []
    for it in items:
        key = _shard_key(it)
        # The cited-probe group is the longest single unit and cannot be split (its budgets assume one
        # worker), so it gets the last shard to itself and everything else spreads over the others.
        if n > 1:
            bucket = n - 1 if key == LONG_GROUP else zlib.crc32(key.encode("utf-8")) % (n - 1)
        else:
            bucket = 0
        (keep if bucket == i else drop).append(it)
    # Popped, not read: a test that runs pytest in a subprocess would otherwise inherit the path and
    # overwrite this shard's report with its own small collection (seen in CI on 7aca0f6).
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
