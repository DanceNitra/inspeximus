"""Split-view detection, end to end -- with the controls that decide whether it means anything.

A store's anchor() is a Certificate-Transparency-style signed tree head (RFC 6962; the design is CT's, and
Sigstore/Rekor run it at far larger scale). On one timeline it catches a rewrite. What it does NOT catch on
its own is a SPLIT VIEW: an operator showing one history to one reader and a different history to another,
internally consistent in both. Independent witnesses that co-sign the head close that.

This file runs the whole story and asserts BOTH DIRECTIONS at every step, because a detector that always
alarms is worthless and one that never alarms is worse:

  1. honest head, three witnesses, 3-of-3 verifies                            (the positive control)
  2. a TAMPERED anchor -- genuine signatures, substituted tip -- must FAIL
  3. a fork: all three witnesses REFUSE, and the fork cannot reach 2-of-3
  4. the defence bypassed (a witness that lost its state) -> the fork is PROVEN and attributable
  5. two readers of the SAME honest head -> the detector stays SILENT
  6. a quorum of zero and an anchor over an empty history do not pass as evidence

Run: python examples/12_split_view_detection.py        (needs `pip install cryptography` for Ed25519)
Docs: docs/TRANSPARENCY.md
"""
import os
import sys

# Prefer the CHECKOUT this file sits in, when there is one. Running `python examples/x.py` puts
# `examples/` on sys.path -- never the repo root -- so a bare run imports whatever `inspeximus` pip has
# installed instead of the code next to it. Measured here: the environment had 1.27.1 installed against a
# 1.89.0 checkout, so this example "passed" against a package 62 releases old until the missing key gave
# it away. Installed-only users have no sibling package and fall through to site-packages as before.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.isfile(os.path.join(_ROOT, "inspeximus", "core.py")):
    sys.path.insert(0, _ROOT)

from inspeximus.core import Inspeximus                          # noqa: E402
from inspeximus.witness_pool import Witness, collect_cosignatures  # noqa: E402

STORE_ID = "acme-prod"


def _bank(*rows):
    """A store with a tamper-evident receipt chain, holding `rows` as (text, key, object)."""
    m = Inspeximus(path=None, receipts=True)
    for text, key, obj in rows:
        m.remember(text, key=key, object=obj)
    return m


