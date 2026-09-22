"""The agent-memory compliance overlay -- turn a live store into an auditor-facing, article-labelled EVIDENCE
report for the MEMORY slice of the EU AI Act + GDPR.

SCOPE, stated up front and repeated in every output: this covers the AGENT-MEMORY slice only -- the records,
corrections and erasures held in THIS store. It is NOT the whole AI system and NOT a certification. The EU AI
Act imposes far more (risk management, data governance, human oversight, conformity assessment...) that no
memory library can satisfy. What inspeximus does is produce the EVIDENCE an accountable controller / provider /
deployer uses to demonstrate the *memory-record* obligations -- tamper-evident logs (Art. 12/19), provable
erasure (GDPR Art. 17), and correction history (Art. 15 / Art. 5(1)(d)) -- with LIVE numbers from the store so
the report is demonstrably true, not asserted.

`compliance_report(store)` returns the structured evidence; `render_html(report)` renders a self-contained
DPO-facing page; the CLI is `inspeximus compliance [--out report.html|--json]`.
"""
from __future__ import annotations
import hashlib as _hashlib
import html as _html
import json as _json
import os as _os
import time
from .core import __version__


def robustness_evidence(probes_dir: str | None = None) -> dict:
    """The Art. 15 evidence rows (3.6.0): the library's own robustness measurements, dated, each naming
    the probe that recomputes it and the sha256 of its receipt.

    The rows ship inside the package (`robustness_evidence.json`, written by
    tools/gen_robustness_evidence.py from the receipts in the source tree, never typed). Each row's
    `status` is decided here, against the receipt file when one can be found:

      verified   the receipt is present at `probes_dir` and hashes to the packaged sha256
      STALE      the receipt is present and hashes to something else: it was edited after the
                 evidence was packaged, so the row's number no longer describes that file
      packaged   no receipt file is reachable (an installed wheel, no source tree): the row is the
                 measurement as packaged at release, and the probe path says how to recompute it

    `probes_dir` defaults to the `probes/` directory beside the package when this is a source
    checkout. These rows describe the LIBRARY, not the caller's store, and they are not a
    certification; the report's disclaimer covers them.
    """
    here = _os.path.dirname(_os.path.abspath(__file__))
    try:
        with open(_os.path.join(here, "robustness_evidence.json"), encoding="utf-8") as fh:
            doc = _json.load(fh)
    except FileNotFoundError:
        return {"kind": "inspeximus.robustness_evidence/1", "rows": [], "probes_dir": None,
                "note": "robustness_evidence.json is missing from this installation"}
    if probes_dir is None:
        # an operator who keeps the receipts elsewhere points at them; the coverage probe has no
        # argument of its own, so this is also how a test hands it a mutated copy
        probes_dir = _os.environ.get("INSPEXIMUS_PROBES_DIR") or None
    if probes_dir is None:
        cand = _os.path.join(_os.path.dirname(here), "probes")
        probes_dir = cand if _os.path.isdir(cand) else None
    rows = []
    for r in doc.get("rows") or []:
        row = dict(r)
        path = _os.path.join(probes_dir, _os.path.relpath(r["receipt"], "probes")) if probes_dir else None
        if path and _os.path.exists(path):
            with open(path, "rb") as fh:
                digest = _hashlib.sha256(fh.read()).hexdigest()
            row["status"] = "verified" if digest == r["receipt_sha256"] else "STALE"
            if row["status"] == "STALE":
                row["receipt_sha256_now"] = digest
        else:
            row["status"] = "packaged"
        rows.append(row)
    return {"kind": doc.get("kind", "inspeximus.robustness_evidence/1"), "note": doc.get("note"),
            "probes_dir": probes_dir, "rows": rows,
            "stale": [r["id"] for r in rows if r["status"] == "STALE"]}

