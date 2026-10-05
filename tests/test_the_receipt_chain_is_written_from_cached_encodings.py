"""AUDIT-B 3.16.3 (AUDIT-A's cheap step): the receipts sidecar is written from per-receipt cached encodings, and
the bytes are the bytes `_dump_chain` writes.

Every receipted write rewrites the whole sidecar. Re-encoding each receipt was most of that cost (AUDIT-A: 0.19 s
against 0.02 s for 13,359 entries). A receipt is built once and appended, so each is encoded once. There is no format
change: the file is "[" + ", ".join(encoded receipts) + "]", which is what `json.dumps` of the list produces.
"""
import json
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.core as core  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path_factory):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path_factory.mktemp("key-home")))


def _entries(n, seed=1):
    rnd = random.Random(seed)
    out, prev = [], "0" * 64
    for i in range(n):
        e = {"ts": 1790000000.0 + i * 0.37, "memory_id": "%010x" % rnd.getrandbits(40),
             "commit": {"text_sha256": "%064x" % rnd.getrandbits(256), "uid": "café 中文 \U0001f600" if i % 7 == 0 else None,
                        "nested": {"a": [1, 2.5, None, True], "b": "x y"}},
             "seq": i, "prev": prev, "hash": "%064x" % rnd.getrandbits(256)}
        if i % 3 == 0:
            e["amends"] = ["mtype"]
            e["amend_reason"] = "slash"
        if i % 5 == 0:
            e["sig"] = "%0128x" % rnd.getrandbits(512)
            e["pubkey"] = "%064x" % rnd.getrandbits(256)
        prev = e["hash"]
        out.append(e)
    return out


@pytest.mark.parametrize("n", [0, 1, 2, 50, 400])
def test_the_cached_text_equals_the_dump_chain_text_byte_for_byte(n):
    chain = _entries(n)
    assert core._dump_chain_cached(chain, {}) == core._dump_chain(chain)


def test_a_growing_chain_stays_byte_identical_and_encodes_only_the_new_receipt(monkeypatch):
    chain, cache = [], {}
    full = _entries(60)
    encodes = {"n": 0}
    real = core._encode_receipt

    def counted(e):
        encodes["n"] += 1
        return real(e)
    monkeypatch.setattr(core, "_encode_receipt", counted)
    for i, e in enumerate(full, 1):
        chain.append(e)
        assert core._dump_chain_cached(chain, cache) == core._dump_chain(chain)
        assert len(cache) == i
    assert encodes["n"] == 60, "each receipt is encoded once across 60 writes"


def test_a_receipt_changed_in_place_is_encoded_again():
    chain, cache = _entries(20), {}
    core._dump_chain_cached(chain, cache)
    chain[5]["hash"] = "f" * 64
    assert core._dump_chain_cached(chain, cache) == core._dump_chain(chain)
    chain[7]["sig"] = "a" * 128
    assert core._dump_chain_cached(chain, cache) == core._dump_chain(chain)
    chain[9]["extra"] = 1
    assert core._dump_chain_cached(chain, cache) == core._dump_chain(chain)


def test_a_receipt_that_left_the_chain_leaves_the_cache_and_a_replaced_chain_is_correct():
    a, b = _entries(30, seed=1), _entries(30, seed=2)
    cache = {}
    core._dump_chain_cached(a, cache)
    assert core._dump_chain_cached(b, cache) == core._dump_chain(b)
    assert len(cache) == 30 and all(v[0] in b for v in cache.values())
    assert core._dump_chain_cached(a[:10], cache) == core._dump_chain(a[:10])
    assert len(cache) == 10


def test_the_sidecar_a_store_writes_is_the_same_bytes_as_before_and_reads_back(tmp_path):
    p = str(tmp_path / "s.json")
    m = Inspeximus(p, receipts=True)
    for i in range(25):
        m.remember(f"fact number {i} é", key=f"k:{i % 5}", object=f"v{i}")
    m.flush()
    side = p + ".receipts.json"
    on_disk = open(side, encoding="utf-8").read()
    assert on_disk == core._dump_chain(m._receipts)
    assert json.loads(on_disk) == m._receipts
    ok, problems = Inspeximus(p, receipts=True).verify_writes()[:2]
    assert ok and not problems, problems


def test_a_second_handle_that_adopts_a_peers_receipts_writes_the_same_bytes(tmp_path):
    p = str(tmp_path / "s.json")
    a, b = Inspeximus(p, receipts=True), Inspeximus(p, receipts=True)
    for i in range(6):
        a.remember(f"alpha {i}", key=f"a:{i}")
    for i in range(6):
        b.remember(f"beta {i}", key=f"b:{i}")           # b adopts a's chain and re-chains its own
    b.flush()
    assert open(p + ".receipts.json", encoding="utf-8").read() == core._dump_chain(b._receipts)
    assert Inspeximus(p, receipts=True).verify_writes()[0]
