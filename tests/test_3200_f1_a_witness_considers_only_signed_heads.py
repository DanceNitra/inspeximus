"""3.20, F1 (Claude Security scan, MEDIUM): a stranger can no longer make a witness refuse a store's honest heads.

THE DEFECT. POST /cosign took any head from anyone. A stranger's head with n_writes=10^15 and a self-consistent
sth_hash (no key, no store) was co-signed, and the store's next honest head was then refused as a rollback, with the
store poisoned (measured on v3.19.1, AUDIT-A's f1_repro.py). A consistency proof cannot stop it: AUDIT-A forged
proofs that merkle.verify_consistency_proof accepts for fabricated trees of 10^9 and 10^15 from the honest root alone.

THE FIX (AUDIT-A's design (c), EM 2026-10-14). The store signs its head; the witness checks the signature against a key
the operator pinned (--pin STORE=PUBKEY, `witness pin`, or /bootstrap with the token and a pubkey). An unsigned or
wrongly signed head for a pinned store is not considered: 403, and nothing written. A replayed genuine head gets its
co-signature again. A rotation is a statement the old key signs; a lost key is re-pinned as a recorded rebound.
`require_signed_heads` (off in 3.20) applies this to every store, and the attestation says `unauthenticated_heads`
while it is off. A 3.19 state file and a 3.19 attestation still verify.
"""
import hashlib
import json
import os
import sys
import threading
import urllib.error
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

pytest.importorskip("cryptography")
from inspeximus import merkle, witness_server  # noqa: E402
from inspeximus.core import Inspeximus, new_ed25519_keypair, sth_hash_of, _canon, _sha256_hex  # noqa: E402
from inspeximus.witness_pool import (Unauthenticated, Witness, rotation_message, sign_head,  # noqa: E402
                                     verify_attestation)

SID = "store-under-test"


def _heads(tmp_path, n=3):
    """`n` honest heads of one real store, in order. Receipts on, so each write moves n_writes and the tip."""
    m = Inspeximus(str(tmp_path / ("s%d.db" % n)), receipts=True)
    out = []
    for i in range(n):
        m.remember("honest note %d" % i, key="k%d" % i)
        m.flush()
        out.append(m.anchor())
    assert len({h["sth_hash"] for h in out}) == n, "CONTROL: the heads differ"
    return out


def _evil(head):
    e = dict(head)
    e.update(n_writes=10 ** 15, n_tombstones=10 ** 15, writes_tip="ab" * 32, tombstones_tip="cd" * 32)
    e["sth_hash"] = sth_hash_of(e)
    return e


@pytest.fixture
def server(tmp_path):
    hk_secret, hk_public = new_ed25519_keypair()
    httpd, w = witness_server.make_server(port=0, state_path=str(tmp_path / "w.json"), pins={SID: hk_public},
                                          bootstrap_token="tok")
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    yield "http://127.0.0.1:%d" % httpd.server_address[1], w, hk_secret, hk_public, tmp_path / "w.json"
    httpd.shutdown()
    th.join(10)