# Obligation wording is conservative and traceable to the consolidated Reg (EU) 2024/1689 / Reg (EU) 2016/679
# texts (see docs/COMPLIANCE.md, which was primary-source checked). "Evidence for", never "guarantees".
_CONTROLS = [
    ("EU AI Act (Reg (EU) 2024/1689)", "Art. 12", "Record-keeping (automatic logging over the lifetime)",
     "High-risk AI systems must technically allow the automatic recording of events (logs) over the system's "
     "lifetime, ensuring a level of traceability appropriate to the intended purpose.",
     "Every write is a hash-linked, timestamped receipt; anchor() emits a signed head commitment over the "
     "whole history; the log is portable and INDEPENDENTLY verifiable offline (audit-build / audit-verify).",
     "write_receipts"),
    ("EU AI Act (Reg (EU) 2024/1689)", "Art. 12 (actions)", "Record-keeping of what the agent did",
     "The automatic logs must enable the traceability of the system's functioning; for the agent that means "
     "each tool and model call, and what the system held when it made it.",
     "The action ledger (<store>.actions.json) appends one signed, hash-chained entry per tool or model call, "
     "each carrying the memory state digest and the ids recall returned before it; verified offline and bound "
     "to the memory receipt chain.",
     "actions"),
    ("EU AI Act (Reg (EU) 2024/1689)", "Art. 14", "Human oversight",
     "High-risk systems must be designed so natural persons can oversee them, including the ability to "
     "decide not to use an output, to override or reverse it, and to stop the system.",
     "oversight() records approve, refuse, override, stop and review events with the person or role who "
     "decided and the action they refer to; oversight_report() lists error actions with no review after them.",
     "oversight_events"),
    ("EU AI Act (Reg (EU) 2024/1689)", "Art. 50", "Transparency obligations (applies from 2 Aug 2026)",
     "Providers must ensure persons are informed that they interact with an AI system, and that generated "
     "content is marked as generated, in the cases Art. 50 names.",
     "disclosure() records per session what the user was shown, in which channel and of which kind, as a "
     "signed entry in the same chain; the report lists sessions with no disclosure.",
     "disclosures"),
    ("EU AI Act (Reg (EU) 2024/1689)", "Art. 73", "Reporting of serious incidents",
     "Providers must report serious incidents to the market surveillance authority immediately after "
     "establishing a causal link, and no later than 15 days after becoming aware (2 days for a widespread "
     "infringement, 10 days for a death).",
     "incident() records the incident with the moment of awareness and the statutory clock, linked to the "
     "ledger entries that are its evidence; incident_report() produces the Art. 73 skeleton and the report "
     "lists incidents past their deadline that carry no reported_ts.",
     "incidents"),
    ("EU AI Act (Reg (EU) 2024/1689)", "Art. 19", "Automatically generated logs (kept/retained)",
     "Providers must keep the automatically generated logs (Art. 12(1)) for a period appropriate to the "
     "intended purpose, of at least six months, keeping them available with their integrity preserved.",
     "Append-only receipt + tombstone chains with a signed anchor; the portable audit bundle is a durable, "
     "tamper-evident snapshot an auditor re-verifies from genesis without the live store.",
     "write_receipts"),
    ("EU AI Act (Reg (EU) 2024/1689)", "Art. 15", "Accuracy, robustness and cybersecurity",
     "High-risk systems must achieve an appropriate level of accuracy and robustness and be resilient against "
     "attempts to alter their use or behaviour (including data/model manipulation).",
     "Keyed supersession serves the corrected value and resists the stale one resurfacing (echo_guard); "
     "verify_claim catches a corrected fact re-asserted; the influence gate + witness co-signing resist "
     "memory-poisoning and operator-side tampering.",
     "superseded"),
    ("EU AI Act (Reg (EU) 2024/1689)", "Art. 10", "Data and data governance (record-level)",
     "Data used by high-risk systems must be governed appropriately -- relevant, representative, and handled "
     "with attention to provenance and errors (at the memory-record level).",
     "check_conflict gates contradictory writes; attestation/provenance binds a record's sources; detect_pii "
     "/ redact_pii and per-type decay support data minimisation within the store.",
     None),
    ("GDPR (Reg (EU) 2016/679)", "Art. 15", "Right of access",
     "The data subject has the right to obtain confirmation of whether personal data are processed, access "
     "to the data, and the available information about their source.",
     "export_subject() returns every record whose source resolves to the subject, as erasure resolves it, "
     "with provenance and correction history, plus the actions taken while those records were recalled; a "
     "manifest hash ties the export to a rights entry on the action ledger.",
     "rights_export"),
    ("GDPR (Reg (EU) 2016/679)", "Art. 16", "Right to rectification",
     "The data subject has the right to obtain the rectification of inaccurate personal data without undue "
     "delay.",
     "rectify() supersedes the value under its key through the ordinary keyed write, so the old value stops "
     "being served, and records who asked and why as a rights entry bound to the memory receipt.",
     "rights_rectify"),
    ("GDPR (Reg (EU) 2016/679)", "Art. 17", "Right to erasure",
     "The controller must erase personal data without undue delay on a valid request, and be able to "
     "demonstrate the erasure took place.",
     "forget_subject / forget_pii hard-delete the subject plus its derived lineage and emit a signed, "
     "content-free tombstone; erasure_certificate / erasure_report are the portable proof-of-deletion.",
     "erasures"),
    ("GDPR (Reg (EU) 2016/679)", "Art. 22", "Automated individual decision-making",
     "Where a decision based solely on automated processing has legal or similarly significant effects, the "
     "data subject has the right to obtain human intervention and to contest the decision.",
     "The same oversight events, tied to the action that produced the decision, are the record that a human "
     "reviewed, overrode or refused it.",
     "oversight_events"),
    ("GDPR (Reg (EU) 2016/679)", "Art. 30", "Records of processing activities",
     "The controller/processor must maintain a record of processing activities.",
     "The write-receipt chain + supersession ledger + erasure log are a technical record of processing at the "
     "memory-record level (what was written, corrected, and erased, and when).",
     "write_receipts"),
    ("GDPR (Reg (EU) 2016/679)", "Art. 5(1)(d)", "Accuracy",
     "Personal data must be accurate and, where necessary, kept up to date; inaccurate data erased or rectified "
     "without delay.",
     "Keyed last-write-wins retires the stale value so recall returns current truth; history() preserves the "
     "correction trail.",
     "superseded"),
    # The documentation and deployer rows. These are generators, not counters: the report says 'available'
    # for them unless the ledger carries the thing the row is about (a retention attestation, an archive).
    ("EU AI Act (Reg (EU) 2024/1689)", "Art. 19 / Art. 26(6)", "Logs kept for at least six months",
     "Providers keep the Art. 12 logs, and deployers the logs under their control, for a period appropriate to "
     "the intended purpose of at least six months.",
     "attest_retention() appends a signed statement of the oldest entry the ledger accounts for and whether the "
     "six-month floor has been observed; archive() rotates old entries under a signed checkpoint without "
     "deleting them, and the verifier follows the checkpoint into the archive.",
     "retention_attestations"),
    ("EU AI Act (Reg (EU) 2024/1689)", "Art. 11 / Annex IV", "Technical documentation",
     "The provider draws up technical documentation containing at least the elements of Annex IV before the "
     "system is placed on the market.",
     "annex_iv() fills the Annex IV sections evidence can fill (logs and how to verify them, data in memory, "
     "oversight events, chain verification, this controls report) and marks the 24 provider fields as operator "
     "input; `inspeximus technical-documentation`.",
     None),
    ("EU AI Act (Reg (EU) 2024/1689)", "Art. 13(3)(f)", "Instructions for use: collecting and interpreting logs",
     "The instructions for use contain the information needed to collect, store and interpret the logs.",
     "instructions_for_use() is that section for this store, written from the code that runs: files, entry "
     "schema, verifier commands, retention behaviour.",
     None),
    ("EU AI Act (Reg (EU) 2024/1689)", "Art. 26", "Deployer duties",
     "The deployer uses the system per its instructions, assigns human oversight, monitors it, keeps its logs, "
     "informs workers and the persons subject to it, and carries out the DPIA where applicable.",
     "deployer_report() writes the evidence under each paragraph (oversight recorded, incidents and the Art. 73 "
     "clock, log age against the floor, disclosures, the offline bundle) and marks the 12 deployer fields as "
     "operator input; `inspeximus deployer-report`.",
     None),
    ("EU AI Act (Reg (EU) 2024/1689)", "Art. 27", "Fundamental rights impact assessment",
     "Named deployers assess the impact on fundamental rights before deploying an Annex III system: processes, "
     "period and frequency, affected persons, risks of harm, oversight, measures on materialisation.",
     "fria_appendix() fills the observed period and frequency, the oversight recorded and the incidents, rights "
     "requests and stops from the ledger, cross-references the DPIA per Art. 27(4), and marks the 7 deployer "
     "fields as operator input.",
     None),
    ("EU AI Act (Reg (EU) 2024/1689)", "Art. 49 / Annex VIII", "Registration in the EU database",
     "Providers of high-risk systems, providers relying on Art. 6(3) and public-authority deployers register the "
     "Annex VIII information in the EU database before placing on the market or putting into service.",
     "registration_export() writes the Annex VIII fields for section A, B or C with the traceability reference, "
     "the information-used description, the instructions for use and, for C, the FRIA and DPIA summaries filled "
     "from evidence; `inspeximus registration-export`.",
     None),
    ("GDPR (Reg (EU) 2016/679)", "Art. 5(1)(e)", "Storage limitation: partitions with expiry and caps",
     "Personal data is kept in a form which permits identification for no longer than necessary.",
     "Memory partitions per agent or per process (inspeximus.partitions) carry an expiry and a size cap enforced "
     "by sweep() with tombstones, and a context partition erases its records when the process ends; the "
     "partitions report shows what is past expiry now and what sits outside any partition.",
     "partitions_open"),
    ("GDPR (Reg (EU) 2016/679)", "Art. 35(7)", "Data protection impact assessment",
     "The assessment contains a description of the processing, an assessment of necessity and proportionality, "
     "an assessment of the risks, and the measures envisaged to address them.",
     "dpia_appendix() fills the (a) inventory and the (d) measures from the store and ledger and marks the "
     "(b) and (c) judgements as operator input.",
     None),
]


