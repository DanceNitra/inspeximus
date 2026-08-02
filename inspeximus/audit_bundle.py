"""Portable audit bundle -- hand a DPO/auditor ONE file and a one-line command, no live store, no library
internals, no PII.

EU AI Act Article 12 (record-keeping / logging; applies to high-risk systems from 2 Dec 2027 for standalone
Annex III systems, deferred from 2 Aug 2026 by Reg (EU) 2026/1744) and GDPR Art.17/30 (erasure + a record
of the erasure ACT) ask an operator to PRODUCE, on demand, a tamper-evident log of what the system recorded,
what changed, and what was erased -- and to let an independent party verify it. inspeximus already computes
every piece (governance_report, supersession_report, anchor, the hash-linked write/tombstone chains); this
module serialises them into ONE self-verifying artifact and ships a STANDALONE verifier the auditor runs
against the file alone:

    python -m inspeximus.audit_bundle build  --path store.json --out bundle.json     # operator exports
    python -m inspeximus.audit_bundle verify bundle.json                             # auditor checks, offline

The bundle is CONTENT-FREE: the write receipts commit to content/attribution HASHES (never text), the
tombstones carry surrogate memory ids + request ids, and the access-control block carries the grant/revoke
ACTS (which agent, which selector label, granted or revoked, when) -- so the artifact proves the ACTS (a
write with this commitment happened at T; a record with this id was erased at T for request R; agent B could
read the records tagged X from T1 until T2) and their append-only integrity, never the content. That is exactly the honest boundary governance_report already states, now portable.

HONEST SCOPE (unchanged, restated in-band): this verifies THIS store's own integrity, not the app's vector
index / prompt logs / backups; it is a tamper-evident record-keeping ARTIFACT, not a compliance certification.
The internal signatures are load-bearing only against a party who does NOT hold receipt_key; for an
operator-adversarial audit the auditor must have witnessed a PRIOR anchor out of band (pass `witnesses=` to
check co-signatures) -- the append-only guarantee comes from that external witness, not from the bundle alone.
"""
from __future__ import annotations
import json
import os
from .core import Inspeximus, _sha256_hex, _canon, _GENESIS, __version__, sth_hash_of

BUNDLE_KIND = "inspeximus.audit_bundle/1"


def _bundle_hash(bundle: dict) -> str:
    """SHA-256 over the whole bundle EXCEPT its own bundle_hash field (canonical, order-independent)."""
    return _sha256_hex(_canon({k: v for k, v in bundle.items() if k != "bundle_hash"}))


def _content_free_writes(store) -> list:
    """Every field the write receipt's hash commits to, plus the hash itself, so an offline verifier can
    re-derive it. Content-free: `commit` is hashes, never text.

    `amends` is OPTIONAL and part of the preimage since 1.68.0 (see Inspeximus._chain_core). Exporting a
    fixed field list dropped it, so a bundle from any store where slash()/restore() had ever run could not
    be verified -- by any version, including the one that produced it. The tombstone exporter below already
    handles its optional `auth` block for exactly this reason."""
    out = []
    for r in store._receipts:
        rec = {k: r.get(k) for k in ("seq", "ts", "memory_id", "commit", "prev", "hash")}
        if r.get("amends"):
            rec["amends"] = r["amends"]
        out.append(rec)
    return out


def _content_free_tombstones(store) -> list:
    # Every field the tombstone's hash commits to (seq/memory_id/ts/request_id/prev + the optional content-free
    # auth block: basis + authorizer pubkey + signature) PLUS the hash and any receipt signature, so an offline
    # verifier can re-derive each hash. All content-free: a hash of PII is still not the PII.
    out = []
    for t in store._tombstones:
        rec = {k: t.get(k) for k in ("seq", "memory_id", "ts", "request_id", "prev", "hash")}
        if t.get("auth"):
            rec["auth"] = t["auth"]
        if t.get("sig"):
            rec["sig"] = t["sig"]
        out.append(rec)
    return out


