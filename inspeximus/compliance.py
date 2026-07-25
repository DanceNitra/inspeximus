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
import html as _html
import time
from .core import __version__

# Obligation wording is conservative and traceable to the consolidated Reg (EU) 2024/1689 / Reg (EU) 2016/679
# texts (see docs/COMPLIANCE.md, which was primary-source checked). "Evidence for", never "guarantees".
_CONTROLS = [
    ("EU AI Act (Reg (EU) 2024/1689)", "Art. 12", "Record-keeping (automatic logging over the lifetime)",
     "High-risk AI systems must technically allow the automatic recording of events (logs) over the system's "
     "lifetime, ensuring a level of traceability appropriate to the intended purpose.",
     "Every write is a hash-linked, timestamped receipt; anchor() emits a signed tree head committing to the "
     "whole history; the log is portable and INDEPENDENTLY verifiable offline (audit-build / audit-verify).",
     "write_receipts"),
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
    ("GDPR (Reg (EU) 2016/679)", "Art. 17", "Right to erasure",
     "The controller must erase personal data without undue delay on a valid request, and be able to "
     "demonstrate the erasure took place.",
     "forget_subject / forget_pii hard-delete the subject plus its derived lineage and emit a signed, "
     "content-free tombstone; erasure_certificate / erasure_report are the portable proof-of-deletion.",
     "erasures"),
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
]


def compliance_report(store, expected_pubkey: str | None = None) -> dict:
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
        controls.append({
            "framework": framework, "article": art, "title": title,
            "obligation": obligation, "inspeximus_evidence": evidence,
            "live_count": count, "status": status,
        })

    return {
        "kind": "inspeximus.compliance_report/1",
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
    if require_receipts and n_receipts and n_records > n_receipts:
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

    if max_pii_age_days is not None:
        checked.append("pii_retention")
        now = now_ts if now_ts is not None else time.time()
        cutoff = now - float(max_pii_age_days) * 86400.0
        tv = getattr(store, "tenant", None)
        stale = [r["id"] for r in getattr(store, "items", [])
                 if r.get("status") == "active" and r.get("pii")
                 and (tv is None or r.get("tenant") == tv) and (r.get("ts") or 0) < cutoff]
        if stale:
            violations.append({"code": "pii_over_retention", "article": "GDPR Art. 5(1)(e)",
                               "detail": f"{len(stale)} active PII record(s) older than {max_pii_age_days} days "
                                         "— storage-limitation breach; run forget_pii()"})

    return {"ok": not violations, "violations": violations, "checked": checked}


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
           if r.get("status") == "active" and (r.get("pii") if pii_only else True)
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