def compliance_report(store, expected_pubkey: str | None = None, probes_dir: str | None = None) -> dict:
    """Article-labelled EVIDENCE report for the agent-memory compliance slice, with LIVE counts from `store`.
    Each control carries an honest per-store status: 'evidence' (the store actually exercises the primitive),
    'available' (shipped but not exercised in this store), or 'needs_receipts' (Art.12/19/30 need receipts=True).
    Returns a json-serialisable dict; NOT a certification (see the in-band `disclaimer`)."""
    anchor = store.anchor()
    gov = store.governance_report(expected_pubkey)
    sup = store.supersession_report()
    n_writes = anchor.get("n_writes") or 0
    n_tomb = anchor.get("n_tombstones") or 0
    n_sup = sup.get("superseded_total") or 0
    receipts_on = bool(getattr(store, "receipts_enabled", False))

    live = {"write_receipts": n_writes, "erasures": n_tomb, "superseded": n_sup}
    ledger = _ledger_counts(store)
    live.update(ledger)
    try:
        from .partitions import Partitions
        prep = Partitions(store).report() if Partitions(store).path.exists() else None
        live["partitions_open"] = (prep["open"] + prep["closed"]) if prep else 0
        live["partitions"] = prep
    except Exception:  # noqa: BLE001 - a store with no registry has no partitions
        live["partitions_open"] = 0

    robustness = robustness_evidence(probes_dir)
    controls = []
    for framework, art, title, obligation, evidence, live_key in _CONTROLS:
        count = live.get(live_key) if live_key else None
        if live_key in ("write_receipts",) and not receipts_on:
            status = "needs_receipts"      # honest: the log only exists if receipts were enabled at write time
        elif live_key is None:
            status = "available"
        elif count and count > 0:
            status = "evidence"
        else:
            status = "available"
        row = {
            "framework": framework, "article": art, "title": title,
            "obligation": obligation, "inspeximus_evidence": evidence,
            "live_count": count, "status": status,
        }
        if art == "Art. 15" and framework.startswith("EU AI Act"):
            # 3.6.0: the measurements the footnote used to say lived only in probes/. A STALE row
            # is named here too, so a reader of the control sees it without opening the section.
            row["robustness_evidence"] = [
                {"id": r["id"], "property": r["property"], "metric": r["metric"], "value": r["value"],
                 "measured_at": r["measured_at"], "status": r["status"]} for r in robustness["rows"]]
            if robustness["stale"]:
                row["status"] = "STALE_EVIDENCE"
        controls.append(row)

    return {
        "kind": "inspeximus.compliance_report/2",
        "inspeximus_version": __version__,
        "scope": "AGENT-MEMORY slice only: the records, corrections and erasures held in THIS inspeximus store. "
                 "NOT the whole AI system, and NOT a certification.",
        "disclaimer": "inspeximus produces the EVIDENCE (tamper-evident logs, provable erasure, correction "
                      "history) an accountable controller / provider / deployer uses to DEMONSTRATE the "
                      "memory-record obligations below. It does not by itself make any system compliant, is not "
                      "a certification, determines no lawful basis, and covers only what this store holds -- not "
                      "your vector index, prompt logs, or backups. The EU AI Act imposes far more (risk "
                      "management, human oversight, conformity assessment) that lies outside any memory library.",
        "receipts_enabled": receipts_on,
        "action_ledger": ledger,
        "partitions": live.get("partitions"),
        "robustness_evidence": robustness,
        "controls": controls,
        "summary": {
            "writes": n_writes,
            "erasures": n_tomb,
            "erasure_requests": len(gov.get("by_request") or {}),
            "superseded": n_sup,
            "integrity_verified": (gov.get("proof") or {}).get("verified"),
            "anchor_sth": anchor.get("sth_hash"),
            "controls_with_evidence": sum(1 for c in controls if c["status"] == "evidence"),
        },
    }


