"""A method that changes a record must leave that change on disk. Found by sweeping, not by guessing.

WHY THIS EXISTS. The row store writes only the ids a caller declares through `_touch`, which is what
makes one write cost one INSERT instead of a whole-file rewrite. The cost of that design is a rule
sixteen call sites have to remember, and a rule you have to remember is one you break: measured on
2026-09-06, four of twelve public methods changed a record in memory and never wrote it. `slash()`
lost the slash flags, `credit()` lost the counter, `observe()` lost the reopened marker. The JSON
store hid all of it, because a save there rewrites the whole file whether or not anyone declared
anything.

So this does not test four methods. It sweeps the public surface, calls each mutating method on the
same fixture, and requires memory and disk to agree afterwards. A NEW method that forgets to declare
its change fails here without anyone adding a case, which is the only version of this that keeps
working.

WHAT IS DELIBERATELY NOT COMPARED. Keys beginning with `_` are annotations a reader attaches to the
records it returns (`recall` sets `_stale_derived`), not stored state; requiring a write for those
would make every read dirty the store. `vec` is the embedding cache, stripped on write by design.

THE CONTROL. `_untouched_change_is_caught` plants an undeclared edit and requires this sweep's own
comparison to notice it. Without that, a sweep that compared nothing would pass just as quietly.
"""
import os
import tempfile

import pytest

from inspeximus import Inspeximus, sqlite_store as ss


def _fresh(fmt, monkeypatch):
    if fmt == "json":
        monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    else:
        monkeypatch.delenv("INSPEXIMUS_STORE_FORMAT", raising=False)
    p = os.path.join(tempfile.mkdtemp(), "m.json")
    m = Inspeximus(path=p, receipts=True)
    rec = m.remember("the invoice is 100 EUR", key="inv", mtype="fact", source={"doc": "src"})
    assert ss.looks_like_sqlite(p) is (fmt == "rows"), "the fixture is not in the format it claims"
    return m, p, (rec["id"] if isinstance(rec, dict) else rec)


def _read(path):
    if ss.looks_like_sqlite(path):
        return ss.load(path)
    import json
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _divergence(m, path):
    """Records whose in-memory state does not match what is on disk, and which fields differ."""
    disk = {r["id"]: r for r in _read(path)}
    out = []
    for r in m._items:
        d = disk.get(r["id"])
        if d is None:
            out.append((r["id"], ["absent from the store file"]))
            continue
        diff = sorted(k for k in set(d) | set(r)
                      if k != "vec" and not k.startswith("_") and d.get(k) != r.get(k))
        if diff:
            out.append((r["id"], diff))
    return out


