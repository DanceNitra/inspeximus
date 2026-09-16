"""The deployer report (EU AI Act Art. 26) with the DPIA (GDPR Art. 35(7)) and FRIA (Art. 27(1))
evidence appendices, generated from the same store and ledger the provider documents.

Art. 26 binds the deployer of a high-risk system, and most of it is organisational: assign
oversight to competent people, use the system per its instructions, inform workers, inform the
persons subject to it. A memory and action ledger cannot show that a person was competent. What it
can show is what happened: who recorded oversight decisions and how often, whether error actions
went unreviewed, which incidents were opened and whether the Art. 73 clock ran out, how long the
logs have been kept and whether they verify, what personal data the store holds and what was
erased on request. This module writes those parts from the live data, marks the rest as operator
input, and names every field it could not fill.

The two appendices reuse the same evidence. GDPR Art. 35(7) lists what a DPIA contains; the
inventory and the measures in (a) and (d) can be filled from the store, the necessity and risk
judgements in (b) and (c) cannot. Art. 27(1) lists what a FRIA contains; the observed period and
frequency (b), the oversight implementation (e) and the measures on materialisation (f) can be
filled, the processes, affected groups and risks of harm (a), (c), (d) cannot. Art. 27(4), as
amended by Regulation (EU) 2026/1744, lets the FRIA cross-reference the DPIA, so the FRIA sections
that share evidence with the DPIA name the DPIA section they rest on.

    from inspeximus.deployer import deployer_report, render_markdown

    doc = deployer_report(store, ledger=led, operator={"deployer": "Acme GmbH", "system_name": "Support agent"})
    open("deployer_report.md", "w").write(render_markdown(doc))

Evidence, not an assessment. Whether Art. 26 or Art. 27 applies to a given deployment is a legal
question the deployer answers; the report carries that answer as an operator field. Article
references are to Regulation (EU) 2024/1689 as amended by Regulation (EU) 2026/1744 and to
Regulation (EU) 2016/679; check them against the Official Journal before relying on them.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from .core import __version__
from .actions import six_months_before
from .technical_documentation import OPERATOR_INPUT, instructions_for_use, render_markdown as _render

__all__ = ["deployer_report", "dpia_appendix", "fria_appendix", "render_markdown",
           "DEPLOYER_FIELDS", "DPIA_FIELDS", "FRIA_FIELDS"]

SIX_MONTHS_DAYS = 183  # documentation only: the floor is tested as six CALENDAR months, see six_months_before()

#: Art. 26 fields only the deployer can write, each mapped to the paragraph it serves.
DEPLOYER_FIELDS = {
    "deployer": "26", "system_name": "26", "provider": "26",
    "high_risk_classification": "26 (applicability: Art. 6(1) or Art. 6(2) and the Annex III area)",
    "oversight_persons": "26(2)", "oversight_competence_and_authority": "26(2)",
    "input_data_controls": "26(4)", "monitoring_procedure": "26(5)",
    "workers_informed": "26(7)", "registration_reference": "26(8)",
    "persons_informed_how": "26(11)", "fria_required": "27(1) (applicability)",
}

#: GDPR Art. 35(7) fields only the controller can write.
DPIA_FIELDS = {
    "processing_description": "35(7)(a)", "purposes": "35(7)(a)", "legitimate_interest": "35(7)(a)",
    "necessity_and_proportionality": "35(7)(b)", "risks_to_rights_and_freedoms": "35(7)(c)",
    "measures_outside_this_store": "35(7)(d)",
}

#: Art. 27(1) fields only the deployer can write.
FRIA_FIELDS = {
    "deployer_processes": "27(1)(a)", "intended_period_and_frequency": "27(1)(b)",
    "affected_persons_and_groups": "27(1)(c)", "specific_risks_of_harm": "27(1)(d)",
    "oversight_measures_per_instructions": "27(1)(e)", "measures_if_risks_materialise": "27(1)(f)",
    "complaint_mechanism": "27(1)(f)",
}


def _sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _safe(fn, default=None):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - a section that cannot be read says so instead of failing the report
        return {"error": f"{type(e).__name__}: {e}"} if default is None else default


def _op(operator: dict, field: str):
    v = operator.get(field)
    return v if v not in (None, "") else OPERATOR_INPUT


# ----------------------------------------------------------------------------- evidence blocks

def _period(ledger, now: float) -> dict:
    """What the ledger shows about when and how often the system acted: Art. 27(1)(b) asks for the
    intended period and frequency; this is the observed one, for the deployer to compare against."""
    if ledger is None:
        return {"observed": False, "note": "no action ledger"}
    entries = ledger.entries()
    actions = [e for e in entries if e.get("kind", "action") == "action"]
    if not actions:
        return {"observed": False, "note": "the ledger holds no action entries"}
    first, last = actions[0]["ts"], actions[-1]["ts"]
    span_days = (last - first) / 86400.0
    actors = set()
    by_day: dict = {}
    for a in actions:
        actors.add(a.get("actor") or "?")
        d = time.strftime("%Y-%m-%d", time.gmtime(a["ts"]))
        by_day[d] = by_day.get(d, 0) + 1
    return {
        "observed": True,
        "note": "what the ledger recorded, for the deployer to compare against the intended period and "
                "frequency; Art. 27(1)(b) asks for the intended one, assessed before first use",
        "first_action_ts": first, "last_action_ts": last,
        "span_days": round(span_days, 3),
        "actions": len(actions),
        # a rate over less than a day is an artefact of two timestamps, not a frequency
        "actions_per_day": round(len(actions) / span_days, 3) if span_days >= 1.0 else None,
        "active_days": len(by_day),
        "busiest_day": max(by_day.items(), key=lambda kv: kv[1]),
        "distinct_actors": len(actors),
        "errors": sum(1 for a in actions if a.get("status") == "error"),
        "ledger_age_days": round((now - entries[0]["ts"]) / 86400.0, 3),
    }


def _log_retention(store, ledger, now: float) -> dict:
    """Art. 26(6): logs kept for at least six months. The floor can only be observed once a log is
    that old, so a young log reports 'not yet testable' with its age, never 'met'."""
    receipts_on = bool(getattr(store, "receipts_enabled", False))
    receipts = list(getattr(store, "_receipts", None) or [])
    out = {"receipts_enabled": receipts_on, "memory_receipts": len(receipts),
           "action_entries": len(ledger) if ledger is not None else 0,
           "floor": "six calendar months (181 to 184 days depending on the start date)"}
    oldest = []
    if receipts:
        oldest.append(("memory_receipts", receipts[0].get("ts")))
    if ledger is not None and (len(ledger) or getattr(ledger, "archived", None)):
        oldest.append(("action_ledger", ledger.oldest_ts() if hasattr(ledger, "oldest_ts")
                       else ledger.entries()[0].get("ts")))
        arc = getattr(ledger, "archived", None)
        if arc:
            out["action_ledger_archives"] = {"files": arc.get("archive_chain"), "archived_entries": arc.get("archived_count"),
                                             "note": "archived entries stay in the chain; the verifier follows the "
                                                     "checkpoint into each archive"}
        stamps = [e for e in ledger.entries() if e.get("kind") == "timestamp"]
        if stamps:
            out["rfc3161_timestamps"] = [{"seq": e["seq"], "ts": e["ts"], "tsa_url": e.get("tsa_url"),
                                          "stamped_hash": e.get("stamped_hash")} for e in stamps[-5:]]
        att = [e for e in ledger.entries() if e.get("kind") == "retention"]
        if att:
            out["retention_attestations"] = [{"seq": e["seq"], "ts": e["ts"], "policy_days": e.get("policy_days"),
                                              "floor_observed": e.get("floor_observed"), "actor": e.get("actor")}
                                             for e in att[-5:]]
    oldest = [(k, t) for k, t in oldest if isinstance(t, (int, float))]
    if not receipts_on and not oldest:
        out["status"] = "no_log"
        out["note"] = "receipts are off and there is no action ledger; nothing here is a log the deployer keeps"
        return out
    if not oldest:
        out["status"] = "empty"
        return out
    ages = {k: round((now - t) / 86400.0, 3) for k, t in oldest}
    out["oldest_entry_age_days"] = ages
    out["truncation"] = ("a receipt chain starts at genesis (prev = 64 zeros) and the verifier rejects a chain "
                         "that does not; the action ledger is append-only and its verifier rejects a missing "
                         "head. A file that has been cut from the front fails both.")
    if min(t for _, t in oldest) <= six_months_before(now):
        out["status"] = "floor_observed"
        out["note"] = (f"the oldest kept entry is {max(ages.values()):.0f} days old, six calendar months or more, "
                       f"so at least six months of log are present; whether every intermediate entry survived is "
                       f"what the chain verifiers check")
    else:
        out["status"] = "not_yet_testable"
        out["note"] = (f"the oldest kept entry is {max(ages.values()):.0f} days old; the six-month floor cannot be "
                       f"observed until the log is six calendar months old. Nothing in this store deletes a "
                       f"receipt or a ledger entry; retention sweeps erase records and append tombstones, and "
                       f"ledger rotation archives entries under a signed checkpoint.")
    return out


def _integrity(store, ledger, expected_pubkey: str | None) -> dict:
    gov = _safe(lambda: store.governance_report(expected_pubkey))
    proof = gov.get("proof") if isinstance(gov, dict) else None
    led = _safe(lambda: ledger.verify(expected_pubkey=expected_pubkey)) if ledger is not None else None
    return {
        "memory_chain_verified": (proof or {}).get("verified") if isinstance(proof, dict) else None,
        "action_ledger_verified": led[0] if isinstance(led, tuple) else None,
        "action_ledger_problems": led[1] if isinstance(led, tuple) else None,
        "signed": bool(getattr(store, "_receipt_sk", None)),
        "how_an_auditor_re_checks": [
            "inspeximus audit-build --out bundle.json && inspeximus audit-verify bundle.json",
            "inspeximus actions verify",
        ],
    }


def _personal_data(store) -> dict:
    pii = _safe(lambda: store.pii_report())
    mem = _safe(lambda: store.memory_report())
    era = _safe(lambda: store.erasure_report())
    grants = _safe(lambda: store.grants(), default=[])
    counts = {}
    if isinstance(pii, dict):
        for k in ("records_with_pii", "active_records", "by_type", "coverage", "untagged_matches"):
            if k in pii:
                counts[k] = pii[k]
    return {
        "records": {k: mem.get(k) for k in ("total", "active", "superseded", "tombstoned")
                    if isinstance(mem, dict) and k in mem} if isinstance(mem, dict) else mem,
        "personal_data_tagged": counts or pii,
        "erasures": {"tombstoned_total": era.get("tombstoned_total"),
                     "distinct_requests": len({e.get("request_id") for e in era.get("erasures", [])
                                               if e.get("request_id")})} if isinstance(era, dict) else era,
        "access_grants": len(grants) if isinstance(grants, list) else grants,
        "note": "counts only; no record text, no erased content, no request ids and no actor identities "
                "appear in this report",
    }


def _oversight(ledger, include_identities: bool = False) -> dict:
    if ledger is None:
        return {"recorded": False, "note": "no action ledger"}
    rep = _safe(lambda: ledger.oversight_report())
    if not isinstance(rep, dict) or "error" in rep:
        return rep
    out = {
        "recorded": rep["oversight_events"] > 0,
        "oversight_events": rep["oversight_events"],
        "by_event": rep["by_event"],
        "distinct_actors": len(rep["by_actor"]),
        "actions": rep["actions"], "actions_with_oversight": rep["actions_with_oversight"],
        "error_actions_without_oversight": rep["error_actions_without_oversight"],
        "stops": rep["stops"],
    }
    if include_identities:
        out["by_actor"] = rep["by_actor"]
    return out


def _incidents(ledger, now: float) -> dict:
    if ledger is None:
        return {"recorded": False, "note": "no action ledger"}
    inc = [e for e in ledger.entries() if e.get("kind") == "incident" and e.get("event") != "reported"]
    rows = []
    for e in inc:
        deadline = e.get("report_deadline_ts")
        reported = ledger._reported_ts(e) if hasattr(ledger, "_reported_ts") else e.get("reported_ts")
        aware = e.get("aware_ts")
        rows.append({"seq": e["seq"], "severity": e.get("severity"), "aware_ts": aware,
                     "days_since_awareness": round((now - aware) / 86400.0, 2) if isinstance(aware, (int, float)) else None,
                     "reported_ts": reported,
                     "not_yet_reported": not reported,
                     "provider_clock_deadline_ts": deadline,
                     "provider_clock_overdue": bool(deadline and now > deadline and not reported),
                     "evidence_entries": len(e.get("evidence") or [])})
    return {"recorded": bool(rows), "incidents": len(rows),
            "not_yet_reported": [r["seq"] for r in rows if r["not_yet_reported"]],
            "provider_clock_overdue": [r["seq"] for r in rows if r["provider_clock_overdue"]],
            "rows": rows,
            "clock": "Art. 26(5): the deployer informs the provider immediately, then the importer or distributor "
                     "and the authorities; the 15, 2 and 10 day deadlines are the provider's under Art. 73, and "
                     "bind the deployer only mutatis mutandis when the provider cannot be reached. "
                     "not_yet_reported is the deployer's number; provider_clock_overdue is shown for reference."}


def _disclosures(ledger) -> dict:
    if ledger is None:
        return {"recorded": False, "note": "no action ledger"}
    rep = _safe(lambda: ledger.oversight_report())
    if not isinstance(rep, dict) or "error" in rep:
        return rep
    return {"recorded": rep["disclosures"] > 0, "disclosures": rep["disclosures"],
            "sessions_disclosed": len(rep["sessions_disclosed"]),
            "disclosure_kinds": sorted({k for v in rep["sessions_disclosed"].values() for k in v})}


def _source_coverage(store) -> dict:
    """How many active records carry a source, measured; a record written without one has none."""
    try:
        items = list(getattr(store, "items", []) or [])
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
    active = [r for r in items if (r.get("status") or "active") == "active"]
    with_source = sum(1 for r in active if r.get("source"))
    return {"active_records": len(active), "with_source": with_source,
            "coverage": round(with_source / len(active), 3) if active else None,
            "note": "provenance(key) resolves a record's source; the influence gate withholds a single-sourced "
                    "value until corroborated. Coverage is measured, not assumed."}


def _rights(ledger) -> dict:
    if ledger is None:
        return {"export": 0, "rectify": 0}
    rep = _safe(lambda: ledger.oversight_report())
    return rep.get("rights_requests", rep) if isinstance(rep, dict) else rep


# ----------------------------------------------------------------------------- the appendices

def dpia_appendix(store, ledger=None, operator: dict | None = None, expected_pubkey: str | None = None,
                  now: float | None = None, include_identities: bool = False) -> dict:
    """GDPR Art. 35(7): the DPIA contents, with (a) inventory and (d) measures filled from evidence."""
    operator = dict(operator or {})
    now = time.time() if now is None else now
    personal = _personal_data(store)
    integrity = _integrity(store, ledger, expected_pubkey)
    retention = _log_retention(store, ledger, now)
    rights = _rights(ledger)
    return {
        "basis": "Regulation (EU) 2016/679 Art. 35(7): the assessment contains at least (a) a systematic description "
                 "of the processing and its purposes, (b) an assessment of necessity and proportionality, (c) an "
                 "assessment of the risks to the rights and freedoms of data subjects, and (d) the measures "
                 "envisaged to address the risks.",
        "35_7_a_description_of_processing": {
            "operator": {"processing_description": _op(operator, "processing_description"),
                         "purposes": _op(operator, "purposes"),
                         "legitimate_interest": _op(operator, "legitimate_interest")},
            "evidence_inventory_of_this_store": personal,
        },
        "35_7_b_necessity_and_proportionality": {
            "operator": _op(operator, "necessity_and_proportionality"),
            "capability": {"retention_sweeps": "inspeximus retention --apply erases records past an age and appends "
                                               "a tombstone each; dry run by default",
                           "storage_limitation_status": "see compliance_check(store, max_pii_age_days=...)"},
            "evidence_note": "none: necessity and proportionality are the controller's judgement",
        },
        "35_7_c_risks_to_rights_and_freedoms": {
            "operator": _op(operator, "risks_to_rights_and_freedoms"),
            "evidence_note": "a risk judgement is the controller's; the inventory in (a) and the integrity "
                             "results in (d) are inputs to it",
        },
        "35_7_d_measures": {
            "operator_measures_outside_this_store": _op(operator, "measures_outside_this_store"),
            "evidence": {
                "integrity": integrity,
                "log_retention": retention,
                "data_subject_rights_exercised": {"access_exports": rights.get("export") if isinstance(rights, dict) else None,
                                                  "rectifications": rights.get("rectify") if isinstance(rights, dict) else None,
                                                  "erasures": personal.get("erasures")},
                "access_control": {"grants": personal.get("access_grants")},
            },
            "capability": {
                "erasure": "forget_subject hard-deletes the subject and its derived lineage and appends a "
                           "signed, content-free tombstone; erasure_certificate is portable evidence of "
                           "deletion from this store",
                "access_control": "grant() and revoke() are receipted; recall_as reads under a grant",
            },
            "limits": "covers this store only: copies in a vector index, prompt logs, model weights and backups "
                      "are outside it and belong in the operator's measures",
        },
        "operator_fields_missing": [f for f in DPIA_FIELDS if operator.get(f) in (None, "")],
        "operator_fields_total": len(DPIA_FIELDS),
    }


def fria_appendix(store, ledger=None, operator: dict | None = None, expected_pubkey: str | None = None,
                  now: float | None = None, dpia_ref: str = "appendix_dpia", include_identities: bool = False) -> dict:
    """Art. 27(1): the FRIA contents, with (b), (e) and (f) filled from the ledger and the sections that
    share evidence with the DPIA cross-referenced to it (Art. 27(4) as amended)."""
    operator = dict(operator or {})
    now = time.time() if now is None else now
    return {
        "basis": "Regulation (EU) 2024/1689 Art. 27(1): an assessment consisting of (a) the deployer's processes in "
                 "which the system is used, (b) the period and frequency of use, (c) the categories of persons and "
                 "groups likely affected, (d) the specific risks of harm, (e) the implementation of human oversight "
                 "measures, and (f) the measures on materialisation of the risks, including internal governance and "
                 "complaint mechanisms.",
        "applicability": {
            "operator": _op(operator, "fria_required"),
            "note": "Art. 27(1) binds bodies governed by public law, private entities providing public services, "
                    "and deployers of Annex III points 5(b) and 5(c) systems, before deploying an Art. 6(2) system, "
                    "with the exception of systems intended for the area in Annex III point 2 (critical "
                    "infrastructure); the deployer states whether that is the case",
        },
        "27_1_a_deployer_processes": _op(operator, "deployer_processes"),
        "27_1_b_period_and_frequency": {
            "operator_intended": _op(operator, "intended_period_and_frequency"),
            "evidence_observed": _period(ledger, now),
        },
        "27_1_c_affected_persons_and_groups": {
            "operator": _op(operator, "affected_persons_and_groups"),
            "dpia_cross_reference": f"{dpia_ref}.35_7_a_description_of_processing (Art. 27(4))",
        },
        "27_1_d_specific_risks_of_harm": {
            "operator": _op(operator, "specific_risks_of_harm"),
            "dpia_cross_reference": f"{dpia_ref}.35_7_c_risks_to_rights_and_freedoms (Art. 27(4))",
        },
        "27_1_e_human_oversight_implementation": {
            "operator_per_instructions": _op(operator, "oversight_measures_per_instructions"),
            "evidence_recorded": _oversight(ledger, include_identities),
        },
        "27_1_f_measures_on_materialisation": {
            "operator": {"measures_if_risks_materialise": _op(operator, "measures_if_risks_materialise"),
                         "complaint_mechanism": _op(operator, "complaint_mechanism")},
            "evidence": {"incidents": _incidents(ledger, now),
                         "rights_requests_handled": _rights(ledger),
                         "stops_recorded": _oversight(ledger, include_identities).get("stops")},
            "dpia_cross_reference": f"{dpia_ref}.35_7_d_measures (Art. 27(4))",
        },
        "operator_fields_missing": [f for f in FRIA_FIELDS if operator.get(f) in (None, "")],
        "operator_fields_total": len(FRIA_FIELDS),
    }


# ----------------------------------------------------------------------------- the report

def deployer_report(store, ledger=None, operator: dict | None = None, expected_pubkey: str | None = None,
                    now: float | None = None, include_identities: bool = False) -> dict:
    """The Art. 26 duties with the evidence each one can draw from the store and ledger, plus the DPIA and
    FRIA appendices. Every field the deployer must write is marked OPERATOR INPUT REQUIRED.

    Identities stay out by default: oversight actors, disclosure sessions and erasure request ids are
    counted, not listed, because a report travels further than the ledger does (an auditor, a register,
    a court). `include_identities=True` lists the oversight actors for the deployer's own copy."""
    operator = dict(operator or {})
    now = time.time() if now is None else now
    op = lambda f: _op(operator, f)  # noqa: E731
    oversight = _oversight(ledger, include_identities)
    incidents = _incidents(ledger, now)
    dpia = dpia_appendix(store, ledger, operator, expected_pubkey, now, include_identities)
    fria = fria_appendix(store, ledger, operator, expected_pubkey, now, include_identities=include_identities)

    duties = {
        "26_1_use_per_instructions": {
            "obligation": "take appropriate technical and organisational measures to use the system in accordance "
                          "with its instructions for use",
            "operator": {"deployer": op("deployer"), "system_name": op("system_name"), "provider": op("provider"),
                         "high_risk_classification": op("high_risk_classification")},
            "evidence": {"instructions_for_use_logs": instructions_for_use(store, ledger)},
        },
        "26_2_human_oversight_assigned": {
            "obligation": "assign human oversight to natural persons who have the necessary competence, training "
                          "and authority, and the necessary support",
            "operator": {"oversight_persons": op("oversight_persons"),
                         "competence_and_authority": op("oversight_competence_and_authority")},
            "evidence": oversight,
            "evidence_note": "the ledger shows who recorded oversight and how often; it cannot show competence",
        },
        "26_4_input_data": {
            "obligation": "to the extent the deployer controls the input data, ensure it is relevant and "
                          "sufficiently representative for the intended purpose",
            "operator": op("input_data_controls"),
            "evidence": _source_coverage(store),
        },
        "26_5_monitoring_and_incidents": {
            "obligation": "monitor the operation on the basis of the instructions for use; on a serious incident "
                          "inform first the provider, then the importer or distributor and the market "
                          "surveillance authorities",
            "operator": op("monitoring_procedure"),
            "evidence": incidents,
        },
        "26_6_log_retention": {
            "obligation": "keep the logs automatically generated by the system, to the extent under the "
                          "deployer's control, for a period appropriate to the intended purpose of at least "
                          "six months",
            "evidence": _log_retention(store, ledger, now),
        },
        "26_7_workers_informed": {
            "obligation": "before use at the workplace, inform workers' representatives and the affected workers",
            "operator": op("workers_informed"),
        },
        "26_8_registration": {
            "obligation": "deployers that are public authorities or Union bodies comply with the Art. 49 "
                          "registration obligations",
            "operator": op("registration_reference"),
            "evidence": {"export": "inspeximus registration-export --section C writes the Annex VIII Section C "
                                   "fields with the FRIA and DPIA summaries drawn from this report"},
        },
        "26_9_dpia": {
            "obligation": "where applicable, use the Art. 13 information to carry out the data protection impact "
                          "assessment under GDPR Art. 35",
            "appendix": "appendix_dpia",
        },
        "26_11_persons_informed": {
            "obligation": "deployers of Annex III systems that make or assist decisions about natural persons "
                          "inform those persons that they are subject to the system",
            "operator": op("persons_informed_how"),
            "evidence": None,
            "evidence_note": "none from the store. Art. 50(1) receipts record what was shown to a person "
                             "interacting with the system; Art. 26(11) addresses persons subject to a decision, "
                             "who may never interact with it, and a receipt holds only the digest of the text "
                             "shown. The disclosure counts are listed under Art. 50 in the compliance report.",
            "related_disclosures": _disclosures(ledger),
        },
        "26_12_cooperation_with_authorities": {
            "obligation": "cooperate with the competent authorities in any action they take on the system",
            "capability": {"portable_bundle": "inspeximus audit-build --out bundle.json produces a content-free "
                                              "bundle an authority verifies offline; export_subject answers an "
                                              "access request; the action ledger file verifies with "
                                              "inspeximus actions verify"},
            "evidence_note": "a capability, not evidence: nothing here shows that an authority asked or was answered",
        },
    }

    missing = [f for f in DEPLOYER_FIELDS if operator.get(f) in (None, "")]
    missing += dpia["operator_fields_missing"] + fria["operator_fields_missing"]
    doc = {
        "kind": "inspeximus.deployer_report/1",
        "generated_at": now,
        "inspeximus_version": __version__,
        "scope": "The Art. 26 deployer duties with the evidence this store and its action ledger can supply, and "
                 "the GDPR Art. 35(7) and Art. 27(1) appendices built from the same evidence. Fields marked "
                 "OPERATOR INPUT REQUIRED are the deployer's own account. Not an assessment, and not a finding "
                 "that Art. 26 or Art. 27 applies.",
        "sections": {
            "1_deployer_duties_art_26": duties,
            "2_appendix_dpia_gdpr_art_35_7": dpia,
            "3_appendix_fria_art_27_1": fria,
        },
        "operator_fields_missing": missing,
        "operator_fields_total": len(DEPLOYER_FIELDS) + len(DPIA_FIELDS) + len(FRIA_FIELDS),
    }
    doc["content_sha256"] = _sha({k: v for k, v in doc.items() if k != "content_sha256"})
    return doc


def render_markdown(doc: dict) -> str:
    return _render(doc, title="Deployer report (Art. 26) with DPIA and FRIA appendices")