def _ledger_counts(store) -> dict:
    """Live counts from the action ledger beside the store, or zeros when there is none. The ledger is
    read through its own verifier so a rewritten file counts as zero evidence rather than as evidence."""
    out = {"actions": 0, "oversight_events": 0, "disclosures": 0, "rights_export": 0, "rights_rectify": 0,
           "incidents": 0, "incidents_overdue": [], "retention_attestations": 0,
           "ledger_present": False, "ledger_verified": None, "error_actions_without_oversight": []}
    try:
        from .actions import ActionLedger
        led = ActionLedger(store)
    except Exception:
        return out
    if not led.path.exists() or (len(led) == 0 and not getattr(led, "archived", None)):
        return out
    out["ledger_present"] = True
    if getattr(led, "archived", None):
        out["archived_entries"] = led.archived.get("archived_count")
        out["archives"] = led.archived.get("archive_chain")
    ok, problems = led.verify()
    out["ledger_verified"] = ok
    if not ok:
        out["ledger_problems"] = problems[:5]
        return out
    rep = led.oversight_report()
    out["actions"] = rep["actions"]
    out["oversight_events"] = rep["oversight_events"]
    out["disclosures"] = rep["disclosures"]
    out["rights_export"] = rep["rights_requests"]["export"]
    out["rights_rectify"] = rep["rights_requests"]["rectify"]
    out["incidents"] = rep["incidents"]
    out["incidents_overdue"] = rep["incidents_overdue"]
    out["error_actions_without_oversight"] = rep["error_actions_without_oversight"]
    out["retention_attestations"] = sum(1 for e in led.entries() if e.get("kind") == "retention")
    if out.get("archives"):
        out["retention_attestations"] += len(out["archives"])   # a checkpoint is a signed retention statement too
    return out


