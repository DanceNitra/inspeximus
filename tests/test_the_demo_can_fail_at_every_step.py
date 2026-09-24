"""`inspeximus demo` checks three claims, and each check must be able to fail.

A demo that passes whatever the library does is an advertisement. So every step has a negative control
that turns off the one mechanism the step shows, and the step must then FAIL for the right reason: the
old value comes back, the subject's bytes are still on disk, the edited copy verifies.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from inspeximus.core import Inspeximus
from inspeximus.demo import OLD, NEW, cannot_run, run_demo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_the_demo_passes_all_three_steps_quickly():
    r = run_demo()
    assert r["ok"], [s["step"] for s in r["steps"] if not s["ok"]]
    assert [s["ok"] for s in r["steps"]] == [True, True, True]
    assert r["seconds"] < 60, r["seconds"]


def test_CONTROL_a_trusting_echo_policy_brings_the_old_value_back():
    r = run_demo(echo_policy="trusting")
    step = r["steps"][0]
    assert not step["ok"] and not r["ok"]
    assert OLD in step["recall_answers"] and NEW not in step["recall_answers"]


def test_the_restatement_is_recorded_as_a_blocked_echo():
    step = run_demo()["steps"][0]
    assert step["restatement"]["intent"] == "echo" and step["restatement"]["action"] == "blocked"
    assert step["restatement_in_store"] == {"records": 1, "status": "superseded", "echo_blocked": True}
    assert NEW in step["recall_answers"]


def test_CONTROL_a_restatement_retired_without_the_echo_mark_is_caught(monkeypatch):
    """The restatement is written and retired, so recall still answers the correction, but the store does
    not record it as a blocked echo. That record is what step 1 claims, so the step fails."""
    real = Inspeximus.route

    def retires_without_the_mark(self, text, *a, **k):
        res = real(self, text, *a, **k)
        for r in self._items:
            if r["id"] == res.get("id"):
                r["meta"] = {mk: mv for mk, mv in (r.get("meta") or {}).items() if mk != "echo_blocked"}
        self._dirty = True
        self.flush()
        return res

    monkeypatch.setattr(Inspeximus, "route", retires_without_the_mark)
    step = run_demo()["steps"][0]
    assert NEW in step["recall_answers"] and OLD not in step["recall_answers"]
    assert step["restatement_in_store"]["records"] == 1 and not step["restatement_in_store"]["echo_blocked"]
    assert not step["ok"]


def test_CONTROL_without_the_forget_the_subject_is_still_on_disk():
    step = run_demo(forget=False)["steps"][1]
    assert not step["ok"]
    assert step["residue_findings"] > 0, "the byte scan must find the subject when nothing was erased"
    assert not step["certificate_valid"], "a certificate for an erasure that never ran must not verify"


def test_CONTROL_a_forget_that_only_reports_is_caught(monkeypatch):
    """forget_subject() says it erased one record and touches nothing. The count is the one a real forget
    reports, so the byte scan and the certificate have to fail the step (review F8: the forget=False
    control above fails on the count alone, so it cannot tell whether the other two are in the verdict)."""
    monkeypatch.setattr(Inspeximus, "forget_subject", lambda self, *a, **k: {"erased": 1, "ids": []})
    step = run_demo()["steps"][1]
    assert not step["ok"]
    assert step["erased"] == 1
    assert step["residue_findings"] > 0 and not step["certificate_valid"]


def test_CONTROL_a_certificate_re_signed_with_another_key_is_caught(monkeypatch):
    """Every tombstone re-signed with a key the demo never minted, and the certificate names that key. It is
    signed and consistent, so it verifies unpinned; only the pin to the store's own key refuses it."""
    from inspeximus.core import _Ed25519SK, new_ed25519_keypair, verify_erasure_certificate
    sk, pub = new_ed25519_keypair()
    signer = _Ed25519SK.from_private_bytes(bytes.fromhex(sk))
    real = Inspeximus.erasure_certificate

    def re_signed(self, *a, **k):
        cert = real(self, *a, **k)
        for t in cert["tombstones"]:
            t["sig"], t["pubkey"] = signer.sign(bytes.fromhex(t["hash"])).hex(), pub
        cert["pubkey"] = pub
        return cert

    monkeypatch.setattr(Inspeximus, "erasure_certificate", re_signed)
    step = run_demo()["steps"][1]
    assert verify_erasure_certificate(step["certificate"])["valid"], "CONTROL: unpinned, it verifies"
    assert step["residue_findings"] == 0 and step["unrelated_record_intact"]
    assert not step["certificate_valid"] and not step["ok"]


def test_CONTROL_a_certificate_check_not_bound_to_the_store_is_caught(monkeypatch):
    """Without the store's receipt chain the certificate cannot be tied to the store it names, and the
    verifier says `store_bound: None`: not performed, which the step must not read as passed."""
    import inspeximus.audit_bundle as audit_bundle
    monkeypatch.setattr(audit_bundle, "load_store_receipts", lambda path: None)
    step = run_demo()["steps"][1]
    assert step["residue_findings"] == 0 and step["unrelated_record_intact"]
    assert not step["certificate_valid"] and not step["ok"]


