"""Transparency log: tests written from the 2026-09-24 mutation run of the evidence modules.

Each test here exists because a named mutant of `inspeximus/merkle.py` or
`inspeximus/transparency.py` survived the whole suite (audits/2026-09-24/mutation-evidence.md).
The mutation is named in each docstring, so the test cannot be "simplified" back into one that
passes either way. Every one was checked in both directions with
`python audits/2026-09-24/mutate_evidence.py kill --ids <id> --tests <this test>`: green on the
original source, red on the mutant.
"""
from __future__ import annotations

import hashlib
import json
import os

import pytest

from inspeximus import new_receipt_keypair, signed_statement
from inspeximus.merkle import (consistency_proof, inclusion_proof, node_hash, root, root_from_inclusion,
                               verify_consistency_proof, verify_inclusion)
from inspeximus.transparency import (RegistrationPolicy, RegistrationRefused, TransparencyService,
                                     verify_registered_statement)

LEAVES = [bytes([i]) * (i + 1) for i in range(11)]
ISSUER = "did:web:issuer.example"


# -- consistency proofs: the two early exits nothing reached --------------------------------------
@pytest.mark.parametrize("n", [2, 3, 5, 8, 11])
def test_an_empty_consistency_proof_proves_nothing(n):
    """SURVIVOR merkle.py:174 `return False` -> `return True` (merkle:174:15:5451bad7).

    No test ever handed the verifier an EMPTY proof for 0 < m < n, so the guard that refuses one was
    never executed. With it flipped, `verify_consistency_proof` accepts ANY pair of roots for any
    sizes -- a rewritten history reads as append-only growth -- and the whole suite stayed green."""
    rn = root(LEAVES[:n])
    for m in range(1, n):
        assert not verify_consistency_proof(m, n, root(LEAVES[:m]), rn, []), (m, n)
        forged = root([b"REWRITTEN"] + LEAVES[1:m])
        assert not verify_consistency_proof(m, n, forged, rn, []), (m, n)


@pytest.mark.parametrize("n", [2, 3, 5, 8, 11])
def test_a_consistency_proof_with_a_hash_to_spare_is_refused(n):
    """SURVIVOR merkle.py:181 `return False` -> `return True` (merkle:181:19:1c53a5a2).

    The walk returns as soon as it runs out of tree with proof hashes left over. Flipped, that exit
    returns True BEFORE either root is compared, so one junk hash appended to any proof of the right
    length makes a forged first root verify. The existing negative controls only ever used proofs of
    the exact length, so the loop never reached this line."""
    rn = root(LEAVES[:n])
    for m in range(1, n):
        good = consistency_proof(LEAVES[:n], m)
        assert verify_consistency_proof(m, n, root(LEAVES[:m]), rn, good), (m, n)   # the control
        assert not verify_consistency_proof(m, n, root(LEAVES[:m]), rn, good + [bytes(32)]), (m, n)
        forged = root([b"REWRITTEN"] + LEAVES[1:m])
        junk = [hashlib.sha256(bytes([k])).digest() for k in range(len(good) + 1)]
        assert not verify_consistency_proof(m, n, forged, rn, junk), (m, n)


def test_consistency_round_trips_past_the_sizes_the_suite_stopped_at():
    """SURVIVORS merkle.py:187 `last >>= 1` -> `last = 1` and -> `last >>= 2`
    (merkle:187:16:e361a38d, merkle:187:25:0810fdcd).

    The loop that climbs the right edge of the old tree while it still coincides with the new
    tree's edge only runs when that edge is several levels deep. The existing round trip stops at
    12 leaves; the first sizes where the mutants reject an HONEST proof are 13 -> 14 and 21 -> 22.
    Every prefix of every tree up to 40 leaves is cheap and covers them."""
    leaves = [bytes([i % 256]) * (i % 7 + 1) for i in range(40)]
    for n in range(1, 41):
        rn = root(leaves[:n])
        for m in range(0, n + 1):
            proof = consistency_proof(leaves[:n], m)
            assert verify_consistency_proof(m, n, root(leaves[:m]), rn, proof), (m, n)