def compliance_check(store, require_receipts: bool = True, max_pii_age_days: float | None = None,
                     prior_anchor: dict | None = None, now_ts: float | None = None) -> dict:
    """CI / CONTINUOUS compliance GATE (read-only, no LLM): assert the invariants a store claiming AI-Act
    record-keeping must hold, and FAIL if the posture regressed. The read-side complement of the point-in-time
    compliance_report — same relationship as `check-code` to a code review. Returns {ok, violations, checked}:
      - receipts_disabled  (Art. 12/19) : tamper-evident logging is off, so no automatic record exists to keep
      - integrity_failed   (Art. 12/15) : the receipt/tombstone chain fails verify_writes (altered out of band)
      - not_append_only    (Art. 12/19) : history is not a consistent extension of a pinned `prior_anchor`
      - pii_over_retention (GDPR 5(1)(e)): active PII records older than `max_pii_age_days` (storage limitation)
    `ok` is True iff no violations — wire `inspeximus compliance --check` into CI so the AI-Act posture cannot
    silently regress. `now_ts` overrides the clock for the retention check (testability)."""
    violations, checked = [], []
    # Count the ACTUAL receipt chain, not the receipts_enabled flag: a store WRITTEN without receipts has an
    # empty chain even when reopened with receipts=True (no sidecar to reload), and that is the real regression.
    n_receipts = len(getattr(store, "_receipts", []))
    has_content = any(r.get("status") == "active" for r in getattr(store, "items", []))

    checked.append("receipts_coverage")
    n_records = len(getattr(store, "items", []) or [])
    # COVERAGE IS PER RECORD, NOT A COUNT. `n_records > n_receipts` compares two integers, so a store whose
    # receipted rows were erased (our own Art.17 path appends tombstones and leaves the write chain) can hold
    # MORE receipts than records while none of the survivors is covered by any of them. Matching ids says
    # which records are actually protected -- and names them, which a count never could.
    covered_ids = {rc.get("memory_id") for rc in getattr(store, "_receipts", [])}
    active_ids = [r.get("id") for r in (getattr(store, "items", []) or [])
                  if r.get("status") == "active"]
    uncovered = [i for i in active_ids if i not in covered_ids]
    if require_receipts and n_receipts and uncovered:
        violations.append({"code": "receipts_partial", "article": "Art. 12/19",
                           "detail": f"{len(uncovered)} of {len(active_ids)} active record(s) are covered by "
                                     f"no write receipt in this chain: {', '.join(uncovered[:5])}"
                                     + (f" (+{len(uncovered) - 5} more)" if len(uncovered) > 5 else "")})
    elif require_receipts and n_receipts and n_records > n_receipts:
        # PARTIAL coverage passed this gate until 1.57.0: the check only fired when the chain was entirely
        # empty, so a store written with receipts off and later reopened with them on (5 unreceipted + 1
        # receipted) reported ok=True with no violations. verify_bundle got this check in 1.54.0; its sibling
        # gate never did -- the same defect, one surface over.
        violations.append({"code": "receipts_partial", "article": "Art. 12/19",
                           "detail": f"only {n_receipts} of {n_records} record(s) carry a write receipt — the "
                                     f"remaining {n_records - n_receipts} were written with receipts disabled "
                                     f"and are not covered by the Art.12/19 chain"})

    checked.append("receipts_enabled")
    if require_receipts and has_content and n_receipts == 0:
        violations.append({"code": "receipts_disabled", "article": "Art. 12/19",
                           "detail": "the store has records but NO write receipts — tamper-evident logging was "
                                     "off at write time, so no automatic Art.12/19 record exists to keep"})

    checked.append("chain_integrity")
    if n_receipts:
        gov = store.governance_report()
        if (gov.get("proof") or {}).get("verified") is False:
            violations.append({"code": "integrity_failed", "article": "Art. 12/15",
                               "detail": "receipt/tombstone chain failed verify_writes — the log was altered out of band"})

    if prior_anchor is not None:
        checked.append("append_only")
        ok, probs = store.verify_consistency(prior_anchor)
        if not ok:
            violations.append({"code": "not_append_only", "article": "Art. 12/19",
                               "detail": "history is not an append-only extension of the pinned anchor: " + "; ".join(probs)})

    limits: list = []
    if max_pii_age_days is not None:
        checked.append("pii_retention")
        now = now_ts if now_ts is not None else time.time()
        cutoff = now - float(max_pii_age_days) * 86400.0
        tv = getattr(store, "tenant", None)
        stale = [r["id"] for r in getattr(store, "items", [])
                 if _still_stored(r) and r.get("pii")
                 and (tv is None or r.get("tenant") == tv) and (r.get("ts") or 0) < cutoff]
        if stale:
            violations.append({"code": "pii_over_retention", "article": "GDPR Art. 5(1)(e)",
                               "detail": f"{len(stale)} stored PII record(s) older than {max_pii_age_days} days "
                                         "— storage-limitation breach; run forget_pii()"})
        # The half a sweep will NOT touch. Named, so "no violations" cannot be read as "no stored PII
        # past its window": superseded PII is still stored, and only forget_pii()/forget_subject()
        # reach it. Silence here is what turned a clean verdict into a false one.
        held = [r["id"] for r in getattr(store, "items", [])
                if _stored_but_not_sweepable(r) and r.get("pii")
                and (tv is None or r.get("tenant") == tv) and (r.get("ts") or 0) < cutoff]
        if held:
            limits.append(f"{len(held)} SUPERSEDED PII record(s) are also older than {max_pii_age_days} "
                          "days. Their content is on disk, but a retention sweep does not erase them "
                          "because they are the prior half of a correction history. Use forget_pii() "
                          "or forget_subject() if the policy requires them gone.")

    return {"ok": not violations, "violations": violations, "checked": checked, "limits": limits}