def _post(url, path, body, headers=None):
    req = urllib.request.Request(url + path, data=json.dumps(body).encode(),
                                 headers=dict({"Content-Type": "application/json"}, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_the_strangers_head_is_not_considered_and_the_honest_one_still_cosigns(server, tmp_path):
    url, w, hk, _pk, state = server
    h1, h2, _h3 = _heads(tmp_path)
    assert _post(url, "/cosign", dict(store_id=SID, anchor=h1, **sign_head(SID, h1, hk)))[0] == 200
    before = state.read_bytes()
    code, body = _post(url, "/cosign", {"store_id": SID, "anchor": _evil(h2)})      # AUDIT-A's F1 head, unsigned
    assert code == 403 and "not signed" in body["unauthenticated"], (code, body)
    assert state.read_bytes() == before, "the refused head changed the witness's state"
    code, body = _post(url, "/cosign", dict(store_id=SID, anchor=h2, **sign_head(SID, h2, hk)))
    assert code == 200, (code, body)
    assert w.poisoned() == [] and w.refusals(SID) == []


def test_a_head_signed_by_another_key_is_not_considered(server, tmp_path):
    url, w, _hk, _pk, state = server
    other, _ = new_ed25519_keypair()
    h1 = _heads(tmp_path, 1)[0]
    before = state.read_bytes()
    code, body = _post(url, "/cosign", dict(store_id=SID, anchor=_evil(h1), **sign_head(SID, _evil(h1), other)))
    assert code == 403 and "not the one pinned" in body["unauthenticated"], body
    assert state.read_bytes() == before and w.last_head(SID) is None


def test_a_bad_signature_without_a_named_key_is_not_considered(server, tmp_path):
    """The signature itself is checked, not only the key a request names."""
    url, w, _hk, _pk, state = server
    other, _ = new_ed25519_keypair()
    h1 = _heads(tmp_path, 1)[0]
    before = state.read_bytes()
    code, body = _post(url, "/cosign", {"store_id": SID, "anchor": h1, "store_sig": sign_head(SID, h1, other)["store_sig"]})
    assert code == 403 and "does not verify" in body["unauthenticated"], body
    assert state.read_bytes() == before


def test_a_replayed_genuine_head_gets_its_cosignature_again_and_is_not_a_rollback(server, tmp_path):
    url, w, hk, _pk, _state = server
    h1, h2, _h3 = _heads(tmp_path)
    c1 = _post(url, "/cosign", dict(store_id=SID, anchor=h1, **sign_head(SID, h1, hk)))
    assert _post(url, "/cosign", dict(store_id=SID, anchor=h2, **sign_head(SID, h2, hk)))[0] == 200
    again = _post(url, "/cosign", dict(store_id=SID, anchor=h1, **sign_head(SID, h1, hk)))
    assert again == c1, (again, c1)
    assert w.refusals(SID) == [] and w.poisoned() == []


def test_a_signed_rollback_by_the_key_holder_is_still_refused_and_recorded(server, tmp_path):
    """Not closed, by design: the holder of the pinned key can still equivocate, and that is what a witness catches."""
    url, w, hk, _pk, _state = server
    h1, h2, _h3 = _heads(tmp_path)
    assert _post(url, "/cosign", dict(store_id=SID, anchor=h2, **sign_head(SID, h2, hk)))[0] == 200
    fork = dict(h2, writes_tip="ef" * 32)
    fork["sth_hash"] = sth_hash_of(fork)
    code, body = _post(url, "/cosign", dict(store_id=SID, anchor=fork, **sign_head(SID, fork, hk)))
    assert code == 409 and "fork" in body["refused"], (code, body)
    assert w.poisoned() == [SID] and len(w.refusals(SID)) == 1


def test_rotation_needs_the_old_keys_statement_and_a_rising_counter(server, tmp_path):
    url, w, hk, _pk, _state = server
    new_secret, new_public = new_ed25519_keypair()
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    old_sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(hk))
    stranger = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(new_ed25519_keypair()[0]))
    bad = {"store_id": SID, "new_pubkey": new_public, "counter": 1,
           "sig": stranger.sign(rotation_message(SID, new_public, 1)).hex()}
    assert _post(url, "/rotate", bad)[0] == 403
    good = dict(bad, sig=old_sk.sign(rotation_message(SID, new_public, 1)).hex())
    assert _post(url, "/rotate", good)[0] == 200
    assert _post(url, "/rotate", good)[0] == 403, "a replayed rotation was accepted"
    h1 = _heads(tmp_path, 1)[0]
    assert _post(url, "/cosign", dict(store_id=SID, anchor=h1, **sign_head(SID, h1, hk)))[0] == 403
    assert _post(url, "/cosign", dict(store_id=SID, anchor=h1, **sign_head(SID, h1, new_secret)))[0] == 200
    assert w.attest(SID)["rebounds"] == [], "a consented rotation is not a rebound"
    # THE COUNTER IS THE ONLY DEFENCE HERE: hand the store back to the old key (counter 2), then replay the first
    # rotation statement, which the now-pinned old key really did sign.
    new_sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(new_secret))
    back = {"store_id": SID, "new_pubkey": _pk, "counter": 2, "sig": new_sk.sign(rotation_message(SID, _pk, 2)).hex()}
    assert _post(url, "/rotate", back)[0] == 200
    code, body = _post(url, "/rotate", good)
    assert code == 403 and "replayed rotation" in body["unauthenticated"], (code, body)


def test_a_lost_key_is_rebound_with_the_token_and_every_attestation_reports_it(server):
    url, w, _hk, _pk, _state = server
    _s, new_public = new_ed25519_keypair()
    assert _post(url, "/bootstrap", {"store_id": SID, "pubkey": new_public})[0] == 403, "pinned without the token"
    assert _post(url, "/bootstrap", {"store_id": SID, "pubkey": new_public}, {"X-Bootstrap-Token": "tok"})[0] == 200
    att = w.attest(SID)
    assert len(att["rebounds"]) == 1 and att["rebounds"][0]["new_pubkey"] == new_public
    got = verify_attestation(att, w.public)
    assert got["signed"] and any("REBOUND" in n for n in got["notes"]), got   # never seen yet, so not `ok`


def test_require_signed_heads_refuses_an_unpinned_store(tmp_path):
    w = Witness(state_path=str(tmp_path / "w.json"), require_signed_heads=True)
    h1 = _heads(tmp_path, 1)[0]
    with pytest.raises(Unauthenticated, match="requires signed heads"):
        w.cosign("unpinned", h1)
    assert w.last_head("unpinned") is None and w.refusals() == []
    assert w.attest("unpinned")["unauthenticated_heads"] is False


