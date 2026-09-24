"""Adversarial review of `inspeximus demo` (shipped in 3.9.0): audits/2026-09-24/demo-review.md.

The question: can the demo print PASS for a claim that is false? Each test removes or bends one piece of
PRODUCT behaviour a step depends on, by patching the library inside this process with `monkeypatch`, and
runs the demo exactly as the command does. F8 instead weakens the demo's own step-2 verdict, to ask whether
the SHIPPED negative controls notice. No source file is edited, so these run under xdist with the rest of
the suite and restore themselves.

Two kinds of test (N1 is a strict xfail too, about the library rather than the demo):

  CONFIRMED_*  the mutation must turn the step into FAIL, and it does. These pass today; they are the
               evidence that a check is load-bearing, and they fail if the check stops being so.
  F<n>_*       the final assert states what the demo SHOULD report. They were written as strict xfails at
               6edea3e, when the step printed PASS (or "valid") for something that was not so. F1 to F7
               are fixed in demo.py and F8 by a control in tests/test_the_demo_can_fail_at_every_step.py,
               so they now run as plain tests. `_finding()` stays for the next one: mark it, fix it,
               remove the mark. The set-up inside a finding is checked with `pytest.fail`, not `assert`,
               so a finding whose premise broke is a FAILURE and cannot hide as an expected xfail.
"""
from __future__ import annotations

import json
import os
import shutil

import pytest

import inspeximus.erasure_residue as erasure_residue
from inspeximus import demo
from inspeximus.core import Inspeximus, _canon, _sha256_hex, verify_erasure_certificate
from inspeximus.demo import KEY, NEW, OLD, SUBJECT_VALUE, run_demo


def _finding(reason):
    return pytest.mark.xfail(strict=True, raises=AssertionError, reason=reason)


def _control(cond, why):
    """A premise of a finding. `pytest.fail` is not an AssertionError, so a broken premise is a failure."""
    if not cond:
        pytest.fail("control: " + why)


def _texts(store_path):
    return [r["text"] for r in Inspeximus(path=str(store_path), receipts=True).items
            if r.get("status") == "active"]


# ── step 1: a correction holds ─────────────────────────────────────────────────────────────────────────

def test_CONFIRMED_echo_guard_off_fails_step_1(monkeypatch):
    """The guard is what holds the correction: off, the keyless restatement outranks it in recall."""
    monkeypatch.setenv("INSPEXIMUS_ECHO_GUARD", "0")
    step = run_demo()["steps"][0]
    assert not step["ok"]
    assert OLD in step["recall_answers"]


def test_CONFIRMED_keyed_supersession_off_fails_step_1(monkeypatch):
    """remember() stripped of key/object: nothing supersedes anything, and the step fails."""
    real = Inspeximus.remember

    def keyless(self, text, *a, **k):
        k.pop("key", None)
        k.pop("object", None)
        return real(self, text, *a, **k)

    monkeypatch.setattr(Inspeximus, "remember", keyless)
    step = run_demo()["steps"][0]
    assert not step["ok"] and OLD in step["recall_answers"]


def test_CONFIRMED_recall_serving_superseded_records_fails_step_1(monkeypatch):
    real = Inspeximus.recall

    def everything(self, query, k=6, **kw):
        kw["include_superseded"] = True
        return real(self, query, k=k, **kw)

    monkeypatch.setattr(Inspeximus, "recall", everything)
    step = run_demo()["steps"][0]
    assert not step["ok"] and OLD in step["recall_answers"]


def test_F5_a_restatement_that_is_never_recorded_fails_step_1(monkeypatch, tmp_path):
    def records_nothing(self, text, key=None, object=None, **kw):
        return {"intent": "echo", "action": "blocked", "key": key, "id": None,
                "note": "unmarked restatement of a superseded value; not restored"}

    monkeypatch.setattr(Inspeximus, "route", records_nothing)
    step = run_demo(keep=str(tmp_path / "kept"))["steps"][0]
    monkeypatch.undo()
    held = [r["text"] for r in Inspeximus(path=str(tmp_path / "kept" / "correction.json"), receipts=True).items]
    _control(not any("just to confirm" in t for t in held), "the store holds no restatement at all")
    assert not step["ok"], "PASS for a restatement that no record in the store holds"


