"""3.20, AUDIT-A N-8 (Info): a store id that only looks different from a pinned one is refused, not a new store.

THE DEFECT. A witness with pinned stores, run without --require-signed-heads, takes unsigned heads for any store id it
has no pin for. "Store-Under-Test", "store-under-test " or "store-under-test\\x00" was such an id beside the pinned
"store-under-test": the witness co-signed unsigned heads for it and kept it in its state (AUDIT-A dr/ss4/f1b.py
section D, on dcce3660). A reader of the attestations can mistake that id for the pinned store.

WHAT IS PINNED HERE. An id that equals a pinned id after case folding, removing whitespace and invisible characters,
and NFKC folding is refused with Unauthenticated, and nothing is written. A genuinely different unpinned id is still
taken unsigned, as before (that default is the F1 known limit, closed by --require-signed-heads).
"""
import hashlib
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

pytest.importorskip("cryptography")
from inspeximus.core import new_ed25519_keypair, sth_hash_of  # noqa: E402
from inspeximus.witness_pool import Unauthenticated, Witness  # noqa: E402

SID = "store-under-test"
LOOK_ALIKES = ["Store-Under-Test", "store-under-test ", " store-under-test", "store-under-test\x00",
               "store-under​-test", "ｓtore-under-test"]


def _head(store_id, n=1):
    h = {"store_id": store_id, "n_writes": n, "writes_tip": hashlib.sha256(b"%d" % n).hexdigest(),
         "n_tombstones": 0, "tombstones_tip": "00" * 32}
    h["sth_hash"] = sth_hash_of(h)
    return h


@pytest.fixture
def witness(tmp_path):
    _sk, pk = new_ed25519_keypair()
    return Witness(state_path=str(tmp_path / "w.json"), pins={SID: pk}), tmp_path / "w.json"


@pytest.mark.parametrize("store_id", LOOK_ALIKES, ids=["case", "trailing space", "leading space", "NUL",
                                                       "zero-width space", "full-width s"])
def test_an_unsigned_head_for_a_look_alike_of_a_pinned_id_is_refused(witness, store_id):
    w, state = witness
    before = state.read_bytes() if state.exists() else None
    with pytest.raises(Unauthenticated, match="differs from the pinned store"):
        w.cosign(store_id, _head(store_id))
    assert (state.read_bytes() if state.exists() else None) == before, "a refused head changed the state"


def test_control_a_different_unpinned_store_is_still_taken_unsigned(witness):
    w, _state = witness
    pub, sig = w.cosign("another-store", _head("another-store"))
    assert pub and sig
