"""3.20, F8 (Claude Security scan, LOW): a residue report's fingerprint cannot be reversed by trying candidates.

THE DEFECT. `erasure_residue._fingerprint(v)` was the first 48 bits of an unsalted sha256 of the value, and its
docstring called it non-reversible. An erased value is usually an e-mail address, a phone number or a name, so anyone
holding the report recovered it by hashing a list of candidates: three candidates were enough for
"alice@example.com" (AUDIT-A low_repro.py, v3.19.1). A residue certificate exists to be handed to a third party,
and `forget_subject`'s `residue_in_store` carried the same hash of whole erased records.

WHAT IS PINNED HERE. Each report fingerprints under its own random HMAC key, never stored. Inside one report the
fingerprints still correlate the findings with each other and with the certificate's `values_searched`; outside it
they match nothing, not even the same value in the next report. A scan_residue finding names its value by
`value_index`, its position in the caller's list, which is how the caller (who knows the values) reads it.
"""
import hashlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from inspeximus import erasure_residue as er  # noqa: E402

ALICE = "alice@example.com"
CANDIDATES = ["bob@example.com", "carol@example.com", ALICE]


def _tree(tmp_path):
    d = tmp_path / "dsar"
    d.mkdir()
    (d / "trace.jsonl").write_text('{"to": "%s"}\n{"cc": "%s"}\n' % (ALICE, ALICE), encoding="utf-8")
    (d / "other.log").write_text("bob@example.com wrote\n", encoding="utf-8")
    return str(d)


def _unsalted(v):
    return hashlib.sha256(v.encode("utf-8")).hexdigest()[:12]


def test_a_candidate_list_does_not_recover_the_value(tmp_path):
    rep = er.scan_residue(_tree(tmp_path), [ALICE])
    fps = {f["fingerprint"] for f in rep["findings"]}
    assert fps, "CONTROL: the scan found the value"
    assert not fps & {_unsalted(c) for c in CANDIDATES}, "the fingerprint is the unsalted sha256 of the value"


def test_the_same_value_gets_another_fingerprint_in_the_next_report(tmp_path):
    root = _tree(tmp_path)
    a = {f["fingerprint"] for f in er.scan_residue(root, [ALICE])["findings"]}
    b = {f["fingerprint"] for f in er.scan_residue(root, [ALICE])["findings"]}
    assert len(a) == 1 and len(b) == 1 and a != b


def test_inside_one_report_the_fingerprint_correlates_and_value_index_names_the_value(tmp_path):
    rep = er.scan_residue(_tree(tmp_path), ["nobody@example.com", ALICE, "bob@example.com"])
    alice = [f for f in rep["findings"] if f["value_index"] == 1]
    bob = [f for f in rep["findings"] if f["value_index"] == 2]
    assert alice and bob, rep["findings"]
    assert len({f["fingerprint"] for f in alice}) == 1, "one value, one fingerprint inside the report"
    assert alice[0]["fingerprint"] != bob[0]["fingerprint"]


def test_a_certificate_lists_the_same_fingerprints_its_findings_carry(tmp_path):
    cert = er.residue_certificate(_tree(tmp_path), ["nobody@example.com", ALICE])
    searched = {v["value_index"]: v["fingerprint"] for v in cert["values_searched"]}
    assert set(searched) == {0, 1}
    assert {f["fingerprint"] for f in cert["findings"]} == {searched[1]}
    assert searched[1] != _unsalted(ALICE)


def test_the_in_store_check_at_erasure_time_is_keyed_too():
    rep = er.scan_records([{"id": "r1", "text": "summary: write to %s" % ALICE}], [ALICE])
    assert rep["findings"], "CONTROL: the surviving record is found"
    assert rep["findings"][0]["fingerprint"] != _unsalted(ALICE)