#: Statuses whose content is on disk AND which a retention sweep may erase without destroying
#: anything another guarantee depends on.
#:
#: Until 2.10.1 the three retention paths asked `status == "active"`. A `discarded` PII record -- one
#: a verifier explicitly REJECTED, so nobody is watching it -- was therefore invisible to detection
#: while its plaintext sat in the file: measured 2026-08-15, with only such a record present,
#: retention_sweep found 0 eligible and compliance_check returned ok with zero violations and
#: Art. 5(1)(e) marked CHECKED, while the email address and the SSN were readable on disk. The
#: remedy the check itself names, forget_pii(), would have cleaned it -- forget_pii never filtered on
#: status -- so the defect was detection, not disposal.
#:
#: `superseded` is deliberately NOT here, and the first version of this fix got that wrong. Its
#: content is equally on disk, but a superseded row is the load-bearing half of a correction: it is
#: what history(), the receipt chain and every supersession guarantee are made of. Sweeping it is a
#: real product decision with audit consequences, not a bug fix, so it is REPORTED as uncovered
#: rather than silently swept or silently ignored -- a check must not claim coverage it does not
#: have.
_SWEEPABLE_STATUSES = frozenset({"active", "provisional", "discarded"})


def _still_stored(r: dict) -> bool:
    """Is this record's personal data in the file AND safe for a retention sweep to erase?

    An allowlist, for the reason the recall path already learned: `discarded` was born after any
    denylist here would have been written, and the next status will be too.
    """
    return (r.get("status") or "active") in _SWEEPABLE_STATUSES


