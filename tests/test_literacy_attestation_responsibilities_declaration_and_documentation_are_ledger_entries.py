"""EU AI Act Art. 4 (AI literacy), Art. 5 (prohibited practices), Art. 25 (responsibilities along the
value chain), Art. 43, 47 and 48 (conformity assessment, EU declaration, CE marking) and Art. 18
(documentation keeping) as ledger evidence.

Art. 4 as amended asks for measures that support literacy, taking listed factors into account, and
guarantees no level for any person, so the record is the measure and the register never scores.
Art. 5(1) lists ten classes (with (ba) and (bb) from the amendment); an attestation is per class,
dated, signed, and the register names the classes with none. Art. 25(1) says why a party became the
provider, 25(2) what the initial provider hands over unless the system was specified not to become
high-risk, 25(4) the written agreement. Annex V lists eight items every declaration carries; Art. 43
picks the procedure, and Annex VII needs a notified body whose number follows the CE marking (48(4)).
Art. 18(1)(a) to (e) names five documents kept ten years from placing on the market; (a) and (e) have
no not-applicable case, (c) and (d) do when no notified body was involved.

Controls: a fresh store reads CAPABILITY on all five rows and EVIDENCE after one artifact each; a
scored literacy record, a not_applicable attestation without its basis, an agreement where nobody is
the provider, the opt-out beside cooperation items, an Annex VII declaration without a notified body,
a CE number that differs from the assessing body's, a retention statement missing the technical
documentation, and a notified-body row that is neither present nor explained are each refused; the
read-only registers and the declaration document move no row; the chain verifies with all five kinds.
"""
from __future__ import annotations

import os
import time

import pytest

from inspeximus import Inspeximus
from inspeximus.actions import (DOCUMENTATION_RETENTION_YEARS, PROHIBITED_PRACTICES, RETENTION_DOCUMENTS,
                                ActionLedger)
from inspeximus.coverage import CAPABILITY, EVIDENCE, coverage
from inspeximus.deployer import deployer_report

cryptography = pytest.importorskip("cryptography")

SHA = "a" * 64
YEAR = 365.25 * 86400.0


def _fresh(tmp_path):
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=os.urandom(32).hex())
    return m, ActionLedger(m, actor="agent")


def _row(rep, oid):
    return next(r for r in rep["rows"] if r["id"] == oid)


def _declaration(led, procedure="annex_vi_internal_control", **kw):
    args = dict(actor="ops", system_name="Assistant", system_type="chat", system_reference="asst-1",
                provider_name="Acme", provider_address="Street 1", conformity_procedure=procedure,
                place="Bratislava", signer_name="R. D.", signer_function="CEO", signed_for="Acme",
                annex_iv_sha256=SHA)
    args.update(kw)
    return led.record_declaration(**args)


# ------------------------------------------------------------------ Art. 4
def test_a_literacy_measure_is_recorded_with_its_considerations_and_never_a_score(tmp_path):
    m, led = _fresh(tmp_path)
    assert _row(coverage(m), "aia-4")["state"] == CAPABILITY
    e = led.record_literacy("ops", "training", "staff", "one hour on what recall returns and what it does not",
                            system="asst-1", context="customer support", considered=["context_of_use", "experience"],
                            persons_affected=["customers"])
    assert e["kind"] == "literacy" and e["action"] == "literacy:training" and "sig" in e
    assert e["considered"] == ["context_of_use", "experience"]
    assert not any(k in e for k in ("score", "level", "grade")), "Art. 4 guarantees no level for any individual"
    reg = led.literacy_register()
    assert reg["measures"] == 1 and reg["by_audience"] == {"staff": 1} and reg["by_measure"] == {"training": 1}
    row = _row(coverage(m), "aia-4")
    assert row["state"] == EVIDENCE and row["count"] == 1
    rep = deployer_report(m, operator={"literacy_programme": "quarterly"}, ledger=led)
    assert rep["sections"]["1_deployer_duties_art_26"]["4_ai_literacy"]["evidence"]["measures"] == 1
    assert rep["sections"]["1_deployer_duties_art_26"]["4_ai_literacy"]["operator"]["literacy_programme"] == "quarterly"


