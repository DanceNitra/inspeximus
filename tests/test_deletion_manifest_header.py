"""A deletion manifest could be repointed at a different data subject and still verify.

`verify()` recomputed `complete` and `residual_targets` from the entries — a fix from an earlier round —
and walked the entry hash chain. It never read the HEADER. `subject`, `request_id`, `basis`,
`authorized_by` and `targets` were bound to nothing, so:

    manifest["subject"]       = "Bob"
    manifest["authorized_by"] = "attacker@evil"
    manifest["request_id"]    = "DSAR-999"
    manifest["targets"]       = ["none"]
    verify(manifest) -> (True, [])

On the one artifact whose entire job is to be evidence that a named person's data was erased, under a
named authority, on a named request. The entries were faithful; the sentence they were attached to was
anyone's to write.

The chain is now SEEDED with a hash of those five fields, rather than given a separate signature, so a
single edit to any of them breaks entry 0's `prev` link — a future verifier that only walks the chain
cannot forget to check them.
"""
import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.deletion_manifest as M  # noqa: E402
from inspeximus.deletion_manifest import _GENESIS, DeletionManifest, ErasureTarget  # noqa: E402


class _Target(ErasureTarget):
    name = "primary-store"

    def erase(self, subject):
        return {"erased": 1}

    def still_recoverable(self, subject, values):
        return False


@pytest.fixture
def manifest():
    dm = DeletionManifest().register(_Target())
    return dm, dm.execute(subject="Alice", values=["alice@example.com"],
                          request_id="DSAR-7", basis="Art.17", authorized_by="dpo@corp")


def test_a_genuine_manifest_verifies(manifest):
    dm, man = manifest
    assert dm.verify(man) == (True, [])


@pytest.mark.parametrize("field,forged", [
    ("subject", "Bob"),
    ("authorized_by", "attacker@evil"),
    ("request_id", "DSAR-999"),
    ("basis", "consent"),
    ("targets", ["none"]),
])
def test_every_header_field_is_bound(field, forged, manifest):
    """One fixture proves one fixture. Each of these was independently rewritable with no effect."""
    dm, man = manifest
    f = copy.deepcopy(man)
    f[field] = forged
    ok, problems = dm.verify(f)
    assert ok is False, f"{field} could be rewritten and the manifest still verified"
    assert any("chain link" in p for p in problems), problems


def test_the_erasure_evidence_itself_still_binds(manifest):
    """The entry chain was never the hole; it must keep working."""
    dm, man = manifest
    f = copy.deepcopy(man)
    f["entries"][0]["erased"] = 999
    assert dm.verify(f)[0] is False


def test_a_flipped_verdict_still_fails(manifest):
    """Recomputed from the entries — the earlier round's fix, pinned here so it cannot regress."""
    dm, man = manifest
    f = copy.deepcopy(man)
    f["entries"][0]["verified_absent"] = False
    f["entries"][0]["still_recoverable"] = True
    assert dm.verify(f)[0] is False

    g = copy.deepcopy(man)
    g["complete"] = True
    g["entries"] = []
    assert dm.verify(g)[0] is False, "an empty manifest audited nothing"


# ── the pre-1.83 escape hatch has to actually let something through ────────────────────────────────
def _legacy(man):
    """A manifest as 1.82 wrote it: chain seeded at genesis, header bound to nothing."""
    legacy = copy.deepcopy(man)
    e = legacy["entries"][0]
    e["prev"] = _GENESIS
    e["hash"] = M._sha256({k: e[k] for k in ("target", "erased", "still_recoverable",
                                             "verified_absent", "error", "ts", "prev")})
    return legacy


def test_a_pre_183_manifest_is_refused_by_default(manifest):
    """"This manifest predates the binding" and "this manifest was repointed at someone else" look
    identical from outside, so the older one is not waved through silently."""
    dm, man = manifest
    ok, problems = dm.verify(_legacy(man))
    assert ok is False
    assert any("bound to NOTHING" in p for p in problems), problems
    assert any("legacy_header=True" in p for p in problems), "a refusal must say how to proceed"


