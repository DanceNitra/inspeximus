"""GDPR Art. 13 and 14 (information to the subject), Art. 20 (portability), Art. 21 (objection) and
Art. 28 (processor obligations) as ledger evidence, and the objection as a store behaviour.

Art. 13(1) and (2) list what a subject is told when data is collected from them; Art. 14 adds the
source and a deadline when it is obtained elsewhere. The record carries the items given and names the
items missing rather than judging the notice. Art. 20(1) asks for a structured, commonly used,
machine-readable format, so the export carries a versioned format and says which records the subject
provided. Art. 21(1) lets the subject object and the controller continue only on compelling legitimate
grounds; 21(2) objection to direct marketing is absolute. So a standing objection withholds the
subject's records from every recall on the STORE, including a record written after it, and an override
needs its grounds and is refused for direct marketing. Art. 28(2) and (3) put a processor under the
controller's written instructions and its sub-processors under written authorisation.

Controls: a fresh store reads CAPABILITY on the four rows and EVIDENCE after one artifact each; a
second subject sharing the canonical host is NOT withheld by the first subject's objection (the
first version of the filter withheld Bob for Alice, and this is the test that caught it); an Art. 14
notice without its source, an item outside the article, an override without grounds, an override of a
direct-marketing objection, a processor without a controller, and a sub-processor without an
authorisation are each refused; the export under Art. 15 is byte-identical in shape to before.
"""
from __future__ import annotations

import os

import pytest

from inspeximus import Inspeximus
from inspeximus.actions import NOTICE_ITEMS, ActionLedger
from inspeximus.coverage import CAPABILITY, EVIDENCE, coverage
from inspeximus.subject_rights import EXPORT_FORMAT, export_subject, record_objection, resolve_objection

cryptography = pytest.importorskip("cryptography")

SHA = "a" * 64


def _fresh(tmp_path):
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=os.urandom(32).hex())
    return m, ActionLedger(m, actor="agent")


def _row(rep, oid):
    return next(r for r in rep["rows"] if r["id"] == oid)


def _texts(m, q="dashboard preference"):
    return sorted(r["text"][:5] for r in m.recall(q, k=10))


# ------------------------------------------------------------------ Art. 13, 14
def test_a_notice_records_the_items_given_and_names_the_items_missing(tmp_path):
    m, led = _fresh(tmp_path)
    assert _row(coverage(m), "gdpr-13")["state"] == CAPABILITY
    e = led.record_notice("support", "crm/alice", "email", ["controller_identity", "purposes_and_legal_basis", "rights"],
                          text_sha256=SHA, request_id="N-1")
    assert e["kind"] == "notice" and e["action"] == "notice:art13" and "sig" in e
    assert e["items"] == ["controller_identity", "purposes_and_legal_basis", "rights"]
    assert set(e["missing"]) == set(NOTICE_ITEMS) - set(e["items"])
    assert e["timing"] == "at_collection" and e["text_sha256"] == SHA
    reg = led.notice_register()
    assert reg["subjects"] == 1 and reg["incomplete"] == ["crm/alice"]
    row = _row(coverage(m), "gdpr-13")
    assert row["state"] == EVIDENCE and row["count"] == 1
    # a complete Art. 14 notice: source and timing required, the two extra items allowed
    e14 = led.record_notice("support", "crm/bob", "letter", list(NOTICE_ITEMS) + ["data_categories", "data_source"],
                            article=14, source="public register", timing="within_one_month")
    assert e14["missing"] == [] and e14["action"] == "notice:art14"
    assert led.notice_register()["incomplete"] == ["crm/alice"]


def test_a_notice_outside_the_article_is_refused(tmp_path):
    m, led = _fresh(tmp_path)
    with pytest.raises(ValueError, match="source"):
        led.record_notice("support", "crm/bob", "letter", ["rights"], article=14, timing="within_one_month")
    with pytest.raises(ValueError, match="timing"):
        led.record_notice("support", "crm/bob", "letter", ["rights"], article=14, source="register")
    with pytest.raises(ValueError, match="items must be"):
        led.record_notice("support", "crm/alice", "email", ["data_source"])          # an Art. 14 item on Art. 13
    with pytest.raises(ValueError, match="items must be"):
        led.record_notice("support", "crm/alice", "email", ["cookie_banner"])
    with pytest.raises(ValueError, match="channel"):
        led.record_notice("support", "crm/alice", "carrier pigeon", ["rights"])
    with pytest.raises(ValueError, match="64-hex"):
        led.record_notice("support", "crm/alice", "email", ["rights"], text_sha256="abc")
    with pytest.raises(ValueError, match="article must be"):
        led.record_notice("support", "crm/alice", "email", ["rights"], article=15)
    assert _row(coverage(m), "gdpr-13")["state"] == CAPABILITY