def test_a_literacy_record_is_refused_outside_the_article_vocabulary(tmp_path):
    m, led = _fresh(tmp_path)
    with pytest.raises(ValueError, match="measure must be"):
        led.record_literacy("ops", "exam", "staff", "x")
    with pytest.raises(ValueError, match="audience must be"):
        led.record_literacy("ops", "training", "customers", "x")
    with pytest.raises(ValueError, match="considered must be"):
        led.record_literacy("ops", "training", "staff", "x", considered=["iq"])
    with pytest.raises(ValueError, match="description"):
        led.record_literacy("ops", "training", "staff", "")
    with pytest.raises(ValueError):
        led.record_literacy("ops", "training", "staff", "x", refers_to=[99])
    assert _row(coverage(m), "aia-4")["state"] == CAPABILITY, "no refused call left a row behind"


# ------------------------------------------------------------------ Art. 5
def test_an_attestation_is_per_class_and_the_register_names_the_classes_with_none(tmp_path):
    m, led = _fresh(tmp_path)
    assert _row(coverage(m), "aia-5")["state"] == CAPABILITY
    assert set(PROHIBITED_PRACTICES) == {"a", "b", "ba", "bb", "c", "d", "e", "f", "g", "h"}
    e = led.record_attestation("ops", "f", "not_applicable", basis="no emotion inference component; text only")
    assert e["kind"] == "attestation" and e["action"] == "attestation:f" and "sig" in e
    assert e["practice_text"] == PROHIBITED_PRACTICES["f"]
    led.record_attestation("ops", "a", "not_used")
    reg = led.attestation_register()
    assert reg["attested"] == 2 and reg["missing"] == ["b", "ba", "bb", "c", "d", "e", "g", "h"]
    f = next(r for r in reg["rows"] if r["practice"] == "f")
    assert f["statement"] == "not_applicable" and f["basis"].startswith("no emotion")
    row = _row(coverage(m), "aia-5")
    assert row["state"] == EVIDENCE and row["count"] == 2 and "2 of 10" in row["detail"]
    # a later attestation on the same class supersedes in the register, and the entry count still grows
    led.record_attestation("ops", "a", "not_applicable", basis="no personalised output")
    reg = led.attestation_register()
    assert reg["attested"] == 2 and next(r for r in reg["rows"] if r["practice"] == "a")["statement"] == "not_applicable"
    assert _row(coverage(m), "aia-5")["count"] == 2, "the row counts classes, not restatements"


def test_a_not_applicable_attestation_needs_its_basis(tmp_path):
    m, led = _fresh(tmp_path)
    with pytest.raises(ValueError, match="basis"):
        led.record_attestation("ops", "h", "not_applicable")
    with pytest.raises(ValueError, match="practice must be"):
        led.record_attestation("ops", "i", "not_used")
    with pytest.raises(ValueError, match="statement must be"):
        led.record_attestation("ops", "a", "compliant")
    assert led.attestation_register()["attested"] == 0


# ------------------------------------------------------------------ Art. 25
def test_responsibilities_name_the_parties_the_trigger_and_the_cooperation_items(tmp_path):
    m, led = _fresh(tmp_path)
    assert _row(coverage(m), "aia-25")["state"] == CAPABILITY
    e = led.record_responsibilities(
        "ops", "MSA-2026-07", agreement_sha256=SHA, trigger="substantial_modification",
        parties=[{"party": "Beta", "role": "new_provider", "obligations": ["Art. 16", "Art. 17"]},
                 {"party": "Acme", "role": "initial_provider", "obligations": ["Art. 25(2)"]},
                 {"party": "Gamma", "role": "third_party_supplier", "obligations": ["Art. 25(4) specification"]}],
        cooperation={"technical_documentation": "annex-iv-v3", "known_limitations_and_failure_modes": "limits.md"})
    assert e["kind"] == "responsibilities" and "sig" in e
    assert [p["role"] for p in e["parties"]] == ["new_provider", "initial_provider", "third_party_supplier"]
    assert e["cooperation"]["technical_documentation"] == "annex-iv-v3"
    reg = led.responsibilities_register()
    assert reg["agreements"] == 1 and reg["entries"][0]["trigger"] == "substantial_modification"
    row = _row(coverage(m), "aia-25")
    assert row["state"] == EVIDENCE and row["count"] == 1


