"""AUDIT-B B-17: erasing a subject must not rebuild the store's id map once per matched record.

`_erasure_collisions` (run by every `forget_subject`) resolved each matched record with
`self._raw_source({r["id"]: r for r in self.items}[rid])`: a dict of the whole store, built again for
every matched id, so erasing k records of an n-record store cost O(k x n). Measured 2026-09-27 on
synthetic row stores where a subject owns 1 record in 30: 0.06 s at 1,000 records, 1.44 s at 10,000
and 57.3 s at 50,000 (1,666 records erased), an exponent of 2.3 between the last two sizes.

The counter is the number of times `items` is read while `_erasure_collisions` runs: once per matched
record on main, once in total after the fix. The control requires the erasure to have matched and
erased every record of the subject and kept a neighbour on the same host, so the counter cannot reach
one by never doing the work.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.core as core  # noqa: E402
from inspeximus import Inspeximus  # noqa: E402

K = 40


@pytest.fixture
def reads(monkeypatch):
    box = {"inside": False, "reads": 0}
    real_prop = core.Inspeximus.items
    real_fn = core.Inspeximus._erasure_collisions

    def fget(self):
        if box["inside"]:
            box["reads"] += 1
        return real_prop.fget(self)

    def collisions(self, *a, **k):
        box["inside"] = True
        try:
            return real_fn(self, *a, **k)
        finally:
            box["inside"] = False

    monkeypatch.setattr(core.Inspeximus, "items", property(fget, real_prop.fset))
    monkeypatch.setattr(core.Inspeximus, "_erasure_collisions", collisions)
    return box


@pytest.mark.xfail(strict=True, raises=AssertionError,
                   reason="B-17: the whole-store id map is rebuilt once per matched record")
def test_an_erasure_reads_the_store_once_to_resolve_its_records(tmp_path, monkeypatch, reads):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    m = Inspeximus(str(tmp_path / "s.json"))
    for i in range(K):
        m.remember(f"alice note {i}", source={"doc": "crm.example.com/alice"})
    m.remember("bob note", source={"doc": "crm.example.com/bob"})     # collides on the canonical host
    for i in range(200):
        m.remember(f"unrelated {i}", source={"doc": f"ops/{i % 9}"})
    reads["reads"] = 0
    res = m.forget_subject("crm.example.com/alice")

    # CONTROL: every one of the subject's records was matched and erased, and the neighbour sharing the
    # canonical host was not, so the per-record resolution ran over K real matches.
    left = [r["text"] for r in m.items]
    if res.get("erased") != K or any(t.startswith("alice") for t in left) or "bob note" not in left:
        pytest.fail(f"control: erased {res.get('erased')} of {K}; bob kept: {'bob note' in left}")
    assert reads["reads"] <= 2, f"{reads['reads']} whole-store reads to resolve {K} matched records"
