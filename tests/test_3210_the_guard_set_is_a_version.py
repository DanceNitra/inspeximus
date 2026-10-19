"""The guard set is a version number, the guards' inputs and the Unicode version (3.21, item 5).

Until 3.21, `_guard_set_hash()` hashed the source of `_instruction_shape`, `_stuffing` and `_assess_read_guards`,
docstrings and comments included. A stamp on a record is valid only under the set it was made with, so any edit of
that text made every stamp in every user's store foreign, and the record was assessed again on every fresh handle
until something re-stamped it (AUDIT-B H-1 on 3.17.1). This file replaces the 3.17.1 hash pin, which guarded the same
cost from the other side.

The source is pinned here instead, to `_GUARD_SET_VERSION`. If `test_the_guards_source_is_pinned_to_the_version`
fails, a guard changed, and you decide which kind of change it is:
- it can judge a text differently: raise `_GUARD_SET_VERSION` in core.py and add the new pin under the new number;
  every stored verdict is then assessed again once, which is the point;
- it cannot (a comment, a rename, a speed-up with the same verdicts): replace the pin under the current number.
"""
from __future__ import annotations

import hashlib
import inspect
import os
import sys
import unicodedata

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import core  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

#: sha256 of the source of `_instruction_shape`, `_stuffing` and `Inspeximus._assess_read_guards`, by guard version.
GUARD_SOURCE = {
    1: "a18312ade5b8c5cfa458086fcb0f1b3554470a1f7fe2fbcd1ae08b8b311c11bb",   # 3.21; the same text as 3.18.0 to 3.20.0
}
CLEAN = "the deploy key rotates on Fridays"


def _source_sha():
    src = "".join(inspect.getsource(f) for f in (core._instruction_shape, core._stuffing, Inspeximus._assess_read_guards))
    return hashlib.sha256(src.encode("utf-8")).hexdigest()


def _inputs(h):
    for name in core._GUARD_INPUTS:
        h.update(f"{name}={core._guard_canon(getattr(core, name))};".encode("utf-8", "surrogatepass"))
    h.update(unicodedata.unidata_version.encode("ascii"))
    return h.hexdigest()[:32]


def _formula(version):
    """The set as 3.21 defines it, computed here without core's own function."""
    h = hashlib.sha256()
    h.update(b"inspeximus read guards v%d;" % version)
    return _inputs(h)


def _formula_320():
    """The set as 3.18.0 to 3.20.0 computed it: the guards' source in place of the version."""
    h = hashlib.sha256()
    for f in (core._instruction_shape, core._stuffing, Inspeximus._assess_read_guards):
        h.update(inspect.getsource(f).encode("utf-8"))
    return _inputs(h)


@pytest.fixture
def fresh_set(monkeypatch):
    monkeypatch.setattr(core, "_GUARD_SET", None)            # computed once per process: recompute it in each test
    yield
    core._GUARD_SET = None


@pytest.fixture
def key_home(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("key-home")
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(home))
    return home


def test_the_guards_source_is_pinned_to_the_version():
    want = GUARD_SOURCE.get(core._GUARD_SET_VERSION)
    got = _source_sha()
    assert want is not None, "no source pin for guard version %d: add %s" % (core._GUARD_SET_VERSION, got)
    assert got == want, (
        "a read guard changed (_instruction_shape, _stuffing or _assess_read_guards), source sha %s, pinned %s for "
        "version %d. If it can judge a text differently, raise _GUARD_SET_VERSION and pin this sha under the new "
        "number. If it cannot, replace the pin under version %d." % (got, want, core._GUARD_SET_VERSION,
                                                                      core._GUARD_SET_VERSION))


def test_the_set_is_the_version_the_inputs_and_the_unicode_version(fresh_set):
    assert core._guard_set_hash() == _formula(core._GUARD_SET_VERSION)


def test_raising_the_version_changes_the_set(fresh_set, monkeypatch):
    before = core._guard_set_hash()
    monkeypatch.setattr(core, "_GUARD_SET_VERSION", core._GUARD_SET_VERSION + 1)
    core._GUARD_SET = None
    after = core._guard_set_hash()
    assert after != before and after == _formula(core._GUARD_SET_VERSION)


def test_another_unicode_version_changes_the_set(fresh_set, monkeypatch):
    before = core._guard_set_hash()
    monkeypatch.setattr(unicodedata, "unidata_version", "0.0.0")
    core._GUARD_SET = None
    assert core._guard_set_hash() != before, "a stamp made under another Unicode database would be trusted"


def test_a_new_stamp_carries_the_versioned_set(tmp_path, key_home, fresh_set):
    m = Inspeximus(str(tmp_path / "s.json"))
    m.remember(CLEAN, key="ops::deploy")
    m.flush()
    rec = next(r for r in Inspeximus(str(tmp_path / "s.json")).items if r.get("key") == "ops::deploy")
    held = rec["meta"]["read_guards"]
    assert held["set"] == _formula(core._GUARD_SET_VERSION), held


def test_a_stamp_made_under_the_3_20_set_is_assessed_again_once(tmp_path, key_home, fresh_set, monkeypatch):
    """The upgrade cost, stated: an authentic 3.20 stamp (its MAC made with this store's key) is foreign once."""
    p = str(tmp_path / "s.json")
    m = Inspeximus(p)
    m.remember(CLEAN, key="ops::deploy")
    rec = next(r for r in m.items if r.get("key") == "ops::deploy")
    old = _formula_320()
    assert old != core._guard_set_hash(), "CONTROL: the 3.20 set differs from the 3.21 set"
    th = hashlib.sha256(CLEAN.encode("utf-8")).hexdigest()
    rec["meta"]["read_guards"] = {"set": old, "mac": core._guard_mac(m._guard_key(), "clean", rec["id"], th, old)}
    m.flush()
    calls = []
    real = core._instruction_shape
    monkeypatch.setattr(core, "_instruction_shape", lambda t: (calls.append(t), real(t))[1])
    assert Inspeximus(p).recall("deploy key rotates", k=5), "CONTROL: the clean record serves"
    assert calls == [CLEAN], "a 3.20 stamp was trusted, or the record was not assessed: %r" % calls