# ── step 2: an erasure can be checked ──────────────────────────────────────────────────────────────────

def test_CONFIRMED_a_forget_that_only_reports_fails_step_2(monkeypatch):
    """forget_subject() says it erased one record and touches nothing: the scan and the certificate both object."""
    monkeypatch.setattr(Inspeximus, "forget_subject", lambda self, *a, **k: {"erased": 1, "ids": []})
    step = run_demo()["steps"][1]
    assert not step["ok"] and step["erased"] == 1
    assert step["residue_findings"] > 0 and not step["certificate_valid"]


def test_CONFIRMED_a_delete_without_a_tombstone_fails_step_2(monkeypatch):
    """The row goes, no tombstone is written. Only the certificate can see this; the byte scan is clean."""
    def delete_quietly(self, subject, request_id=None, **kw):
        ids = [r["id"] for r in self.items if subject in json.dumps(r.get("source"))]
        self._items[:] = [r for r in self._items if r["id"] not in ids]
        self._dirty = True
        self.flush()
        return {"erased": len(ids), "ids": ids}

    monkeypatch.setattr(Inspeximus, "forget_subject", delete_quietly)
    step = run_demo()["steps"][1]
    assert not step["ok"]
    assert step["residue_findings"] == 0, "the scan cannot see a missing tombstone"
    assert not step["certificate_valid"], "the certificate is the check that must catch it"


def test_CONFIRMED_secure_delete_off_fails_step_2(monkeypatch):
    """SQLite without secure_delete leaves the erased bytes in free pages. Only the byte scan can see this."""
    import inspeximus.sqlite_store as sqlite_store
    real_connect = sqlite_store.sqlite3.connect

    class _NoSecureDelete:
        def __init__(self, con):
            self._con = con

        def execute(self, sql, *a):
            if "secure_delete" in sql:
                sql = "PRAGMA secure_delete=OFF"
            return self._con.execute(sql, *a)

        def __getattr__(self, name):
            return getattr(self._con, name)

    monkeypatch.setattr(sqlite_store.sqlite3, "connect", lambda *a, **k: _NoSecureDelete(real_connect(*a, **k)))
    step = run_demo()["steps"][1]
    assert not step["ok"]
    assert step["certificate_valid"], "the certificate cannot see unreclaimed bytes"
    assert step["residue_findings"] > 0, "the byte scan is the check that must catch it"


def test_F1_the_demo_certificate_is_signed():
    cert = run_demo()["steps"][1]["certificate"]
    verdict = verify_erasure_certificate(cert)
    assert verdict["checks"]["signed"] is True, verdict["limits"]
    assert cert.get("pubkey"), "an unsigned certificate names no key"


def _forged_certificate(self, request_id=None, **kw):
    """A certificate anyone can write: no key, one sha256, for a record id that never existed."""
    t = {"seq": 0, "memory_id": "0000000000", "ts": 0.0, "request_id": request_id, "prev": "0" * 64}
    t["hash"] = _sha256_hex(_canon(t))
    return {"inspeximus_erasure_certificate": "1.0", "scoped_to": request_id, "request_ids": [request_id],
            "erased_memory_ids": [t["memory_id"]], "count": 1, "tombstones": [t], "pubkey": None,
            "anchor": {"tombstones_tip": t["hash"]}}


def test_F1_a_certificate_forged_without_a_key_does_not_read_valid(monkeypatch):
    monkeypatch.setattr(Inspeximus, "erasure_certificate", _forged_certificate)
    step = run_demo(forget=False)["steps"][1]
    _control(step["erased"] == 0 and step["residue_findings"] > 0, "the forget never ran")
    _control(not step["ok"], "the step still FAILs overall, on `erased` and the byte scan")
    assert not step["certificate_valid"], "'certificate: valid' for an erasure that never ran"