def test_an_agreement_where_nobody_is_the_provider_is_refused_and_so_is_the_opt_out_beside_cooperation(tmp_path):
    m, led = _fresh(tmp_path)
    with pytest.raises(ValueError, match="provider's obligations"):
        led.record_responsibilities("ops", "MSA", [{"party": "Beta", "role": "deployer"}])
    with pytest.raises(ValueError, match="role"):
        led.record_responsibilities("ops", "MSA", [{"party": "Beta", "role": "vendor"}])
    with pytest.raises(ValueError, match="trigger must be"):
        led.record_responsibilities("ops", "MSA", [{"party": "Beta", "role": "provider"}], trigger="bought_it")
    with pytest.raises(ValueError, match="cooperation items"):
        led.record_responsibilities("ops", "MSA", [{"party": "Beta", "role": "provider"}], cooperation={"source_code": "x"})
    with pytest.raises(ValueError, match="exclude each other"):
        led.record_responsibilities("ops", "MSA", [{"party": "Beta", "role": "provider"}],
                                    cooperation={"technical_documentation": "x"}, not_to_be_changed_into_high_risk=True)
    with pytest.raises(ValueError, match="at least one party"):
        led.record_responsibilities("ops", "MSA", [])
    assert _row(coverage(m), "aia-25")["state"] == CAPABILITY


# ------------------------------------------------------------------ Art. 43, 47, 48
def test_a_declaration_carries_every_annex_v_item_and_renders_as_one_document(tmp_path):
    m, led = _fresh(tmp_path)
    assert _row(coverage(m), "aia-43")["state"] == CAPABILITY
    e = _declaration(led, personal_data=True, harmonised_standards=["EN 1234:2025"], other_union_law=["Regulation (EU) 2017/745"],
                     ce_marking={"digital_access": "https://example.test/ce", "affixed_to": "the interface"})
    assert e["kind"] == "declaration" and "sig" in e and e["sole_responsibility"] is True
    doc = led.declaration_document(e["seq"])
    assert doc["kind"] == "inspeximus.eu_declaration_of_conformity/1"
    items = doc["annex_v"]
    assert list(items) == ["1_system", "2_provider", "3_sole_responsibility", "4_conformity", "5_personal_data",
                           "6_standards", "7_notified_body", "8_signature"]
    assert items["1_system"]["reference"] == "asst-1" and items["2_provider"]["name"] == "Acme"
    assert "2017/745" in items["4_conformity"] and "2016/679" in items["5_personal_data"]
    assert items["6_standards"]["harmonised_standards"] == ["EN 1234:2025"]
    assert items["7_notified_body"].startswith("not applicable")
    assert items["8_signature"]["name"] == "R. D." and items["8_signature"]["place"] == "Bratislava"
    assert doc["technical_documentation_sha256"] == SHA and doc["hash"] == e["hash"] and doc["signed"]
    assert doc["ce_marking"]["digital_access"] == "https://example.test/ce"
    row = _row(coverage(m), "aia-43")
    assert row["state"] == EVIDENCE and row["count"] == 1
    assert "partial" in row, "the assessment itself stays the provider's; the row says so"


