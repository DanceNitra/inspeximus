"""One command that shows, for every obligation of an AI-agent operator under the EU AI Act and the
GDPR, what evidence this store holds, what the library could produce but this store has not yet
recorded, and what the library does not cover at all.

WHY THIS EXISTS. On 2026-09-17 the owner asked why the product still described itself as "the
agent-memory slice" when the plan since 2026-09-15 is the whole evidence product. The honest answer
was that nobody could say in one place which duties were covered: the plan's own table said "build"
for four duties that had shipped in 2.29.0, and the pages said "slice" out of habit. A claim that
cannot be measured drifts. This module is the measurement: a fixed registry of obligations, each with
a probe that reads the store and the ledger beside it and returns one of four states.

STATES, per obligation:
  EVIDENCE     this store holds at least one artifact for the duty (an entry, a receipt, a record),
               and the probe names how many.
  CAPABILITY   the library produces the artifact, and this store has none yet. That is the honest
               state of a fresh store for most duties: the tool is there, the operator has not used it.
  NOT COVERED  no call in the library produces evidence for the duty. The `gap` names what would.
  NOT APPLICABLE  the duty does not fall on an operator of an AI agent (for example the obligations
               of a general-purpose model provider), with the reason.

SCOPE OF THE REGISTRY. The duties that bind a PROVIDER or a DEPLOYER of an AI system that runs an
agent, plus the duties that bind every operator (Art. 4, Art. 5, Art. 50), plus the GDPR articles a
controller answers about the personal data such an agent holds. General-purpose model provider
duties (Art. 53 to 55) are listed as not applicable rather than left out, so a reader sees the edge
of the scope rather than assuming it. Article numbers are the consolidated text of Regulation (EU)
2024/1689 as amended by Regulation (EU) 2026/1744, and Regulation (EU) 2016/679.

WHAT THIS IS NOT. A coverage row says that an artifact exists, not that it satisfies an assessor.
"EVIDENCE for Art. 9" will mean a risk register with entries, not a finding that the risks were the
right ones. The reports this library prints say "evidence" and never "certified", and so does this.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

EVIDENCE = "EVIDENCE"
CAPABILITY = "CAPABILITY"
NOT_COVERED = "NOT COVERED"
NOT_APPLICABLE = "NOT APPLICABLE"


# ----------------------------------------------------------------------------- probes
# Each probe returns (count, detail). count > 0 means EVIDENCE; 0 means CAPABILITY. A probe that
# raises is reported as CAPABILITY with the error in `detail`, never as EVIDENCE.

def _ledger(store):
    from .actions import ActionLedger
    led = ActionLedger(store)
    if not led.path.exists():
        return None
    return led


def _entries(store, kind: str) -> list[dict]:
    led = _ledger(store)
    if led is None:
        return []
    return [e for e in led.entries() if e.get("kind", "action") == kind]


def _p_actions(store):
    n = len(_entries(store, "action"))
    signed = sum(1 for e in _entries(store, "action") if e.get("sig"))
    return n, f"{n} action(s) in the ledger, {signed} signed, each with a memory-state digest"


def _p_receipts(store):
    n = len(getattr(store, "_receipts", None) or [])
    return n, f"{n} write receipt(s) in the chain" + ("" if n else "; open the store with receipts=True")


def _p_retention(store):
    n = len(_entries(store, "retention"))
    led = _ledger(store)
    arch = len((getattr(led, "archived", None) or {}).get("archive_chain") or []) if led else 0
    return n + arch, f"{n} retention attestation(s), {arch} signed archive checkpoint(s)"


def _p_oversight(store):
    ev = _entries(store, "oversight")
    kinds = sorted({e.get("action") or e.get("event") for e in ev})
    return len(ev), f"{len(ev)} oversight event(s)" + (f": {', '.join(str(k) for k in kinds)}" if kinds else "")


def _p_disclosures(store):
    n = len(_entries(store, "disclosure"))
    return n, f"{n} disclosure receipt(s)"


def _p_incidents(store):
    n = len(_entries(store, "incident"))
    return n, f"{n} incident record(s) with the Art. 73 reporting clock"


def _p_risks(store):
    ev = _entries(store, "risk")
    ids = {e.get("risk_id") for e in ev}
    return len(ev), f"{len(ev)} risk entr(ies) over {len(ids)} risk id(s) in the register"


def _p_monitoring(store):
    n = len(_entries(store, "monitoring"))
    return n, f"{n} post-market monitoring report(s) signed into the ledger"


def _p_corrective(store):
    n = len(_entries(store, "corrective"))
    return n, f"{n} corrective action(s) recorded, with the parties informed"


def _p_authority(store):
    n = len(_entries(store, "authority"))
    return n, f"{n} authority request(s) recorded, with what was provided"


def _p_breaches(store):
    ev = [e for e in _entries(store, "breach") if not e.get("event")]
    return len(ev), f"{len(ev)} breach record(s) with the 72-hour clock"


def _p_literacy(store):
    n = len(_entries(store, "literacy"))
    return n, f"{n} AI-literacy measure(s) recorded, with audience and considerations"


def _p_attestations(store):
    ev = _entries(store, "attestation")
    classes = {e.get("practice") for e in ev}
    return len(classes), f"{len(classes)} of 10 prohibited-practice classes attested, {len(ev)} attestation(s)"


def _p_responsibilities(store):
    n = len(_entries(store, "responsibilities"))
    return n, f"{n} value-chain agreement(s) recorded, with the parties and roles"


def _p_declarations(store):
    n = len(_entries(store, "declaration"))
    return n, f"{n} EU declaration(s) of conformity recorded, with the Annex V items"


def _p_documentation(store):
    n = len(_entries(store, "documentation"))
    return n, f"{n} documentation-retention attestation(s) over the Art. 18(1) documents"


def _p_notices(store):
    ev = _entries(store, "notice")
    subjects = {e.get("subject") for e in ev}
    return len(ev), f"{len(ev)} Art. 13/14 notice(s) recorded for {len(subjects)} subject(s)"


def _p_roles(store):
    n = len(_entries(store, "processing_role"))
    return n, f"{n} Art. 28 role declaration(s) recorded"


def _p_objections(store):
    ev = [e for e in _entries(store, "rights") if e.get("event") == "objection"]
    withholding = len([o for o in (getattr(store, "objections", lambda: [])() or [])
                       if o.get("status") in ("standing", "upheld")])
    return len(ev), f"{len(ev)} objection(s) recorded, {withholding} standing or upheld and withheld from recall"


def _p_lifecycle(store):
    n = len(_entries(store, "lifecycle"))
    return n, f"{n} lifecycle event(s)"


def _p_rights(kind: str):
    def probe(store):
        ev = [e for e in _entries(store, "rights") if (e.get("action") or "").endswith(kind)]
        return len(ev), f"{len(ev)} {kind} request(s) recorded in the ledger"
    return probe


def _p_tombstones(store):
    n = len(getattr(store, "_tombstones", None) or [])
    return n, f"{n} signed content-free tombstone(s)"


def _p_sources(store):
    items = list(store.items)
    with_src = sum(1 for r in items if r.get("source"))
    return with_src, f"{with_src} of {len(items)} record(s) carry a source, which is what a subject request resolves on"


def _p_pii(store):
    items = list(store.items)
    n = sum(1 for r in items if (r.get("meta") or {}).get("pii") or r.get("pii"))
    return n, f"{n} record(s) tagged as personal data; pii_report() and retention() read them"


def _p_partitions(store):
    try:
        from .partitions import partitions_report
        rep = partitions_report(store)
        n = len(rep.get("partitions") or [])
    except Exception as e:                                        # noqa: BLE001
        return 0, f"partitions_report unavailable: {type(e).__name__}"
    return n, f"{n} memory partition(s) with expiry"


def _p_report(fn_name: str, module: str):
    """A duty whose artifact is a generated document. EVIDENCE when the document builds AND the store
    holds something for it to describe (a record, a receipt or a ledger entry); CAPABILITY on an
    empty store, where the document would be a template with nothing in it."""
    def probe(store):
        import importlib
        mod = importlib.import_module(f"inspeximus.{module}")
        fn = getattr(mod, fn_name)
        try:
            fn(store)
        except TypeError:
            fn(store, None)
        led = _ledger(store)
        n = len(list(store.items)) + len(getattr(store, "_receipts", None) or []) + (len(led) if led else 0)
        return n, f"{fn_name}() builds over {len(list(store.items))} record(s) and {len(led) if led else 0} ledger entr(ies)"
    return probe


def _p_robustness(store):
    """Art. 15 (3.6.0): the anchor as before, and the evidence rows must be present and none STALE."""
    from .compliance import robustness_evidence
    ev = robustness_evidence()
    n_rows = len(ev["rows"])
    if not n_rows:
        return 0, "robustness_evidence.json carries no rows"
    if ev["stale"]:
        return 0, f"{len(ev['stale'])} of {n_rows} robustness evidence row(s) STALE: {', '.join(ev['stale'])}"
    try:
        anc = store.anchor()
        n = int(anc.get("n_writes") or 0) + int(anc.get("n_tombstones") or 0)
    except Exception as e:                                        # noqa: BLE001
        return 0, f"anchor() unavailable: {type(e).__name__}"
    # The rows describe the LIBRARY and are the same on every store, so they do not make a fresh
    # store's row EVIDENCE: the count is the store's own anchor, as before, and the rows gate it.
    return n, (f"{n_rows} robustness evidence row(s) ({', '.join(r['id'] for r in ev['rows'])}) verified, "
               f"anchor over {anc.get('n_writes')} write(s)")


def _p_qms(store):
    led = _ledger(store)
    if led is None:
        return 0, "no action ledger, so no qms register"
    reg = led.qms_register()
    return reg["procedures"], (f"{reg['procedures']} qms procedure(s), {len(reg['overdue'])} overdue, "
                               f"aspects covered: {''.join(reg['aspects_covered']) or 'none'}")


def _p_anchor(store):
    try:
        anc = store.anchor()
        n = int(anc.get("n_writes") or 0) + int(anc.get("n_tombstones") or 0)
    except Exception as e:                                        # noqa: BLE001
        return 0, f"anchor() unavailable: {type(e).__name__}"
    return n, f"anchor over {anc.get('n_writes')} write(s) and {anc.get('n_tombstones')} tombstone(s); verify_writes() and audit_the_audits() are the checks"


# ----------------------------------------------------------------------------- the registry
# One row per obligation. `probe` None means the library has no artifact for it (NOT COVERED) or
# the duty is out of scope (NOT APPLICABLE, with `why`).

OBLIGATIONS: list[dict[str, Any]] = [
    # ---- EU AI Act: every operator
    {"id": "aia-4", "law": "AI Act", "article": "Art. 4", "duty": "AI literacy of staff", "who": "provider, deployer",
     "probe": _p_literacy, "artifact": "record_literacy() with the measure, audience and Art. 4 considerations; literacy_register(); the count in the deployer report"},
    {"id": "aia-5", "law": "AI Act", "article": "Art. 5", "duty": "prohibited practices are not used", "who": "provider, deployer",
     "probe": _p_attestations, "artifact": "record_attestation() per Art. 5(1) class, dated and signed; attestation_register() names the classes with none"},
    {"id": "aia-50", "law": "AI Act", "article": "Art. 50", "duty": "disclosure that the user interacts with an AI system; marking of generated content",
     "who": "provider, deployer", "probe": _p_disclosures, "artifact": "record_disclosure()"},
    # ---- EU AI Act: high-risk provider
    {"id": "aia-9", "law": "AI Act", "article": "Art. 9", "duty": "risk management system over the lifecycle", "who": "provider",
     "probe": _p_risks, "artifact": "ActionLedger.risk() entries, risk_register(); 9(2)(a) to (d), 9(5), 9(9) fields"},
    {"id": "aia-10", "law": "AI Act", "article": "Art. 10", "duty": "data governance: provenance, relevance, bias examination of the data the system works on",
     "who": "provider", "probe": _p_sources, "artifact": "provenance(), pii_report(), retention(); per-record source and lineage"},
    {"id": "aia-11", "law": "AI Act", "article": "Art. 11, Annex IV", "duty": "technical documentation", "who": "provider",
     "probe": _p_report("annex_iv", "technical_documentation"), "artifact": "annex_iv()"},
    {"id": "aia-12", "law": "AI Act", "article": "Art. 12", "duty": "automatic recording of events over the lifetime", "who": "provider",
     "probe": _p_actions, "artifact": "ActionLedger.record(); actions verify; export-trail"},
    {"id": "aia-13", "law": "AI Act", "article": "Art. 13", "duty": "instructions for use, including how to read the logs", "who": "provider",
     "probe": _p_report("instructions_for_use", "technical_documentation"), "artifact": "instructions_for_use()"},
    {"id": "aia-14", "law": "AI Act", "article": "Art. 14", "duty": "human oversight: intervene, override, stop", "who": "provider, deployer",
     "probe": _p_oversight, "artifact": "record_oversight(): approve, refuse, override, stop, review"},
    {"id": "aia-15", "law": "AI Act", "article": "Art. 15", "duty": "accuracy, robustness, cybersecurity, resilience to poisoning", "who": "provider",
     "probe": _p_robustness, "artifact": "receipt chain, verify_writes(), influence gate, echo guard, audit_the_audits(); "
                                         "compliance_report().robustness_evidence: the echo, poisoning and split-view measurements as dated rows with the receipt sha"},
    {"id": "aia-17", "law": "AI Act", "article": "Art. 17", "duty": "quality management system", "who": "provider",
     "probe": _p_qms, "artifact": "record_qms() per procedure with owner, version, review date and the Art. 17(1) aspect; qms_register() names the overdue ones; compliance_report(), audit bundle"},
    {"id": "aia-18", "law": "AI Act", "article": "Art. 18", "duty": "keep the documentation ten years", "who": "provider",
     "probe": _p_documentation, "artifact": "attest_documentation_retention() over the Art. 18(1)(a) to (e) documents, with the ten-year end date"},
    {"id": "aia-19", "law": "AI Act", "article": "Art. 19", "duty": "keep the logs at least six months", "who": "provider",
     "probe": _p_retention, "artifact": "attest_retention(), archive under a signed checkpoint"},
    {"id": "aia-20", "law": "AI Act", "article": "Art. 20", "duty": "corrective actions: withdraw, disable, recall; inform the chain", "who": "provider",
     "probe": _p_corrective, "artifact": "corrective_action() with the parties informed; corrective_action_report(seq)"},
    {"id": "aia-21", "law": "AI Act", "article": "Art. 21", "duty": "cooperation with authorities: hand over documentation and logs", "who": "provider",
     "probe": _p_authority, "artifact": "authority_request() naming the request, its scope and what was provided (by reference); the audit bundle, export-trail and compliance_report() are what is handed over"},
    {"id": "aia-25", "law": "AI Act", "article": "Art. 25", "duty": "responsibilities along the value chain", "who": "provider, distributor, deployer",
     "probe": _p_responsibilities, "artifact": "record_responsibilities() with the parties, roles, the 25(1) trigger and the 25(2) items; responsibilities_register()"},
    {"id": "aia-43", "law": "AI Act", "article": "Art. 43, 47, 48", "duty": "conformity assessment, EU declaration of conformity, CE marking", "who": "provider",
     "probe": _p_declarations, "artifact": "record_declaration() with the Annex V items, the Art. 43 procedure and the Art. 48 marking; declaration_document(seq)",
     "boundary": "the conformity assessment itself is carried out by the provider or a notified body. The ledger holds the declaration drawn up from it, which is the evidence side of this duty. This is a statement of who does what, not a gap in the library"},
    {"id": "aia-49", "law": "AI Act", "article": "Art. 49, Annex VIII", "duty": "registration in the EU database", "who": "provider, some deployers",
     "probe": _p_report("registration_export", "technical_documentation"), "artifact": "registration_export() sections A, B, C"},
    {"id": "aia-72", "law": "AI Act", "article": "Art. 72", "duty": "post-market monitoring plan and reports", "who": "provider",
     "probe": _p_monitoring, "artifact": "post_market_report() signed into the ledger, carrying the plan by name, version and hash",
     "boundary": "the monitoring plan is the provider's own document, and the Commission's template (Art. 72(3)) is not published yet. The library records the plan by name, version and hash and signs the reports against it. This is a statement of who does what, not a gap in the library"},
    {"id": "aia-73", "law": "AI Act", "article": "Art. 73", "duty": "serious incident reporting within the deadlines", "who": "provider",
     "probe": _p_incidents, "artifact": "record_incident(), incident_report() with the reporting clock"},
    # ---- EU AI Act: deployer
    {"id": "aia-26", "law": "AI Act", "article": "Art. 26", "duty": "deployer duties: use per instructions, assign oversight, monitor, keep logs, inform workers",
     "who": "deployer", "probe": _p_report("deployer_report", "deployer"), "artifact": "deployer_report()"},
    {"id": "aia-27", "law": "AI Act", "article": "Art. 27", "duty": "fundamental rights impact assessment", "who": "deployer (public bodies, named private uses)",
     "probe": _p_report("fria_appendix", "deployer"), "artifact": "fria_appendix()"},
    {"id": "aia-86", "law": "AI Act", "article": "Art. 86", "duty": "explanation of an individual decision to the affected person", "who": "deployer",
     "probe": _p_rights("explanation"), "artifact": "decision_explanation(seq, actor=...): the action, what it knew, the oversight on it, in one document, logged as rights:explanation"},
    # ---- EU AI Act: out of scope for an agent operator
    {"id": "aia-53", "law": "AI Act", "article": "Art. 53 to 55", "duty": "general-purpose AI model provider duties", "who": "GPAI model provider",
     "probe": None, "why": "an operator of an agent built on a model is not the model's provider; the model provider's documentation is an input to Annex IV, not this store's output"},
    {"id": "aia-60", "law": "AI Act", "article": "Art. 60, 61", "duty": "real-world testing outside sandboxes, informed consent", "who": "provider testing pre-market",
     "probe": None, "why": "a testing regime, not an operating one; the consent records it needs are the disclosure and oversight events above once a test runs"},
    # ---- GDPR
    {"id": "gdpr-5", "law": "GDPR", "article": "Art. 5(2)", "duty": "accountability: demonstrate compliance", "who": "controller",
     "probe": _p_receipts, "artifact": "receipt chain, anchor, transparency log, offline verifiers"},
    {"id": "gdpr-13", "law": "GDPR", "article": "Art. 13, 14", "duty": "information given to the data subject at collection", "who": "controller",
     "probe": _p_notices, "artifact": "record_notice() per subject with the article, channel, items given and items missing; notice_register()"},
    {"id": "gdpr-15", "law": "GDPR", "article": "Art. 15", "duty": "right of access", "who": "controller",
     "probe": _p_rights("export"), "artifact": "export_subject()"},
    {"id": "gdpr-16", "law": "GDPR", "article": "Art. 16", "duty": "rectification", "who": "controller",
     "probe": _p_rights("rectify"), "artifact": "rectify()"},
    {"id": "gdpr-17", "law": "GDPR", "article": "Art. 17", "duty": "erasure", "who": "controller",
     "probe": _p_tombstones, "artifact": "forget_subject(), erasure_certificate(), erasure-verify"},
    {"id": "gdpr-20", "law": "GDPR", "article": "Art. 20", "duty": "portability in a machine-readable format", "who": "controller",
     "probe": _p_rights("portability"), "artifact": "export_subject(basis='portability'): the Art. 20 response with a versioned format and per-record portable flags; rights:portability entry"},
    {"id": "gdpr-21", "law": "GDPR", "article": "Art. 21", "duty": "objection: stop the processing", "who": "controller",
     "probe": _p_objections, "artifact": "record_objection() withholds the subject's records from recall on the store; resolve_objection(); rights:objection entries"},
    {"id": "gdpr-22", "law": "GDPR", "article": "Art. 22", "duty": "automated decisions: human review on request", "who": "controller",
     "probe": _p_oversight, "artifact": "record_oversight(review) on the action"},
    {"id": "gdpr-25", "law": "GDPR", "article": "Art. 25", "duty": "data protection by design and by default", "who": "controller",
     "probe": _p_partitions, "artifact": "memory partitions with expiry, retention sweep, PII tagging"},
    {"id": "gdpr-28", "law": "GDPR", "article": "Art. 28", "duty": "processor obligations and records", "who": "controller, processor",
     "probe": _p_roles, "artifact": "record_processing_role() with the controller, the 28(3) instructions and the 28(2) sub-processors; processing_roles()"},
    {"id": "gdpr-30", "law": "GDPR", "article": "Art. 30", "duty": "records of processing activities", "who": "controller",
     "probe": _p_report("compliance_report", "compliance"), "artifact": "compliance_report() records section"},
    {"id": "gdpr-33", "law": "GDPR", "article": "Art. 33, 34", "duty": "breach notification within 72 hours, and to the subject", "who": "controller",
     "probe": _p_breaches, "artifact": "breach() with the 72-hour clock and the 33(3) fields; breach_notified() to the authority, the subjects or the public; breach_report(seq)"},
    {"id": "gdpr-35", "law": "GDPR", "article": "Art. 35", "duty": "data protection impact assessment", "who": "controller",
     "probe": _p_report("dpia_appendix", "deployer"), "artifact": "dpia_appendix()"},
]


def gap_names() -> set:
    """The functions the NOT COVERED rows promise, in the order the plan closes them.

    A doc may name these in backticks (the plan does, in section 0) without claiming they exist;
    the test that reads docs for capabilities exempts exactly this set, and the coverage test fails
    the day one of them ships without its row being rewritten. Two registries would drift."""
    names = set()
    for ob in OBLIGATIONS:
        if ob.get("probe") is None and not ob.get("why"):
            names |= set(re.findall(r"`([a-z_]+)`", ob["gap"]))
    return names


def coverage(store) -> dict:
    """The matrix for one store. Every row carries `state`, `detail`, and the artifact or gap."""
    rows = []
    for ob in OBLIGATIONS:
        row = {k: ob[k] for k in ("id", "law", "article", "duty", "who")}
        if ob.get("why"):
            row["state"] = NOT_APPLICABLE
            row["detail"] = ob["why"]
        elif ob.get("probe") is None:
            row["state"] = NOT_COVERED
            row["detail"] = ob["gap"]
        else:
            try:
                n, detail = ob["probe"](store)
            except Exception as e:                                # noqa: BLE001
                n, detail = 0, f"probe could not run: {type(e).__name__}: {e}"[:200]
            row["state"] = EVIDENCE if n > 0 else CAPABILITY
            row["count"] = int(n)
            row["detail"] = detail
            row["artifact"] = ob.get("artifact")
            # A NOTE THAT STATES A FACT IS NOT AN OPEN ROW. These two say which document belongs
            # to the provider or a notified body rather than to this library, and reading them as
            # unfinished work is what the rename is for. `partial` stays beside it for readers
            # written before 3.6.3, and both carry the same text.
            if ob.get("boundary"):
                row["boundary"] = ob["boundary"]
                row["partial"] = ob["boundary"]
        rows.append(row)
    counts = {s: sum(1 for r in rows if r["state"] == s) for s in (EVIDENCE, CAPABILITY, NOT_COVERED, NOT_APPLICABLE)}
    in_scope = counts[EVIDENCE] + counts[CAPABILITY] + counts[NOT_COVERED]
    return {"kind": "inspeximus.coverage/1", "ts": time.time(), "store": str(getattr(store, "path", "")),
            "rows": rows, "counts": counts, "in_scope": in_scope,
            "covered": counts[EVIDENCE] + counts[CAPABILITY],
            "scope": ("Evidence that this store holds an artifact for a duty, or that the library can produce one. "
                      "Not a certification, not a conformity assessment, and not a finding that the artifact satisfies "
                      "an assessor. Duties of general-purpose model providers are listed as not applicable.")}


def render_text(rep: dict) -> str:
    w = max(len(r["article"]) for r in rep["rows"])
    lines = [f"coverage of {rep['store'] or 'the store'}: {rep['covered']} of {rep['in_scope']} in-scope duties covered "
             f"({rep['counts'][EVIDENCE]} with evidence in this store, {rep['counts'][CAPABILITY]} capability only), "
             f"{rep['counts'][NOT_COVERED]} not covered, {rep['counts'][NOT_APPLICABLE]} not applicable", ""]
    for r in rep["rows"]:
        tag = {EVIDENCE: "EVIDENCE      ", CAPABILITY: "CAPABILITY    ", NOT_COVERED: "NOT COVERED   ",
               NOT_APPLICABLE: "NOT APPLICABLE"}[r["state"]]
        lines.append(f"  {tag} {r['law']:<6} {r['article']:<{w}}  {r['duty']}")
        lines.append(f"  {'':14} {'':6} {'':{w}}  {r['detail']}")
        if r.get("boundary"):
            lines.append(f"  {'':14} {'':6} {'':{w}}  scope: {r['boundary']}")
    lines.append("")
    lines.append("  " + rep["scope"])
    return "\n".join(lines)


def render_markdown(rep: dict) -> str:
    """The table the plan document and the site carry, generated so they cannot drift from the code."""
    out = ["| law | article | duty | who | state | artifact or gap |", "|---|---|---|---|---|---|"]
    for r in rep["rows"]:
        what = r.get("artifact") or r["detail"]
        if r.get("boundary"):
            what += f"; scope: {r['boundary']}"
        out.append(f"| {r['law']} | {r['article']} | {r['duty']} | {r['who']} | {r['state']} | {what} |")
    return "\n".join(out)


def _cli(argv=None):
    import argparse
    import sys
    ap = argparse.ArgumentParser(description="the obligation matrix for one store")
    ap.add_argument("--path", default="inspeximus_memory.json")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--markdown", action="store_true")
    a = ap.parse_args(argv)
    from .core import Inspeximus
    rep = coverage(Inspeximus(a.path, receipts=True))
    if a.json:
        print(json.dumps(rep, indent=2, ensure_ascii=False))
    elif a.markdown:
        print(render_markdown(rep))
    else:
        print(render_text(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