def _acl_acts(store) -> list:
    """Every agent-to-agent read GRANT and REVOCATION, oldest first -- the access-control half of the
    record-keeping question ("who was allowed to read what, and when was it taken back").

    It needs no chain of its own: a grant is an ordinary store record, so it is already covered by the
    write receipt chain exported above and by the anchor. What is added here is the READING of those
    records, because a receipt commits to a hash and an auditor cannot recover "bob could read the billing
    tag between T1 and T2" from a hash.

    STILL NO MEMORY TEXT. The fields are the access-control act itself -- the agents, the selector label or
    the surrogate record ids it names, and whether it granted or revoked. Nothing from any memory's content
    crosses into the bundle here, which is the boundary the rest of this module keeps."""
    # AttributeError ONLY, deliberately narrow. A store object from before the ACL simply has no
    # grant_log(), and that is not a failure. A bare `except Exception` here would also swallow a real
    # error and hand the auditor a bundle whose access-control section is empty because it BROKE -- an
    # absent ACL block reads as "no grants were ever issued", which is a statement, not a silence.
    if not hasattr(store, "grant_log"):
        return []
    acts = store.grant_log()
    keep = ("id", "key", "agent", "by", "kind", "value", "state", "status", "ts")
    return [{k: a.get(k) for k in keep} for a in sorted(acts, key=lambda x: x.get("ts") or 0)]


def build_bundle(store, expected_pubkey: str | None = None, sign=None) -> dict:
    """Serialise a store's record-keeping state into one portable, self-verifying artifact. `expected_pubkey`
    pins the signature-authenticity check; `sign(bytes)->hex` (opt-in) lets an external witness co-sign the
    anchor. Returns the bundle dict (json-serialisable). Content-free -- no memory text leaves the store."""
    anchor = store.anchor(sign=sign)
    bundle = {
        "kind": BUNDLE_KIND,
        "inspeximus_version": __version__,
        "generated_ts": anchor.get("ts"),
        "tenant": getattr(store, "tenant", None),
        "anchor": anchor,
        "governance": store.governance_report(expected_pubkey),
        "supersession": store.supersession_report(),
        "n_records": len(getattr(store, "items", []) or []),   # content-free count: lets the verifier tell
        #                                        "empty store" from "store with data but receipts disabled"
        "write_chain": _content_free_writes(store),
        "tombstone_chain": _content_free_tombstones(store),
        "grants": _acl_acts(store),
    }
    bundle["bundle_hash"] = _bundle_hash(bundle)
    return bundle


def _rewalk(records: list, kind: str) -> tuple[str, int]:
    """Re-derive the hash-chain tip over `records` from genesis, verifying each record's own hash and prev-link.
    Returns (tip, first_bad_index): first_bad_index == -1 means the whole chain is internally consistent."""
    prev = _GENESIS
    for i, r in enumerate(records):
        if r.get("prev") != prev:
            return prev, i
        core = Inspeximus._chain_core(r, kind)
        if _sha256_hex(_canon(core)) != r.get("hash"):
            return prev, i
        prev = r.get("hash")
    return prev, -1


