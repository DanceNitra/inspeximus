"""verify_writes() answers its per-receipt chain questions from one pass, and judges exactly as before.

audits/2026-09-24/scale.md measured verify_writes() -- and erasure_certificate(), which calls it -- at
0.12 s / 8.9 s / 175 s for 1k / 10k / 50k receipts. For every receipt it scanned every receipt again to
learn which fields a LATER receipt for the same memory declared it `amends`; the legacy branch did the
same for the highest seq, and a record gone from the store scanned every tombstone. The index answers
all three from one pass.

A faster verifier that forgives one more receipt is a laundering path, so speed is not what these
tests are about. They hold the index to the verdicts of the scan it replaced (`core._CHAIN_INDEX =
False` runs the scan) on random histories with random tampering, and pin the attacks the index must
keep catching: an appended, well-formed `amends` naming an immutable field, an amendment that does not
forgive its own receipt, an out-of-band delete next to a genuine erasure, and the legacy branch.
"""
import os
import random
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.core as core
from inspeximus import Inspeximus

SUBJECTS = ["crm/alice", "crm/bob", "hr/carol", "wiki/dave"]


def _path():
    return os.path.join(tempfile.mkdtemp(), "m.json")


def _rehash(r):
    r["hash"] = core._sha256_hex(core._canon(Inspeximus._chain_core(r, "write")))
    return r


def _append_forged(m, rec, amends, seq=None):
    """A receipt anyone with file access can append: well-formed, correctly hashed, linked to the tip."""
    r = {"ts": rec.get("ts"), "memory_id": rec["id"], "commit": Inspeximus._write_commit(rec),
         "seq": len(m._receipts) if seq is None else seq, "prev": m._receipts[-1]["hash"]}
    if amends:
        r["amends"] = list(amends)
    m._receipts.append(_rehash(r))
    return r


def _to_pre_split(m):
    """Rebuild the chain as a <=1.67 store wrote it: no immutable_sha256 / mtype in the commit."""
    prev = core._GENESIS
    for r in m._receipts:
        r["commit"].pop("immutable_sha256", None)
        r["commit"].pop("mtype", None)
        r["prev"] = prev
        _rehash(r)
        prev = r["hash"]


def _graduate(m, rid):
    for _ in range(20):
        m.credit([rid], outcome=True)


def _verdicts(m, monkeypatch, **kw):
    out = []
    for index in (True, False):
        monkeypatch.setattr(core, "_CHAIN_INDEX", index)
        try:
            out.append(("returned", m.verify_writes(**kw)))
        except Exception as e:                                  # noqa: BLE001 -- raising must agree too
            out.append(("raised", type(e).__name__, str(e)))
    return out


def _random_history(seed):
    rnd = random.Random(seed)
    m = Inspeximus(path=_path(), receipts=True)
    ids = []
    for i in range(rnd.randint(8, 20)):
        key = "k%d" % rnd.randint(0, 5) if rnd.random() < 0.4 else None
        ids.append(m.remember("fact %d about %s" % (i, rnd.choice(["x", "y", "z"])), key=key,
                              object=("v%d" % i) if key else None,
                              source={"doc": rnd.choice(SUBJECTS)}))
    live = lambda: [r for r in m.items if r.get("status") == "active"]           # noqa: E731
    for rid in rnd.sample(ids, k=1):
        if any(r["id"] == rid and r.get("status") == "active" for r in m.items):
            _graduate(m, rid)
            for op in rnd.sample(["slash", "restore", "slash"], k=rnd.randint(1, 3)):
                getattr(m, op)([rid], scope="memory")
    if rnd.random() < 0.5:
        m.forget_subject(rnd.choice(SUBJECTS))
    if rnd.random() < 0.25:
        _to_pre_split(m)
    for _ in range(rnd.randint(0, 3)):
        recs = live()
        if not recs or not m._receipts:
            break
        rec = rnd.choice(recs)
        what = rnd.choice(["text", "mtype", "key", "drop", "forge", "forge_low_seq", "amends_unhashed",
                           "seq_str", "amends_int", "status"])
        if what == "text":
            rec["text"] = "EDITED"
        elif what == "mtype":
            rec["mtype"] = rnd.choice(["semantic", "procedural", "episodic"])
        elif what == "key":
            rec["key"] = "payout::wallet"
        elif what == "status":
            rec["status"] = "superseded"
        elif what == "drop":
            m._items = [r for r in m._items if r is not rec]
        elif what in ("forge", "forge_low_seq"):
            fields = rnd.sample(["mtype", "immutable_sha256", "status_sha256", "attrib_sha256"],
                                k=rnd.randint(0, 3))
            seq = rnd.randint(0, len(m._receipts) - 1) if what == "forge_low_seq" else None
            _append_forged(m, rec, fields, seq=seq)
        elif what == "amends_unhashed":
            rnd.choice(m._receipts)["amends"] = ["immutable_sha256", "mtype"]
        elif what == "seq_str":
            rnd.choice(m._receipts)["seq"] = "7"
        elif what == "amends_int":
            rnd.choice(m._receipts)["amends"] = 5
    return m


@pytest.mark.parametrize("seed", range(30))
def test_the_index_reaches_the_verdicts_of_the_scan_it_replaced(seed, monkeypatch):
    """Same (ok, problems) -- or the same exception -- on every random history, for both legacy modes."""
    m = _random_history(seed)
    for legacy_strict in (True, False):
        fast, scan = _verdicts(m, monkeypatch, legacy_strict=legacy_strict)
        assert fast == scan, (seed, legacy_strict)


