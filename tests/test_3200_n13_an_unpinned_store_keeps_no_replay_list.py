"""3.20, AUDIT-A N-13 (Info): a witness keeps its list of co-signed heads only for pinned stores.

THE DEFECT. N-1 made the witness keep every co-signed head, as a 12-byte prefix, so a replay of any of them is answered
rather than recorded as a rollback. It kept the list for every store. On an unpinned witness, a stranger who submits
distinct increasing heads for an invented store id grew the state by 12 bytes per head, and each co-signature got
slower: 9.2 ms at the first head, 17.1 ms at 6,000 (AUDIT-A, dr/ss5/seen_growth.py, on d0e5b2ed; 3.19.1 stayed flat).

WHAT IS PINNED HERE. An unpinned store adds nothing to the list, and keeps the 3.19 rule: a replay of an older head is
a rollback (test_witness_adversarial.py::test_a_witness_refuses_to_co_sign_a_rollback, back in its 3.19.1 form). A
pinned store still keeps every head, so N-1 holds there.
"""
import hashlib
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

pytest.importorskip("cryptography")
from inspeximus.core import new_ed25519_keypair, sth_hash_of  # noqa: E402
from inspeximus.witness_pool import Witness, sign_head  # noqa: E402


def _head(store_id, n):
    h = {"store_id": store_id, "n_writes": n, "writes_tip": hashlib.sha256(b"tip %d" % n).hexdigest(),
         "n_tombstones": 0, "tombstones_tip": "00" * 32}
    h["sth_hash"] = sth_hash_of(h)
    return h


def _seen(path):
    return json.load(open(str(path), encoding="utf-8")).get("seen") or {}


def test_an_unpinned_store_adds_nothing_to_the_replay_list(tmp_path):
    w = Witness(state_path=str(tmp_path / "w.json"))
    for n in range(1, 51):
        w.cosign("invented-store", _head("invented-store", n))
    assert w.refusals("invented-store") == [], "CONTROL: 50 increasing heads co-sign"
    assert not _seen(tmp_path / "w.json").get("invented-store"), "an unpinned store's heads are kept in the state"


def test_a_pinned_store_still_keeps_every_head(tmp_path):
    sk, pk = new_ed25519_keypair()
    w = Witness(state_path=str(tmp_path / "w.json"), pins={"pinned-store": pk})
    heads = [_head("pinned-store", n) for n in range(1, 51)]
    first = w.cosign("pinned-store", heads[0], **sign_head("pinned-store", heads[0], sk))
    for h in heads[1:]:
        w.cosign("pinned-store", h, **sign_head("pinned-store", h, sk))
    assert len(_seen(tmp_path / "w.json").get("pinned-store") or []) == 50
    assert w.cosign("pinned-store", heads[0], **sign_head("pinned-store", heads[0], sk)) == first
    assert w.refusals("pinned-store") == []
