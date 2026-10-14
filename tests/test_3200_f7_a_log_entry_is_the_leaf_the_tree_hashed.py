"""3.20, F7 (Claude Security scan, LOW, repository tooling): the published verifier ties each entry to its hashed leaf.

THE DEFECT. The verify.py that tools/publish_static_log.py writes took `leaf_hash` from log.jsonl and checked each
payload against `payload_sha256` from the same line. It never opened entries/<n>.leaf.json, so a log.jsonl whose
record differed from the leaf the tree hashed passed, and so did a payload matched against the edited record.

WHAT IS PINNED HERE. For every line with a leaf file, the leaf bytes must hash to `leaf_hash`, the record on the line
must be the leaf they encode, and the payload check reads the record from the leaf. A line with no leaf file (a log
published without the service key) is reported as not checked, never passed silently.

SCOPE: this is the generator. The static copy already published (transparency/ in this repository and the hosted
copy) keeps its older verify.py until it is regenerated, which is a separate step.
"""
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
pytest.importorskip("cryptography")
from test_a_log_a_stranger_can_check_without_us import _publisher, _service, _site  # noqa: E402


def _verify(site):
    r = subprocess.run([sys.executable, "verify.py"], cwd=str(site), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _rewrite_line(site, index, fn):
    path = site / "log.jsonl"
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    fn(rows[index])
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8", newline="\n")


def test_control_an_honest_log_verifies_and_every_entry_is_tied(tmp_path):
    site, _ = _site(tmp_path, 3)
    code, out = _verify(site)
    assert code == 0 and "checked: 4 entries are the leaves the tree hashed." in out, out


def test_a_record_in_log_jsonl_that_differs_from_its_leaf_fails(tmp_path):
    site, _ = _site(tmp_path, 3)
    _rewrite_line(site, 2, lambda r: r["entry"].update(subject="s:forged"))
    code, out = _verify(site)
    assert code == 1 and "is not the leaf the tree hashed" in out, out


def test_a_leaf_file_that_does_not_hash_to_its_leaf_hash_fails(tmp_path):
    site, _ = _site(tmp_path, 3)
    leaf = site / "entries" / "2.leaf.json"
    leaf.write_bytes(leaf.read_bytes().replace(b"s:1", b"s:9"))
    code, out = _verify(site)
    assert code == 1 and "does not hash to the leaf_hash" in out, out


def test_a_payload_is_checked_against_the_leaf_not_the_line(tmp_path):
    site, _ = _site(tmp_path, 3)
    import hashlib
    forged = hashlib.sha256(b"forged").hexdigest()      # a line whose digest the forged text really hashes to
    payloads = {"by_digest": {forged: "forged"}}
    _rewrite_line(site, 2, lambda r: r["entry"].update(payload_sha256=forged))
    (site / "payloads.json").write_text(json.dumps(payloads), encoding="utf-8")
    code, out = _verify(site)
    assert code == 1, "a payload matched against an edited line passed: " + out


def test_a_line_with_no_leaf_file_is_reported_as_not_checked(tmp_path):
    site, _ = _site(tmp_path, 3)
    os.remove(str(site / "entries" / "3.leaf.json"))
    code, out = _verify(site)
    assert code == 0, out                                # the tree itself still verifies
    assert "not checked: 1 entries have no leaf file" in out and "checked: 3 entries are the leaves" in out, out
