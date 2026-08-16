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
from .core import (Inspeximus, _sha256_hex, _canon, _GENESIS, __version__, sth_hash_of, _int_or,
                    _HAVE_ED)
if _HAVE_ED:                 # OPTIONAL, exactly as in core and witness_pool: the Ed25519 names exist
    from .core import _Ed25519PK   # only when `cryptography` is installed. I imported them
else:                              # unconditionally in witness_pool an hour earlier, broke the base
    _Ed25519PK = None              # install, had it caught by this same examples suite -- and then
    #                                did it again here. A zero-dependency package that cannot be
    #                                imported without an extra is not zero-dependency.

BUNDLE_KIND = "inspeximus.audit_bundle/1"


def _bundle_hash(bundle: dict) -> str:
    """SHA-256 over the whole bundle EXCEPT its own bundle_hash field (canonical, order-independent)."""
    return _sha256_hex(_canon({k: v for k, v in bundle.items() if k != "bundle_hash"}))


def _content_free_writes(store) -> list:
    """Every field the write receipt's hash commits to, plus the hash itself, so an offline verifier can
    re-derive it. Content-free: `commit` is hashes, never text.

    THE FIELD LIST IS DERIVED, NOT WRITTEN DOWN. `amends` became part of the preimage in 1.68.0 and this
    exporter's fixed list dropped it, so a bundle from any store where slash()/restore() had ever run could
    not be verified -- by any version, including the one that produced it. That was fixed by adding a
    special case for `amends`, which left the real defect in place: a hand-maintained list of preimage
    fields, two hundred lines away from the preimage.

    It reproduced immediately. `amend_reason` (added after 2.9.1) joined `_chain_core` and this list did
    not know,
    and `test_every_surface_agrees_after_an_amendment` failed on all three fixtures with "write chain
    breaks at index 1" while verify_writes() and anchor() both said clean -- the same split the
    _chain_core docstring records.

    So the export now IS the preimage, by construction: whatever `_chain_core` commits to is what ships,
    plus the hash so an offline verifier can compare. A field added to the preimage cannot be forgotten
    here again, because there is nothing here to forget it in.

    Content-free either way: `commit` is hashes, never text."""
    out = []
    for r in store._receipts:
        rec = dict(Inspeximus._chain_core(r, "write"))
        rec["hash"] = r.get("hash")
        if r.get("sig"):                      # receipt signature travels with it when one was minted
            rec["sig"] = r["sig"]
            # AND THE KEY IT CLAIMS. Without this the bundle carried signatures an offline verifier
            # had nothing to check them against, so verify_bundle could only COUNT them. Exporting it
            # is not itself a guarantee -- an editor rewrites the key along with the signature under
            # an unkeyed bundle_hash -- which is why the verifier pins against a key supplied out of
            # band and says PRESENT-BUT-UNVERIFIED when it has none.
            if r.get("pubkey"):
                rec["pubkey"] = r["pubkey"]
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


def _derived_store_id(store) -> str:
    """A store identity an operator cannot rename, for witness fork-memory.

    The genesis receipt's hash: fixed at the first write, stable across copies and renames, and
    unforgeable without breaking the chain that every other check in this file walks.

    A RECEIPTS-DISABLED STORE HAS NO IDENTITY OF ITS OWN, and the fallback is marked `unkeyed:` so
    that it cannot be mistaken for one. It used to return the bare path while the docstring claimed
    "saying so beats inventing one" -- it did not say so, it returned the operator-chosen filename,
    which is precisely the scheme the derived id was introduced to replace. Measured: three records,
    receipts off, `cp -r` to a second directory, and the copy was co-signed by the same witness as a
    first contact with zero refusals. The `cp` bypass this function exists to close was fully open
    for every store without receipts.

    The `unkeyed:` prefix is load-bearing, not decoration: build_bundle refuses to witness one, and
    verify_bundle refuses a bundle that claims co-signatures over one.
    """
    recs = getattr(store, "_receipts", None) or []
    if recs and recs[0].get("hash"):
        return "insp1:" + str(recs[0]["hash"])[:32]
    return "unkeyed:" + str(getattr(store, "path", "") or "store")


