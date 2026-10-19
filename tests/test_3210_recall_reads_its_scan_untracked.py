"""3.21 item 11: recall's scan reads its records with `dict.get`, and an edit made after a recall is still written.

On 3.20.0 a held recall over 5,000 rows called the tracked `get` about 35,000 times, about 11 per candidate: the
stuffed-flag check, the decay, the reliability, the position sort, the token caches and the stale-derived check. Each
is a Python-level call. Those reads are of scalars or of containers that are only read, so they go through `dict.get`.
A record's edits are still declared, because an edit goes through the record's own `__setitem__` and through the
tracked containers that `__getitem__` and `get` hand out, never through what the scan read.
"""
import os

import pytest

from inspeximus import core
from inspeximus.core import Inspeximus

WORDS = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima".split()


@pytest.fixture(autouse=True)
def _keep_the_format():
    old = os.environ.get("INSPEXIMUS_STORE_FORMAT")
    yield
    if old is None:
        os.environ.pop("INSPEXIMUS_STORE_FORMAT", None)
    else:
        os.environ["INSPEXIMUS_STORE_FORMAT"] = old


def _store(tmp_path, fmt, n):
    if fmt == "json":
        os.environ["INSPEXIMUS_STORE_FORMAT"] = "json"
    else:
        os.environ.pop("INSPEXIMUS_STORE_FORMAT", None)
    p = str(tmp_path / ("s.json" if fmt == "json" else "s.db"))
    m = Inspeximus(p)
    for i in range(n):
        m.remember(" ".join(WORDS[(i + j) % len(WORDS)] for j in range(6)) + " entry %d" % i)
    m.flush()
    with open(p, "rb") as fh:
        assert fh.read(16).startswith(b"[" if fmt == "json" else b"SQLite format 3"), "CONTROL: the %s format" % fmt
    return p


def test_a_held_recall_reads_its_candidates_untracked(tmp_path, monkeypatch):
    p = _store(tmp_path, "db", 600)
    h = Inspeximus(p)
    for _ in range(3):
        h.recall("alpha bravo", k=5)                       # held: the index is built and the guards assessed
    calls = []
    real = core._TrackedDict.get

    def counted(self, k, default=None):
        calls.append(k)
        return real(self, k, default)
    monkeypatch.setattr(core._TrackedDict, "get", counted)
    out = h.recall("alpha bravo", k=5)
    monkeypatch.undo()
    assert len(out) == 5, "CONTROL: the recall found its candidates"
    # The returned records are still read through the tracked get when the answer is built: about 9 reads each.
    assert len(calls) <= 15 * len(out), "a held recall made %d tracked reads for %d results" % (len(calls), len(out))


@pytest.mark.parametrize("fmt", ["json", "db"])
def test_an_in_place_edit_after_a_recall_is_written(tmp_path, fmt):
    p = _store(tmp_path, fmt, 40)
    h = Inspeximus(p)
    hits = h.recall("alpha bravo charlie", k=5)
    hits = h.recall("alpha bravo charlie", k=5)            # the scan has read these records, untracked
    rid = hits[0]["id"]
    other = next(r for r in h.items if r["id"] != rid)["id"]
    rec = next(r for r in h.items if r["id"] == rid)
    rec.setdefault("meta", {})["note"] = "edited after recall"
    rec["links"].append(other)
    rec["good"] = 7
    rec["text"] = "a replaced text about zulu"
    assert h._dirty, "CONTROL: the edits marked the store"
    h.flush()
    back = next(r for r in Inspeximus(p).items if r["id"] == rid)
    assert back["meta"].get("note") == "edited after recall"
    assert other in back["links"]
    assert back["good"] == 7
    assert back["text"] == "a replaced text about zulu"
    assert [r["id"] for r in h.recall("zulu", k=1)] == [rid], "the held handle matched the record by its old words"
