"""AUDIT-A review of B-25: after each erasure path, no file beside the store holds an erased row's text."""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from test_audit_b_erasure_reaches_archive_segments import _no_env, _store  # noqa: E402,F401
from inspeximus.core import Inspeximus  # noqa: E402


def _holders(d, texts):
    out = {}
    for n in sorted(os.listdir(d)):
        p = os.path.join(d, n)
        if os.path.isfile(p):
            b = open(p, "rb").read()
            hit = [t for t in texts if t.encode() in b or t.encode("utf-16-le") in b]
            if hit:
                out[n] = len(hit)
    return out


def _texts(idx):
    return [f"export number {i} of the payroll batch" for i in idx]


@pytest.mark.parametrize("path", ["subject", "ids", "pii", "where"])
def test_no_file_beside_the_store_holds_an_erased_archived_text(tmp_path, monkeypatch, path):
    p, ids, hot = _store(tmp_path, monkeypatch)
    d = os.path.dirname(p)
    if path == "subject":
        idx = range(0, 24, 4)
        Inspeximus(p).forget_subject("hr/alice", request_id="r")
    elif path == "ids":
        idx = [1, 5, 9]
        Inspeximus(p).forget(ids=[ids[i] for i in idx], request_id="r")
    elif path == "pii":
        idx = range(0, 24, 6)
        Inspeximus(p).forget_pii(types=["email"], request_id="r")
    else:
        idx = [3, 7]
        want = {ids[i] for i in idx}
        Inspeximus(p).forget(where=lambda r: r.get("id") in want, request_id="r")
    before = _holders(d, _texts(idx))
    assert not before, f"erased text still in: {before}"
    kept = [i for i in range(24) if i not in set(idx)]
    if not _holders(d, _texts(kept[:1])):
        pytest.fail("control: a record that was NOT erased is found in no file, so the grep sees nothing")
