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

import pytest

from inspeximus import Inspeximus

from _store_io import load_store, save_store


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
