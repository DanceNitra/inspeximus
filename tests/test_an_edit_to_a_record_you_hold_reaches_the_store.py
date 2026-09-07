"""Every way a caller can change a record it holds must reach the store file.

WHY THIS EXISTS. On a whole-file JSON store this class of defect cannot arise: a save rewrites
everything, so an edit made anywhere is on disk whether or not anyone declared it. A row store
writes only the ids something declared, so the records handed back to callers are wrapped and each
wrapper declares its own change. That moves the burden from the save path onto the wrapper's method
coverage, and method coverage is exactly the kind of thing that is 95% complete and silent about the
rest.

MEASURED 2026-09-07, by a compatibility audit rather than by this suite: `rec |= {"value": 77.0}`
showed 77.0 in memory, declared nothing, and read back 1.0 after a flush. `__ior__` is a C-level slot
that does not route through `update`, so overriding `update` had never covered it. `tags *= 2` was
the same hole one type over.

So this does not test two operators. It sweeps every mutation route the dict and list protocols
offer, on the record, on a nested dict, and on a nested list. A route added by a future Python, or
one nobody thought of, fails here without anyone adding a case.

THE CONTROLS. A route that raises tells us nothing about persistence, so each case must actually
change the record. And an unwrapped plain dict must FAIL the same comparison, or the comparison is
measuring nothing.
"""
import os
import tempfile

import pytest

from inspeximus import Inspeximus, sqlite_store as ss


def _fresh():
    p = os.path.join(tempfile.mkdtemp(), "m.json")
    m = Inspeximus(path=p, receipts=True)
    m._save_min_s = 0
    m.remember("the invoice is 100 EUR", key="inv", mtype="fact",
               meta={"vendor": "acme", "lines": [1, 2]}, tags=["a", "b"])
    # A KEY THE LOADER DOES NOT PUT BACK. `pop` and `del` were first aimed at `links`, and both
    # "failed": the record is normalised on read, so a removed built-in key is restored with its
    # default and the comparison saw no removal. That is the loader doing its job, not a lost write.
    # A caller-added key is the only kind whose removal is observable.
    m.items[0]["scratch"] = "removable"
    m.flush()
    assert ss.looks_like_sqlite(p), "the fixture is not a row store, so it cannot test this at all"
    n = Inspeximus(path=p)
    n._save_min_s = 0
    assert "scratch" in n.items[0], "the fixture key did not survive a round trip, so pop/del are void"
    return n, p


ROUTES = {
    "assign":            lambda r: r.__setitem__("value", 77.0),
    "update":            lambda r: r.update({"value": 77.0}),
    "ior":               lambda r: r.__ior__({"value": 77.0}),
    "setdefault":        lambda r: r.setdefault("brand_new_key", 77.0),
    "pop":               lambda r: r.pop("scratch", None),
    "del":               lambda r: r.__delitem__("scratch"),
    "popitem":           lambda r: r.popitem(),
    "nested assign":     lambda r: r["meta"].__setitem__("vendor", "beta"),
    "nested update":     lambda r: r["meta"].update({"vendor": "beta"}),
    "nested ior":        lambda r: r["meta"].__ior__({"vendor": "beta"}),
    "nested pop":        lambda r: r["meta"].pop("vendor", None),
    "nested del":        lambda r: r["meta"].__delitem__("vendor"),
    "nested clear":      lambda r: r["meta"].clear(),
    "list append":       lambda r: r["tags"].append("c"),
    "list extend":       lambda r: r["tags"].extend(["c"]),
    "list iadd":         lambda r: r["tags"].__iadd__(["c"]),
    "list imul":         lambda r: r["tags"].__imul__(2),
    "list insert":       lambda r: r["tags"].insert(0, "c"),
    "list assign":       lambda r: r["tags"].__setitem__(0, "z"),
    "list del":          lambda r: r["tags"].__delitem__(0),
    "list remove":       lambda r: r["tags"].remove("a"),
    "list pop":          lambda r: r["tags"].pop(),
    "list sort":         lambda r: r["tags"].sort(reverse=True),
    "list reverse":      lambda r: r["tags"].reverse(),
    "list clear":        lambda r: r["tags"].clear(),
    "list in a dict":    lambda r: r["meta"]["lines"].append(3),
}


def _comparable(rec):
    return {k: v for k, v in dict(rec).items() if k != "vec" and not k.startswith("_")}


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_a_caller_edit_reaches_disk(route):
    m, path = _fresh()
    rec = m.items[0]
    before = _comparable(rec)

    ROUTES[route](rec)
    after = _comparable(rec)
    assert after != before, (
        "the '%s' case did not change the record, so its persistence assertion is vacuous" % route)

    m.flush()
    on_disk = _comparable(Inspeximus(path=path).items[0])
    assert on_disk == after, (
        "'%s' changed the record in memory and the store file does not carry it. A row store writes "
        "only what the wrapper declares, so an unwrapped mutation route is a silent loss: %r vs %r"
        % (route, after, on_disk))


def test_an_unwrapped_record_fails_this_same_comparison():
    """CONTROL. Without this, the sweep above could be passing because the store rewrites everything.

    A plain dict inserted behind the tracker's back declares nothing, so the identical assertion must
    FAIL for it. If this control ever stops failing, the sweep is no longer measuring the wrapper.
    """
    m, path = _fresh()
    raw = dict(m._items[0])
    m._items[0] = raw                                    # unwrapped, so no route can declare anything
    m._touched.clear()
    raw["value"] = 77.0
    m.flush()
    assert Inspeximus(path=path).items[0].get("value") != 77.0, (
        "an undeclared edit reached disk anyway, so this sweep cannot tell a working wrapper from a "
        "store that rewrites itself regardless")


def test_a_copy_is_not_the_stored_record():
    """`copy()` is documented as detached. A copy that can write to the store is worse than none.

    `dict(self)` was not enough: a nested container wrapped by an earlier read stayed shared with the
    original, so editing the copy marked the store dirty and the value survived a reload.
    """
    m, path = _fresh()
    rec = m.items[0]
    rec["meta"]                                          # the read that wraps the nested dict
    c = rec.copy()
    m._touched.clear()

    c["meta"]["vendor"] = "injected"
    c["tags"].append("injected")
    assert not m._touched, "writing to a copy marked the store dirty, so the copy is not detached"
    assert rec["meta"]["vendor"] == "acme", "the copy's nested dict is the original's"
    assert "injected" not in rec["tags"], "the copy's nested list is the original's"

    m.flush()
    reread = Inspeximus(path=path).items[0]
    assert reread["meta"]["vendor"] == "acme", "an edit to a copy reached the store file"