def test_an_annex_vii_declaration_needs_its_notified_body_and_the_ce_number_must_match(tmp_path):
    m, led = _fresh(tmp_path)
    with pytest.raises(ValueError, match="notified body"):
        _declaration(led, procedure="annex_vii_notified_body")
    with pytest.raises(ValueError, match="only for an Annex VII"):
        _declaration(led, notified_body={"name": "NB", "id": "0123"})
    with pytest.raises(ValueError, match="Art. 48\\(4\\)"):
        _declaration(led, procedure="annex_vii_notified_body", notified_body={"name": "NB", "id": "0123", "certificate": "C-1"},
                     ce_marking={"notified_body_id": "9999"})
    with pytest.raises(ValueError, match="conformity_procedure must be"):
        _declaration(led, procedure="self_declared")
    with pytest.raises(ValueError, match="Annex V items"):
        _declaration(led, signer_name="")
    with pytest.raises(ValueError, match="64-hex"):
        _declaration(led, annex_iv_sha256="abc")
    assert _row(coverage(m), "aia-43")["state"] == CAPABILITY
    e = _declaration(led, procedure="annex_vii_notified_body", notified_body={"name": "NB", "id": "0123", "certificate": "C-1"},
                     ce_marking={"notified_body_id": "0123"})
    doc = led.declaration_document(e["seq"])
    assert doc["annex_v"]["7_notified_body"] == {"name": "NB", "id": "0123", "procedure": "Annex VII", "certificate": "C-1"}
    with pytest.raises(ValueError, match="not a declaration"):
        led.record("tool:x", inputs={}, actor="agent")
        led.declaration_document(e["seq"] + 1)


# ------------------------------------------------------------------ Art. 18
def test_documentation_retention_names_the_five_documents_and_the_ten_year_end(tmp_path):
    m, led = _fresh(tmp_path)
    assert _row(coverage(m), "aia-18")["state"] == CAPABILITY
    d = _declaration(led)
    placed = time.time() - 2 * YEAR
    e = led.attest_documentation_retention(
        "ops", placed, declaration_seq=d["seq"],
        documents=[{"kind": "technical_documentation", "sha256": SHA},
                   {"kind": "eu_declaration_of_conformity", "ref": f"seq:{d['seq']}"},
                   {"kind": "notified_body_changes", "present": False, "not_applicable_reason": "Annex VI, no notified body"},
                   {"kind": "notified_body_decisions", "present": False, "not_applicable_reason": "Annex VI, no notified body"}])
    assert e["kind"] == "documentation" and "sig" in e
    assert set(e["documents"]) == set(RETENTION_DOCUMENTS)
    assert e["retention_years"] == DOCUMENTATION_RETENTION_YEARS == 10
    assert abs(e["retention_end_ts"] - (placed + 10 * YEAR)) < 1.0
    assert e["within_period"] is True and 1.9 < e["years_elapsed"] < 2.1
    assert e["gaps"] == ["quality_management_system"], "an absent QMS is a gap on the record, not a refusal"
    assert e["declaration"]["seq"] == d["seq"] and e["declaration"]["hash"] == d["hash"]
    row = _row(coverage(m), "aia-18")
    assert row["state"] == EVIDENCE and row["count"] == 1
    # after the period the statement says so
    late = led.attest_documentation_retention("ops", time.time() - 11 * YEAR, documents=[
        {"kind": "technical_documentation", "sha256": SHA}, {"kind": "eu_declaration_of_conformity", "sha256": SHA},
        {"kind": "notified_body_changes", "present": False, "not_applicable_reason": "none"},
        {"kind": "notified_body_decisions", "present": False, "not_applicable_reason": "none"}])
    assert late["within_period"] is False


