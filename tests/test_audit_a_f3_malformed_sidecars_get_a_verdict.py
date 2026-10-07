"""AUDIT-A, 3.16.x: a sidecar the store reads must never turn a malformed file into a raw exception.

verify_writes(), recommit(), context_unbound() and governance_report() raise AttributeError, TypeError or
KeyError when a receipt in `<store>.receipts.json` has a commit that is not a mapping, a memory_id that is not
a string, a seq that is not a number, or is not an object at all. erasure_certificate() and forget() raise
the same on a malformed `<store>.tombstones.json`, and everything that reads the archive log raises a raw
JSONDecodeError, UnicodeDecodeError or ValueError on a malformed `<store>.archive.json`, including forget() of a
record that was never archived. A verifier that crashes reports nothing: the caller sees an exception, not
(False, [problem]), and a caller that catches broadly reads the crash as "not my problem".
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _store_io import load_receipts, save_receipts
from inspeximus import archive  # noqa: E402
import inspeximus.core as core  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

DAY, T0 = 86400.0, 1790000000.0

BAD_RECEIPTS = {
    "commit is a string": lambda c: c[0].__setitem__("commit", "context_sha256 partition_sha256 value_sha256"),
    "commit is a list": lambda c: c[0].__setitem__("commit", ["context_sha256"]),
    "commit is an int": lambda c: c[0].__setitem__("commit", 7),
    "memory_id is a list": lambda c: c[0].__setitem__("memory_id", ["a"]),
    "memory_id is missing": lambda c: c[0].pop("memory_id"),
    "seq is a string": lambda c: c[0].__setitem__("seq", "9"),
    "seq is null": lambda c: c[0].__setitem__("seq", None),
    "an entry is a string": lambda c: c.__setitem__(0, "garbage"),
    "an entry is null": lambda c: c.__setitem__(0, None),
}


@pytest.fixture
def clean(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keyhome"))
    return tmp_path


@pytest.mark.parametrize("shape", sorted(BAD_RECEIPTS))
def test_a_malformed_receipt_gets_a_verdict_not_an_exception(clean, shape):
    p = str(clean / "st" / "memory.json")
    os.makedirs(os.path.dirname(p))
    m = Inspeximus(p, receipts=True)
    m.remember("fact one", key="k1")
    m.remember("fact two", key="k2")
    chain = load_receipts(p)
    assert len(chain) >= 2 and isinstance(chain[0].get("commit"), dict), "control: the fixture wrote a real chain"
    BAD_RECEIPTS[shape](chain)
    save_receipts(p, chain)
    ok, problems = Inspeximus(p, receipts=True).verify_writes()
    assert ok is False and problems, shape
    Inspeximus(p, receipts=True).context_unbound()
    Inspeximus(p, receipts=True).recommit()


def test_a_malformed_archive_log_does_not_stop_the_erasure_of_a_record_that_was_never_archived(clean, monkeypatch):
    p = str(clean / "st" / "coding_memory.json")
    os.makedirs(os.path.dirname(p))
    m = Inspeximus(p)
    ids = []
    for i in range(4):
        monkeypatch.setattr(core.time, "time", lambda i=i: T0 - 40 * DAY + i)
        ids.append(m.remember(f"ran: export {i}", key=f"cmd:c{i}", tags=["bash"], mtype="episodic"))
    monkeypatch.setattr(core.time, "time", lambda: T0)
    m.remember("a recent note that stays hot", key="hot-1")
    assert archive.apply(Inspeximus(p), 7, now=T0)["applied"], "control: an archive exists"
    hot = [r["id"] for r in Inspeximus(p)._items]
    assert len(hot) == 1, "control: one row is still hot"
    open(p + ".archive.json", "wb").write(b"\x00\xff garbage")
    try:
        Inspeximus(p).forget(ids=hot)
    except Exception as e:                                   # a refusal is allowed; a raw decode error is not
        assert isinstance(e, archive.SegmentsUnreachable), f"{type(e).__name__}: {e}"