def bind_content(bundle: dict, store_items: list) -> dict:
    """Bind a bundle's commitments to the CONTENT a store is serving today.

    `verify_bundle` proves the chain is internally consistent and matches the signed anchor. It proves
    nothing about what the store now says, because the bundle is content-free by design -- it carries
    hashes, never text. So an auditor holding only a bundle can be shown a clean chain over substituted
    content, which is exactly what an out-of-band edit followed by a legitimate amendment produces.

    This closes that gap without putting content in the bundle: hand it the bundle AND a dump of the
    store, and it re-derives each record's commitment and compares it to the EARLIEST receipt that
    covers that record -- the original commitment, not the latest one, because the latest is precisely
    what an amendment would have rewritten.

    Returns {ok, checked, mismatched, unreceipted, orphaned, problems}:
      mismatched  -- the record is present but no longer hashes to what its first receipt committed to;
      unreceipted -- the store holds a record the chain never covered (added out of band);
      orphaned    -- the chain covers a record the store no longer holds (deleted out of band, and NOT
                     explained by a tombstone: a legitimate erasure leaves one, so check the tombstone
                     chain before reading this as tampering).

    HONEST SCOPE. This binds content to the chain; it does not tell you the content was ever TRUE, and it
    cannot help if the attacker controls both the store and the bundle. Its value is that a bundle
    witnessed or held externally becomes usable against a store dump produced later -- the independent
    source of truth that log integrity alone never supplies (RFC 6962's inclusion-is-not-validity, in
    small).
    """
    problems: list = []
    chain = bundle.get("write_chain") or []
    if not chain:
        return {"ok": False, "checked": 0, "mismatched": [], "unreceipted": [], "orphaned": [],
                "problems": ["the bundle carries no write chain, so there is nothing to bind content to"]}

    first: dict = {}                      # memory_id -> the EARLIEST commitment for it
    for r in sorted(chain, key=lambda x: x.get("seq", 0)):
        first.setdefault(r.get("memory_id"), (r.get("commit") or {}))

    by_id = {r.get("id"): r for r in (store_items or [])}
    mismatched, unreceipted, orphaned = [], [], []

    compared = 0                          # records actually RE-HASHED, which is the only number that
                                          # evidences anything -- len(first) counts receipts, not checks
    for mid, commit in first.items():
        rec = by_id.get(mid)
        if rec is None:
            orphaned.append(mid)
            continue
        compared += 1
        now = Inspeximus._write_commit(rec)
        # Compare only the fields the bundle actually carries, so a bundle written by an older version
        # (no immutable_sha256) is checked on what it does commit to rather than reported as broken.
        for field in ("immutable_sha256", "content_sha256", "value_sha256", "attrib_sha256"):
            if field in commit and commit[field] != now.get(field):
                mismatched.append({"memory_id": mid, "field": field})
                break

    for mid in by_id:
        if mid not in first:
            unreceipted.append(mid)

    if mismatched:
        problems.append(f"{len(mismatched)} record(s) no longer match the commitment their FIRST receipt "
                        f"made: the chain is intact but the content it covers has changed")
    if unreceipted:
        problems.append(f"{len(unreceipted)} record(s) in the store are covered by NO receipt in this "
                        f"bundle -- written out of band, or the bundle predates them")
    if orphaned:
        problems.append(f"{len(orphaned)} record(s) the chain covers are absent from the store; a "
                        f"legitimate erasure leaves a tombstone, so check the tombstone chain before "
                        f"reading this as tampering")
    if compared == 0 and first:
        # ZERO COMPARISONS, reported as a pass. Hand it an empty store -- or one whose ids were re-minted
        # while the text was rewritten -- and every record lands in `orphaned`, nothing is re-hashed, and
        # `ok` came back True with "content checked" printed beside the verdict. An audit that compared
        # nothing is the strongest possible version of a check that cannot fail.
        problems.append(f"NOT ONE of the {len(first)} record(s) in this bundle was found in the store, so "
                        f"no content was compared at all -- this is not a clean content check, it is the "
                        f"absence of one (wrong store, or the ids were re-minted)")
    return {"ok": bool(compared) and not mismatched, "checked": compared, "receipted": len(first),
            "mismatched": mismatched, "unreceipted": unreceipted, "orphaned": orphaned,
            "problems": problems}


