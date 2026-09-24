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

from inspeximus.demo import OLD, NEW, run_demo

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
    assert NEW in step["recall_answers"]


def test_CONTROL_without_the_forget_the_subject_is_still_on_disk():
    step = run_demo(forget=False)["steps"][1]
    assert not step["ok"]
    assert step["residue_findings"] > 0, "the byte scan must find the subject when nothing was erased"
    assert not step["certificate_valid"], "a certificate for an erasure that never ran must not verify"


def test_the_erasure_leaves_no_trace_and_the_certificate_verifies():
    step = run_demo()["steps"][1]
    assert step["erased"] == 1 and step["certificate_valid"] and step["residue_findings"] == 0
    assert step["files_scanned"] > 0, "CONTROL: a scan that read no file finds nothing by construction"


def test_CONTROL_without_the_edit_both_copies_verify():
    step = run_demo(tamper=False)["steps"][2]
    assert not step["ok"]
    assert step["untouched copy"]["verifies"] and step["edited copy"]["verifies"]


def test_the_edited_copy_is_refused_and_the_record_is_named():
    step = run_demo()["steps"][2]
    assert step["untouched copy"]["verifies"]
    refused = step["edited copy"]
    assert not refused["verifies"] and any("memory " in p for p in refused["problems"])


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