def _stored_but_not_sweepable(r: dict) -> bool:
    """On disk, but erasing it would destroy a correction history. Reported, never swept."""
    return (r.get("status") or "active") == "superseded"


def retention_sweep(store, max_age_days: float, now_ts: float | None = None, pii_only: bool = True,
                    apply: bool = False, basis: str | None = None, request_id: str | None = None) -> dict:
    """Storage-limitation ENFORCEMENT (GDPR Art. 5(1)(e); the enforce-side of compliance_check's
    pii_over_retention flag). Finds ACTIVE records older than `max_age_days` and, with `apply=True`, hard-deletes
    them — emitting a signed tombstone per record, so the erasure is itself auditable. DRY-RUN by default
    (`apply=False`): returns what WOULD be erased so you review before enforcing. `pii_only` (default True)
    restricts the window to PII-tagged records; False applies it to every record. Deterministic, no LLM.
    Returns {eligible, ids, cutoff_ts, applied, erased, request_id}."""
    now = now_ts if now_ts is not None else time.time()
    cutoff = now - float(max_age_days) * 86400.0
    tv = getattr(store, "tenant", None)
    ids = [r["id"] for r in getattr(store, "items", [])
           if _still_stored(r) and (r.get("pii") if pii_only else True)
           and (tv is None or r.get("tenant") == tv) and (r.get("ts") or 0) < cutoff]
    out = {"eligible": len(ids), "ids": ids, "cutoff_ts": cutoff, "applied": False, "erased": 0,
           "request_id": None}
    if apply and ids:
        rid = request_id or f"retention-{int(max_age_days)}d"
        res = store.forget(ids=ids, request_id=rid,
                           basis=(basis or f"retention policy: older than {max_age_days} days"))
        out["applied"] = True
        out["erased"] = res.get("forgotten", len(ids))
        out["request_id"] = rid
        # carry the coverage statement out with the result. A retention sweep hard-deletes, so the same
        # question applies to it as to any other erasure -- did this reach the stores we do not own? --
        # and forget() now answers it. Dropping the field here would leave the caller of the compliance
        # surface with less information than the caller of the primitive underneath it.
        if "coverage" in res:
            out["coverage"] = res["coverage"]
    return out


_STATUS_LABEL = {"evidence": "Evidence in this store", "available": "Available (not exercised here)",
                 "needs_receipts": "Enable receipts=True"}


def render_html(report: dict) -> str:
    """Self-contained, restrained DPO-facing HTML for a compliance_report(). No external assets, no JS."""
    def esc(x): return _html.escape(str(x))
    s = report.get("summary", {})
    rows = []
    for c in report["controls"]:
        cnt = "" if c["live_count"] is None else f" &middot; {c['live_count']}"
        rows.append(
            f"<tr><td class='art'>{esc(c['framework'].split('(')[0].strip())}<br><b>{esc(c['article'])}</b></td>"
            f"<td><b>{esc(c['title'])}</b><div class='ob'>{esc(c['obligation'])}</div>"
            f"<div class='ev'>{esc(c['inspeximus_evidence'])}</div></td>"
            f"<td class='st st-{esc(c['status'])}'>{esc(_STATUS_LABEL.get(c['status'], c['status']))}{cnt}</td></tr>")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>inspeximus - agent-memory compliance evidence</title>