def test_the_default_still_takes_an_unpinned_unsigned_head_and_says_so(tmp_path):
    """Compatibility: 3.20 changes nothing for an operator who has pinned nothing, and the attestation tells the
    auditor that this witness took the head unsigned."""
    w = Witness(state_path=str(tmp_path / "w.json"))
    h1 = _heads(tmp_path, 1)[0]
    w.cosign("legacy", h1)
    att = w.attest("legacy")
    assert att["unauthenticated_heads"] is True and att["pinned"] is False
    got = verify_attestation(att, w.public)
    assert got["ok"] and any("unsigned" in n for n in got["notes"]), got


def test_a_319_state_file_still_loads_and_a_stripped_pin_breaks_the_mac(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    secret, _pub = new_ed25519_keypair()
    p = tmp_path / "w.json"
    body = {"heads": {}, "refusals": [], "poisoned": [], "unauthenticated_loads": [], "bootstrapped": []}
    sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(secret))
    body["mac"] = sk.sign(bytes.fromhex(_sha256_hex(_canon({k: body[k] for k in body if k != "mac"})))).hex()
    p.write_text(json.dumps(body))
    w = Witness(secret_hex=secret, state_path=str(p))                    # the 3.19 MAC, over the five 3.19 keys
    assert w.attest("x")["memory_authenticated"] is True
    _s, hk_pub = new_ed25519_keypair()
    w.pin(SID, hk_pub)
    st = json.loads(p.read_text())
    assert st["pins"][SID]["pubkey"] == hk_pub
    del st["pins"]                                                      # an attacker unpinning the store by hand
    p.write_text(json.dumps(st))
    with pytest.raises(ValueError, match="does not authenticate"):
        Witness(secret_hex=secret, state_path=str(p))


def test_a_319_attestation_still_verifies_and_a_stripped_field_breaks_the_hash(tmp_path):
    w = Witness(state_path=str(tmp_path / "w.json"))
    att = w.attest("legacy")
    assert verify_attestation(att, w.public)["signed"]
    stripped = {k: v for k, v in att.items() if k != "unauthenticated_heads"}
    assert any("edited in transit" in p for p in verify_attestation(stripped, w.public)["problems"])


def test_a_forged_consistency_proof_verifies_so_it_cannot_authenticate_a_head(tmp_path):
    """AUDIT-A's forge_consistency.py, kept: from the honest root alone, random nodes make a proof that
    verify_consistency_proof accepts for a fabricated tree of 10^15. That is why the witness authenticates the SUBMITTER
    by signature, and a pinned store refuses the fabricated head however good its proof looks."""
    leaves = [b"honest %d" % i for i in range(16)]
    r1 = merkle.root(leaves)

    def run(m, n, first_root, proof):
        node, last = m - 1, n - 1
        while node & 1:
            node >>= 1
            last >>= 1
        fr = sr = first_root
        for h in proof:
            if last == 0:
                return None
            if node & 1 or node == last:
                fr, sr = merkle.node_hash(h, fr), merkle.node_hash(h, sr)
                while node & 1 == 0 and node != 0:
                    node >>= 1
                    last >>= 1
            else:
                sr = merkle.node_hash(sr, h)
            node >>= 1
            last >>= 1
        return (last == 0 and fr == first_root), sr
    n, forged = 10 ** 15, None
    for k in range(1, 70):
        proof = [hashlib.sha256(b"forged %d %d" % (n, i)).digest() for i in range(k)]
        r = run(16, n, r1, proof)
        if r and r[0]:
            forged = (proof, r[1])
            break
    assert forged and merkle.verify_consistency_proof(16, n, r1, forged[1], forged[0]), "CONTROL: the forgery works"
    _s, pk = new_ed25519_keypair()
    w = Witness(state_path=str(tmp_path / "w.json"), pins={SID: pk})
    with pytest.raises(Unauthenticated):
        w.cosign(SID, _evil(_heads(tmp_path, 1)[0]))


def test_scrapi_signs_its_head_and_a_witness_pinned_to_its_key_cosigns_it(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from inspeximus.transparency import RegistrationPolicy, TransparencyService
    svc_sk = Ed25519PrivateKey.generate()
    svc = TransparencyService(str(tmp_path / "reg.log"), RegistrationPolicy("p", accepted_issuers=None, notes=""),
                              svc_sk.sign, lambda m, s: True, service_pubkey=svc_sk.public_key().public_bytes_raw().hex())
    w = Witness(state_path=str(tmp_path / "w.json"), pins={svc.store_id: svc.service_pubkey})
    got = svc.witnessed_head([w])
    assert got["met"] and not got["refused"], got
    with pytest.raises(Unauthenticated):
        w.cosign(svc.store_id, _evil(got["head"]))                      # a stranger's head for the service's store