@pytest.mark.parametrize("n", [1, 2, 4, 7])
def test_two_different_roots_at_the_same_size_are_a_fork_not_a_consistency(n):
    """SURVIVOR merkle.py:165 `... and not proof` -> `... or not proof` (merkle:165:15:ab4a184d).

    At m == n the only honest answer is "the roots are equal". Every existing test at m == n passed
    the SAME root twice, so the mutant -- which accepts any two roots as long as the proof is empty --
    stayed green. Two different roots for one tree size are exactly what a split view looks like."""
    honest = root(LEAVES[:n])
    fork = root([b"FORKED"] + LEAVES[1:n])
    assert verify_consistency_proof(n, n, honest, honest, [])                     # the control
    assert not verify_consistency_proof(n, n, honest, fork, [])
    assert not verify_consistency_proof(n, n, fork, honest, [])


def test_an_inclusion_proof_for_index_n_is_refused():
    """SURVIVOR merkle.py:106 `0 <= m < n` -> `0 <= m <= n` (merkle:106:17:f7317f2a).

    The bounds tests go through `inclusion_proof()`, which raises before the verifier is reached, and
    the verifier's own negative controls use indexes inside the tree. With the bound widened, leaf 0
    of a two-leaf tree verifies at index 2 -- a position that does not exist -- using leaf 0's honest
    path, so a receipt could place a record anywhere past the end of the log."""
    two = LEAVES[:2]
    r = root(two)
    path = inclusion_proof(two, 0)
    assert verify_inclusion(two[0], 0, 2, path, r)                                  # the control
    assert not verify_inclusion(two[0], 2, 2, path, r)
    assert root_from_inclusion(two[0], 2, 2, path) is None


@pytest.mark.parametrize("claimed", [3, 4, 6])
def test_a_path_too_short_for_the_claimed_tree_size_is_refused(claimed):
    """SURVIVOR merkle.py:122 `r if sn == 0 else None` -> `r if (sn == 0) or True else None`
    (merkle:122:11:aabe62bb).

    A path that ends before it has climbed the whole claimed tree leads to an interior node, not the
    root. Accepted, leaf 0 of a two-leaf log verifies as a member of a larger tree whose root is the
    two-leaf root: the tree size a Receipt states would be whatever the issuer wrote. No test
    presented a path shorter than the size it claimed."""
    two = LEAVES[:2]
    r = root(two)
    path = inclusion_proof(two, 0)
    assert not verify_inclusion(two[0], 0, claimed, path, r)
    assert root_from_inclusion(two[0], 0, claimed, path) is None


@pytest.mark.parametrize("m,n", [(5, 4), (8, 4), (3, 2)])
def test_a_rollback_is_refused_whatever_the_proof_says(m, n):
    """SURVIVOR merkle.py:162 `m < 0 or n < m` -> `m < 0 and n < m` (merkle:162:7:3b54fd68).

    The only rollback test passed an EMPTY proof, which a later guard refuses anyway, so the size
    check itself was never the thing that answered. Here the proof is built so that the rest of the
    walk would accept it: only `n < m` stands between it and True."""
    a, h1, h2 = (hashlib.sha256(bytes([k])).digest() for k in (1, 2, 3))
    proof = [a, h1, h2]
    walked = node_hash(node_hash(a, h1), h2)
    for first, second in ((a, walked), (a, node_hash(a, h1))):
        assert not verify_consistency_proof(m, n, first, second, proof), (m, n)
    assert not verify_consistency_proof(-2, n, a, node_hash(a, h1), [a, h1]), "a negative size"


# -- the Transparency Service ----------------------------------------------------------------------
def _keypair():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (Ed25519PrivateKey as SK,
                                                                   Ed25519PublicKey as PK)
    sk_hex, pk_hex = new_receipt_keypair()
    sk, pk = SK.from_private_bytes(bytes.fromhex(sk_hex)), PK.from_public_bytes(bytes.fromhex(pk_hex))

    def verify(msg, sig):
        try:
            pk.verify(sig, msg)
            return True
        except Exception:
            return False
    return sk.sign, verify, pk_hex


@pytest.fixture()
def service(tmp_path):
    isign, iverify, _ = _keypair()
    ssign, sverify, spub = _keypair()
    ts = TransparencyService(str(tmp_path / "log.jsonl"),
                             RegistrationPolicy("p1", accepted_issuers=[ISSUER]), ssign, iverify,
                             service_pubkey=spub)
    return ts, isign, iverify, sverify