def test_the_legacy_flag_actually_accepts_it(manifest):
    """THE thing to get wrong. The first version appended the note to `problems`, and since ok is
    `not problems` the flag changed nothing at all — an escape hatch that does not open. Notes and
    problems are separate now."""
    dm, man = manifest
    ok, msgs = dm.verify(_legacy(man), legacy_header=True)
    assert ok is True, "the flag must do what its name says"
    assert any("CANNOT be verified here" in m for m in msgs), "and must still state what it gave up"


def test_the_legacy_flag_does_not_launder_a_repointed_manifest(manifest):
    """It applies only to a chain that starts at genesis. A 1.83 manifest whose header was rewritten has a
    seeded chain, so the flag cannot reach it."""
    dm, man = manifest
    f = copy.deepcopy(man)
    f["subject"] = "Bob"
    assert dm.verify(f, legacy_header=True)[0] is False


# ── whose signature counts ─────────────────────────────────────────────────────────────────────────
def _signed():
    ed = pytest.importorskip(
        "cryptography.hazmat.primitives.asymmetric.ed25519",
        reason="signing needs cryptography")
    sk = ed.Ed25519PrivateKey.generate()
    skh = sk.private_bytes_raw().hex()
    pkh = sk.public_key().public_bytes_raw().hex()
    dm = DeletionManifest(sign_sk_hex=skh, pubkey_hex=pkh).register(_Target())
    return ed, dm, pkh, dm.execute(subject="Alice", values=["a@x"], request_id="DSAR-8",
                                   authorized_by="dpo@corp")


def test_an_unpinned_signature_is_a_note_not_a_failure():
    """It is a scope statement, not a defect: failing here would break every signed manifest that exists,
    and a check that cries wolf gets switched off. verify_writes draws the same line."""
    _, dm, _, man = _signed()
    ok, msgs = dm.verify(man)
    assert ok is True
    assert any("expected_pubkey not pinned" in m for m in msgs), msgs


def test_pinning_the_key_catches_a_manifest_re_signed_by_someone_else():
    """Without a pin, each entry is verified against the key stored INSIDE it — so a rewriter signs with
    their own and passes."""
    ed, dm, pkh, man = _signed()
    assert dm.verify(man, expected_pubkey=pkh) == (True, [])

    evil = ed.Ed25519PrivateKey.generate()
    f = copy.deepcopy(man)
    f["entries"][0]["pubkey"] = evil.public_key().public_bytes_raw().hex()
    f["entries"][0]["sig"] = evil.sign(bytes.fromhex(f["entries"][0]["hash"])).hex()

    assert dm.verify(f)[0] is True, "unpinned, this is exactly the hole -- documented, not hidden"
    assert dm.verify(f, expected_pubkey=pkh)[0] is False, "pinned, it must be caught"


def test_a_wrong_signer_is_NAMED_not_just_rejected():
    """The pinned-key comparison changes no verdict — verifying the signature against the pinned key
    already fails — so on its own it is a guard that cannot fail, which is the shape this repository keeps
    deleting. It earns its place by what it SAYS: an auditor holding a rejected manifest needs to know it
    was signed by the wrong party, not merely that a signature did not check out. So the message is the
    thing under test."""
    ed, dm, pkh, man = _signed()
    evil = ed.Ed25519PrivateKey.generate()
    f = copy.deepcopy(man)
    f["entries"][0]["pubkey"] = evil.public_key().public_bytes_raw().hex()
    f["entries"][0]["sig"] = evil.sign(bytes.fromhex(f["entries"][0]["hash"])).hex()

    ok, problems = dm.verify(f, expected_pubkey=pkh)
    assert ok is False
    assert any("not the pinned key" in p for p in problems), \
        f"a rejected manifest must say it was signed by someone else: {problems}"