# ------------------------------------------------------------------ Art. 20
def test_a_portability_export_is_labelled_versioned_and_flags_what_the_subject_provided(tmp_path):
    m, led = _fresh(tmp_path)
    assert _row(coverage(m), "gdpr-20")["state"] == CAPABILITY
    a = m.remember("Alice prefers the blue dashboard", key="alice-pref", source={"doc": "crm/alice"})
    m.remember("Summary: Alice likes blue", derived_from=[a], source={"doc": "summariser"})
    plain = export_subject(m, "crm/alice", ledger=led)
    assert "response_to" not in plain and "format" not in plain and "portable" not in plain["records"][0]
    assert _row(coverage(m), "gdpr-20")["state"] == CAPABILITY, "an Art. 15 export is not the Art. 20 response"
    port = export_subject(m, "crm/alice", ledger=led, basis="portability", request_id="P-1")
    assert port["response_to"] == "GDPR Art. 20" and port["format"] == EXPORT_FORMAT
    assert port["format"]["version"] == 1 and port["format"]["media_type"] == "application/json"
    by_why = {r["why"]: r["portable"] for r in port["records"]}
    assert by_why == {"direct": True, "inherited": False}
    assert port["counts"]["portable"] == 1 and port["counts"]["records"] == 2
    entry = led._at(port["ledger_entry"]["seq"])
    assert entry["action"] == "rights:portability" and entry["format"]["version"] == 1
    row = _row(coverage(m), "gdpr-20")
    assert row["state"] == EVIDENCE and row["count"] == 1 and "partial" not in row
    with pytest.raises(ValueError, match="basis must be"):
        export_subject(m, "crm/alice", basis="erasure")


# ------------------------------------------------------------------ Art. 21
def test_an_objection_withholds_the_subject_from_recall_including_a_later_write_and_survives_a_reopen(tmp_path):
    m, led = _fresh(tmp_path)
    assert _row(coverage(m), "gdpr-21")["state"] == CAPABILITY
    m.remember("Alice prefers the blue dashboard", key="alice-pref", source={"doc": "crm/alice"})
    m.remember("Bob prefers the red dashboard", key="bob-pref", source={"doc": "crm/bob"})
    assert _texts(m) == ["Alice", "Bob p"]
    o = record_objection(m, "crm/alice", "dpo", "own_situation", ledger=led, request_id="OBJ-1")
    assert o["status"] == "standing" and o["withheld_at_objection"] == 1
    assert led._at(o["ledger_entry"]["seq"])["action"] == "rights:objection"
    assert _texts(m) == ["Bob p"], "Alice withheld; Bob, who shares the canonical host 'crm', is NOT"
    m.remember("Alice now wants the green dashboard", key="alice-pref", source={"doc": "crm/alice"})
    assert _texts(m) == ["Bob p"], "a record written after the objection is withheld too"
    m.flush()
    again = Inspeximus(str(tmp_path / "mem.json"))
    assert _texts(again) == ["Bob p"], "the objection is persisted beside the store"
    assert again.objections()[0]["status"] == "standing"
    # Art. 15 still sees the subject's records: an objection is not an erasure
    assert export_subject(m, "crm/alice")["counts"]["records"] == 2
    row = _row(coverage(m), "gdpr-21")
    assert row["state"] == EVIDENCE and row["count"] == 1 and "1 standing" in row["detail"]
    with pytest.raises(ValueError, match="already standing"):
        record_objection(m, "crm/alice", "dpo", "own_situation")


def test_an_override_needs_its_grounds_and_direct_marketing_cannot_be_overridden(tmp_path):
    m, led = _fresh(tmp_path)
    m.remember("Alice prefers the blue dashboard", key="alice-pref", source={"doc": "crm/alice"})
    m.remember("Carol prefers the grey dashboard", key="carol-pref", source={"doc": "crm/carol"})
    record_objection(m, "crm/alice", "dpo", "own_situation", ledger=led)
    record_objection(m, "crm/carol", "dpo", "direct_marketing", ledger=led)
    assert _texts(m) == []
    with pytest.raises(ValueError, match="compelling legitimate grounds"):
        resolve_objection(m, "crm/alice", "dpo", "overridden", ledger=led)
    with pytest.raises(ValueError, match="cannot be overridden"):
        resolve_objection(m, "crm/carol", "dpo", "overridden", grounds="we would like to", ledger=led)
    with pytest.raises(ValueError, match="outcome must be"):
        resolve_objection(m, "crm/alice", "dpo", "ignored")
    with pytest.raises(ValueError, match="no standing objection"):
        resolve_objection(m, "crm/nobody", "dpo", "upheld")
    r = resolve_objection(m, "crm/alice", "dpo", "overridden", grounds="defence of a legal claim, case 12/2026",
                          ledger=led, request_id="OBJ-1")
    assert r["status"] == "overridden" and r["resolved"]["grounds"].startswith("defence")
    assert led._at(r["ledger_entry"]["seq"])["action"] == "rights:objection_resolved"
    assert _texts(m) == ["Alice"], "recall resumes for Alice; Carol's marketing objection stands"
    u = resolve_objection(m, "crm/carol", "dpo", "upheld", ledger=led)
    assert u["status"] == "upheld" and _texts(m) == ["Alice"], "upheld keeps the records withheld"
    with pytest.raises(ValueError):
        record_objection(m, "crm/alice", "", "own_situation")
    with pytest.raises(ValueError, match="ground must be"):
        record_objection(m, "crm/alice", "dpo", "vibes")
    ok, problems = led.verify()
    assert ok and problems == []