<style>
:root{{--fg:#1f2328;--muted:#59636e;--bd:#d1d9e0;--acc:#0d7d84;--ok:#1a7f37;--av:#9a6700;--bg:#fff;--alt:#f6f8fa}}
*{{box-sizing:border-box}}body{{font:15px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;color:var(--fg);
background:var(--bg);margin:0;padding:2rem 1rem}}.wrap{{max-width:960px;margin:0 auto}}
h1{{font-size:1.5rem;margin:0 0 .25rem}}.sub{{color:var(--muted);margin:0 0 1.25rem}}
.disc{{background:#fff8e6;border:1px solid #e6d9a8;border-left:4px solid var(--av);border-radius:6px;
padding:.75rem 1rem;font-size:.9rem;color:#503;margin:1rem 0 1.5rem}}
.kpis{{display:flex;gap:.75rem;flex-wrap:wrap;margin:0 0 1.25rem}}
.kpi{{border:1px solid var(--bd);border-radius:8px;padding:.6rem .9rem;min-width:110px}}
.kpi b{{display:block;font-size:1.35rem;color:var(--acc)}}.kpi span{{color:var(--muted);font-size:.8rem}}
table{{border-collapse:collapse;width:100%;font-size:.9rem}}
td,th{{border:1px solid var(--bd);padding:.6rem .7rem;vertical-align:top;text-align:left}}
th{{background:var(--alt)}}tr:nth-child(even){{background:var(--alt)}}
.art{{white-space:nowrap;color:var(--muted)}}.ob{{color:var(--muted);margin:.3rem 0}}
.ev{{font-size:.85rem}}.st{{white-space:nowrap;font-weight:600;font-size:.82rem}}
.st-evidence{{color:var(--ok)}}.st-available{{color:var(--muted)}}.st-needs_receipts{{color:var(--av)}}
footer{{color:var(--muted);font-size:.8rem;margin-top:1.5rem;border-top:1px solid var(--bd);padding-top:.75rem}}
@media(prefers-color-scheme:dark){{:root{{--fg:#e6edf3;--muted:#9aa;--bd:#30363d;--bg:#0d1117;--alt:#161b22}}
.disc{{background:#241a00;color:#e8d}}}}
</style></head><body><div class="wrap">
<h1>Agent-memory compliance evidence</h1>
<p class="sub">inspeximus v{esc(report.get('inspeximus_version'))} &middot; {esc(report.get('scope'))}</p>
<div class="disc">{esc(report.get('disclaimer'))}</div>
<div class="kpis">
<div class="kpi"><b>{esc(s.get('writes'))}</b><span>write receipts</span></div>
<div class="kpi"><b>{esc(s.get('erasures'))}</b><span>erasures (tombstones)</span></div>
<div class="kpi"><b>{esc(s.get('superseded'))}</b><span>corrections</span></div>
<div class="kpi"><b>{esc(s.get('integrity_verified'))}</b><span>chain integrity</span></div>
</div>
<table><thead><tr><th>Framework</th><th>Obligation &amp; inspeximus evidence</th><th>Status in this store</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<footer>Generated by <code>inspeximus compliance</code>. Evidence, not certification &mdash; see the disclaimer above.
Anchor STH: <code>{esc((s.get('anchor_sth') or '')[:32])}...</code></footer>
</div></body></html>"""


def _cli(argv=None):
    import argparse, os, json
    from .core import Inspeximus
    ap = argparse.ArgumentParser(prog="inspeximus compliance",
                                 description="Article-labelled agent-memory compliance EVIDENCE report.")
    ap.add_argument("--path", help="store file (default: $INSPEXIMUS_PATH or ./inspeximus_memory.json)")
    ap.add_argument("--out", default=None, help="write a self-contained HTML report here")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    ap.add_argument("--expected-pubkey", default=None)
    a = ap.parse_args(argv)
    p = a.path or os.environ.get("INSPEXIMUS_PATH") or "inspeximus_memory.json"
    store = Inspeximus(path=p, receipts=True)
    rep = compliance_report(store, expected_pubkey=a.expected_pubkey)
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    elif a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(render_html(rep))
        print(f"wrote compliance report -> {a.out}  "
              f"({rep['summary']['controls_with_evidence']}/{len(rep['controls'])} controls with live evidence)")
    else:
        for c in rep["controls"]:
            print(f"  [{_STATUS_LABEL.get(c['status'], c['status'])}] {c['article']} {c['title']}")
        print(f"\nscope: {rep['scope']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
