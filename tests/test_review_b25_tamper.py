"""AUDIT-A review of B-25: a tampered segment or log refuses the erasure before anything is erased."""
import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
from test_audit_b_erasure_reaches_archive_segments import _archived, _no_env, _state, _store  # noqa: E402,F401
from inspeximus import archive  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402


def _seg(p):
    return os.path.join(os.path.dirname(p), sorted(archive.listed_segments(p))[0])


def test_a_same_size_edit_of_a_segment_refuses_the_erasure(tmp_path, monkeypatch):
    p, ids, hot = _store(tmp_path, monkeypatch)
    s = _seg(p)
    b = bytearray(open(s, "rb").read())
    i = b.find(b"payroll")
    assert i > 0
    b[i] = ord("P")
    open(s, "wb").write(bytes(b))
    before = _state(p)
    with pytest.raises(archive.SegmentsUnreachable):
        Inspeximus(p).forget_subject("hr/alice", request_id="r")
    assert _state(p) == before, "a refused erasure changed something"


def _rolled_back(tmp_path, monkeypatch, n):
    p, ids, hot = _store(tmp_path, monkeypatch)
    Inspeximus(p).forget(ids=[ids[1]], request_id="first")
    lp = archive.log_path(p)
    doc = json.load(open(lp, encoding="utf-8"))
    doc["entries"] = doc["entries"][:-n]
    open(lp, "w", encoding="utf-8").write(json.dumps(doc))
    return p, ids


def test_a_dropped_amend_is_recommitted_and_the_certificate_still_verifies_the_archive(tmp_path, monkeypatch):
    p, ids = _rolled_back(tmp_path, monkeypatch, 1)
    Inspeximus(p).forget_subject("hr/alice", request_id="r")
    cert = Inspeximus(p).erasure_certificate()
    arch = [x for x in cert["self_check"]["problems"] if "archive" in x or "segment" in x]
    assert not arch, arch


def test_a_log_rolled_back_past_the_intent_refuses_the_erasure(tmp_path, monkeypatch):
    p, ids = _rolled_back(tmp_path, monkeypatch, 2)
    before = _state(p)
    with pytest.raises(archive.SegmentsUnreachable):
        Inspeximus(p).forget_subject("hr/alice", request_id="r")
    assert _state(p) == before, "a refused erasure changed something"