def _statement(isign, text=b"a fact", subject="memory:abc", issuer=ISSUER):
    return signed_statement(hashlib.sha256(text).digest(), issuer, subject, isign)


def test_a_receipt_is_checked_against_the_root_the_caller_trusts(service):
    """SURVIVORS transparency.py:336 `expected_root=bytes(expected_root)` -> `expected_root=None`
    and -> dropped (transparency:336:9:08b4e395, transparency:336:9:560fa570).

    `expected_root` is the root a witness co-signed; it is the only operator-adversarial input this
    verifier takes. Every existing call passed the log's own current root, so a verifier that
    ignored the argument and checked the receipt against the root the receipt itself carries
    answered the same way. Here the receipt is genuine and the trusted root is a different log's."""
    ts, isign, iverify, sverify = service
    tr = ts.register_transparent(_statement(isign))
    leaf, trusted = ts.entry_leaf(1), ts.root()
    ok = verify_registered_statement(tr, iverify, sverify, leaf, trusted)
    assert ok["ok"] is True, ok["problems"]                                           # the control
    foreign = hashlib.sha256(b"a root some other log published").digest()
    out = verify_registered_statement(tr, iverify, sverify, leaf, foreign)
    assert out["ok"] is False
    assert out["receipt"]["ok"] is False
    assert out["bound"] is True                       # the binding alone must not carry the verdict


def test_the_verdict_needs_the_receipt_and_the_statement_not_only_the_binding(service):
    """SURVIVOR transparency.py:363 `st["ok"] and rc["ok"] and out["bound"]` ->
    `st["ok"] and rc["ok"] or out["bound"]` (transparency:363:21:567e2a2d).

    Every negative test here broke the binding too, so `bound` was False whenever the verdict had to
    be False. A statement that IS the one the entry names, carried by a receipt that fails, must
    still be refused: a correct pointer to a log entry is not a proof that the entry is in the log."""
    ts, isign, iverify, sverify = service
    tr = ts.register_transparent(_statement(isign))
    leaf = ts.entry_leaf(1)
    out = verify_registered_statement(tr, iverify, iverify, leaf, ts.root())   # wrong service key
    assert out["bound"] is True and out["receipt"]["ok"] is False
    assert out["ok"] is False
    out = verify_registered_statement(tr, lambda _m, _s: False, sverify, leaf, ts.root())
    assert out["bound"] is True and out["statement"]["ok"] is False
    assert out["ok"] is False


def test_a_pinned_issuer_is_enforced(service):
    """SURVIVORS transparency.py:327 `expected_issuer=expected_issuer` -> `None` / dropped
    (transparency:327:9:d9c8ea72, transparency:327:9:05710222).

    The one test that passed `expected_issuer` passed the issuer the statement really has, so a
    verifier that never forwarded the pin gave the same answer. Pinned to someone else, the same
    genuine statement must be refused."""
    ts, isign, iverify, sverify = service
    tr = ts.register_transparent(_statement(isign))
    args = (tr, iverify, sverify, ts.entry_leaf(1), ts.root())
    assert verify_registered_statement(*args, expected_issuer=ISSUER)["ok"] is True  # the control
    out = verify_registered_statement(*args, expected_issuer="did:web:someone-else.example")
    assert out["ok"] is False
    assert out["statement"]["ok"] is False


def test_a_registration_after_a_policy_change_records_the_new_policy(service):
    """SURVIVORS transparency.py:185-186, the keys of the entry `set_policy()` appends
    (`"policy_sha256"` -> `"XXpolicy_sha256XX"` / upper-case, `"ts"` -> ...).

    The only test that changed the policy then had a registration REFUSED, which stops before the
    new entry's `policy_sha256` is read. RFC 9943 s5.1.1 asks for the policy in force at
    registration to be applied and recorded; this registers successfully under the second policy."""
    ts, isign, iverify, sverify = service
    p2 = RegistrationPolicy("p2", accepted_issuers=[ISSUER], require_subject_prefix="memory:")
    seq = ts.set_policy(p2)
    entry = json.loads(ts.entry_leaf(seq).decode("utf-8"))
    assert entry["kind"] == "registration-policy" and isinstance(entry["ts"], float)
    assert entry["policy_sha256"] == p2.digest()
    tr = ts.register_transparent(_statement(isign))
    got = json.loads(ts.entry_leaf(ts.size() - 1).decode("utf-8"))
    assert got["policy_sha256"] == p2.digest() != RegistrationPolicy("p1", [ISSUER]).digest()
    assert verify_registered_statement(tr, iverify, sverify, ts.entry_leaf(ts.size() - 1),
                                       ts.root())["ok"] is True
    assert ts.policy is p2