def verify_bundle(bundle: dict, witnesses: list | None = None, threshold: int = 1,
                  store_items: list | None = None) -> dict:
    """STANDALONE offline verification of an audit bundle -- needs only the file (no store, no receipt key).
    Checks, in order: (1) the bundle's own hash; (2) the write chain re-walks from genesis and its tip+count
    match the anchor; (3) same for the tombstone chain; (4) the anchor's sth_hash is internally consistent;
    (5) if `witnesses` (allowlisted pubkeys) is given and the anchor carries co-signatures, k-of-n verifies.
    Returns {ok, checks:[...passed...], problems:[...failed...], summary:{...}}. `ok` is True iff no problems.
    Note: (5) is the only operator-ADVERSARIAL check; without a witnessed prior anchor, a key-holder rewrite is
    internally consistent by construction -- 1-4 prove append-only INTEGRITY, not that the operator is honest.
    Checks (6) coverage-of-the-store and (7) chain-covers-every-record are ADVISORY for the same reason and
    one step weaker: they read fields the exporter controls (`governance.proof.verified`, `n_records`) under an
    UNKEYED bundle hash, so both can be forged by recomputing it. They catch a misconfigured or accidentally
    altered export -- the common case -- and prove nothing against a determined one.

    CONTENT. The bundle carries hashes and never text, so none of the above says anything about what the
    store is serving today: a clean chain over substituted content verifies PASS here, which is precisely
    what an out-of-band edit plus a legitimate amendment produces. Pass `store_items=` (a dump of the
    store) and check (8) re-derives each record's commitment against the EARLIEST receipt covering it, and
    a mismatch becomes a problem like any other. Without it the verdict is still returned -- the chain
    genuinely did verify -- but `summary.content_checked` is False and `limits` says so in words, because
    an `ok` that quietly omits the one thing an auditor came to ask is worse than a refusal."""
    checks, problems = [], []

    def ok(msg): checks.append(msg)
    def bad(msg): problems.append(msg)

    if not isinstance(bundle, dict) or bundle.get("kind") != BUNDLE_KIND:
        return {"ok": False, "checks": [], "problems": [f"not an {BUNDLE_KIND} bundle"], "summary": {}}

    # (1) bundle integrity
    if bundle.get("bundle_hash") == _bundle_hash(bundle):
        ok("bundle_hash matches (no field was altered after export)")
    else:
        bad("bundle_hash MISMATCH -- the bundle was modified after export")

    anchor = bundle.get("anchor") or {}
    wc = bundle.get("write_chain") or []
    tc = bundle.get("tombstone_chain") or []

    # (2) write chain
    w_tip, w_bad = _rewalk(wc, "write")
    if w_bad != -1:
        bad(f"write chain breaks at index {w_bad} (bad prev-link or hash)")
    elif w_tip != anchor.get("writes_tip") or len(wc) != anchor.get("n_writes"):
        bad(f"write chain tip/count does not match anchor "
            f"(chain: {len(wc)} recs tip {w_tip[:12]}..., anchor: {anchor.get('n_writes')} tip "
            f"{str(anchor.get('writes_tip'))[:12]}...)")
    else:
        ok(f"write chain verifies from genesis: {len(wc)} append-only records -> anchor tip")

    # (3) tombstone (erasure) chain
    t_tip, t_bad = _rewalk(tc, "tombstone")
    if t_bad != -1:
        bad(f"tombstone chain breaks at index {t_bad} (bad prev-link or hash)")
    elif t_tip != anchor.get("tombstones_tip") or len(tc) != anchor.get("n_tombstones"):
        bad("tombstone chain tip/count does not match anchor")
    else:
        ok(f"erasure chain verifies from genesis: {len(tc)} tombstones -> anchor tip")

    # (4) anchor internal consistency. Shared with core.verify_cosigned_anchor / witness_cosign, which is
    # the point: this check lived HERE only, so the primitive every other witness surface calls shipped
    # without it and a field-substituted anchor verified as co-signed by 3 of 3 witnesses.
    recomputed = sth_hash_of(anchor)
    if recomputed == anchor.get("sth_hash"):
        ok("anchor sth_hash is internally consistent")
    else:
        bad("anchor sth_hash does not match its own fields")

    # (5) external witness co-signatures (the only operator-adversarial check)
    cosigs = anchor.get("cosignatures")
    if witnesses:
        if cosigs:
            v = Inspeximus.verify_cosigned_anchor(anchor, cosigs, witnesses, threshold=threshold)
            if v.get("ok"):
                ok(f"external witnesses co-signed the anchor: {v.get('count')}/{threshold} (operator-adversarial)")
            else:
                bad(f"witness co-signature check FAILED (need {threshold}, got {v.get('count')})")
        else:
            bad("witnesses supplied but the anchor carries no co-signatures -- not operator-adversarially verifiable")
    elif cosigs:
        ok(f"anchor carries {len(cosigs)} co-signature(s) (pass witnesses= to verify them)")

    gov = bundle.get("governance") or {}

    # (6) and (7) are ADVISORY, and the distinction matters. `bundle_hash` is an unkeyed SHA-256 over the
    # bundle's own fields, so an exporter who wants to lie can set `governance.proof.verified` to True or
    # `n_records` to len(write_chain) and recompute it in three lines -- both were demonstrated. These two
    # checks therefore catch a MISCONFIGURED or accidentally-tampered export, never a determined operator.
    # That is the same boundary checks 1-4 already have (internal consistency is not operator honesty); only
    # the witness co-signature in (5) is operator-adversarial. Kept because the accidental case is the common
    # one -- an unreceipted store exported in good faith -- and labelled so nobody reads them as proof.

    # (6) the bundle's OWN verdict on the store it came from. build_bundle() already wrote
    # governance.proof.verified, and until 1.54.0 the verifier never read it -- so a bundle exported from a
    # store whose records had been edited out of band carried `verified: False` and still verified PASS with
    # zero problems. The auditor runs THIS side; it must not be the side that skips the finding.
    proof = (gov.get("proof") or {})
    if proof.get("verified") is False:
        bad("the store reported its own write-verification as FAILED at export time"
            + (f": {'; '.join(map(str, proof.get('problems') or []))}" if proof.get("problems") else "")
            + " -- the chains below re-walk consistently, but the RECORDS no longer match their receipts")

    # (6b) the governance SUMMARY must agree with the tombstone chain it summarises. Every sibling
    # figure already gets this: n_records is checked against the write chain below, and the anchor's
    # counts against both chains in (2)/(3). governance was the one that was not, so a bundle could
    # carry `erasures_total: 0, by_request: {}` while its own tombstone_chain held two tombstones and
    # still verify with ZERO problems -- demonstrated. That is the summary an auditor reads FIRST
    # contradicting the evidence underneath it.
    # SCOPE, matching the note above: this is an internal-consistency check, not operator honesty. An
    # exporter determined to lie edits both halves and re-seals; only the witness co-signature in (5)
    # reaches that. This catches the misconfigured or accidentally-mangled export -- the common case --
    # and, unlike before, stops the two halves of the same fact disagreeing in silence.
    gov_total = gov.get("erasures_total")
    if isinstance(gov_total, int) and gov_total != len(tc):
        bad(f"governance.erasures_total says {gov_total} but the tombstone chain carries {len(tc)} "
            f"tombstone(s) -- the summary and the evidence in this same bundle disagree")
    gov_by_req = gov.get("by_request")
    if isinstance(gov_by_req, dict) and isinstance(gov_total, int):
        summed = sum(v.get("erased", 0) for v in gov_by_req.values() if isinstance(v, dict))
        if summed != gov_total:
            bad(f"governance.by_request accounts for {summed} erasure(s) but erasures_total says "
                f"{gov_total} -- the per-request breakdown does not add up to its own total")

    # (7) an empty chain proves nothing, and 'PASS' on nothing is the most misleading output here. A
    # receipts-disabled store exported a bundle that verified clean with writes=0.
    n_records = bundle.get("n_records")
    if wc and isinstance(n_records, int) and n_records > len(wc):
        # The empty-chain check below only fires when NOTHING is receipted. A store written with receipts
        # OFF and later reopened with them ON has a chain that covers only the tail: 6 records, 1 receipt,
        # and the bundle verified clean -- forging one of the 5 unreceipted records changed nothing.
        bad(f"only {len(wc)} of {n_records} record(s) are covered by a write receipt -- the remaining "
            f"{n_records - len(wc)} were written with receipts disabled and are NOT protected by this chain")
    if not wc and not tc:
        if n_records:
            bad(f"this bundle carries NO write or tombstone receipts, yet the store holds {n_records} "
                f"record(s) -- receipts were not enabled, so there is nothing here to verify, which is "
                f"not the same as verified")
        elif n_records == 0:
            ok("store is empty: nothing to verify (this is not evidence of anything)")
        else:                                    # pre-1.54.0 bundle, no n_records field
            bad("this bundle carries NO write or tombstone receipts -- nothing here to verify, which is "
                "not the same as verified")

    limits: list = []
    # (7b) ACCESS CONTROL. Grants are ordinary records, so each one has a write receipt in the chain above --
    # which means the bundle can be asked whether its ACL summary is backed by the evidence beside it. An
    # act listed here with no receipt was appended to the bundle, not written to the store; that is the
    # forgery this catches, and it is the same class as the governance/tombstone disagreement in (6b).
    # It is only assertable when the chain covers the WHOLE store -- with receipts enabled part-way through,
    # an uncovered grant is ordinary history, so it is reported as a limit instead of an accusation.
    acl_acts = bundle.get("grants") or []
    if acl_acts:
        receipted_ids = {r.get("memory_id") for r in wc}
        missing = [a.get("id") for a in acl_acts if a.get("id") not in receipted_ids]
        full_coverage = bool(wc) and isinstance(n_records, int) and n_records <= len(wc)
        live = sum(1 for a in acl_acts if a.get("status") == "active" and a.get("state") == "granted")
        if missing and full_coverage:
            bad(f"{len(missing)} access-control act(s) in this bundle are covered by NO write receipt "
                f"({', '.join(map(str, missing[:5]))}{' ...' if len(missing) > 5 else ''}) -- the ACL "
                f"summary claims a grant the store's own write chain never recorded")
        elif missing:
            limits.append(f"{len(missing)} access-control act(s) predate this store's write receipts, so "
                          f"the ACL summary is not backed by the chain for those -- not an accusation")
        else:
            ok(f"access control: {len(acl_acts)} grant/revocation act(s), all covered by the write chain; "
               f"{live} grant(s) in force at export")

    # (8) CONTENT -- the one thing checks 1-7 structurally cannot see. Folded in only when the caller hands
    # over the store; otherwise the omission is stated rather than left to be inferred from an absent line.
    content_checked = store_items is not None
    if content_checked:
        b = bind_content(bundle, store_items)
        mismatched = b.get("mismatched") or []
        if mismatched:
            bad(f"{len(mismatched)} record(s) no longer match the commitment their FIRST receipt made: "
                + ", ".join(f"{m.get('memory_id')} ({m.get('field')})" for m in mismatched[:5])
                + (" ..." if len(mismatched) > 5 else ""))
        elif not b.get("ok"):
            for pr in (b.get("problems") or []):
                if "no content was compared" in pr:
                    bad(pr)
        else:
            ok(f"content binds to the receipts: {b.get('checked', 0)} of {b.get('receipted', 0)} "
               f"receipted record(s) still hash to what their earliest receipt committed to")
        # A store that GREW since the bundle was taken, or a record erased since, is ordinary operation --
        # the bundle is a snapshot, not a lease. Only `mismatched` is a tamper signal, which is why
        # bind_content itself defines ok as `not mismatched`. Reporting the other two as failures would
        # false-alarm on every normal write, the same defect as the naive anchor-tip comparison.
        for mid in (b.get("unreceipted") or [])[:5]:
            limits.append(f"record {mid} is covered by no receipt in this bundle (written after it was "
                          f"taken, or with receipts disabled) -- not checked, not an accusation")
        orph = b.get("orphaned") or []
        for mid in orph[:5]:
            limits.append(f"record {mid} is in the chain but absent from the store today; check the "
                          f"tombstone chain before reading it as a deletion out of band")
        if len(orph) > 5:
            # Truncating to five without saying so turned twenty substituted records into five lines.
            limits.append(f"...and {len(orph) - 5} more record(s) in the chain are absent from the store")
    else:
        limits.append("CONTENT NOT CHECKED: this bundle is content-free by design, so a clean chain over "
                      "substituted text verifies here. Pass store_items= (or call bind_content) to close it.")
    if not (witnesses and cosigs):
        limits.append("NOT OPERATOR-ADVERSARIAL: without witness co-signatures on a prior anchor, a "
                      "key-holder rewrite is internally consistent by construction.")

    return {
        "ok": not problems,
        "checks": checks,
        "problems": problems,
        "limits": limits,
        "summary": {
            "content_checked": content_checked,
            "writes": anchor.get("n_writes"),
            "erasures": anchor.get("n_tombstones"),
            "erasure_requests": len(gov.get("by_request") or {}),
            "superseded_total": (bundle.get("supersession") or {}).get("superseded_total", 0),
            "acl_acts": len(acl_acts),
            "grants_in_force": sum(1 for a in acl_acts
                                   if a.get("status") == "active" and a.get("state") == "granted"),
            "operator_adversarial": bool(witnesses and cosigs),
            "inspeximus_version": bundle.get("inspeximus_version"),
        },
    }


