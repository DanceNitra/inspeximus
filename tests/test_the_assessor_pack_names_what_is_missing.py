"""The pack is only worth anything if a gap is NAMED. Every test here is about a gap.

An assessor pack that renders with a blank where a field should be is worse than no pack: the blank
reads as an answer. So `complete` is False while anything is unanswered, each missing field is named
with the document that needs it, and the check runs twice by two different routes, because a check
that shares its source with the thing it checks cannot catch a disagreement.

The regression at the bottom is the defect this work found on our own live store: a tombstone
written without a request id became a `None` dict key, and `json.dumps(sort_keys=True)` then raised,
so the audit bundle and the whole pack could not be serialised at all.
"""
from __future__ import annotations

import json

import pytest

from inspeximus import Inspeximus
from inspeximus.assessor_pack import (FIELD_GROUPS, answers_from_form, assessor_pack, intake_form,
                                      render_markdown)
from inspeximus.technical_documentation import OPERATOR_INPUT


@pytest.fixture()
def store(tmp_path):
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True)
    m.remember("a decision an assessor will read", key="k1")
    m.remember("a correction of that decision", key="k1")
    m.flush()
    return m


def _all_answered(form):
    filled = dict(form)
    for row in filled["fields"]:
        row["value"] = "answered: " + row["field"]
    return answers_from_form(filled)


def test_the_form_asks_for_every_field_the_documents_need_exactly_once():
    form = intake_form()
    asked = [row["field"] for row in form["fields"]]
    assert len(asked) == len(set(asked)), "a field must be asked once, however many documents use it"
    for spec in FIELD_GROUPS.values():
        fields = spec["fields"]
        for field in (fields.keys() if isinstance(fields, dict) else fields):
            assert field in asked, field


def test_a_field_used_twice_says_both_places():
    form = intake_form()
    shared = [row for row in form["fields"] if len(row["used_by"]) > 1]
    assert shared, "no field is shared, so this test is not measuring what it claims"
    assert all(len(set(row["used_by"])) == len(row["used_by"]) for row in shared)


def test_an_empty_form_leaves_the_pack_incomplete_and_names_the_gaps(store):
    pack = assessor_pack(store)
    assert pack["complete"] is False
    assert pack["unfilled"], "an empty form must name the missing fields"
    assert pack["placeholders_in_output"], "and the documents must carry the marker"
    row = pack["unfilled"][0]
    assert set(row) == {"field", "document", "used_by"}


def test_a_filled_form_renders_a_complete_pack(store):
    pack = assessor_pack(store, operator=_all_answered(intake_form()))
    assert pack["errors"] == {}
    assert pack["unfilled"] == [] and pack["placeholders_in_output"] == []
    assert pack["complete"] is True
    assert set(pack["documents"]) >= {"technical_documentation", "deployer_report",
                                      "registration_a", "registration_b", "registration_c",
                                      "audit_bundle"}


def test_one_missing_field_is_named_rather_than_left_blank(store):
    """The case the acceptance is about: 65 of 66 answered, and the one gap is visible."""
    answers = _all_answered(intake_form())
    answers.pop("intended_purpose")
    pack = assessor_pack(store, operator=answers)
    assert pack["complete"] is False
    named = {row["field"] for row in pack["unfilled"]}
    assert "intended_purpose" in named
    assert any("intended_purpose" in path for path in pack["placeholders_in_output"])


def test_the_two_checks_are_independent(store):
    """One reads the rendered documents, the other reads what each generator says about itself.

    They are kept separate on purpose. A pack that derived both from the same source could report
    a clean result while a document carried a marker the generator had forgotten to report.
    """
    answers = _all_answered(intake_form())
    answers.pop("oversight_persons")
    pack = assessor_pack(store, operator=answers)
    reported = {row["field"] for row in pack["unfilled"]}
    in_output = pack["placeholders_in_output"]
    assert "oversight_persons" in reported
    assert any("oversight_persons" in p for p in in_output)


def test_the_markdown_puts_the_gaps_before_the_documents(store):
    text = render_markdown(assessor_pack(store))
    assert "## Not complete" in text
    assert text.index("## Not complete") < text.index("## Documents")
    assert OPERATOR_INPUT not in text.split("## Documents")[0], "the summary names fields, not markers"


def test_a_generator_that_raises_is_reported_rather_than_dropped(store, monkeypatch):
    """A pack that silently omits a document it could not build is the failure it exists to prevent."""
    import inspeximus.assessor_pack as ap
    monkeypatch.setattr(ap._deployer, "deployer_report",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no ledger")))
    pack = assessor_pack(store, operator=_all_answered(intake_form()))
    assert "deployer_report" in pack["errors"]
    assert pack["complete"] is False
    assert "deployer_report" not in pack["documents"]


# -- the regression that the live store found ----------------------------------------------------
def test_a_tombstone_without_a_request_id_does_not_break_serialisation(tmp_path):
    """Measured on our own store: a None dict key made json.dumps(sort_keys=True) raise, so the
    audit bundle and the assessor pack could not be produced at all."""
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True)
    mid = m.remember("a record that will be erased", key="k")
    m.flush()
    m.forget(mid)                                                # no request_id given
    m.flush()
    report = m.governance_report()
    assert None not in report["by_request"]
    assert json.dumps(report, sort_keys=True)                    # this is what used to raise
    from inspeximus.audit_bundle import build_bundle
    assert json.dumps(build_bundle(m), sort_keys=True, default=str)


def test_a_request_id_that_is_given_is_still_used(tmp_path):
    """The control for the fix: the placeholder must not swallow a real id."""
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True)
    mid = m.remember("a record erased on request", key="k")
    m.flush()
    m.forget(mid, request_id="dsar-2026-001")
    m.flush()
    assert "dsar-2026-001" in m.governance_report()["by_request"]


# -- the defect dogfooding the pack on our own store found -----------------------------------------
def test_a_shared_field_names_every_document_it_lands_in(store):
    """`used_by` used to keep whichever FIELD_GROUP came last, so a row could name a document it is
    not in. Measured on our own store: rows under `registration_a` were labelled section B."""
    answers = _all_answered(intake_form())
    answers.pop("authorised_representative")
    pack = assessor_pack(store, operator=answers)
    rows = [r for r in pack["unfilled"] if r["field"] == "authorised_representative"]
    assert rows, "the fixture stopped producing the shared field, so this test proves nothing"
    for row in rows:
        assert isinstance(row["used_by"], list)
        assert len(row["used_by"]) > 1, "a field used by two sections must name both"
    # and the form's answer and the pack's answer must agree, because they are two readings of one map
    form_row = [r for r in intake_form()["fields"] if r["field"] == "authorised_representative"][0]
    assert rows[0]["used_by"] == form_row["used_by"]


def test_the_markdown_still_reads_with_a_list_of_places(store):
    text = render_markdown(assessor_pack(store))
    assert "## Not complete" in text
    assert "[" not in text.split("## Documents")[0].split("## Not complete")[1][:4000], \
        "a python list must not be printed into the markdown"