def test_a_subject_prefix_admits_what_it_names(service):
    """SURVIVOR transparency.py:111 `str(subject)` -> `str(None)` (transparency:111:49:2623c212).

    The prefix rule was only ever tested with a subject that FAILS it, and the string "None" fails
    every prefix too. A rule that refuses everything passes a test that only asks for refusals."""
    ts, isign, _iverify, _sverify = service
    ts.set_policy(RegistrationPolicy("p2", accepted_issuers=[ISSUER], require_subject_prefix="memory:"))
    ts.register(_statement(isign, subject="memory:abc"))                              # admitted
    with pytest.raises(RegistrationRefused):
        ts.register(_statement(isign, subject="other:abc"))


def test_the_payload_ceiling_admits_exactly_its_own_size():
    """SURVIVORS transparency.py:113 `payload_len > max` -> `>=` (transparency:113:22:b296b601) and
    the default `max_payload_bytes: int = 4096` -> `4097` (transparency:68:82:804b6be9)."""
    ok = {"signature_ok": True, "issuer": ISSUER, "subject": "memory:x"}
    p = RegistrationPolicy("p")
    assert p.max_payload_bytes == 4096
    assert p.check(ok, 4096) == []
    assert any("over the 4096" in w for w in p.check(ok, 4097))


def test_each_missing_claim_is_its_own_refusal():
    """SURVIVORS transparency.py:102/105/110 (`why.append(...)` -> `why.append(None)` and the
    wording). No test sent a statement without a verified signature, without an issuer or without a
    subject, so none of these three reasons had ever been produced."""
    p = RegistrationPolicy("p", accepted_issuers=[ISSUER])
    assert p.check({"signature_ok": False, "issuer": ISSUER, "subject": "s"}, 1) == [
        "the statement's signature does not verify"]
    assert p.check({"signature_ok": True, "issuer": None, "subject": "s"}, 1) == ["no Issuer claim"]
    assert p.check({"signature_ok": True, "issuer": ISSUER, "subject": ""}, 1) == ["no Subject claim"]


def test_a_service_without_a_signer_or_without_a_verifier_is_refused(tmp_path):
    """SURVIVOR transparency.py:129 `not callable(sign) or not callable(verify_issuer)` -> `and`
    (transparency:129:11:c86e6b9e). Missing ONE of the two was never tried."""
    sign, verify, _ = _keypair()
    for s, v in ((None, verify), (sign, None)):
        with pytest.raises(TypeError, match="needs a signer and an issuer verifier"):
            TransparencyService(str(tmp_path / "l.jsonl"), RegistrationPolicy("p"), s, v)


def test_two_logs_at_two_paths_have_two_identities(tmp_path):
    """SURVIVORS transparency.py:135 (`str(path)` -> `str(None)`, `[:16]` -> `[:17]`, the "scitt:"
    prefix). `store_id` is what a witness keys its memory of a log on; with `str(None)` every log
    in the world shared one identity, so a witness would compare heads of unrelated logs."""
    sign, verify, _ = _keypair()
    a = TransparencyService(str(tmp_path / "a.jsonl"), RegistrationPolicy("p"), sign, verify)
    b = TransparencyService(str(tmp_path / "b.jsonl"), RegistrationPolicy("p"), sign, verify)
    assert a.store_id != b.store_id
    for ts in (a, b):
        want = "scitt:" + hashlib.sha256(os.path.abspath(ts.path).encode("utf-8")).hexdigest()[:16]
        assert ts.store_id == want
        assert ts.head()["store_id"] == ts.store_id == ts.describe()["store_id"]