def test_F3_an_erasure_that_takes_an_unrelated_record_with_it_fails_step_2(monkeypatch, tmp_path):
    real = Inspeximus.forget_subject

    def over_erase(self, subject, request_id=None, basis=None, **kw):
        # A matcher too broad for its subject: every active record's own source goes under the one request.
        res = real(self, subject, request_id=request_id, basis=basis, **kw)
        erased = res["erased"]
        for doc in {(r.get("source") or {}).get("doc") for r in self.items if r.get("status") == "active"}:
            if doc:
                erased += real(self, doc, request_id=request_id, basis=basis)["erased"]
        return {**res, "erased": erased}

    monkeypatch.setattr(Inspeximus, "forget_subject", over_erase)
    step = run_demo(keep=str(tmp_path / "kept"))["steps"][1]
    monkeypatch.undo()
    _control(step["erased"] == 2, "two records were erased")
    _control(not any("deploy window" in t for t in _texts(tmp_path / "kept" / "erasure" / "erasure.json")),
             "the unrelated record is gone from the store")
    assert not step["ok"], "PASS for an erasure that also destroyed another record"


def test_F4_a_subject_left_on_disk_under_her_name_fails_step_2(monkeypatch, tmp_path):
    real = Inspeximus.forget_subject

    def redact_instead(self, subject, *a, **k):
        texts = [r["text"] for r in self.items if subject in json.dumps(r.get("source"))]
        res = real(self, subject, *a, **k)
        for t in texts:
            self.remember(t.replace(SUBJECT_VALUE, "[erased]"))
        return res

    monkeypatch.setattr(Inspeximus, "forget_subject", redact_instead)
    step = run_demo(keep=str(tmp_path / "kept"))["steps"][1]
    monkeypatch.undo()
    on_disk = erasure_residue.scan_residue(str(tmp_path / "kept" / "erasure"), ["Jana Novak"])
    _control(not on_disk["ok"] and on_disk["findings"], "'Jana Novak' is in the store's bytes")
    assert not step["ok"], "PASS with the subject's name still in the store"


def test_F7_a_byte_scan_that_read_no_file_fails_step_2(monkeypatch, tmp_path):
    real = erasure_residue.scan_residue
    empty = tmp_path / "nothing-here"
    empty.mkdir()
    monkeypatch.setattr(erasure_residue, "scan_residue", lambda root, values, **k: real(str(empty), values, **k))
    step = run_demo()["steps"][1]
    _control(step["files_scanned"] == 0, "the scan read no file")
    assert not step["ok"], "PASS on a scan that looked at nothing"



_SELF_REPORT_ONLY = """\
import inspeximus.demo as demo

_real = demo._step_erasure


def _self_report_only(root, forget):
    step = _real(root, forget)
    step["ok"] = step["erased"] >= 1          # the certificate and the byte scan dropped from the verdict
    return step


demo._step_erasure = _self_report_only
"""


