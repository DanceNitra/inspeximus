"""An erasure lists the store's directory a fixed number of times, whatever the number of records.

3.15.2 made an erasure remove the merge tools' backups (A-12), and finding them lists the store's
directory twice. The removal ran inside `_emit_tombstone`, once per erased record, so erasing k records
listed the directory 2k times. The perf gate did not see it on Linux: it counts `Inspeximus.items` reads
and `str.lower` calls, and only Windows lowercased the names it listed. Counted here directly, on every
platform. A batch erasure now removes the backups once, in forget().
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus


def _listings(tmp_path, monkeypatch, k):
    d = tmp_path / f"k{k}"
    d.mkdir()
    m = Inspeximus(str(d / "s.json"), receipts=True)
    for i in range(k):
        m.remember(f"subject record {i}", tags=["pii"], source={"doc": "hr/alice"})
    m.remember("an unrelated record", tags=["ops"], source={"doc": "ops/1"})
    m.flush()
    real, seen = os.listdir, []

    def counted(path=".", *a, **kw):
        if isinstance(path, (str, bytes, os.PathLike)) and \
                os.path.normcase(os.path.abspath(os.fsdecode(path))) == os.path.normcase(str(d)):
            seen.append(path)
        return real(path, *a, **kw)

    with monkeypatch.context() as mp:
        mp.setattr(os, "listdir", counted)
        m.forget_subject("hr/alice")
    if any("subject record" in (r.get("text") or "") for r in m.items):
        pytest.fail("control: the erasure left the subject's records, so nothing was measured")
    return len(seen)


def test_an_erasure_lists_the_store_directory_a_fixed_number_of_times(tmp_path, monkeypatch):
    few, many = _listings(tmp_path, monkeypatch, 2), _listings(tmp_path, monkeypatch, 40)
    if few == 0:
        pytest.fail("control: the erasure never listed the store's directory, so nothing was counted")
    assert many == few, f"erasing 2 records listed the directory {few} times, erasing 40 listed it {many}"