def test_the_head_says_a_transparency_log_has_no_tombstones(service):
    """SURVIVORS transparency.py:258-259, the head's fixed fields (`"n_tombstones": 0` -> `1`,
    `"tombstones_tip": ""` -> `"XXXX"`, the key names and `"kind"`). The witness pipeline only ever
    compared a head with itself, so the fields could say anything consistent."""
    ts, isign, _iv, _sv = service
    ts.register(_statement(isign))
    h = ts.head()
    assert h["n_tombstones"] == 0 and h["tombstones_tip"] == ""
    assert h["kind"] == "scitt-transparency-log"
    assert h["n_writes"] == ts.size() == 2 and h["writes_tip"] == ts.root().hex()


def test_a_receipt_for_the_next_index_is_none_not_an_error(service):
    """SURVIVOR transparency.py:233 `index < len` -> `index <= len` (transparency:233:25:5470fae6).

    `receipt_for` promises None for an index that is not there ("not yet" versus "never", the
    SCRAPI 204/404 distinction). The first index past the end is the one a poller asks for; widened,
    it raised IndexError from inside the tree code instead."""
    ts, isign, _iv, _sv = service
    ts.register(_statement(isign))
    assert ts.receipt_for(ts.size() - 1) is not None
    assert ts.receipt_for(ts.size()) is None
    assert ts.receipt_for(-1) is None


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_the_library_re_derives_the_log_it_published(tmp_path):
    """SURVIVORS transparency.py:161 (`_leaves`: `sort_keys=True` -> `False` / `None` / dropped,
    `separators=(",", ":")` -> dropped) and transparency.py:93 (`canonical`: the same arguments).

    Producer and verifier both call `_leaves()`, so a change to the encoding is invisible to any test
    that only round-trips through one service: the tree is rebuilt consistently, just differently.
    What it breaks is every Receipt already issued. `transparency/` is the log this repository
    publishes; its leaf hashes and head were written by this code, so the library must still
    re-derive them byte for byte, from the entries whatever order their keys are stored in. That is
    the same stability pin `test_interop_vectors_against_certificate_transparency` gives the tree,
    applied to the log format."""
    rows = [json.loads(ln) for ln in open(os.path.join(REPO, "transparency", "log.jsonl"), encoding="utf-8")
            if ln.strip()]
    head = json.load(open(os.path.join(REPO, "transparency", "head.json"), encoding="utf-8"))
    path = tmp_path / "republished.jsonl"

    def reordered(x):                  # the same entry with every object's keys in REVERSE order
        if isinstance(x, dict):
            return {k: reordered(x[k]) for k in sorted(x, reverse=True)}
        return [reordered(v) for v in x] if isinstance(x, list) else x
    # Written back in another key order, with spaces: a leaf is the CANONICAL form of an entry, so it
    # cannot depend on how some other tool happened to lay the line out on disk.
    path.write_text("".join(json.dumps(reordered(r["entry"])) + "\n" for r in rows), encoding="utf-8")
    sign, verify, _ = _keypair()
    ts = TransparencyService(str(path), RegistrationPolicy("unused"), sign, verify)
    assert ts.size() == head["n_writes"] == len(rows)
    from inspeximus.merkle import leaf_hash
    for i, r in enumerate(rows):
        assert leaf_hash(ts.entry_leaf(i)).hex() == r["leaf_hash"], f"leaf {i} re-encodes differently"
    assert ts.root().hex() == head["writes_tip"]
    policy = rows[0]["entry"]["policy"]
    assert RegistrationPolicy(policy["name"]).digest() == rows[0]["entry"]["policy_sha256"]


def test_a_leaf_is_ascii_whatever_the_statement_says(service):
    """SURVIVORS transparency.py:161 `ensure_ascii=True` -> `False` / `None` / dropped.

    The published log holds only ASCII, so the pin above cannot see this one. A subject with a
    non-ASCII character is the case: escaped, the leaf is the same bytes in every encoding a reader
    might open the log with; raw, it is UTF-8 on one machine and something else on another."""
    ts, isign, iverify, sverify = service
    tr = ts.register_transparent(_statement(isign, subject="memory:café"))
    leaf = ts.entry_leaf(1)
    leaf.decode("ascii")                                                  # raises if not ASCII
    assert b"\\u00e9" in leaf
    assert verify_registered_statement(tr, iverify, sverify, leaf, ts.root())["ok"] is True
