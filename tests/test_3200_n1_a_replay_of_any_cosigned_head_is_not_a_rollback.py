"""3.20, AUDIT-A N-1 (Low): a replay of ANY head a witness co-signed is answered, not recorded as a rollback.

THE DEFECT. F1 answers a replayed genuine head with its co-signature again, because heads are published and a replay
is not evidence against anybody. But the witness remembered only the last 256 co-signed heads per store. A stranger who
replayed a genuine signed head older than that got a rollback refusal recorded against the store, which was then
marked poisoned (AUDIT-A, dr/ss4/f1b.py section F, on dcce3660). Honest heads kept being co-signed, so there was no
outage, but the attestation showed a fork alarm against an honest operator.

WHAT IS PINNED HERE. Every co-signed head is remembered (as a 12-byte prefix of its sth_hash), across a restart, so a
replay of the first of 300 heads is answered with its co-signature and records nothing. A signed head the witness never
co-signed, behind the latest, is still refused and recorded: the replay rule must not become a way to pass a rollback.
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

SID = "store-under-test"
N = 300


def _head(n):
    """A self-consistent head at write count `n`: tips derived from n, sth_hash from the fields."""
    tip = hashlib.sha256(b"tip %d" % n).hexdigest()
    h = {"store_id": SID, "n_writes": n, "writes_tip": tip, "n_tombstones": 0, "tombstones_tip": "00" * 32}
    h["sth_hash"] = sth_hash_of(h)
    return h


WITNESS_SECRET = new_ed25519_keypair()[0]


def _witness(path, pk):
    return Witness(secret_hex=WITNESS_SECRET, state_path=str(path), pins={SID: pk})


def _cosign(w, head, sk):
    return w.cosign(SID, head, **sign_head(SID, head, sk))


@pytest.fixture
def cosigned(tmp_path):
    sk, pk = new_ed25519_keypair()
    w = _witness(tmp_path / "w.json", pk)
    heads = [_head(i + 1) for i in range(N)]
    first = _cosign(w, heads[0], sk)
    for h in heads[1:]:
        _cosign(w, h, sk)
    assert w.refusals(SID) == [] and w.poisoned() == [], "CONTROL: 300 honest heads co-sign"
    return w, sk, pk, heads, first, tmp_path / "w.json"


def test_a_replay_of_the_first_of_300_heads_gets_its_cosignature_again(cosigned):
    w, sk, _pk, heads, first, _ = cosigned
    assert _cosign(w, heads[0], sk) == first
    assert w.refusals(SID) == [] and w.poisoned() == []


def test_the_replay_is_still_answered_after_a_restart(cosigned):
    _w, sk, pk, heads, first, path = cosigned
    w2 = _witness(path, pk)
    assert _cosign(w2, heads[0], sk) == first
    assert w2.refusals(SID) == [] and w2.poisoned() == []
    seen = json.load(open(str(path), encoding="utf-8"))["seen"][SID]
    assert len(seen) == N and all(len(k) == 24 for k in seen), "every head is kept, as a 12-byte prefix"


def test_a_signed_head_never_cosigned_behind_the_latest_is_still_a_rollback(cosigned):
    w, sk, _pk, _heads, _first, _ = cosigned
    behind = _head(5)
    behind["writes_tip"] = "ef" * 32
    behind["sth_hash"] = sth_hash_of(behind)
    try:
        _cosign(w, behind, sk)
    except Exception:                                  # noqa: BLE001 - the refusal's type is the server's concern
        pass
    assert w.refusals(SID) and w.poisoned() == [SID], "a never co-signed head behind the latest passed as a replay"
