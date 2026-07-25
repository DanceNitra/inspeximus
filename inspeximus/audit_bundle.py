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

The bundle is CONTENT-FREE: the write receipts commit to content/attribution HASHES (never text), and the
tombstones carry surrogate memory ids + request ids -- so the artifact proves the ACTS (a write with this
commitment happened at T; a record with this id was erased at T for request R) and their append-only integrity,
never the content. That is exactly the honest boundary governance_report already states, now portable.

HONEST SCOPE (unchanged, restated in-band): this verifies THIS store's own integrity, not the app's vector
index / prompt logs / backups; it is a tamper-evident record-keeping ARTIFACT, not a compliance certification.
The internal signatures are load-bearing only against a party who does NOT hold receipt_key; for an
operator-adversarial audit the auditor must have witnessed a PRIOR anchor out of band (pass `witnesses=` to
check co-signatures) -- the append-only guarantee comes from that external witness, not from the bundle alone.
"""
from __future__ import annotations
import json
from .core import Inspeximus, _sha256_hex, _canon, _GENESIS, __version__

BUNDLE_KIND = "inspeximus.audit_bundle/1"


def _bundle_hash(bundle: dict) -> str:
    """SHA-256 over the whole bundle EXCEPT its own bundle_hash field (canonical, order-independent)."""
    return _sha256_hex(_canon({k: v for k, v in bundle.items() if k != "bundle_hash"}))


def _content_free_writes(store) -> list:
    return [{k: r.get(k) for k in ("seq", "ts", "memory_id", "commit", "prev", "hash")}
            for r in store._receipts]


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


def verify_bundle(bundle: dict, witnesses: list | None = None, threshold: int = 1) -> dict:
    """STANDALONE offline verification of an audit bundle -- needs only the file (no store, no receipt key).
    Checks, in order: (1) the bundle's own hash; (2) the write chain re-walks from genesis and its tip+count
    match the anchor; (3) same for the tombstone chain; (4) the anchor's sth_hash is internally consistent;
    (5) if `witnesses` (allowlisted pubkeys) is given and the anchor carries co-signatures, k-of-n verifies.
    Returns {ok, checks:[...passed...], problems:[...failed...], summary:{...}}. `ok` is True iff no problems.
    Note: (5) is the only operator-ADVERSARIAL check; without a witnessed prior anchor, a key-holder rewrite is
    internally consistent by construction -- 1-4 prove append-only INTEGRITY, not that the operator is honest."""
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

    # (4) anchor internal consistency
    recomputed = _sha256_hex(_canon({k: anchor.get(k) for k in
                            ("n_writes", "writes_tip", "n_tombstones", "tombstones_tip")}))
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

    # (6) the bundle's OWN verdict on the store it came from. build_bundle() already wrote
    # governance.proof.verified, and until 1.54.0 the verifier never read it -- so a bundle exported from a
    # store whose records had been edited out of band carried `verified: False` and still verified PASS with
    # zero problems. The auditor runs THIS side; it must not be the side that skips the finding.
    proof = (gov.get("proof") or {})
    if proof.get("verified") is False:
        bad("the store reported its own write-verification as FAILED at export time"
            + (f": {'; '.join(map(str, proof.get('problems') or []))}" if proof.get("problems") else "")
            + " -- the chains below re-walk consistently, but the RECORDS no longer match their receipts")

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

    return {
        "ok": not problems,
        "checks": checks,
        "problems": problems,
        "summary": {
            "writes": anchor.get("n_writes"),
            "erasures": anchor.get("n_tombstones"),
            "erasure_requests": len(gov.get("by_request") or {}),
            "superseded_total": (bundle.get("supersession") or {}).get("superseded_total", 0),
            "operator_adversarial": bool(witnesses and cosigs),
            "inspeximus_version": bundle.get("inspeximus_version"),
        },
    }


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
    res = verify_bundle(bundle, witnesses=wl, threshold=a.threshold)
    for c in res["checks"]:
        print(f"  OK   {c}")
    for pr in res["problems"]:
        print(f"  FAIL {pr}")
    print(f"\nVERDICT: {'PASS' if res['ok'] else 'FAIL'}  "
          f"({res['summary'].get('writes')} writes, {res['summary'].get('erasures')} erasures"
          f"{', operator-adversarial' if res['summary'].get('operator_adversarial') else ''})")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