def test_a_retention_statement_without_the_required_documents_is_refused(tmp_path):
    m, led = _fresh(tmp_path)
    ok = [{"kind": "technical_documentation", "sha256": SHA}, {"kind": "eu_declaration_of_conformity", "sha256": SHA},
          {"kind": "notified_body_changes", "present": False, "not_applicable_reason": "none"},
          {"kind": "notified_body_decisions", "present": False, "not_applicable_reason": "none"}]
    with pytest.raises(ValueError, match="technical_documentation must be present"):
        led.attest_documentation_retention("ops", time.time(), ok[1:])
    with pytest.raises(ValueError, match="eu_declaration_of_conformity must be present"):
        led.attest_documentation_retention("ops", time.time(), [ok[0]] + ok[2:])
    with pytest.raises(ValueError, match="notified_body_changes is present, or not applicable"):
        led.attest_documentation_retention("ops", time.time(), ok[:2] + [ok[3]])
    with pytest.raises(ValueError, match="needs a sha256 or a ref"):
        led.attest_documentation_retention("ops", time.time(), [{"kind": "technical_documentation"}] + ok[1:])
    with pytest.raises(ValueError, match="64 hex"):
        led.attest_documentation_retention("ops", time.time(), [{"kind": "technical_documentation", "sha256": "xyz"}] + ok[1:])
    with pytest.raises(ValueError, match="kind in"):
        led.attest_documentation_retention("ops", time.time(), ok + [{"kind": "source_code", "sha256": SHA}])
    led.record("tool:x", inputs={}, actor="agent")
    with pytest.raises(ValueError, match="declaration entry"):
        led.attest_documentation_retention("ops", time.time(), ok, declaration_seq=0)
    assert _row(coverage(m), "aia-18")["state"] == CAPABILITY


# ------------------------------------------------------------------ the chain and the reports
def test_the_chain_verifies_with_all_five_kinds_and_the_reports_count_them(tmp_path):
    m, led = _fresh(tmp_path)
    led.record_literacy("ops", "briefing", "contractor", "the retirement rule")
    for c in PROHIBITED_PRACTICES:
        led.record_attestation("ops", c, "not_used")
    led.record_responsibilities("ops", "MSA", [{"party": "Acme", "role": "provider"}])
    d = _declaration(led)
    led.attest_documentation_retention("ops", time.time() - YEAR, declaration_seq=d["seq"], documents=[
        {"kind": "technical_documentation", "sha256": SHA}, {"kind": "eu_declaration_of_conformity", "ref": "seq:%d" % d["seq"]},
        {"kind": "quality_management_system", "ref": "qms/2026"},
        {"kind": "notified_body_changes", "present": False, "not_applicable_reason": "none"},
        {"kind": "notified_body_decisions", "present": False, "not_applicable_reason": "none"}])
    ok, problems = led.verify()
    assert ok and problems == []
    kinds = {e["kind"] for e in led.entries()}
    assert {"literacy", "attestation", "responsibilities", "declaration", "documentation"} <= kinds
    pm = led.post_market_report(since=0)
    assert pm["literacy_measures"] == 1 and pm["attestations"] == {"entries": 10, "classes_attested": 10}
    assert pm["responsibilities_agreements"] == 1 and pm["declarations"] == 1 and pm["documentation_attestations"] == 1
    assert pm["requirements"]["Art. 4"] == "literacy_measures" and pm["requirements"]["Art. 18"] == "documentation_attestations"
    cov = coverage(m)
    assert cov["counts"]["NOT COVERED"] == 3, "gdpr-13, gdpr-21 and gdpr-28 remain; every Act row is covered"
    assert all(_row(cov, r)["state"] == EVIDENCE for r in ("aia-4", "aia-5", "aia-18", "aia-25", "aia-43"))
    # the registers and the document are read-only: no row moved
    before = [e["hash"] for e in led.entries()]
    led.literacy_register(); led.attestation_register(); led.responsibilities_register(); led.declaration_document(d["seq"])
    assert [e["hash"] for e in led.entries()] == before


def test_record_refuses_an_unknown_kind_and_accepts_the_five_new_ones(tmp_path):
    m, led = _fresh(tmp_path)
    with pytest.raises(ValueError, match="kind must be"):
        led.record("x", inputs={}, actor="agent", kind="conformity")
    for k in ("literacy", "attestation", "responsibilities", "declaration", "documentation"):
        assert led.record("x", inputs={}, actor="agent", kind=k)["kind"] == k
