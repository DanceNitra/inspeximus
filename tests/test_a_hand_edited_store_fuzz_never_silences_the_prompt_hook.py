"""The fuzz behind F-29 (3.16.5): every top-level field and every meta field of a record, set by hand-editing the raw store file
to wrong types, huge integers, inf, NaN, lone surrogates and deep nesting, each case a store of its own. The hook must answer
every time, with no `failed` and no traceback. About 90 s on one worker, so the mutation entries use the quick file
(test_one_bad_record_cannot_silence_the_prompt_hook.py) and this one runs in the suite."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _bad_record_io import (VALUES, IDS, META_EXTRA, _hand_edit, _project, _prompt, cc, sandbox, _isolate)  # noqa: E402,F401


def _fields():
    """Every top-level field and every meta field a record of this version carries, discovered, not listed."""
    import tempfile
    d = tempfile.mkdtemp()
    p = os.path.join(d, "p")
    os.makedirs(os.path.join(p, ".git"))
    os.environ["INSPEXIMUS_KEY_HOME"] = os.path.join(d, "kh")
    try:
        m = cc._store(p)
        m.remember("a note about the release", key="victim")
        r = dict(m.items[0])
        top = sorted(k for k in r if k != "meta")
        meta = sorted(set((r.get("meta") or {}).keys()) | set(META_EXTRA))
    finally:
        os.environ.pop("INSPEXIMUS_KEY_HOME", None)
    return top, meta


TOP, META = _fields()
CASES = [("top", f, i) for f in TOP for i in range(len(VALUES))] + [("meta", f, i) for f in META for i in range(len(VALUES))]


def test_the_fuzz_covers_every_field_and_every_shape():
    assert len(TOP) >= 15 and "text" in TOP and "value" in TOP and "tags" in TOP and "ts" in TOP, TOP
    assert "quarantined" in META and "read_guards" in META, META
    assert len(CASES) == (len(TOP) + len(META)) * len(VALUES) and len(CASES) > 400


def test_every_field_set_by_hand_to_every_wrong_shape_leaves_the_hook_answering(sandbox):
    """One store per case: the good decision must always be served, with no `failed` and no traceback on stderr. (The block shows
    two mechanics at most, so a good plain note is not required: the decision is the proof that the read answered.)"""
    silenced, isolated = [], []
    for n, (where, field, vi) in enumerate(CASES):
        p, store = _project(sandbox, "c%d" % n)
        value = VALUES[vi]
        if where == "top":
            _hand_edit(store, lambda d, f=field, v=value: d.__setitem__(f, v))
        else:
            _hand_edit(store, lambda d, f=field, v=value: d.setdefault("meta", {}).__setitem__(f, v) if isinstance(d.get("meta"), dict) else None)
        _isolate._SAID.clear()
        out, err = _prompt(p)
        if "failed" in err or "Traceback" in err or "release process uses the gate" not in out:
            silenced.append((where, field, IDS[vi], err.strip()[-110:]))
        if "left out of this answer" in err:
            isolated.append((where, field, IDS[vi]))
        # whatever the record does, the output is ONE block and never carries the raw value of an invalid field
        assert out.count("relevant project memory") <= 1
    assert not silenced, "%d of %d cases silenced or broke the hook; first: %s" % (len(silenced), len(CASES), silenced[:6])
    # CONTROL: the fuzz must still reach the isolation. F-29's meta.quarantined shapes are cases that load, rank and raise (the numbers are repaired at load since F-36); if no case is
    # isolated any more, the fixture stopped reproducing the defect and the green above measures nothing.
    assert ("meta", "quarantined", "str") in isolated and ("meta", "quarantined", "list2") in isolated, isolated
    print("fuzz: %d cases, %d isolated" % (len(CASES), len(isolated)))


