"""3.20, F4 (Claude Security scan, LOW, forced only): a checkpoint witness cosigns one root per size.

THE DEFECT. CheckpointWitness's docstring says "the check and the write under one lock", and nothing held one. Two
add-checkpoint submissions that both read the held head before either wrote were both judged against it, so two
different roots at one size were both cosigned: the fork a witness exists to refuse. Measured on v3.19.1 (AUDIT-A
f4_repro.py): in sequence X gets 200 and Y 409; with both threads made to read the held head first, both get 200. With
no forced interleaving it happened in 0 of 20 trials, so the window is narrow, and the test forces it. `_save` also
wrote every state through one fixed `<state>.tmp`, so two writers shared a temporary file.

WHAT IS PINNED HERE. The load, the check and the save run under `_StoreLock` on the state file, so the forced
interleaving yields one 200 and one 409, and the state holds the root that was cosigned. `_save` writes through a
temporary file of its own.
"""
import os
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import serialization as ser  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from inspeximus import merkle  # noqa: E402
from inspeximus.checkpoint import signed_checkpoint  # noqa: E402
from inspeximus.witness_checkpoint import CheckpointWitness, Refused, build_request  # noqa: E402

ORIGIN = "example.test/log"


def _keys():
    k = Ed25519PrivateKey.generate()
    return (k.private_bytes(ser.Encoding.Raw, ser.PrivateFormat.Raw, ser.NoEncryption()).hex(),
            k.public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw).hex())


LSK, LPK = _keys()
WSK, WPK = _keys()
BASE = [b"e%d" % i for i in range(4)]
X = BASE + [b"X4", b"X5"]
Y = BASE + [b"Y4", b"Y5"]


def _note(leaves, n):
    return signed_checkpoint(ORIGIN, n, merkle._root_hashed([merkle.leaf_hash(x) for x in leaves[:n]]), LSK, LPK)


def _req(leaves):
    return build_request(4, merkle.consistency_proof(leaves[:6], 4), _note(leaves, 6))


def _witness(tmp_path):
    w = CheckpointWitness(str(tmp_path / "w.json"), {ORIGIN: LPK}, "example.test/w", WSK, WPK)
    w.max_per_minute = 10 ** 6
    assert w.add_checkpoint(build_request(0, [], _note(BASE, 4)))[0] == 200
    return w


def _call(w, body, out, key):
    try:
        out[key] = w.add_checkpoint(body)[0]
    except Refused as e:
        out[key] = e.status


def test_control_in_sequence_the_second_root_is_refused(tmp_path):
    w = _witness(tmp_path)
    out = {}
    _call(w, _req(X), out, "X")
    _call(w, _req(Y), out, "Y")
    assert out == {"X": 200, "Y": 409}


def test_two_roots_at_one_size_are_not_both_cosigned_when_both_read_the_head_first(tmp_path):
    w = _witness(tmp_path)
    real_load = w._load
    bar = threading.Barrier(2, timeout=2)

    def load():
        state = real_load()
        try:
            bar.wait()                                   # hold until the other submission has read the head too
        except threading.BrokenBarrierError:
            pass                                         # it could not: the lock kept it out, which is the fix
        return state
    w._load = load
    out = {}
    ts = [threading.Thread(target=_call, args=(w, _req(leaves), out, name)) for name, leaves in (("X", X), ("Y", Y))]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    assert sorted(out.values()) == [200, 409], "both roots at size 6 were cosigned: %r" % out
    won = X if out["X"] == 200 else Y
    held = w.latest(ORIGIN)
    assert held["size"] == 6 and bytes.fromhex(held["root"]) == merkle._root_hashed(
        [merkle.leaf_hash(x) for x in won]), "the state does not hold the root that was cosigned"


def test_a_save_does_not_go_through_a_shared_temporary_name(tmp_path):
    w = _witness(tmp_path)
    os.mkdir(str(tmp_path / "w.json.tmp"))               # what a second writer's fixed temp name would collide with
    out = {}
    _call(w, _req(X), out, "X")
    assert out == {"X": 200} and w.latest(ORIGIN)["size"] == 6
    leftovers = [n for n in os.listdir(str(tmp_path)) if n.endswith(".tmp") and n != "w.json.tmp"]
    assert leftovers == [], leftovers