def build_bundle(store, expected_pubkey: str | None = None, sign=None, witnesses=None,
                 store_id: str | None = None, cross_tenant_chain: bool = False) -> dict:
    """Serialise a store's record-keeping state into one portable, self-verifying artifact.

    `expected_pubkey` pins the signature-authenticity check. `sign(bytes)->hex` attaches ONE witness
    signature to the head.

    `witnesses=` is the k-of-n path, and it exists because the parts were already here and nothing
    joined them. `collect_cosignatures()` returned a list, `verify_cosigned_anchor()` consumed one,
    and check (5) of verify_bundle read `anchor["cosignatures"]` -- but NOTHING ever wrote that key.
    So the only operator-adversarial check in the artifact was reachable only by a caller who built
    the anchor, called the collector, hand-stuffed the result into the anchor and then built the
    bundle around it. Measured 2026-08-16: `cosignatures` was absent from every anchor this library
    produced.

    WHY IT MATTERS MORE THAN THE OTHER CHECKS. Everything else in a bundle is the operator vouching
    for the operator. A receipt chain signed with `receipt_key` catches an editor who lacks the key;
    it cannot catch the party who HOLDS it, and that party is whoever runs the store. An independent
    witness attesting the log's height at a point in time is the one thing here that an operator
    cannot forge alone -- so a bundle without one is a self-certification, and verify_bundle now says
    that out loud instead of passing in silence.

    A witness that REFUSES is not dropped: refusals ride in the bundle as `witness_refusals`, because
    an honest witness only refuses a fork or a rollback and that is the alarm, not an error to
    swallow.
    """
    # A TENANT-BOUND STORE CANNOT EXPORT A HONEST BUNDLE, and the artifact says it is content-free
    # and fit to hand a DPO. `n_records` and `baseline_complete` read `store.items`, which IS
    # tenant-scoped; `_content_free_writes` / `_content_free_tombstones` read `_receipts` /
    # `_tombstones`, which are NOT. Measured on the candidate: an acme-bound export carried globex's
    # memory ids and the GDPR request_id "GDPR-GLOBEX-SECRET-REQ-0042".
    #
    # THE OBVIOUS FIX DOES NOT WORK, which is why this refuses instead of filtering: a tenant-scoped
    # chain breaks the `prev` links, so it cannot be re-walked from genesis, and re-walking from
    # genesis is the whole point of shipping the chain. Until a per-tenant chain exists, the honest
    # move is to make the caller say they accept a cross-tenant chain, and to say so in the artifact.
    if getattr(store, "tenant", None) is not None and not cross_tenant_chain:
        raise ValueError(
            f"refusing to build a bundle from a handle bound to tenant "
            f"{getattr(store, 'tenant', None)!r}: the receipt and tombstone chains are STORE-WIDE, so "
            f"the artifact would carry other tenants' memory ids and erasure request_ids while "
            f"describing itself as content-free. Export from an unbound (operator) handle, or pass "
            f"cross_tenant_chain=True to accept and disclose that the chain spans tenants.")

    anchor = store.anchor(sign=sign)
    # WITNESS MEMORY IS KEYED ON THE LOG, ALWAYS. `store_id=` is a DISPLAY LABEL now, not the key.
    #
    # The first version of this fix moved the rename from the default to a parameter and I wrote
    # "an operator cannot rename or copy their way to a fresh witness identity" above it. That was
    # false and the next adversarial round proved it in one line: export the forked history under
    # `store_id="anything-else"` and the very witness holding the fork memory co-signs it, with
    # verify_bundle(require_witnessed=True) returning ok=True and zero problems. A guarantee with a
    # caller-supplied bypass parameter is not a guarantee.
    _wid = _derived_store_id(store)
    if witnesses and _wid.startswith("unkeyed:"):
        # REFUSING BEATS PRODUCING SOMETHING THAT LOOKS LIKE PROTECTION -- the same rule as
        # _guard_key_location and receipt_key_for. Two measured reasons, either one sufficient:
        #
        #  * The ANCHOR COMMITS TO NOTHING. With receipts off, a store holding three records still
        #    reports n_writes=0 and writes_tip=000...0, so a witness cannot tell it from an empty
        #    store, cannot tell it from ANY other receipts-disabled store, and has nothing to
        #    compare on the next submission. The fork guard is inert. What comes back is a valid
        #    signature over zeros, and the bundle then prints "external witnesses co-signed the
        #    anchor (operator-adversarial)" in green.
        #  * The IDENTITY IS THE FILENAME, so `cp -r` is a fresh first contact.
        raise ValueError(
            "refusing to witness a store with receipts disabled: its anchor commits to nothing "
            "(n_writes=0, writes_tip=all zeros, whatever the store actually holds), so a witness "
            "co-signature over it would verify while proving nothing, and its identity would be "
            "the filename, which `cp` changes. Open the store with receipts=True.")
    _sid = store_id or _wid
    if witnesses:
        from .witness_pool import collect_cosignatures
        # THE IDENTITY A WITNESS KEYS ITS FORK MEMORY ON MUST NOT BE OPERATOR-CHOSEN.
        #
        # This defaulted to the store's FILE PATH. Measured on the candidate: roll history back, copy
        # the store to a second path, and every witness co-signs it as a first contact -- and the
        # verdict does not merely fail to object, it prints `operator-adversarial`. The positive
        # control (the same rollback at the ORIGINAL path) is refused, so the memory works; the
        # attacker just renames the thing it remembers. No test saw it: all 14 call sites in tests/
        # pass store_id= explicitly, so the default path was exercised by nothing.
        #
        # Derived from the LOG instead: the genesis receipt's hash. It is fixed at the store's first
        # write, survives copying and renaming, and an operator who alters it has broken the chain
        # every other check walks. A caller-supplied `store_id` still wins -- a deployment may have
        # its own naming -- but it is now an override, not the only option.
        _res = collect_cosignatures(_wid, anchor, witnesses)
        if _res.get("cosignatures"):
            anchor["cosignatures"] = [list(c) for c in _res["cosignatures"]]
        if _res.get("refused"):
            anchor["witness_refusals"] = _res["refused"]
    bundle = {
        "kind": BUNDLE_KIND,
        "inspeximus_version": __version__,
        "generated_ts": anchor.get("ts"),
        "tenant": getattr(store, "tenant", None),
        # THE ID THIS WAS WITNESSED UNDER, so an auditor can tell whether an attestation is even
        # about this store: one issued for an unrelated store used to read as a clean bill of health.
        # A view field, not a commitment -- an operator who edits it only makes their own
        # attestations stop matching, which is the direction that fails safe.
        #
        # BOTH ids, because a caller may pass `store_id=` on one export and omit it on the next, and
        # the derived id is what the omission produces. Punishing that inconsistency with a
        # fork-shaped accusation is a false positive I created and a test caught: an honest operator
        # who used a name once is not presenting "a different store".
        # A DISPLAY LABEL. Witness memory and the attestation check both key on the derived id
        # below; this is here so a human can tell which deployment they are looking at.
        "store_id": _sid,
        "store_id_derived": _derived_store_id(store),
        # DISCLOSED IN-BAND. A reader of this file must not have to know the export flag to learn
        # that the chain covers more than the tenant named above it.
        "cross_tenant_chain": bool(cross_tenant_chain
                                   and getattr(store, "tenant", None) is not None),
        "anchor": anchor,
        "governance": store.governance_report(expected_pubkey),
        "supersession": store.supersession_report(),
        "n_records": len(getattr(store, "items", []) or []),   # content-free count: lets the verifier tell
        #                                        "empty store" from "store with data but receipts disabled"
        # WAS EVERY RECORD COVERED WHEN THIS WAS TAKEN? Without it, "uncovered at audit time" cannot
        # be read: it might mean the record was planted, or it might mean the baseline was never
        # clean and this bundle was always a partial claim. Stating it here makes the later verdict
        # about the RECORD instead of about the export.
        "baseline_complete": not [r for r in (getattr(store, "items", []) or [])
                                  if r.get("id") not in {rc.get("memory_id")
                                                         for rc in (getattr(store, "_receipts", None) or ())}],
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
        for field in ("immutable_sha256", "content_sha256", "value_sha256",
                      "status_sha256", "attrib_sha256"):
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
                  require_signed: bool = False, store_receipts: list | None = None,
                  require_witnessed: bool = False, attestations: list | None = None,
                  expected_pubkey: str | None = None,
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
    # `limits` belongs here, beside them. It used to be created after the governance section,
    # so every check above that point had to park what it wanted to say in a deferred list --
    # and the third check that needed it hit UnboundLocalError instead. A list half the
    # function cannot reach is a trap laid for the next check.
    limits: list = []

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

    # (4b) THE MERKLE ROOTS, recomputed from the chains this bundle carries.
    #
    # `_STH_FIELDS` is ("n_writes", "writes_tip", "n_tombstones", "tombstones_tip") -- the tips and
    # counts, NOT the roots. So `writes_root`, `tombstones_root` and `root_hash` sat in the anchor
    # bound by nothing and checked by nobody. Measured 2026-08-15: zero any of the three, reseal
    # `bundle_hash` (self-computed and documented as advisory), and verify_bundle returned ok=True.
    # They matter because the ROOT is what an inclusion proof verifies against -- the SCITT-style
    # receipt an auditor checks without the log -- so a substituted root lets a proof over a forged
    # tree verify clean.
    #
    # Recomputed with the same leaf definition the store uses (`_chain_core`), which is why it can be
    # done offline from the bundle alone.
    try:
        from .merkle import root as _mroot
        for _kind, _recs, _field in (("write", wc, "writes_root"),
                                     ("tombstone", tc, "tombstones_root")):
            if _field not in anchor:
                continue
            _leaves = [_canon(Inspeximus._chain_core(r, _kind)) for r in _recs]
            _re = _mroot(_leaves).hex()
            if _re != anchor.get(_field):
                bad(f"anchor {_field} does not match the {_kind} chain in this bundle "
                    f"(recomputed {_re[:12]}..., anchor says {str(anchor.get(_field))[:12]}...)")
            else:
                ok(f"anchor {_field} re-derives from the {_kind} chain")
        if "root_hash" in anchor:
            _rh = _sha256_hex(_canon({k: anchor.get(k) for k in
                                      ("n_writes", "writes_root", "n_tombstones",
                                       "tombstones_root", "merkle")}))
            if _rh != anchor.get("root_hash"):
                bad("anchor root_hash does not match its own root fields")
    except Exception as e:                       # a verifier must report, never crash
        limits.append(f"merkle roots NOT re-derived ({type(e).__name__}: {e}) -- treat the roots "
                      f"in this anchor as unchecked")

    # (4c) SIGNATURE COVERAGE, stated as a number rather than left to be inferred from silence.
    #
    # An attacker who can edit the bundle can DELETE every `sig` and `pubkey` and reseal it, and the
    # result verified ok=True with nothing anywhere saying the artifact had ever been signed.
    # Measured 2026-08-15: 3 of 3 signatures stripped, verdict PASS, no problem raised. The chain is
    # still internally consistent -- that is the point, and it is why "no signature" must be reported
    # as a FACT about this bundle instead of as an absence of findings.
    #
    # HONEST LIMIT: this cannot prove a bundle WAS signed. An attacker who strips the signatures also
    # strips the pubkey, and an unsigned bundle from an unsigned store is legitimate. What it buys is
    # that an auditor is told which one they are holding, and `require_signed=True` lets one who
    # knows the store signs its receipts refuse the downgrade outright.
    # PRESENT IS NOT VERIFIED, and until 2.10.6 this counted `sig` keys and printed "all N chain
    # entries carry a signature". Measured on the candidate: rewrite a record, re-sign the chain with
    # a key you minted yourself -- or put the literal string "deadbeef" in every `sig` -- re-run the
    # ordinary exporter, and the auditor's offline verdict was PASS with require_signed=True. The
    # must-fail control (one receipt hash corrupted) failed correctly, so the chain walk was alive;
    # only the signature check was decorative.
    #
    # `verify_writes` had the real check the whole time (`expected_pubkey`, `warn_unpinned`) and this
    # surface never got it -- the one-surface-of-two shape, again, and this time it made a published
    # changelog claim ("signing now pays") false at the bundle while true at verify_writes.
    #
    # PINNING IS THE WHOLE POINT. Verifying a signature against the key carried NEXT TO IT proves
    # only that whoever wrote the bundle owned a keypair. `expected_pubkey` must come from outside
    # the artifact; with none, the honest report is PRESENT BUT UNVERIFIED, never "signed".
    _signed = _verified = _badsig = 0
    _wrongkey = []
    for _r in (list(wc) + list(tc)):
        if not _r.get("sig"):
            continue
        _signed += 1
        _pk = expected_pubkey or _r.get("pubkey")
        if expected_pubkey and _r.get("pubkey") and _r["pubkey"] != expected_pubkey:
            _wrongkey.append(_r.get("memory_id") or _r.get("seq"))
            continue
        if not (_pk and _HAVE_ED):
            continue
        try:
            _Ed25519PK.from_public_bytes(bytes.fromhex(_pk)).verify(
                bytes.fromhex(_r["sig"]), bytes.fromhex(_r["hash"]))
            _verified += 1
        except Exception:
            _badsig += 1
    _total = len(wc) + len(tc)
    if _total:
        if _wrongkey:
            bad(f"{len(_wrongkey)} chain entrie(s) are signed by a key that is NOT the one pinned: "
                f"{_wrongkey[:5]}")
        if _badsig:
            bad(f"{_badsig} chain signature(s) DO NOT VERIFY against the key they name")
        if _signed == 0:
            (bad if require_signed else limits.append)(
                f"UNSIGNED: 0 of {_total} chain entries carry a signature. The chain is internally "
                f"consistent, which is not the same as attributable -- an editor who can rewrite the "
                f"sidecar can rewrite the chain too. Pass require_signed=True to refuse this.")
        elif _signed < _total:
            bad(f"PARTIALLY SIGNED: {_signed} of {_total} chain entries carry a signature -- a chain "
                f"that is signed in places is not signed")
        elif expected_pubkey and not _HAVE_ED:
            # CANNOT CHECK IS NOT A MISMATCH. On a base install (no `cryptography`) every signature
            # was skipped, `_verified` stayed 0, and this reported "only 0 of N chain signatures
            # verify against the pinned key" -- an accusation of forgery produced by a missing
            # optional dependency. The auditor's correct next step is `pip install cryptography`,
            # not an incident.
            #
            # The pin is not entirely dead here: comparing the pubkey FIELD is a string comparison
            # and still runs, so a chain naming the wrong key is still caught above. What cannot run
            # is the signature verification itself.
            (bad if require_signed else limits.append)(
                f"CANNOT CHECK {_signed} chain signature(s) against the pinned key: this install has "
                f"no Ed25519 backend. `pip install cryptography` (or `inspeximus[crypto]`) and run "
                f"this again -- the key each entry NAMES was still compared, but the signatures "
                f"themselves were not verified.")
        elif expected_pubkey and _verified == _signed and not _badsig and not _wrongkey:
            ok(f"all {_total} chain signatures VERIFY against the pinned key")
        elif expected_pubkey:
            bad(f"only {_verified} of {_signed} chain signatures verify against the pinned key")
        else:
            # The honest wording for the unpinned case, and `require_signed` means VERIFIED, so it
            # refuses here rather than accepting a count.
            (bad if require_signed else limits.append)(
                f"{_signed} of {_total} chain entries carry a signature, PRESENT BUT UNVERIFIED: no "
                f"expected_pubkey was supplied, so they can only be checked against a key carried "
                f"inside this same artifact -- which proves the writer owned a keypair, not which "
                f"one. Pass expected_pubkey= from out of band.")

    # (5) external witness co-signatures (the only operator-adversarial check)
    cosigs = anchor.get("cosignatures")
    if cosigs and str(bundle.get("store_id_derived") or "").startswith("unkeyed:"):
        # build_bundle will not produce this, but a bundle is a file and anyone can write one. A
        # co-signature over a receipts-disabled store's all-zero anchor is a valid signature over
        # nothing, and it must not be read as the operator-adversarial check.
        bad("this bundle carries witness co-signatures over a store with NO receipt chain: the "
            "anchor they signed commits to nothing (n_writes=0, writes_tip=all zeros) and the "
            "identity they signed it under is a filename, so the signatures are valid and vouch "
            "for nothing")
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
    else:
        # SAY IT. With no witnesses passed and no co-signatures present, this branch used to be
        # silent, so a self-certified bundle produced a page of OK lines and a PASS with nothing
        # anywhere indicating that every one of those checks was the operator vouching for the
        # operator. Same lesson as the signature coverage two checks up: absence of a finding is not
        # a finding of absence, and an auditor has to be told which artifact they are holding.
        (bad if require_witnessed else limits.append)(
            "SELF-CERTIFIED: this anchor carries no external co-signature. Every check above is the "
            "operator's own record-keeping verified against itself -- an operator holding receipt_key "
            "can rewrite the whole history and re-sign it so it verifies internally. Only a witnessed "
            "anchor is adversarial against the operator. Pass require_witnessed=True to refuse this.")
    # (5b) WHAT THE WITNESSES SAY, asked of THEM rather than read out of this artifact.
    #
    # Everything above is checked against a bundle the OPERATOR built. 2.10.3 recorded witness
    # refusals in it, which helps an honest operator prove diligence and does not bind a dishonest
    # one: measured 2026-08-16, deleting `witness_refusals` and resealing left an auditor without the
    # allowlist looking at an ordinary SELF-CERTIFIED bundle, with three refusals invisible.
    #
    # An attestation comes from the witness. Pass what they hand you and this compares it to the
    # anchor in front of you: a refusal they recorded, a head they last saw that is NOT this one, or
    # a store they have never seen at all.
    _att_keys = set()
    for _att in (attestations or []):
        from .witness_pool import verify_attestation
        _wk = str(_att.get("witness") or "")
        _att_keys.add(_wk)
        # PINNED TO THE ALLOWLIST. This called verify_attestation with no key, so the signature was
        # checked against the key carried INSIDE the statement -- which proves whoever produced it
        # owned a keypair, not which one. Measured on the candidate: an attestation from a key not on
        # the auditor's allowlist was accepted silently while the real witness's three refusals went
        # unmentioned. The pin was implemented and unit-tested and simply not wired -- the same
        # "the parts were here and nothing joined them" shape build_bundle(witnesses=) was written
        # to fix.
        _v = verify_attestation(_att, witness_pubkey=(_wk if witnesses and _wk in witnesses else None))
        if witnesses and _wk not in witnesses:
            bad(f"an attestation from {_wk[:12]}... is not on the witness allowlist: an unknown "
                f"witness vouching for a store is not evidence, it is a stranger")
            continue
        if not _v["signed"]:
            limits.append(f"an attestation from {_wk[:12]}... is unsigned, so it "
                          f"is a claim rather than evidence")
        elif not witnesses:
            # THE SAME CAVEAT THE CHAIN SIGNATURES GET, and it was missing here. With no allowlist
            # the statement is verified against the key printed inside it, so a forged attestation
            # forged consistently -- key, hash and signature -- verifies. It then carries the whole
            # operator-adversarial verdict, which is the one place in this file where an unpinned
            # check is most likely to be mistaken for an external one.
            limits.append(
                f"the attestation from {_wk[:12]}... is SIGNED BUT UNPINNED: with no witnesses= "
                f"allowlist it was checked against the key carried inside the statement, which "
                f"proves whoever wrote it owned a keypair, not that they are a witness you trust. "
                f"Pass witnesses=[...] with keys you got out of band.")
        for _pr in _v["problems"]:
            bad(f"witness {_wk[:12]}...: {_pr}")

        # BOUND TO THIS STORE. The attestation names a store_id and nothing compared it to anything,
        # so a statement issued for an unrelated store read as a clean bill of health here. The
        # bundle now records the id it was witnessed under (see build_bundle).
        _seen = (_att.get("last_head") or {})

        # DOES THIS STATEMENT EVEN CONCERN THIS LOG? The name is the weak answer: a caller may pass
        # `store_id=` on one export and omit it on the next, and punishing that with a fork-shaped
        # accusation is a false positive -- one I wrote and a control test caught within the run.
        #
        # The CHAIN is the strong answer. If the head the witness signed appears in this bundle's own
        # chain at the height they signed it, this is demonstrably the same log whatever either side
        # calls it. Names are for humans; the hash decides. Same lesson as `store_id` itself, one
        # level up: derive it from the log, never from a label someone chose.
        # AGAINST THE DERIVED ID ONLY. Accepting a match against EITHER id let the label do the
        # work again: an attestation naming the operator's chosen label satisfied the check while
        # the derived id -- the one the witness actually keyed on -- was never compared.
        _wid_b = bundle.get("store_id_derived")
        _name_matches = (not _att.get("store_id")) or (_att["store_id"] == _wid_b)
        _n_seen = _int_or(_seen.get("n_writes"), 0)
        _chain_links = bool(_seen.get("writes_tip")) and 0 < _n_seen <= len(wc)             and wc[_n_seen - 1].get("hash") == _seen["writes_tip"]
        if not _name_matches and not _chain_links:
            bad(f"witness {_wk[:12]}... attested store {str(_att['store_id'])[:24]!r}, and nothing "
                f"connects it to this bundle: it is not this log's derived id "
                f"({str(_wid_b)[:24]!r}) and the head they signed is not in this chain. A statement "
                f"about a different store says nothing about this one")
            continue
        if not _name_matches:
            limits.append(
                f"witness {_wk[:12]}... calls this store {str(_att['store_id'])[:24]!r} while this "
                f"log's derived id is {str(_wid_b)[:24]!r}; the head they signed IS in this chain, "
                f"so it is the same log under two names")

        if _seen.get("writes_tip") and anchor.get("writes_tip") \
                and _seen["writes_tip"] != anchor.get("writes_tip"):
            _n_seen = _int_or(_seen.get("n_writes"), 0)
            _newer = _int_or(anchor.get("n_writes"), 0) > _n_seen
            # A TALLER BUNDLE IS NOT AUTOMATICALLY HONEST GROWTH. The bundle carries its whole chain,
            # so the claim is checkable: if the witness signed tip T at height n, this chain's entry
            # n-1 MUST hash to T. Measured on the candidate: rewrite a record, append two ordinary
            # writes, and the mismatch was downgraded to a NOTE reading "which this bundle extends"
            # -- an out-of-band witness statement exonerating a fork, in words that were false.
            _forked = False
            if _newer and 0 < _n_seen <= len(wc):
                _forked = wc[_n_seen - 1].get("hash") != _seen["writes_tip"]
            if _forked:
                bad(f"witness {_wk[:12]}... signed head {str(_seen['writes_tip'])[:12]}... at "
                    f"n_writes={_n_seen}, and this bundle's own chain has "
                    f"{str(wc[_n_seen - 1].get('hash'))[:12]}... at that height: this is a FORK, not "
                    f"growth -- the bundle did not extend what the witness saw, it replaced it")
            else:
                (limits.append if _newer else bad)(
                    f"witness {_wk[:12]}... last saw head "
                    f"{str(_seen.get('writes_tip'))[:12]}... at n_writes={_seen.get('n_writes')}, "
                    f"{'which this bundle extends, verified against its own chain' if _newer else 'which this bundle CONTRADICTS at the same or lower height'}")

    # SILENCE FROM AN ALLOWLISTED WITNESS. An auditor who asked three witnesses and was handed two
    # statements has been told nothing about the third, and that absence was invisible.
    if witnesses and attestations is not None:
        _missing = [w for w in witnesses if w not in _att_keys]
        if _missing:
            limits.append(
                f"{len(_missing)} allowlisted witness(es) produced no attestation: "
                f"{[str(m)[:12] + '...' for m in _missing[:3]]}. A witness that was not asked, or "
                f"did not answer, vouches for nothing -- and looks the same as one that agreed.")

    if anchor.get("witness_refusals"):
        # An honest witness refuses only a fork or a rollback, so a refusal recorded at export time is
        # the loudest signal in the artifact -- louder than any check here, because it comes from
        # outside.
        bad(f"{len(anchor['witness_refusals'])} witness(es) REFUSED to co-sign this anchor at export: "
            f"{[r.get('reason', '')[:80] for r in anchor['witness_refusals'][:3]]}. An honest witness "
            f"refuses only a fork or a rollback.")

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
    # A CROSS-TENANT BUNDLE COMPARES TWO DIFFERENT POPULATIONS here -- `n_records` is scoped and
    # the chain is not -- so a tenant's genuinely uncovered records become unreportable. Say so
    # rather than printing a number that means nothing.
    n_records = bundle.get("n_records")
    if bundle.get("cross_tenant_chain"):
        limits.append("record-count coverage NOT CHECKED: this bundle's chain spans tenants while "
                      "its record count is tenant-scoped, so the two are not comparable. Export "
                      "from an unbound handle for this check.")
        n_records = None
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
        # SPLIT `unreceipted` INTO GROWTH AND INJECTION -- and NOT by `ts`.
        #
        # `ts` is a field in the record, so the attacker writes it. 2.10.2 discriminated on
        # `ts <= generated_ts`, which closed the erasure-slack hole and left this one: forward-date
        # the planted record and it reads as ordinary growth. Measured, and stated in that release's
        # own changelog as a residual. No cleverer heuristic over `ts` can work -- the information
        # separating "written later" from "planted with a later timestamp" is not in the file.
        #
        # IT IS IN THE LIVE CHAIN. With receipts on, legitimate growth is receipted in the store's
        # CURRENT chain (just not in this bundle's snapshot of it); an injection is receipted in
        # neither. Measured 2026-08-16: the later write appears in the live chain, the forward-dated
        # plant does not. So we ask the chain, and `ts` stops deciding anything.
        #
        # HONEST SCOPE. This holds against an attacker who can edit the STORE. One who can also
        # append to the `.receipts` sidecar mints a receipt for their record and it reads as growth
        # again -- the documented unsigned-chain limit, closed by `receipt_key=`/an external signer
        # (their forged entry is then unsigned, which the coverage check above reports as PARTIALLY
        # SIGNED) and, against the operator themself, only by an externally witnessed anchor.
        #
        # Without `store_receipts` the old heuristic still runs, but says in words that it is one.
        _gen_ts = bundle.get("generated_ts")
        _live_ids = ({rc.get("memory_id") for rc in store_receipts}
                     if store_receipts is not None else None)
        if _live_ids is None:
            limits.append("GROWTH NOT VERIFIED: no store receipt chain was supplied, so records the "
                          "bundle does not cover are classified by their own `ts` -- a field the "
                          "writer controls. Pass store_receipts= for the chain-membership check.")

        # THE BUNDLE'S CHAIN MUST BE A PREFIX OF THE LIVE ONE. Append-only is the property the whole
        # artifact rests on, and nothing checked it across the two: measured 2026-08-16, truncating
        # the live chain after the export left verify_bundle reporting ok=True. Without this, the
        # membership test below is worth little either -- an operator who rewrites history can make
        # any record "covered".
        if store_receipts is not None:
            _bh = [r.get("hash") for r in wc]
            _lh = [r.get("hash") for r in store_receipts]
            if len(_lh) < len(_bh):
                bad(f"the store's receipt chain is SHORTER than this bundle's ({len(_lh)} < "
                    f"{len(_bh)}): history was rolled back or truncated after the export")
            elif _lh[:len(_bh)] != _bh:
                bad("this bundle's receipt chain is not a PREFIX of the store's current chain: "
                    "history was rewritten after the export, not merely appended to")
            else:
                ok(f"the store's chain extends this bundle's append-only "
                   f"({len(_bh)} -> {len(_lh)} entries)")
                # AND THE EXTENSION MUST BE SIGNED IF THE BUNDLE WAS. The signature check further up
                # walks the BUNDLE's chain; the attacker appends to the LIVE one, which it never
                # reads. Measured 2026-08-16, and it is the hole in this very fix: on a SIGNED store,
                # planting a record plus a hand-minted unsigned receipt for it made the record read
                # as ordinary growth and the bundle verified clean. Signing the chain has to buy
                # something here, or "use receipt_key=" is advice that does not pay.
                _new = store_receipts[len(_bh):]
                if _bh and all(r.get("sig") for r in wc) and _new:
                    _unsigned = [r.get("memory_id") for r in _new if not r.get("sig")]
                    if _unsigned:
                        bad(f"this bundle's chain is signed but {len(_unsigned)} of the "
                            f"{len(_new)} entries appended since are NOT: {_unsigned[:5]}"
                            f"{' ...' if len(_unsigned) > 5 else ''}. A signed chain that grows "
                            f"unsigned entries was appended to by something without the key.")

        if bundle.get("baseline_complete") is False:
            limits.append("the store already held records covered by no receipt when this bundle was "
                          "taken, so an uncovered record today is not evidence on its own -- this "
                          "bundle was a partial claim from the start")
        #
        # The wording "written after the bundle was taken -- not an accusation" is right for GROWTH,
        # which is ordinary operation. It is wrong for a record that ALREADY EXISTED when the bundle
        # was generated and is covered by no receipt: nothing legitimate produces that.
        #
        # Check (7) above compares COUNTS, and a count has slack: each erasure removes a record and
        # leaves its receipt behind, so on a store that has performed one GDPR erasure -- exactly the
        # store an audit bundle exists for -- an injected record fits in the gap. Measured 2026-08-15:
        # same injection, same verifier. Without a prior erasure, records=4 receipts=3 -> FAIL. With
        # one prior erasure, records=3 receipts=3 -> PASS, and the auditor read VERDICT: PASS.
        #
        # HONEST RESIDUAL, stated because it is not closed: `ts` is attacker-writable, so forward-
        # dating the injected record makes it look like growth again. This raises the cost from
        # "free" to "you must also fake the timestamp"; it does not eliminate it. Closing it properly
        # is a build-time refusal to export a store with uncovered records without saying so.
        _gen_ts = bundle.get("generated_ts")
        _by_id = {r.get("id"): r for r in (store_items or []) if isinstance(r, dict)}
        _preexisting, _growth = [], []
        for mid in (b.get("unreceipted") or []):
            if _live_ids is not None:
                (_growth if mid in _live_ids else _preexisting).append(mid)
                continue
            _r = _by_id.get(mid) or {}
            _ts = _r.get("ts")
            if isinstance(_gen_ts, (int, float)) and isinstance(_ts, (int, float)) and _ts <= _gen_ts:
                _preexisting.append(mid)
            else:
                _growth.append(mid)
        if _preexisting:
            # AN INCOMPLETE BASELINE IS NOT AN ACCUSATION. `baseline_complete: False` was appended
            # to `limits` and then ignored here, so a store that simply turned receipts on part-way
            # was told through the shipped CLI that its records "were inserted out of band" -- in
            # text IDENTICAL to the one a genuine plant produces, which makes the genuine signal
            # unreadable. `verify_writes` already models this exact case with `coverage_strict`; the
            # bundle path did not.
            _baseline_gap = bundle.get("baseline_complete") is False
            _why = ("is covered by no receipt in the store's CURRENT chain either, and this bundle's "
                    "baseline was ALREADY incomplete when it was taken, so this may predate the "
                    "chain rather than have been inserted -- compare against a copy you trust"
                    if (_live_ids is not None and _baseline_gap) else
                    "is covered by no receipt in the store's CURRENT chain either, so it was not "
                    "written by this store -- it was inserted out of band"
                    if _live_ids is not None else
                    "existed when this bundle was generated (by its own `ts`, which the writer "
                    "controls) and is covered by no receipt")
            bad(f"{len(_preexisting)} record(s): {_preexisting[:5]}"
                f"{' ...' if len(_preexisting) > 5 else ''} -- each {_why}.")
        for mid in _growth[:5]:
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


def load_store_receipts(path):
    """The store's CURRENT receipt chain, or None if the path is not there.

    An auditor holding the store file holds its `.receipts` sidecar too, and that chain is what
    separates growth from injection without trusting a timestamp. Same existence guard as its
    sibling, for the same reason: opening a store CREATES it.
    """
    if not os.path.exists(path):
        return None
    return list(getattr(Inspeximus(path=path, receipts=True), "_receipts", None) or [])


def _cli(argv=None):
    """`python -m inspeximus.audit_bundle` -- kept working, no longer a second implementation.

    THIS USED TO BE A FULL COPY of the audit-build / audit-verify handlers in cli.py, and it drifted
    exactly the way a second implementation of one decision always does. When `--expected-pubkey`
    and `--require-signed` were added to `inspeximus audit-verify` -- the fix for "the pin is
    reachable only from the side that does not need it" -- this copy did not get them, so an auditor
    who reached for this entrypoint got the unpinned verdict and nothing told them the stronger
    check existed. Nobody noticed until the mutation harness refused a spec whose target line had
    moved out from under it.

    Porting the two flags across would have bought one round of agreement and then diverged again on
    the next change. So this translates its own argv onto the real CLI instead: old invocations keep
    working, `--expected-pubkey` and `--require-signed` are available here for the first time, and
    audit-verify is implemented in exactly one place. `tests/test_mutation_check_harness.py` has the
    one remaining target, and `test_there_is_only_one_audit_verify` fails if a copy comes back.
    """
    import argparse
    ap = argparse.ArgumentParser(prog="inspeximus.audit_bundle",
                                 description="Build / verify a portable inspeximus audit bundle. "
                                             "A thin alias for `inspeximus audit-build` / "
                                             "`inspeximus audit-verify`, which is where these live.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="export a store's record-keeping state to a portable bundle")
    b.add_argument("--path", help="store file (default: $INSPEXIMUS_PATH or ./inspeximus_memory.json)")
    b.add_argument("--out", default="inspeximus_audit_bundle.json", help="output json path")
    b.add_argument("--expected-pubkey", default=None,
                   help="pin the signature-authenticity check to this key")
    v = sub.add_parser("verify", help="verify a bundle OFFLINE (needs only the file)")
    v.add_argument("bundle", help="the bundle json to verify")
    v.add_argument("--witnesses", default=None, help="comma-separated allowlisted witness pubkeys (hex)")
    v.add_argument("--threshold", type=int, default=1, help="k-of-n witness threshold")
    v.add_argument("--store", default=None,
                   help="the store file the bundle came from; binds the receipts to the CONTENT it "
                        "serves today. Without it a clean chain over substituted text reads PASS.")
    v.add_argument("--expected-pubkey", default=None,
                   help="pin the chain signatures to a key you got OUT OF BAND. Reaching this "
                        "entrypoint no longer costs you the pinned check.")
    v.add_argument("--require-signed", action="store_true",
                   help="refuse a bundle whose chain is unsigned or only present-but-unverified")
    a = ap.parse_args(argv)

    from .cli import main as _main
    if a.cmd == "build":
        fwd = (["--path", a.path] if a.path else []) + ["audit-build", "--out", a.out]
        if a.expected_pubkey:
            fwd += ["--expected-pubkey", a.expected_pubkey]
        return _main(fwd)

    fwd = ["audit-verify", a.bundle, "--threshold", str(a.threshold)]
    if a.witnesses:
        fwd += ["--witnesses", a.witnesses]
    if a.store:
        fwd += ["--store", a.store]
    if a.expected_pubkey:
        fwd += ["--expected-pubkey", a.expected_pubkey]
    if a.require_signed:
        fwd += ["--require-signed"]
    return _main(fwd)


if __name__ == "__main__":
    raise SystemExit(_cli())
