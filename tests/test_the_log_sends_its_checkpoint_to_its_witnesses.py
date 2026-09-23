"""The log sends each new checkpoint to its witnesses, and remembers only what they cosigned.

Driven against our own witness over real HTTP on a port the OS picks. Interop with a witness we did
not write is a separate CI job (tlog-witness-interop.yml); these tests hold the sender's own rules:
send when the log grew, prove the growth, recover when the memory is lost, never move the memory on
a refusal.
"""
from __future__ import annotations

import http.server
import json
import os
import socketserver
import sys
import threading

import pytest

from inspeximus import merkle
from inspeximus.checkpoint import signed_checkpoint
from inspeximus.witness_checkpoint import CheckpointWitness, make_handler

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import send_to_witnesses as stw                                         # noqa: E402

ORIGIN = "example.test/sender"


def _keys():
    from cryptography.hazmat.primitives import serialization as s
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    k = Ed25519PrivateKey.generate()
    return (k.private_bytes(s.Encoding.Raw, s.PrivateFormat.Raw, s.NoEncryption()).hex(),
            k.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw).hex())


def _publish(site, sk, pk, n):
    leaves = [("entry %d" % i).encode() for i in range(n)]
    mtl = [merkle.leaf_hash(x) for x in leaves]
    os.makedirs(site, exist_ok=True)
    with open(os.path.join(site, "checkpoint"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(signed_checkpoint(ORIGIN, n, merkle._root_hashed(mtl), sk, pk))
    with open(os.path.join(site, "log.jsonl"), "w", encoding="utf-8", newline="\n") as fh:
        for i, h in enumerate(mtl):
            fh.write(json.dumps({"index": i, "leaf_hash": h.hex()}) + "\n")


@pytest.fixture()
def world(tmp_path):
    log_sk, log_pk = _keys()
    w_sk, w_pk = _keys()
    witness = CheckpointWitness(str(tmp_path / "w.json"), {ORIGIN: log_pk}, "example.test/w", w_sk, w_pk)
    calls = []
    real = witness.add_checkpoint

    def counted(body):
        calls.append(body)
        return real(body)
    witness.add_checkpoint = counted

    class T(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
    httpd = T(("127.0.0.1", 0), make_handler(witness))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield {"site": str(tmp_path / "site"), "state": str(tmp_path / "sent.json"), "log": (log_sk, log_pk),
           "witnesses": [{"name": "example.test/w", "url": "http://127.0.0.1:%d" % httpd.server_address[1]}],
           "calls": calls, "w_pk": w_pk}
    httpd.shutdown()


def test_first_send_then_growth_is_proved_from_the_remembered_size(world):
    sk, pk = world["log"]
    _publish(world["site"], sk, pk, 3)
    assert stw.send(world["site"], world["witnesses"], world["state"], min_interval=0)["results"][0]["result"] == "cosigned"
    _publish(world["site"], sk, pk, 11)
    out = stw.send(world["site"], world["witnesses"], world["state"], min_interval=0)
    assert out["results"][0] == {"witness": "example.test/w", "result": "cosigned", "size": 11}
    assert world["calls"][-1].startswith(b"old 3\n"), "growth must be proved from the size cosigned"
    note = open(os.path.join(world["site"], "cosignatures", "example.test_w.note"), encoding="utf-8").read()
    assert note.count("— ") == 2, "the note carries the log's signature and the witness's"


def test_nothing_is_sent_when_the_log_has_not_grown(world):
    sk, pk = world["log"]
    _publish(world["site"], sk, pk, 4)
    stw.send(world["site"], world["witnesses"], world["state"], min_interval=0)
    before = len(world["calls"])
    out = stw.send(world["site"], world["witnesses"], world["state"], min_interval=0)
    assert out["results"][0]["result"] == "current" and len(world["calls"]) == before


def test_a_lost_memory_recovers_through_the_409(world):
    sk, pk = world["log"]
    _publish(world["site"], sk, pk, 5)
    stw.send(world["site"], world["witnesses"], world["state"], min_interval=0)
    os.remove(world["state"])
    _publish(world["site"], sk, pk, 9)
    out = stw.send(world["site"], world["witnesses"], world["state"], min_interval=0)
    assert out["results"][0] == {"witness": "example.test/w", "result": "resynced", "size": 5}
    assert world["calls"][-1].startswith(b"old 0\n"), "one request per run, even while resyncing"
    out = stw.send(world["site"], world["witnesses"], world["state"], min_interval=0)
    assert out["results"][0]["result"] == "cosigned"
    assert world["calls"][-1].startswith(b"old 5\n"), "the next run proves from the witness's size"


def test_a_refusal_never_moves_the_remembered_size(world):
    other_sk, other_pk = _keys()
    _publish(world["site"], other_sk, other_pk, 2)          # signed by a key the witness does not trust
    out = stw.send(world["site"], world["witnesses"], world["state"], min_interval=0)
    assert out["results"][0]["result"] == "refused"
    remembered = json.load(open(world["state"], encoding="utf-8")).get(world["witnesses"][0]["url"], {})
    assert "size" not in remembered, "a refusal must not record a cosigned size"
    assert "attempted_ts" in remembered, "a refused request still counts against the rate"


def test_a_second_request_inside_the_hour_waits(world):
    sk, pk = world["log"]
    _publish(world["site"], sk, pk, 2)
    stw.send(world["site"], world["witnesses"], world["state"], now=1000.0)
    _publish(world["site"], sk, pk, 6)
    before = len(world["calls"])
    out = stw.send(world["site"], world["witnesses"], world["state"], now=1000.0 + 3599)
    assert out["results"][0]["result"] == "waiting" and len(world["calls"]) == before
    out = stw.send(world["site"], world["witnesses"], world["state"], now=1000.0 + 3600)
    assert out["results"][0]["result"] == "cosigned"