def _graduated():
    m = Inspeximus(path=_path(), receipts=True)
    rid = m.remember("alpha is the value for k0", key="k0", object="alpha")
    _graduate(m, rid)
    return m, rid


def test_an_appended_well_formed_amends_cannot_launder_a_text_edit():
    """The 2026-08-15 path: edit the text, then append ONE correctly hashed receipt whose `amends` names
    immutable_sha256. The chain link is genuine; only the `_AMENDABLE` intersection stops it, and the
    index narrows `amends` itself, so it has to narrow it to exactly that vocabulary."""
    m, rid = _graduated()
    m.slash([rid], scope="memory")
    rec = next(r for r in m.items if r["id"] == rid)
    rec["text"] = "Revenue is 900M"
    _append_forged(m, rec, ["immutable_sha256", "mtype"])      # commits the FORGED text, correctly hashed
    assert m._receipts[-1]["commit"] == Inspeximus._write_commit(rec), "the new receipt itself matches"
    ok, problems = m.verify_writes()
    assert ok is False, problems
    assert any("its TEXT or KEY no longer matches" in p for p in problems), problems
    assert m.erasure_certificate()["self_check"]["verified"] is False


def test_an_amendment_forgives_earlier_receipts_and_not_its_own():
    """`seq > r.seq`, strictly: slash()'s receipt forgives the first receipt's `mtype`, but a later
    out-of-band mtype edit is judged by the slash receipt itself, which nothing after it amends."""
    m, rid = _graduated()
    m.slash([rid], scope="memory")
    assert m.verify_writes() == (True, [])
    next(r for r in m.items if r["id"] == rid)["mtype"] = "procedural"
    ok, problems = m.verify_writes()
    assert ok is False and any("its TYPE no longer matches" in p for p in problems), problems


def test_an_amendment_with_an_earlier_seq_forgives_nothing_after_it():
    """A forged amendment placed BEFORE the receipt it wants to silence. Seq, not chain position, is
    what "later" means, as it was in the scan."""
    m, rid = _graduated()
    rec = next(r for r in m.items if r["id"] == rid)
    rec["mtype"] = "procedural"
    first = min(r["seq"] for r in m._receipts if r["memory_id"] == rid)
    _append_forged(m, rec, ["mtype"], seq=first - 1)
    ok, problems = m.verify_writes()
    assert ok is False and any("its TYPE no longer matches" in p for p in problems), problems


def test_an_out_of_band_delete_is_caught_next_to_a_genuine_erasure():
    m = Inspeximus(path=_path(), receipts=True)
    m.remember("alice's address", source={"doc": "crm/alice"})
    gone = m.remember("bob's address", source={"doc": "crm/bob"})
    m.remember("carol's address", source={"doc": "hr/carol"})
    m.forget_subject("crm/alice")
    assert m.verify_writes() == (True, [])
    m._items = [r for r in m._items if r["id"] != gone]
    ok, problems = m.verify_writes()
    assert ok is False
    assert [p for p in problems if "deleted out-of-band" in p] == [
        f"memory {gone}: written but missing from the store (deleted out-of-band)"], problems


def test_the_legacy_quiet_mode_checks_the_latest_receipt_only():
    """legacy_strict=False on a pre-1.68 chain: an amended record verifies (the earlier receipts are not
    checked), and an edit is still caught on the latest one."""
    m, rid = _graduated()
    _to_pre_split(m)
    _append_forged(m, next(r for r in m.items if r["id"] == rid), ())
    for r in m._receipts:
        r["commit"].pop("immutable_sha256", None)
        r["commit"].pop("mtype", None)
    prev = core._GENESIS
    for r in m._receipts:
        r["prev"] = prev
        prev = _rehash(r)["hash"]
    rec = next(r for r in m.items if r["id"] == rid)
    rec["mtype"] = "procedural" if rec["mtype"] != "procedural" else "semantic"
    _append_forged(m, rec, ())
    for r in m._receipts[-1:]:
        r["commit"].pop("immutable_sha256", None)
        r["commit"].pop("mtype", None)
        _rehash(r)
    assert m.verify_writes(legacy_strict=False)[0] is True, m.verify_writes(legacy_strict=False)
    assert m.verify_writes(legacy_strict=True)[0] is False
    rec["text"] = "EDITED"
    assert m.verify_writes(legacy_strict=False)[0] is False


def test_a_large_chain_is_verified_in_one_pass(monkeypatch):
    """The quadratic, pinned without a clock: count how often receipts are READ for the memory_id / seq /
    `amends` questions. The scan reads R per receipt; the index must not."""
    m = Inspeximus(path=_path(), receipts=True)
    for i in range(120):
        m.remember("fact %d" % i, key="k%d" % (i % 30), object="v%d" % i)

    class Counting(dict):
        reads = 0

        def get(self, k, d=None):
            if k in ("seq", "amends"):
                Counting.reads += 1
            return super().get(k, d)

        def __getitem__(self, k):
            if k == "memory_id":
                Counting.reads += 1
            return super().__getitem__(k)

    m._receipts = [Counting(r) for r in m._receipts]
    R = len(m._receipts)
    reads = {}
    for index in ("default", False):          # "default": whatever the module ships with
        if index is False:
            monkeypatch.setattr(core, "_CHAIN_INDEX", False)
        Counting.reads = 0
        assert m.verify_writes(value_strict=False)[0] is True
        reads[index] = Counting.reads
    assert reads[False] > R * R, reads          # the counter sees the scan it replaced
    assert reads["default"] <= 20 * R, reads         # a constant number of reads per receipt (11 measured)