def test_F8_the_shipped_tests_notice_a_step_2_verdict_that_trusts_the_forget_alone(monkeypatch, tmp_path):
    import subprocess
    import sys

    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    # Premise, in this process: the weakened verdict prints PASS for a forget that erased nothing.
    # Record the REAL step with monkeypatch before the exec: the snippet assigns the weakened step onto the
    # module itself, so recording it afterwards made undo() restore the weakened one, and every later test
    # on this xdist worker ran a step 2 that trusts the forget's own count.
    monkeypatch.setattr(demo, "_step_erasure", demo._step_erasure)
    exec(compile(_SELF_REPORT_ONLY, "self_report_only", "exec"), {})
    monkeypatch.setattr(Inspeximus, "forget_subject", lambda self, *a, **k: {"erased": 1, "ids": []})
    _control(run_demo()["steps"][1]["ok"], "the weakened verdict passes a forget that erased nothing")
    monkeypatch.undo()

    # The shipped file, run against that weakened demo. The two CLI tests start a fresh interpreter that the
    # patch cannot reach, and they do not read step 2's verdict, so they are deselected.
    t = tmp_path / "tests"
    t.mkdir()
    shutil.copy(os.path.join(here, "test_the_demo_can_fail_at_every_step.py"), t)
    (t / "conftest.py").write_text(_SELF_REPORT_ONLY, encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist",
                           "-c", str(tmp_path / "pytest.ini"), "--rootdir", str(tmp_path),
                           "-k", "not command_runs and not json_output", str(t)],
                          cwd=str(tmp_path), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          env={**os.environ, "PYTHONPATH": root})
    _control(" passed" in proc.stdout or " failed" in proc.stdout, "the shipped file ran: " + proc.stdout[-400:]
             + proc.stderr[-400:])
    assert proc.returncode != 0, "the shipped tests stay green on a step 2 that trusts the forget's own count"

# ── step 3: a tamper is caught ─────────────────────────────────────────────────────────────────────────

def test_CONFIRMED_receipts_that_do_not_commit_the_text_fail_step_3(monkeypatch):
    real = Inspeximus._write_commit

    def no_text(rec, retires=()):
        c = dict(real(rec, retires))
        c.pop("immutable_sha256", None)
        c.pop("content_sha256", None)
        return c

    monkeypatch.setattr(Inspeximus, "_write_commit", staticmethod(no_text))
    step = run_demo()["steps"][2]
    assert not step["ok"] and step["edited copy"]["verifies"]


def test_CONFIRMED_without_the_receipt_text_check_the_edited_copy_verifies(monkeypatch):
    """The refusal comes from the receipt comparison and nothing else: drop that one problem and the edit passes."""
    real = Inspeximus.verify_writes

    def without_receipt_check(self, *a, **k):
        _, problems = real(self, *a, **k)
        problems = [p for p in problems if "no longer matches its write receipt" not in p]
        return (not problems, problems)

    monkeypatch.setattr(Inspeximus, "verify_writes", without_receipt_check)
    step = run_demo()["steps"][2]
    assert not step["ok"] and step["edited copy"]["verifies"]


def test_CONFIRMED_the_edit_is_refused_as_a_receipt_mismatch_on_the_edited_record(tmp_path):
    """Control for F6: today the problem does name the edited record; F6 is that nothing requires it."""
    step = run_demo(keep=str(tmp_path / "kept"))["steps"][2]
    edited = [r for r in Inspeximus(path=str(tmp_path / "kept" / "edited_copy" / "store.json"),
                                    receipts=True).items if "oslo" in r["text"]]
    assert len(edited) == 1
    assert step["edited copy"]["problems"] == [
        f"memory {edited[0]['id']}: its TEXT or KEY no longer matches its write receipt (edited after write)"]


def test_F6_a_refusal_that_names_the_wrong_record_fails_step_3(monkeypatch):
    real = Inspeximus.verify_writes

    def wrong_record(self, *a, **k):
        ok, problems = real(self, *a, **k)
        return ok, [("memory 0000000000:" + p.split(":", 1)[1]) if p.startswith("memory ") else p
                    for p in problems]

    monkeypatch.setattr(Inspeximus, "verify_writes", wrong_record)
    step = run_demo()["steps"][2]
    _control(step["edited copy"]["problems"] and
             all(p.startswith("memory 0000000000:") for p in step["edited copy"]["problems"]),
             "the refusal names a record id that is not in the store")
    assert not step["ok"], "PASS with the refusal pinned on a record nobody edited"