def test_an_objection_is_tenant_scoped(tmp_path):
    m, _led = _fresh(tmp_path)
    a = m.for_tenant("acme")
    b = m.for_tenant("beta")
    a.remember("Alice prefers the blue dashboard", key="alice-pref", source={"doc": "crm/alice"})
    b.remember("Alice prefers the blue dashboard", key="alice-pref", source={"doc": "crm/alice"})
    a.object_processing("crm/alice", "dpo", "own_situation")
    assert _texts(a) == [] and _texts(b) == ["Alice"], "the other tenant's Alice is a different subject"
    assert len(a.objections()) == 1 and b.objections() == []


# ------------------------------------------------------------------ Art. 28
def test_a_processing_role_names_the_controller_the_instructions_and_the_authorised_sub_processors(tmp_path):
    m, led = _fresh(tmp_path)
    assert _row(coverage(m), "gdpr-28")["state"] == CAPABILITY
    e = led.record_processing_role("ops", "processor", controller="Acme GmbH", instructions_ref="DPA-2026-03",
                                   instructions_sha256=SHA,
                                   sub_processors=[{"name": "Hetzner", "authorised_by": "Acme GmbH", "authorised_ts": 1.0}],
                                   purposes=["support assistant memory"], categories=["contact data"], store_ref="mem.json")
    assert e["kind"] == "processing_role" and e["action"] == "processing_role:processor" and "sig" in e
    assert e["sub_processors"] == [{"name": "Hetzner", "authorised_by": "Acme GmbH", "authorised_ts": 1.0}]
    reg = led.processing_roles()
    assert reg["declarations"] == 1 and reg["current"]["role"] == "processor" and reg["current"]["controller"] == "Acme GmbH"
    row = _row(coverage(m), "gdpr-28")
    assert row["state"] == EVIDENCE and row["count"] == 1
    c = led.record_processing_role("ops", "controller")
    assert led.processing_roles()["current"]["seq"] == c["seq"]


def test_a_processor_without_its_controller_or_instructions_is_refused(tmp_path):
    m, led = _fresh(tmp_path)
    with pytest.raises(ValueError, match="names the controller"):
        led.record_processing_role("ops", "processor", instructions_ref="DPA")
    with pytest.raises(ValueError, match="written instructions"):
        led.record_processing_role("ops", "sub_processor", controller="Acme")
    with pytest.raises(ValueError, match="authorised"):
        led.record_processing_role("ops", "processor", controller="Acme", instructions_ref="DPA",
                                   sub_processors=[{"name": "Hetzner"}])
    with pytest.raises(ValueError, match="role must be"):
        led.record_processing_role("ops", "vendor")
    with pytest.raises(ValueError, match="64-hex"):
        led.record_processing_role("ops", "controller", instructions_sha256="xyz")
    assert _row(coverage(m), "gdpr-28")["state"] == CAPABILITY


# ------------------------------------------------------------------ the matrix
def test_every_in_scope_row_is_covered_and_the_reports_count_the_new_kinds(tmp_path):
    m, led = _fresh(tmp_path)
    cov = coverage(m)
    assert cov["counts"]["NOT COVERED"] == 0, [r["id"] for r in cov["rows"] if r["state"] == "NOT COVERED"]
    assert cov["in_scope"] == 36 and cov["covered"] == 36
    led.record_notice("support", "crm/alice", "email", ["rights"])
    led.record_processing_role("ops", "controller")
    m.remember("Alice prefers the blue dashboard", key="alice-pref", source={"doc": "crm/alice"})
    record_objection(m, "crm/alice", "dpo", "own_situation", ledger=led)
    resolve_objection(m, "crm/alice", "dpo", "upheld", ledger=led)
    pm = led.post_market_report(since=0)
    assert pm["notices"] == 1 and pm["processing_roles"] == 1
    assert pm["objections"] == {"recorded": 1, "resolved": 1}
    assert pm["requirements"]["GDPR Art. 21"] == "objections"
    for k in ("notice", "processing_role"):
        assert led.record("x", inputs={}, actor="agent", kind=k)["kind"] == k
    ok, problems = led.verify()
    assert ok and problems == []