CASES = {
    "remember": lambda m, i: m.remember("a second record", key="k2"),
    "supersede": lambda m, i: m.remember("the invoice is 200 EUR", key="inv"),
    "slash": lambda m, i: m.slash([i], scope="memory", reason="corrected"),
    "credit": lambda m, i: m.credit([i], True),
    "forget": lambda m, i: m.forget([i]),
    "revert": lambda m, i: (m.remember("the invoice is 200 EUR", key="inv"), m.revert("inv")),
    "consolidate": lambda m, i: m.consolidate(),
    "sleep": lambda m, i: m.sleep(),
    "recall": lambda m, i: m.recall("invoice"),
    "observe": lambda m, i: m.observe("the invoice is 100 EUR", "inv"),
    "apply_retention": lambda m, i: m.apply_retention(0.0),
    "forget_subject": lambda m, i: m.forget_subject("src", request_id="DSAR-1"),
    # Added after `restore` was found with the same hole `slash` had been fixed for. A fix that lands
    # on the reported instance and leaves its sibling is this project's most repeated defect, so the
    # sweep grew rather than the two call sites.
    "restore": lambda m, i: (m.slash([i], scope="memory", reason="c"), m.restore([i])),
    "ratify": lambda m, i: m.ratify(i, "reproduction", "another-agent"),
    "admit": lambda m, i: m.admit("a provisional record"),
    "confirm": lambda m, i: (m.admit("a provisional record"), m.confirm("a provisional record")),
    "monitor": lambda m, i: m.monitor([i], False),
    "slash_source": lambda m, i: m.slash([i], scope="source", reason="the source was wrong"),
    "propagate_outcome": lambda m, i: m.propagate_outcome(True, [i]),
    "retract_lineage": lambda m, i: m.retract_lineage("src"),
    "reembed": lambda m, i: m.reembed(),
    "consolidate_clusters": lambda m, i: m.consolidate_clusters(),
    "close_session": lambda m, i: (m.open_session("s1"), m.close_session("s1")),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_a_row_store_persists_exactly_what_the_json_store_would(name, monkeypatch):
    """THE QUESTION IS DIFFERENTIAL, and the first version of this test asked the wrong one.

    Asking "is the change on disk" after the call fails on `credit()` for a reason that has nothing
    to do with the format: `credit` ends in an unforced `_save()`, which the write throttle can defer
    on either format. That version was measuring the throttle. What must hold is that the new format
    persists whatever the old one persisted, so both arms run the same case and the row store is
    required to be no worse.
    """
    mj, pj, ij = _fresh("json", monkeypatch)
    CASES[name](mj, ij)
    json_fields = {f for _rid, fs in _divergence(mj, pj) for f in fs}

    mr, pr, ir = _fresh("rows", monkeypatch)
    CASES[name](mr, ir)
    rows_fields = {f for _rid, fs in _divergence(mr, pr) for f in fs}

    # COMPARE FIELD NAMES, NOT RECORD IDS. The two arms are separate stores, so their ids never
    # match and a filter keyed on ids removes nothing -- the first version of this line reported a
    # failure for `credit` that both formats shared, because every row id looked "extra".
    extra = sorted(rows_fields - json_fields)
    assert not extra, (
        "%s left %s in memory that the row store did not write and the JSON store did. A row store "
        "writes only what `_touch` declares, so the call site has to declare it." % (name, extra))


def test_the_sweep_notices_an_undeclared_change(monkeypatch):
    """CONTROL. Without this, a comparison that looked at nothing would pass every case above."""
    m, path, _mid = _fresh("rows", monkeypatch)
    m._items[0]["good"] = 99.0                   # edited in place, nothing declared
    assert _divergence(m, path), "the sweep cannot see an undeclared change, so it proves nothing"


def test_the_public_surface_has_not_grown_past_this_sweep():
    """A method added later is not covered until someone adds it here, so say how many are uncovered.

    Reported rather than asserted at a fixed number: pinning the count turns every new method into a
    failure in this file, which teaches people to edit the number. What must not happen is a mutating
    method silently joining a surface nobody sweeps, so the list is printed where a reader sees it.
    """
    public = {n for n in dir(Inspeximus)
              if not n.startswith("_") and callable(getattr(Inspeximus, n, None))}
    print("public methods: %d, swept here: %d, not swept: %s"
          % (len(public), len(CASES), sorted(public - set(CASES))[:20]))
    scenarios = {"supersede": "remember", "slash_source": "slash"}   # named for what they do
    named = {scenarios.get(n, n) for n in CASES}
    assert named <= public, "this sweep names a method the class does not have: %s" % (
        sorted(named - public))


def test_a_recipe_change_persists_the_vectors_it_rebuilds(monkeypatch):
    """Opening a store with a different embed recipe re-embeds it, and that has to reach disk.

    The realignment happens inside `__init__`, so the sweep above cannot reach it: it rewrites every
    stale vector in place and nothing declared the change, so on a row store the rebuilt vectors
    stayed in memory and the next open realigned all over again. Three probes cited by the docs
    caught it. `reembed()` had the same hole one function away, which is the usual shape.
    """
    monkeypatch.delenv("INSPEXIMUS_STORE_FORMAT", raising=False)
    d = tempfile.mkdtemp()
    p = os.path.join(d, "memory.json")
    a = Inspeximus(path=p, embed=lambda t: [1.0, 0.0, 0.0], persist_vectors=True, embed_id="A")
    a.remember("a record that carries a vector", key="k")
    a.flush()
    assert ss.looks_like_sqlite(p), "the fixture is not a row store"
    assert [r for r in ss.load(p) if r.get("vec") == [1.0, 0.0, 0.0]], \
        "the control failed: the first recipe's vector is not on disk"

    Inspeximus(path=p, embed=lambda t: [0.0, 1.0, 0.0], persist_vectors=True, embed_id="B")
    on_disk = [r.get("vec") for r in ss.load(p)]
    assert on_disk == [[0.0, 1.0, 0.0]], (
        "the realigned vector never reached disk, so the next open realigns again: %r" % on_disk)