def _rewrite_receipts_without_a_key(store_path):
    """What an editor WITHOUT any key does after editing the text: recompute the two text hashes of every
    receipt from the records on disk, drop any signature (a chain with none is accepted unless one is
    demanded), and re-link the chain. sha256 is public; nothing here is secret."""
    records = {r["id"]: r for r in Inspeximus(path=store_path, receipts=True).items}
    sidecar = store_path + ".receipts.json"
    with open(sidecar, encoding="utf-8") as fh:
        chain = json.load(fh)
    prev = "0" * 64
    for r in chain:
        rec = records[r["memory_id"]]
        imm = {"text": rec["text"], "key": rec.get("key")}
        con = {"text": rec["text"], "key": rec.get("key"), "mtype": rec.get("mtype")}
        if rec.get("nonce"):
            imm["nonce"] = con["nonce"] = rec["nonce"]
        r["commit"]["immutable_sha256"] = _sha256_hex(_canon(imm))
        r["commit"]["content_sha256"] = _sha256_hex(_canon(con))
        r.pop("sig", None)
        r.pop("pubkey", None)
        r["prev"] = prev
        r["hash"] = _sha256_hex(_canon(Inspeximus._chain_core(r, "write")))
        prev = r["hash"]
    with open(sidecar, "w", encoding="utf-8") as fh:
        json.dump(chain, fh, indent=2)


def test_F2_an_edit_that_also_rewrites_the_receipts_is_refused_by_step_3(monkeypatch):
    real = Inspeximus.verify_writes
    rewritten = []

    def after_the_sidecar_is_rewritten(self, *a, **k):
        p = str(self.path)
        if "edited" in p:
            _rewrite_receipts_without_a_key(p)
            self = Inspeximus(path=p, receipts=True)
            rewritten.append(any("is oslo" in r["text"] for r in self.items))
        return real(self, *a, **k)

    monkeypatch.setattr(Inspeximus, "verify_writes", after_the_sidecar_is_rewritten)
    step = run_demo()["steps"][2]
    _control(rewritten == [True], "the edited copy, with the edit in it, had its sidecar rewritten once")
    _control(step["untouched copy"]["verifies"], "the untouched copy still verifies")
    assert not step["edited copy"]["verifies"], "an edit behind the library's back verifies"


def test_F2_the_demo_stores_are_signed(tmp_path):
    run_demo(keep=str(tmp_path / "kept"))
    ok, problems = Inspeximus(path=str(tmp_path / "kept" / "untouched_copy" / "store.json"),
                              receipts=True).verify_writes(require_signed=True)
    assert ok, problems


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=(
    "N1, library, a documented limit rather than a demo defect (core.py, verify_writes, 'THE HEAD KEPT "
    "OUTSIDE THE STORE'): the head is bound to the chain's first receipt, so a keyless rewrite that starts "
    "at receipt 0 makes the head look like it belongs to another store and the check is skipped without a "
    "word. Listed because it means the F2 fix has to be a key, not verifying at the original path."))
def test_N1_the_head_kept_outside_does_not_cover_a_rewrite_from_the_first_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keys"))
    d = tmp_path / "tamper"
    d.mkdir()
    p = str(d / "store.json")
    m = Inspeximus(path=p, receipts=True)          # the demo's store, left at the path it was written to
    m.remember(f"deploy region is {NEW}", key=KEY, object=NEW)
    m.remember("the deploy window is Tuesday")
    del m
    _control(Inspeximus(path=p, receipts=True).read_head(), "the head exists outside the store")
    with open(p, "rb") as fh:
        raw = fh.read()
    with open(p, "wb") as fh:
        fh.write(raw.replace(f"is {NEW}".encode(), b"is oslo", 1))
    _control(not Inspeximus(path=p, receipts=True).verify_writes()[0], "the plain edit is refused")
    _rewrite_receipts_without_a_key(p)
    m = Inspeximus(path=p, receipts=True)
    _control(any("is oslo" in r["text"] for r in m.items), "the edit is still in the store")
    ok, problems = m.verify_writes()
    assert not ok, "the head for this exact path records a different chain, and nothing says so"
