"""Round two on the 2.10.6 candidate: eleven more, and what each of them actually was.

The first round's fixes were themselves attackable, which is the whole argument for running the pass
against the CANDIDATE. Versions 2.10.1 through 2.10.5 all shipped on the same day because the pass
ran after publishing; had that continued, these would have been 2.10.7 through 2.10.17.

Three of the eleven were FALSE CLAIMS I had written into comments while fixing round one -- code
that said it closed something and did not. Those are the ones pinned hardest here, because a wrong
comment survives every test suite in the world and is read as a guarantee.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

from inspeximus import Inspeximus
from inspeximus.audit_bundle import build_bundle, verify_bundle
from inspeximus.core import new_ed25519_keypair
from inspeximus.witness_pool import Witness, verify_attestation

SK, PK = new_ed25519_keypair()
OTHER_SK, OTHER_PK = new_ed25519_keypair()


def _store(n=3, key=SK):
    d = tempfile.mkdtemp()
    ix = Inspeximus(path=os.path.join(d, "s.json"), receipts=True, receipt_key=key)
    for i in range(n):
        ix.remember(f"record {i}", key=f"k{i}", object=str(i))
    ix.flush()
    return ix


def _witness_with_state():
    d = tempfile.mkdtemp()
    sp = os.path.join(d, "w.json")
    w = Witness(state_path=sp)
    ix = _store(2)
    wid = build_bundle(ix, witnesses=[w])["store_id_derived"]
    return sp, w, wid, ix


# ═══════════════════════════════════════ F1: the MAC did not close what its comment claimed
def test_stripping_the_mac_is_accepted_and_is_reported_in_every_statement():
    """THE FALSE CLAIM. The comment ended "one MAC per persist closes it". It did not: an attacker
    editing the state file deletes the `mac` key too, lands in the pre-2.10.6 upgrade branch, and is
    believed. The flag recording that -- `_unauthenticated_load` -- was written in four places, read
    in ZERO, and cleared again by the next persist, so nobody was ever told.

    It cannot be closed by refusing, because a stripped file and a genuinely old one are identical
    by inspection and refusing every old file takes existing witnesses offline on upgrade. So the
    fact is made durable and SIGNED instead: the attacker can strip the MAC, but not the witness's
    key, and every statement it issues from that memory says so."""
    sp, w, wid, _ix = _witness_with_state()
    assert w.attest(wid)["memory_authenticated"] is True

    st = json.load(open(sp, encoding="utf-8"))
    assert st.pop("mac", None), "the fixture has no MAC to strip: this test would prove nothing"
    st["heads"][wid]["n_writes"] = 1                      # the edit the MAC exists to stop
    json.dump(st, open(sp, "w", encoding="utf-8"))

    reborn = Witness(w._secret, state_path=sp)            # starts: upgrade safety
    a = reborn.attest(wid)
    assert a["memory_authenticated"] is False and a["unauthenticated_loads"] == 1
    out = verify_attestation(a, witness_pubkey=w.public)
    assert not out["ok"] and any("could not authenticate" in x for x in out["problems"]), out


def test_the_taint_is_signed_so_it_cannot_be_edited_out_of_the_statement():
    """Reporting it is worthless if whoever carries the statement can delete the field."""
    sp, w, wid, _ix = _witness_with_state()
    st = json.load(open(sp, encoding="utf-8")); st.pop("mac")
    json.dump(st, open(sp, "w", encoding="utf-8"))
    a = Witness(w._secret, state_path=sp).attest(wid)
    a["memory_authenticated"] = True                       # the courier tidies it up
    out = verify_attestation(a, witness_pubkey=w.public)
    assert not out["ok"] and any("does not match its own fields" in x for x in out["problems"])


def test_the_taint_survives_the_next_persist():
    """The old flag was cleared by `_persist`, so one co-signature after the tampering erased the
    only trace. It lives inside the MAC'd body now, which is also what makes it un-removable from
    the next write on."""
    sp, w, wid, ix = _witness_with_state()
    st = json.load(open(sp, encoding="utf-8")); st.pop("mac")
    json.dump(st, open(sp, "w", encoding="utf-8"))
    reborn = Witness(w._secret, state_path=sp)
    ix.remember("more", key="z", object="9"); ix.flush()
    reborn.cosign(wid, ix.anchor())                        # persists, and used to clear the flag
    assert reborn.attest(wid)["memory_authenticated"] is False
    assert Witness(w._secret, state_path=sp).attest(wid)["memory_authenticated"] is False


def test_an_operator_who_has_migrated_can_refuse_outright():
    """Accepting an un-MAC'd file is an UPGRADE concession, not a policy. Once every witness has
    persisted once, there is no reason left to accept one."""
    sp, w, wid, _ix = _witness_with_state()
    Witness(w._secret, state_path=sp, require_authenticated_state=True)      # MAC'd: fine
    st = json.load(open(sp, encoding="utf-8")); st.pop("mac")
    json.dump(st, open(sp, "w", encoding="utf-8"))
    with pytest.raises(ValueError, match="require_authenticated_state"):
        Witness(w._secret, state_path=sp, require_authenticated_state=True)


def test_control_an_untouched_witness_reports_its_memory_authenticated():
    """The must-not-cry-wolf control. If a clean witness ever reported an unauthenticated load, the
    field would be noise inside a week and the four tests above would be measuring a constant."""
    sp, w, wid, ix = _witness_with_state()
    for _ in range(3):
        ix.remember("honest", key=f"h{_}", object=str(_)); ix.flush()
        Witness(w._secret, state_path=sp).cosign(wid, ix.anchor())
    a = Witness(w._secret, state_path=sp).attest(wid)
    assert a["memory_authenticated"] is True and a["unauthenticated_loads"] == 0
    assert verify_attestation(a, witness_pubkey=w.public)["ok"]


# ═══════════════════════════════════════ F5: the pin was reachable only from the side that
#                                             does not need it
def test_the_cli_can_pin_the_key_at_verify_time():
    """`audit-build --expected-pubkey` shipped in 2.10.2 and is nearly useless -- the operator builds
    the bundle, so checking their own key against their own artifact tells them nothing. The auditor
    holds a key from out of band and runs `audit-verify`, which had no way to accept one. Over the
    shell, the strongest attribution check in the package was available only to the wrong party."""
    ix = _store(2)
    d = tempfile.mkdtemp()
    bp = os.path.join(d, "b.json")
    json.dump(build_bundle(ix), open(bp, "w", encoding="utf-8"))

    def run(*extra):
        # `--json` is a GLOBAL option, so it goes BEFORE the subcommand. Placed after it, argparse
        # exits 2 with an empty stdout, and json.loads("") then reports a JSON error rather than the
        # usage error that actually happened.
        return subprocess.run([sys.executable, "-m", "inspeximus.cli", "--json", "audit-verify", bp,
                               *extra], capture_output=True, text=True,
                              env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})

    good = json.loads(run("--expected-pubkey", PK).stdout)
    assert any("VERIFY against the pinned key" in c for c in good["checks"]), good

    bad = json.loads(run("--expected-pubkey", OTHER_PK).stdout)
    assert not bad["ok"] and any("NOT the one pinned" in p for p in bad["problems"]), bad


def test_the_mcp_surface_can_pin_it_too(monkeypatch):
    """The same gap on the surface an agent actually calls."""
    import importlib
    monkeypatch.setenv("INSPEXIMUS_PATH", os.path.join(tempfile.mkdtemp(), "s.json"))
    import inspeximus.mcp_server as m
    m = importlib.reload(m)
    b = build_bundle(_store(2))
    assert any("VERIFY against the pinned key" in c
               for c in m.verify_audit_bundle(b, expected_pubkey=PK)["checks"])
    assert not m.verify_audit_bundle(b, expected_pubkey=OTHER_PK)["ok"]


# ═══════════════════════════════════════ F6: "cannot check" is not "does not match"
def test_a_base_install_says_it_cannot_check_rather_than_accusing_anyone(monkeypatch):
    """Without `cryptography` every signature is skipped, `_verified` stays 0, and this reported
    "only 0 of N chain signatures verify against the pinned key" -- an accusation of forgery
    manufactured by a missing optional dependency. The remedy is an install, not an incident."""
    import inspeximus.audit_bundle as ab
    b = build_bundle(_store(2))
    monkeypatch.setattr(ab, "_HAVE_ED", False)
    out = ab.verify_bundle(b, expected_pubkey=PK)
    assert out["ok"] and any("CANNOT CHECK" in x for x in out["limits"]), out
    assert not any("verify against the pinned key" in p for p in out["problems"]), out["problems"]
    # ...and it is still a FAILURE when the caller said the signatures must hold.
    assert not ab.verify_bundle(b, expected_pubkey=PK, require_signed=True)["ok"]


def test_a_base_install_still_catches_a_chain_that_NAMES_the_wrong_key(monkeypatch):
    """The half that does not need crypto: comparing the pubkey field is a string comparison. If
    this stopped working, the caveat above would be covering for a real regression."""
    import inspeximus.audit_bundle as ab
    monkeypatch.setattr(ab, "_HAVE_ED", False)
    out = ab.verify_bundle(build_bundle(_store(2, key=OTHER_SK)), expected_pubkey=PK)
    assert not out["ok"] and any("NOT the one pinned" in p for p in out["problems"]), out


# ═══════════════════════════════════════ F7: an unpinned attestation carried the whole verdict
def test_an_unpinned_attestation_is_reported_as_unpinned():
    """The chain signatures get this caveat and the attestation did not -- and the attestation is
    where it matters most, because it carries the only operator-adversarial claim in the file. With
    no allowlist a consistently forged statement (key, hash and signature all the attacker's own)
    verifies exactly like a real one."""
    sp, w, wid, ix = _witness_with_state()
    out = verify_bundle(build_bundle(ix), attestations=[w.attest(wid)])
    assert any("SIGNED BUT UNPINNED" in x for x in out["limits"]), out["limits"]
    # Pinned, the caveat is gone -- a caveat that fires unconditionally is one nobody reads.
    pinned = verify_bundle(build_bundle(ix), witnesses=[w.public], attestations=[w.attest(wid)])
    assert not any("SIGNED BUT UNPINNED" in x for x in pinned["limits"]), pinned["limits"]


# ═══════════════════════════════════════ F10: a count taken after the step that skips it
def test_binding_coverage_does_not_rise_as_sources_go_missing():
    """`bound` was incremented after the ORPHANED branch, so a record whose source cannot be
    resolved today was dropped from the count -- making the reported observation-binding coverage go
    UP as sources disappeared. It is a fact about the RECORD (was the fingerprint taken from what
    was actually read?), not about whether the file is still there."""
    import hashlib
    d = tempfile.mkdtemp()
    src = os.path.join(d, "doc.txt")
    blob = b"the source text"
    open(src, "wb").write(blob)
    ix = Inspeximus(path=os.path.join(d, "s.json"))
    # `observed_sha256` is what makes it OBSERVATION-bound. A bare `doc` locator gets a write-time
    # hash, which is the other number entirely -- the distinction this whole metric exists for, and
    # the first version of this test forgot it and measured 0.0 against 0.0.
    ix.remember("derived from the doc", key="a", object="1",
                source={"doc": src, "observed_sha256": hashlib.sha256(blob).hexdigest()})
    ix.flush()

    def cov(r):
        return r["coverage"]["declared_observation_binding_coverage"]

    assert cov(ix.check_sources()) == 1.0

    os.unlink(src)                       # the source goes missing; the record did not change
    after = ix.check_sources()
    assert after["counts"]["ORPHANED"] == 1
    assert cov(after) == 1.0, f"it fell to {cov(after)} because the source vanished"


# ═══════════════════════════════════════ F11: the bypass the key guard does NOT close
@pytest.mark.skipif(not hasattr(os, "link"), reason="no hardlink support")
def test_a_hardlink_puts_the_key_inside_the_store_directory_and_the_guard_says_outside():
    """PINNED AS A KNOWN LIMIT, not as a bug to be silently fixed later. A hardlink is one inode
    with two names and `realpath` has nothing to resolve, so the key is readable from inside the
    data directory while the guard, asked about the other name, correctly answers "outside".

    It stays open deliberately: closing it means walking the store directory comparing st_ino on
    every key read, and the link can be made after that walk anyway. The guard is about the
    documented `receipt.key`-in-the-working-directory ACCIDENT, and its docstring now says so. If
    someone "fixes" this test by making the guard refuse, read that paragraph first.
    """
    from inspeximus.core import _guard_key_location
    sd, kd = tempfile.mkdtemp(), tempfile.mkdtemp()
    real = os.path.join(kd, "receipt.key")
    open(real, "w", encoding="utf-8").write(SK)
    try:
        os.link(real, os.path.join(sd, "k"))
    except OSError:
        pytest.skip("hardlink not permitted here")
    _guard_key_location(kd, os.path.join(sd, "s.json"))          # no refusal: the same bytes, twice
    assert open(os.path.join(sd, "k"), encoding="utf-8").read() == SK, \
        "the fixture did not actually link: this test would be vacuous"


def test_control_the_accident_the_guard_does_close_is_still_closed():
    """The must-still-fail control for the test above: the ordinary case has to keep refusing, or
    "known limit" would be documenting a guard that stopped working."""
    from inspeximus.core import _guard_key_location
    d = tempfile.mkdtemp()
    with pytest.raises(ValueError, match="inside the store's own directory"):
        _guard_key_location(d, os.path.join(d, "s.json"))