@pytest.mark.parametrize("left", ["email", "name", "subject id"])
def test_CONTROL_each_identifier_of_the_subject_left_on_disk_is_caught(monkeypatch, left):
    """The record is erased and certified, and one identifier of the subject stays behind in a new record:
    the email or the name in its text, or the subject id as its source. The scan has to look for all three."""
    from inspeximus.demo import SUBJECT, SUBJECT_NAME, SUBJECT_VALUE
    real = Inspeximus.forget_subject

    def leaves_one(self, subject, *a, **k):
        res = real(self, subject, *a, **k)
        if left == "subject id":
            self.remember("a contact preference was noted", source={"doc": SUBJECT})
        else:
            self.remember("contact: " + (SUBJECT_VALUE if left == "email" else SUBJECT_NAME))
        return res

    monkeypatch.setattr(Inspeximus, "forget_subject", leaves_one)
    step = run_demo()["steps"][1]
    assert step["certificate_valid"] and step["unrelated_record_intact"]
    assert step["residue_findings"] > 0 and not step["ok"]


@pytest.mark.parametrize("defect", ["also erases the unrelated record", "reports two"])
def test_CONTROL_an_erasure_of_the_wrong_size_is_caught(monkeypatch, defect):
    """Each defect gets past every other check in step 2: the certificate attests what was really erased
    and the scan is clean. A forget that takes the unrelated record and reports one is seen only by the
    unrelated-record check; one that erases the subject and reports two only by the count."""
    real = Inspeximus.forget_subject

    def wrong_size(self, subject, *a, **k):
        res = real(self, subject, *a, **k)
        if defect == "also erases the unrelated record":
            real(self, "ops-notes", *a, **k)
            return res
        return {**res, "erased": res["erased"] + 1}

    monkeypatch.setattr(Inspeximus, "forget_subject", wrong_size)
    step = run_demo()["steps"][1]
    assert step["certificate_valid"] and step["residue_findings"] == 0
    assert step["unrelated_record_intact"] is (defect == "reports two")
    assert not step["ok"]


def test_CONTROL_a_byte_scan_that_never_read_the_store_file_is_caught(monkeypatch, tmp_path):
    """The scan reads one file somewhere else and finds nothing. A file count of 1 is not evidence about the
    store; the store file has to be among the files read."""
    import inspeximus.erasure_residue as erasure_residue
    real = erasure_residue.scan_residue
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "notes.txt").write_text("nothing about anyone", encoding="utf-8")
    monkeypatch.setattr(erasure_residue, "scan_residue",
                        lambda root, values, **k: real(str(elsewhere), values, **k))
    step = run_demo()["steps"][1]
    assert step["files_scanned"] == 1 and step["residue_findings"] == 0
    assert not step["store_file_scanned"] and not step["ok"]


def test_the_erasure_leaves_no_trace_and_the_certificate_verifies():
    step = run_demo()["steps"][1]
    assert step["erased"] == 1 and step["certificate_valid"] and step["residue_findings"] == 0
    assert step["certificate_signed"] and step["certificate"]["pubkey"] == step["receipt_pubkey"]
    assert step["unrelated_record_intact"]
    assert step["files_scanned"] > 0, "CONTROL: a scan that read no file finds nothing by construction"
    assert step["store_file_scanned"]


def test_CONTROL_without_the_edit_both_copies_verify():
    step = run_demo(tamper=False)["steps"][2]
    assert not step["ok"]
    assert step["untouched copy"]["verifies"] and step["edited copy"]["verifies"]


def test_the_edited_copy_is_refused_and_the_record_is_named():
    step = run_demo()["steps"][2]
    assert step["untouched copy"]["verifies"]
    refused = step["edited copy"]
    assert not refused["verifies"]
    assert any(p.startswith("memory %s:" % step["edited_record"]) for p in refused["problems"])
    assert step["refusal_names_the_edited_record"]


def test_without_cryptography_the_demo_says_so_and_prints_no_PASS(monkeypatch, capsys):
    """It cannot sign, so it does not run: an unsigned run would print PASS for a certificate anyone can
    write. Exit 2, because nothing was checked, and the message names the extra to install."""
    import inspeximus.core as core
    from inspeximus import cli
    monkeypatch.setattr(core, "_HAVE_ED", False)
    assert "inspeximus[crypto]" in cannot_run()
    with pytest.raises(RuntimeError, match=r"inspeximus\[crypto\]"):
        run_demo()
    assert cli.main(["demo"]) == 2
    out = capsys.readouterr()
    assert "PASS" not in out.out + out.err and "inspeximus[crypto]" in out.err
    assert cli.main(["--json", "demo"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_the_demo_leaves_the_environment_as_it_found_it(monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", "/somewhere/else")
    monkeypatch.delenv("INSPEXIMUS_NO_UPDATE_CHECK", raising=False)
    run_demo()
    assert os.environ["INSPEXIMUS_KEY_HOME"] == "/somewhere/else"
    assert "INSPEXIMUS_NO_UPDATE_CHECK" not in os.environ


def test_the_command_runs_without_touching_the_working_directory(tmp_path):
    keep = tmp_path / "kept"
    proc = subprocess.run([sys.executable, "-m", "inspeximus.cli", "demo", "--keep", str(keep)],
                          cwd=str(tmp_path), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env={**os.environ, "PYTHONPATH": ROOT})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.count("PASS") == 3 and "FAIL" not in proc.stdout
    assert sorted(p.name for p in tmp_path.iterdir()) == ["kept"], "the demo created a file of its own"
    cert = json.loads((keep / "erasure_certificate.json").read_text(encoding="utf-8"))
    assert cert.get("inspeximus_erasure_certificate")


def test_the_json_output_carries_every_step(tmp_path):
    proc = subprocess.run([sys.executable, "-m", "inspeximus.cli", "--json", "demo"], cwd=str(tmp_path),
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env={**os.environ, "PYTHONPATH": ROOT})
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["ok"] and len(out["steps"]) == 3