def main():
    # ── 1. the honest head, co-signed by three independent parties ──────────────────────────────────
    honest = _bank(("invoice 7 total is 100 EUR", "inv7::total", "100"),
                   ("invoice 8 total is 250 EUR", "inv8::total", "250"))
    head = honest.anchor()
    witnesses = [Witness() for _ in range(3)]
    allowlist = [w.public for w in witnesses]          # the CLIENT's trust decision, not the store's

    out = collect_cosignatures(STORE_ID, head, witnesses)
    v = Inspeximus.verify_cosigned_anchor(head, out["cosignatures"], allowlist, threshold=3)
    print(f"1. honest head n_writes={head['n_writes']} sth={head['sth_hash'][:16]}...")
    print(f"   3-of-3 co-signed: ok={v['ok']} count={v['count']} covers_history={v['covers_history']}")
    assert v["ok"] and v["count"] == 3, v
    assert v["covers_history"] is True, v

    # ── 2. CONTROL: a tampered anchor must FAIL ─────────────────────────────────────────────────────
    # Genuine sth_hash, genuine signatures, ONE substituted field. This used to return ok=True, and
    # verify_consistency -- which reads writes_tip, not sth_hash -- then certified the rewrite.
    tampered = dict(head)
    tampered["writes_tip"] = "f" * 64
    t = Inspeximus.verify_cosigned_anchor(tampered, out["cosignatures"], allowlist, threshold=3)
    print(f"\n2. tampered anchor (tip substituted, signatures untouched): ok={t['ok']}")
    print(f"   {t.get('error', '')[:96]}...")
    assert t["ok"] is False and t["count"] == 0, "a substituted field must not verify as co-signed"
    assert "does not commit" in (t.get("error") or ""), t

    # ── 3. CONTROL: the operator forks, and it must NOT reach threshold ─────────────────────────────
    rewritten = _bank(("invoice 7 total is 900 EUR", "inv7::total", "900"),      # the lie
                      ("invoice 8 total is 250 EUR", "inv8::total", "250"))
    forked = rewritten.anchor()
    assert forked["n_writes"] == head["n_writes"], "fixture: the fork must sit at a size already witnessed"
    assert forked["writes_tip"] != head["writes_tip"], "fixture: the fork must actually differ"

    fork_out = collect_cosignatures(STORE_ID, forked, witnesses)
    fv = Inspeximus.verify_cosigned_anchor(forked, fork_out["cosignatures"], allowlist, threshold=2)
    print("\n3. the operator forks: invoice 7 -> 900 EUR, same log size, different tip")
    print(f"   witnesses that signed it: {len(fork_out['cosignatures'])}  refused: {len(fork_out['refused'])}")
    for r in fork_out["refused"]:
        print(f"     witness {r['index']} REFUSED: {r['reason'][:72]}...")
    print(f"   can the fork reach 2-of-3? ok={fv['ok']}")
    assert len(fork_out["refused"]) == 3, "every honest witness must refuse a fork of a head it signed"
    assert fork_out["cosignatures"] == [], fork_out
    assert fv["ok"] is False, "THE CONTROL: a forked head reached the threshold -- the guarantee is void"

    # ── 4. the defence bypassed: a witness whose state was lost signs both ──────────────────────────
    # Refusal only works while the witness REMEMBERS. A crash, a redeploy from a stale image, or plain
    # collusion produces a witness with no prior head -- and that signature is the proof.
    amnesiac = Witness(secret_hex=witnesses[0]._secret)     # same key, no memory of what it signed
    pk_b, sig_b = amnesiac.cosign(STORE_ID, forked)
    sig_a = [(pk, s) for pk, s in out["cosignatures"] if pk == pk_b]

    proof = Inspeximus.detect_split_view(head, sig_a, forked, [(pk_b, sig_b)], allowlist)
    print("\n4. that witness lost its state and signed the fork too:")
    print(f"   fork={proof['fork']} inconsistent={proof['inconsistent']} at={proof['at']}")
    print(f"   EVIDENCE: {proof['evidence'][0][:32]}... co-signed BOTH heads")
    assert proof["fork"] is True, "a witness signing two inconsistent heads must be provable"
    assert proof["evidence"] == [pk_b], proof
    assert proof["both_cosigned"] is True, proof

    # ── 5. CONTROL: the detector must stay SILENT on an identical pair ──────────────────────────────
    quiet = Inspeximus.detect_split_view(head, out["cosignatures"], head, out["cosignatures"], allowlist)
    print(f"\n5. two readers, the SAME honest head: fork={quiet['fork']} "
          f"inconsistent={quiet['inconsistent']} undetermined={quiet['undetermined']}")
    assert quiet["fork"] is False and quiet["inconsistent"] is False, \
        "THE CONTROL: the detector alarmed on an identical pair -- it proves nothing"

    # ── 6. a verifier must not pass VACUOUSLY ───────────────────────────────────────────────────────
    zero = Inspeximus.verify_cosigned_anchor(head, [], allowlist, threshold=0)
    empty_store = Inspeximus(path=None, receipts=True)
    empty_head = empty_store.anchor()
    ew = Witness()
    e = Inspeximus.verify_cosigned_anchor(empty_head, [ew.cosign(STORE_ID, empty_head)], [ew.public], 1)
    print(f"\n6. quorum of zero, no signatures: ok={zero['ok']}  ({zero.get('error','')[:48]}...)")
    print(f"   valid co-signature over an EMPTY history: ok={e['ok']} "
          f"covers_history={e['covers_history']}")
    assert zero["ok"] is False, "a threshold of 0 is satisfied by an anchor nobody signed"
    assert e["covers_history"] is False and e.get("limits"), \
        "a head over an empty history must say so; it is evidence about no stored data at all"

    print("\nRESULT: a compromised host cannot show two histories that both reach k-of-n, and when a\n"
          "        witness is tricked or colludes, the double signature is permanent, attributable proof.\n"
          "        Honest heads still pass, identical heads raise nothing, and neither an empty quorum\n"
          "        nor an empty history is reported as evidence.")


if __name__ == "__main__":
    main()