def load_store_items(path):
    """The store dump for `--store`, or None if the path is not there.

    ONE implementation, called from both entry points. Opening a store CREATES it, so a mistyped path
    would hand the auditor a clean verdict over an empty store they had just made -- the erasure-
    certificate defect of 1.70.0, in a third place. The guard was written for `inspeximus audit-verify`
    in 1.79.0 and did not reach `python -m inspeximus.audit_bundle verify`, which is the invocation that
    release's own CHANGELOG prints. A fix that lands at one call site while the class lives one file over
    is the shape this repository meets most often; the answer is not to add the check twice.
    """
    if not os.path.exists(path):
        return None
    return list(Inspeximus(path=path, receipts=True).items)


def _cli(argv=None):
    import argparse, os
    ap = argparse.ArgumentParser(prog="inspeximus.audit_bundle",
                                 description="Build / verify a portable inspeximus audit bundle.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="export a store's record-keeping state to a portable bundle")
    b.add_argument("--path", help="store file (default: $INSPEXIMUS_PATH or ./inspeximus_memory.json)")
    b.add_argument("--out", default="inspeximus_audit_bundle.json", help="output json path")
    b.add_argument("--expected-pubkey", default=None, help="pin the signature-authenticity check to this key")
    v = sub.add_parser("verify", help="verify a bundle OFFLINE (needs only the file)")
    v.add_argument("bundle", help="the bundle json to verify")
    v.add_argument("--witnesses", default=None, help="comma-separated allowlisted witness pubkeys (hex)")
    v.add_argument("--threshold", type=int, default=1, help="k-of-n witness threshold")
    v.add_argument("--store", default=None,
                   help="the store file the bundle came from; binds the receipts to the CONTENT it serves "
                        "today. Without it a clean chain over substituted text still reads PASS.")
    a = ap.parse_args(argv)

    if a.cmd == "build":
        p = a.path or os.environ.get("INSPEXIMUS_PATH") or "inspeximus_memory.json"
        store = Inspeximus(path=p, receipts=True)          # reload the persisted receipt/tombstone chains
        bundle = build_bundle(store, expected_pubkey=a.expected_pubkey)
        if bundle["anchor"]["n_writes"] == 0:
            print("note: this store has no write receipts -- it was not written with receipts enabled, so the "
                  "bundle proves nothing. Write with Inspeximus(path=..., receipts=True) (or `inspeximus "
                  "--receipts remember ...`) to build an auditable chain.")
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(bundle, f, ensure_ascii=False, indent=2)
        print(f"wrote audit bundle -> {a.out}  "
              f"({bundle['anchor']['n_writes']} writes, {bundle['anchor']['n_tombstones']} erasures)")
        return 0

    with open(a.bundle, encoding="utf-8") as f:
        bundle = json.load(f)
    wl = [w.strip() for w in a.witnesses.split(",")] if a.witnesses else None
    items = None
    if a.store:
        items = load_store_items(a.store)
        if items is None:
            print(f"  FAIL --store {a.store} does not exist; refusing to create a store while verifying, "
                  f"because an empty one verifies clean")
            return 1
    res = verify_bundle(bundle, witnesses=wl, threshold=a.threshold, store_items=items)
    for c in res["checks"]:
        print(f"  OK   {c}")
    for pr in res["problems"]:
        print(f"  FAIL {pr}")
    # Printed with the verdict, never below it: a PASS whose scope is only visible to someone who read the
    # docstring is the same silent assurance this check exists to remove.
    for lim in res.get("limits") or []:
        print(f"  NOTE {lim}")
    print(f"\nVERDICT: {'PASS' if res['ok'] else 'FAIL'}  "
          f"({res['summary'].get('writes')} writes, {res['summary'].get('erasures')} erasures"
          f", content {'checked' if res['summary'].get('content_checked') else 'NOT checked'}"
          f"{', operator-adversarial' if res['summary'].get('operator_adversarial') else ''})")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
