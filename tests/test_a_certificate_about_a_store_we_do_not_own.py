"""A residue certificate is the auditor's form of `scan_residue`, so every check in it must be able to fail.

WHY IT EXISTS. `scan_residue` answers about a store we do not own, and returns a dict. A dict is the
scanner's word. An auditor is engaged to state a fact about someone else's system to a third party, so the
deliverable has to be a document that the third party checks without trusting the scanner and without the
scanner's key. `residue_certificate` is that document and `verify_residue_certificate` is that check.

The certificate cannot borrow the design of `Inspeximus.erasure_certificate()`. That one is issued by the
operator about the ACT of erasure and its evidence is a tombstone chain the operator owns. A scanner has no
chain and cannot get one. Its only evidence is the bytes it read, so the certificate commits to SHA-256 per
file, and re-verification re-walks the directory and compares. That is what makes the finding checkable
rather than assertable, and it is the single load-bearing idea in the file.

WHAT THESE TESTS PIN. Each check gets a case that makes it say no, because a verifier that returns valid on
every input has measured nothing. In particular:

  * `valid` is about the DOCUMENT and `ok` is about the STORE. A dirty store must still produce a valid
    certificate, or the artifact becomes useless exactly when it matters.
  * Bytes that changed after the scan make the certificate STALE, not invalid. Merging the two either hides
    a real change or condemns an honest historical record.
  * A file created after the scan is invisible to it. The verifier has to say so, because silence there
    reads as coverage.
  * The pubkey is signed INSIDE the core, so a signature cannot be re-attributed to another key.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import new_receipt_keypair
from inspeximus.erasure_residue import residue_certificate, verify_residue_certificate

SECRET = "alice@example.com"


def _dirty():
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "a.json"), "w", encoding="utf-8") as fh:
        json.dump({"note": "nothing sensitive here"}, fh)
    with open(os.path.join(d, "b.log"), "w", encoding="utf-8") as fh:
        fh.write("user %s signed in\n" % SECRET)
    return d


def _clean():
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "a.json"), "w", encoding="utf-8") as fh:
        json.dump({"note": "nothing sensitive here"}, fh)
    return d


def test_a_dirty_store_still_yields_a_valid_certificate():
    """The separation the whole design rests on: the document is sound, the store is not."""
    sk, pk = new_receipt_keypair()
    d = _dirty()
    cert = residue_certificate(d, [SECRET], sk)
    assert cert["ok"] is False, "the value is on disk, so the scan is not clean"
    assert len(cert["findings"]) == 1
    v = verify_residue_certificate(cert, root=d, expected_pubkey=pk)
    assert v["valid"] is True, v["problems"]
    assert v["checks"]["signature_valid"] is True


def test_a_clean_store_says_clean():
    """CONTROL. Without it, every `ok is False` above is consistent with a scanner that never says clean."""
    sk, _pk = new_receipt_keypair()
    cert = residue_certificate(_clean(), [SECRET], sk)
    assert cert["ok"] is True
    assert cert["findings"] == []


def test_the_value_is_never_echoed():
    sk, _pk = new_receipt_keypair()
    blob = json.dumps(residue_certificate(_dirty(), [SECRET], sk))
    assert SECRET not in blob, "the certificate leaked the value it was asked to hunt for"
    assert "alice" not in blob


def test_editing_a_finding_breaks_the_signature():
    """The point of signing. An operator who receives a dirty certificate must not be able to clean it."""
    sk, pk = new_receipt_keypair()
    cert = residue_certificate(_dirty(), [SECRET], sk)
    cert["findings"] = []
    cert["ok"] = True
    v = verify_residue_certificate(cert, expected_pubkey=pk)
    assert v["valid"] is False
    assert v["checks"]["signature_valid"] is False


def test_editing_the_manifest_is_caught_by_its_own_commitment():
    sk, _pk = new_receipt_keypair()
    cert = residue_certificate(_dirty(), [SECRET], sk)
    cert["manifest"] = cert["manifest"][:1]
    v = verify_residue_certificate(cert)
    assert v["checks"]["manifest_committed"] is False
    assert v["valid"] is False


def test_a_signature_cannot_be_re_attributed_to_another_key():
    """The pubkey is inside the signed core. Swapping it must not produce a document that verifies."""
    sk, _pk = new_receipt_keypair()
    _sk2, pk2 = new_receipt_keypair()
    cert = residue_certificate(_dirty(), [SECRET], sk)
    cert["pubkey"] = pk2
    v = verify_residue_certificate(cert)
    assert v["checks"]["signature_valid"] is False


def test_pinning_the_wrong_key_fails_even_on_a_self_consistent_document():
    """A signature checked only against the key the document names proves internal consistency, nothing
    more. An auditor pins the key they expect."""
    sk, _pk = new_receipt_keypair()
    _sk2, pk2 = new_receipt_keypair()
    cert = residue_certificate(_dirty(), [SECRET], sk)
    v = verify_residue_certificate(cert, expected_pubkey=pk2)
    assert v["checks"]["pubkey_pinned"] is False
    assert v["valid"] is False
    # CONTROL: the same document against the right key is valid, so the failure is the pin and not the doc.
    assert verify_residue_certificate(cert, expected_pubkey=_pk_of(cert))["valid"] is True


def _pk_of(cert):
    return cert["pubkey"]


def test_an_unsigned_certificate_is_reported_as_naming_nobody():
    cert = residue_certificate(_dirty(), [SECRET])
    v = verify_residue_certificate(cert)
    assert v["signed"] is False
    assert any("unsigned" in p for p in v["problems"])
    # It is still structurally valid, because re-walking the manifest is evidence on its own.
    assert v["valid"] is True


def test_a_file_changed_after_the_scan_makes_the_certificate_stale_not_invalid():
    sk, pk = new_receipt_keypair()
    d = _dirty()
    cert = residue_certificate(d, [SECRET], sk)
    with open(os.path.join(d, "a.json"), "a", encoding="utf-8") as fh:
        fh.write("\nappended after the scan\n")
    v = verify_residue_certificate(cert, root=d, expected_pubkey=pk)
    assert v["valid"] is True, "a stale certificate is still a truthful record of an earlier moment"
    assert v["checks"]["bytes_unchanged"] is False
    assert v["bytes"]["changed"] == ["a.json"]
    assert any("STALE" in p for p in v["problems"])


def test_a_file_deleted_after_the_scan_is_reported_missing():
    sk, _pk = new_receipt_keypair()
    d = _dirty()
    cert = residue_certificate(d, [SECRET], sk)
    os.remove(os.path.join(d, "b.log"))
    v = verify_residue_certificate(cert, root=d)
    assert v["bytes"]["missing"] == ["b.log"]
    assert v["checks"]["bytes_unchanged"] is False


def test_a_file_created_after_the_scan_is_reported_as_uncovered():
    """Silence about a new file reads as coverage, which is the failure mode this whole module exists for."""
    sk, _pk = new_receipt_keypair()
    d = _clean()
    cert = residue_certificate(d, [SECRET], sk)
    assert cert["ok"] is True
    with open(os.path.join(d, "leaked.log"), "w", encoding="utf-8") as fh:
        fh.write(SECRET)
    v = verify_residue_certificate(cert, root=d)
    assert v["bytes"]["added"] == ["leaked.log"]
    assert any("never read" in p for p in v["problems"])


def test_an_unchanged_store_reports_no_changes():
    """CONTROL for the three tests above. Without it they are consistent with a comparison that flags
    everything."""
    sk, _pk = new_receipt_keypair()
    d = _dirty()
    cert = residue_certificate(d, [SECRET], sk)
    b = verify_residue_certificate(cert, root=d)["bytes"]
    assert b["changed"] == [] and b["missing"] == [] and b["added"] == []
    assert len(b["unchanged"]) == b["manifest_files"] == 2


def test_the_verifier_re_walks_with_the_certificates_own_parameters():
    """A verifier using its own defaults would report every file in a skipped directory as added, and the
    certificate would look worse the more the scanner honestly excluded."""
    sk, _pk = new_receipt_keypair()
    d = _clean()
    os.mkdir(os.path.join(d, ".git"))
    with open(os.path.join(d, ".git", "objects.pack"), "wb") as fh:
        fh.write(b"binary noise")
    cert = residue_certificate(d, [SECRET], sk)
    assert ".git" in cert["scan_parameters"]["skip_dirs"]
    v = verify_residue_certificate(cert, root=d)
    assert v["bytes"]["added"] == [], "the verifier used its own walk instead of the recorded one"
    # CONTROL: the scan itself must have declared the hole rather than passing over it silently.
    assert any(s["path"] == ".git" for s in cert["skipped"])
    assert cert["ok"] is False, "an unsearched directory is not a clean result"


def test_a_non_certificate_is_rejected_rather_than_parsed():
    v = verify_residue_certificate({"hello": "world"})
    assert v["valid"] is False
    assert v["problems"] == ["not an inspeximus residue certificate"]


def test_an_unknown_version_is_refused():
    sk, _pk = new_receipt_keypair()
    cert = residue_certificate(_clean(), [SECRET], sk)
    cert["inspeximus_residue_certificate"] = "9.9"
    v = verify_residue_certificate(cert)
    assert v["checks"]["version_known"] is False
    assert v["valid"] is False


def test_the_scope_travels_with_the_document():
    """A signed clean bill of health that does not carry its own limits is an overclaim. The match is
    literal and case-sensitive, so the certificate has to say so in band."""
    sk, _pk = new_receipt_keypair()
    cert = residue_certificate(_clean(), [SECRET], sk)
    scope = cert["scope"]
    assert "literal" in scope and "case-sensitive" in scope
    assert "does not make the finding true" in scope
    # The scope is inside the signed core, so it cannot be stripped from a document that still verifies.
    cert["scope"] = "everything is fine"
    assert verify_residue_certificate(cert)["checks"]["signature_valid"] is False


def test_the_certificate_round_trips_through_json():
    """It is handed to a regulator as a file, so it has to survive serialisation with its signature."""
    sk, pk = new_receipt_keypair()
    d = _dirty()
    cert = json.loads(json.dumps(residue_certificate(d, [SECRET], sk)))
    v = verify_residue_certificate(cert, root=d, expected_pubkey=pk)
    assert v["valid"] is True, v["problems"]


def test_signing_without_the_crypto_extra_fails_loudly(monkeypatch):
    """A silently unsigned certificate is worse than an error, because it looks like the signed one."""
    import inspeximus.erasure_residue as mod
    monkeypatch.setattr(mod, "_HAVE_ED", False)
    with pytest.raises(RuntimeError, match="cryptography"):
        residue_certificate(_clean(), [SECRET], "00" * 32)


# ---------------------------------------------------------------------------------------------------
# The CLI half. An auditor runs a command; the library is what the command is made of.
# ---------------------------------------------------------------------------------------------------

def _cli(*args):
    import subprocess
    # encoding= is required: text=True alone decodes with the machine's locale codec (cp1250 here),
    # so a child printing UTF-8 returns stdout=None and every assertion below reads a defect it
    # never measured.
    r = subprocess.run([sys.executable, "-m", "inspeximus.cli", *args], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout + r.stderr


def _client_dir(dirty=True):
    d = tempfile.mkdtemp()
    target = os.path.join(d, "clientstore")
    os.mkdir(target)
    with open(os.path.join(target, "vectors.json"), "w", encoding="utf-8") as fh:
        json.dump({"payload": SECRET if dirty else "nothing"}, fh)
    return d, target


def _keyfile(d):
    sk, pk = new_receipt_keypair()
    path = os.path.join(d, "scanner.key")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(sk)
    return path, pk


def test_scanning_a_client_directory_does_not_mint_a_store_of_our_own():
    """THE defect this move fixed. `residue` sat after `m = _store(...)`, so auditing someone else's
    directory silently created our store at --path. Nothing in the output mentioned it, and a mistyped
    path is exactly how a clean bill of health gets produced by the typo."""
    d, target = _client_dir()
    ours = os.path.join(d, "ours.json")
    code, _out = _cli("--path", ours, "residue", "--root", target, "--value", SECRET)
    assert code == 1, "a dirty store must exit non-zero so the verb works as a gate"
    assert not os.path.exists(ours), "scanning a foreign directory created our own store"


def test_a_store_writing_verb_at_the_same_path_does_create_one():
    """CONTROL. Without it, the assertion above passes just as well if the path were never writable, or
    if the CLI had stopped creating stores at all."""
    d, _target = _client_dir()
    ours = os.path.join(d, "ours.json")
    code, out = _cli("--path", ours, "remember", "a fact worth keeping")
    assert code == 0, out
    assert os.path.exists(ours), "the control cannot fire: no verb created a store here"


def test_the_cli_writes_a_certificate_that_the_verify_verb_accepts():
    d, target = _client_dir()
    keyf, pk = _keyfile(d)
    cert = os.path.join(d, "cert.json")
    code, _out = _cli("residue", "--root", target, "--value", SECRET, "--cert-out", cert,
                      "--sign-key-file", keyf, "--label", "client-acme")
    assert code == 1 and os.path.exists(cert)
    code, out = _cli("residue-verify", cert, "--root", target, "--expected-pubkey", pk)
    assert code == 0, out
    assert "DOCUMENT: valid" in out
    # The two verdicts stay apart: a sound document about a store that is not clean.
    assert "STORE AT SCAN TIME: residue found" in out


def test_the_verify_verb_exits_non_zero_on_a_tampered_certificate():
    """CONTROL for the exit code above, which otherwise only shows that nothing failed."""
    d, target = _client_dir()
    keyf, _pk = _keyfile(d)
    cert = os.path.join(d, "cert.json")
    _cli("residue", "--root", target, "--value", SECRET, "--cert-out", cert, "--sign-key-file", keyf)
    with open(cert, encoding="utf-8") as fh:
        doc = json.load(fh)
    doc["findings"] = []
    doc["ok"] = True
    with open(cert, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    code, out = _cli("residue-verify", cert)
    assert code == 1
    assert "DOCUMENT: INVALID" in out


def test_a_signing_key_without_an_output_path_is_refused():
    """Signing writes nothing without --cert-out, so accepting the flag would produce an unsigned scan
    from a command line that asked for a signature."""
    d, target = _client_dir()
    keyf, _pk = _keyfile(d)
    code, out = _cli("residue", "--root", target, "--value", SECRET, "--sign-key-file", keyf)
    assert code == 2, out
    assert "needs --cert-out" in out


def test_an_unreadable_signing_key_stops_rather_than_falling_through_to_unsigned():
    d, target = _client_dir()
    cert = os.path.join(d, "cert.json")
    code, out = _cli("residue", "--root", target, "--value", SECRET, "--cert-out", cert,
                     "--sign-key-file", os.path.join(d, "no-such.key"))
    assert code == 2, out
    assert "cannot read the signing key" in out
    assert not os.path.exists(cert), "an unsigned certificate was written after the key failed to load"


# ---------------------------------------------------------------------------------------------------
# The remembering half. Signing answers whether a document is genuine. Remembering answers whether the
# store is getting worse, which is what a second visit is paid to settle.
# ---------------------------------------------------------------------------------------------------

from inspeximus import Inspeximus                                          # noqa: E402
from inspeximus.erasure_residue import certificate_drift, certificate_summary   # noqa: E402


def _auditor_store():
    return Inspeximus(path=os.path.join(tempfile.mkdtemp(), "auditor.json"), receipts=True)


def _scan(target, sk, label="client-acme"):
    return residue_certificate(target, [SECRET], sk, root_label=label)


def test_a_first_scan_reports_no_drift():
    sk, _pk = new_receipt_keypair()
    r = _auditor_store().remember_certificate(_scan(_clean(), sk))
    assert r["first_scan"] is True
    assert r["drift"] is None
    assert r["key"] == "residue::client-acme"


def test_a_store_that_went_from_clean_to_dirty_raises_the_alarm():
    sk, _pk = new_receipt_keypair()
    ix, d = _auditor_store(), _clean()
    ix.remember_certificate(_scan(d, sk))
    with open(os.path.join(d, "leak.log"), "w", encoding="utf-8") as fh:
        fh.write(SECRET)
    r = ix.remember_certificate(_scan(d, sk))
    assert r["first_scan"] is False
    assert r["drift"]["clean_to_dirty"] is True
    assert r["drift"]["findings_delta"] == 1
    assert r["drift"]["bytes_changed"] is True


def test_re_scanning_an_unchanged_store_reports_no_change():
    """CONTROL. Without it the alarm above is consistent with a detector that fires on every second scan."""
    sk, _pk = new_receipt_keypair()
    ix, d = _auditor_store(), _clean()
    ix.remember_certificate(_scan(d, sk))
    r = ix.remember_certificate(_scan(d, sk))
    assert r["drift"]["clean_to_dirty"] is False
    assert r["drift"]["findings_delta"] == 0
    assert r["drift"]["bytes_changed"] is False


def test_a_remediated_store_is_reported_as_recovered():
    sk, _pk = new_receipt_keypair()
    ix, d = _auditor_store(), _dirty()
    ix.remember_certificate(_scan(d, sk))
    os.remove(os.path.join(d, "b.log"))
    r = ix.remember_certificate(_scan(d, sk))
    assert r["drift"]["dirty_to_clean"] is True
    assert r["drift"]["findings_delta"] == -1


def test_a_re_scan_supersedes_rather_than_accumulates():
    """The reason the record is keyed. `recall` has to return the current state of the store, and the
    trail has to stay readable in order."""
    sk, _pk = new_receipt_keypair()
    ix, d = _auditor_store(), _clean()
    r = ix.remember_certificate(_scan(d, sk))
    with open(os.path.join(d, "leak.log"), "w", encoding="utf-8") as fh:
        fh.write(SECRET)
    ix.remember_certificate(_scan(d, sk))
    trail = ix.history(r["key"])
    assert len(trail) == 2
    assert [h["status"] for h in trail] == ["superseded", "active"]
    assert "clean" in trail[0]["text"] and "1 finding(s)" in trail[1]["text"]


def test_two_stores_are_remembered_apart():
    """CONTROL for the supersession above: keying on the label must not collapse distinct engagements."""
    sk, _pk = new_receipt_keypair()
    ix = _auditor_store()
    a = ix.remember_certificate(_scan(_clean(), sk, label="client-acme"))
    b = ix.remember_certificate(_scan(_clean(), sk, label="client-globex"))
    assert a["key"] != b["key"]
    assert b["first_scan"] is True, "the second client inherited the first client's history"


def test_the_remembered_summary_carries_no_content_and_binds_to_the_file():
    sk, _pk = new_receipt_keypair()
    cert = _scan(_dirty(), sk)
    summary = certificate_summary(cert)
    assert SECRET not in json.dumps(summary)
    assert summary["manifest_sha256"] == cert["manifest_sha256"]
    assert summary["signature"] == cert["signature"], "nothing ties the record to a certificate on disk"
    assert "manifest" not in summary, "the per-file list is too large to keep in a memory store"


def test_remembering_something_that_is_not_a_certificate_is_refused():
    with pytest.raises(ValueError, match="residue certificate"):
        _auditor_store().remember_certificate({"root_label": "client-acme", "ok": True})


def test_file_level_drift_names_what_moved():
    sk, _pk = new_receipt_keypair()
    d = _clean()
    before = _scan(d, sk)
    with open(os.path.join(d, "leak.log"), "w", encoding="utf-8") as fh:
        fh.write(SECRET)
    with open(os.path.join(d, "a.json"), "w", encoding="utf-8") as fh:
        fh.write('{"note":"edited"}')
    after = _scan(d, sk)
    drift = certificate_drift(before, after)
    assert drift["comparable"] is True
    assert drift["added"] == ["leak.log"]
    assert drift["changed"] == ["a.json"]
    assert drift["removed"] == []
    assert drift["clean_to_dirty"] is True


def test_drift_is_ordered_by_issue_time_not_argument_position():
    sk, _pk = new_receipt_keypair()
    d = _clean()
    before = _scan(d, sk)
    with open(os.path.join(d, "leak.log"), "w", encoding="utf-8") as fh:
        fh.write(SECRET)
    after = _scan(d, sk)
    assert certificate_drift(after, before) == certificate_drift(before, after)


def test_drift_between_two_different_stores_is_refused():
    """A file set that differs because the ROOT differed is not drift, and reporting it as drift would
    invent a change out of an argument."""
    sk, _pk = new_receipt_keypair()
    drift = certificate_drift(_scan(_clean(), sk, label="client-acme"),
                              _scan(_clean(), sk, label="client-globex"))
    assert drift["comparable"] is False
    assert any("different stores" in p for p in drift["problems"])


def test_drift_between_two_different_walks_is_refused():
    """Same store, different skip list: files appear because the walk changed, not because the store did."""
    sk, _pk = new_receipt_keypair()
    d = _clean()
    os.mkdir(os.path.join(d, ".git"))
    with open(os.path.join(d, ".git", "obj"), "wb") as fh:
        fh.write(b"noise")
    narrow = residue_certificate(d, [SECRET], sk, root_label="c")
    wide = residue_certificate(d, [SECRET], sk, root_label="c", skip_dirs=set())
    drift = certificate_drift(narrow, wide)
    assert drift["comparable"] is False
    assert any("different parameters" in p for p in drift["problems"])
    # CONTROL: two scans with the SAME parameters over the same store do compare.
    assert certificate_drift(narrow, residue_certificate(d, [SECRET], sk, root_label="c"))["comparable"]


def test_drift_rejects_a_document_that_is_not_a_certificate():
    sk, _pk = new_receipt_keypair()
    drift = certificate_drift(_scan(_clean(), sk), {"added": ["everything"]})
    assert drift["comparable"] is False
    assert drift["added"] == []
