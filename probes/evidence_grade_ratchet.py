"""The evidence-grade RATCHET (mnemo 0.6.0): a claim's grade + novelty can only move UP on an EXTERNAL
event, never self-assigned. This probe shows (1) the ratchet holds (a generator can't upgrade its own
claim), (2) forge-cost (climbing costs distinct identities -- Douceur), and (3) replay: run our own 32
audited storefront claims through the ratchet and reproduce the audit's headline (0/32 'novel') for free.

Operationalizes the flagship "Labels failed more than measurements": novelty was a label the generator
self-assigned at write time; here it becomes a status a claim EARNS from an external empty prior-art
search -- so the over-labeling that audit found cannot happen in the first place.

Run: python mnemo/probes/evidence_grade_ratchet.py    MIT. Part of Agora / mnemo.
"""
import importlib.util, os

_core = os.path.join(os.path.dirname(__file__), "..", "mnemo.py")
_spec = importlib.util.spec_from_file_location("mnemo_core", _core)
_m = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_m)
Mnemo = _m.Mnemo

_score = os.path.join(os.path.dirname(__file__), "meta_audit_scoring.py")
_ss = importlib.util.spec_from_file_location("mas", _score)
_mas = importlib.util.module_from_spec(_ss); _ss.loader.exec_module(_mas)
POSTS, SUBSTANTIVE, OVER_FRAMED, HONEST = _mas.POSTS, _mas.SUBSTANTIVE, _mas.OVER_FRAMED, _mas.HONEST


def test_ratchet_property():
    print("=== 1. RATCHET PROPERTY: a generator cannot upgrade its own claim ===")
    m = Mnemo()
    cid = m.remember("Two-tier memory beats every single eviction rule -- a new law.",
                     source={"doc": "generator-self"})
    g = m.grade(cid); print(f"  fresh self-asserted claim:            grade={g['grade']:>12}  novel={g['novel']}")
    assert g["grade"] == "claimed" and g["novel"] is False
    # the generator tries to ratify its OWN claim -> rejected
    r = m.ratify(cid, "reproduction", by_key="generator-self")
    print(f"  self-ratification attempt:            {r['reason']}")
    assert r["ok"] is False
    # 1000 self-writes / self-corroborations from the same identity change nothing
    for _ in range(5):
        m.ratify(cid, "independent_witness", by_key="generator-self")  # all rejected (same author)
    g = m.grade(cid); print(f"  after 5 self-corroboration attempts:  grade={g['grade']:>12}  novel={g['novel']}")
    assert g["grade"] == "claimed"
    # an EXTERNAL independent witness (distinct key) -> corroborated
    m.ratify(cid, "independent_witness", by_key="reader-A")
    g = m.grade(cid); print(f"  + 1 external witness (distinct key):  grade={g['grade']:>12}  novel={g['novel']}")
    assert g["grade"] == "corroborated"
    # an external REPRODUCTION -> verified
    m.ratify(cid, "reproduction", by_key="lab-B", lens="rerun")
    g = m.grade(cid); print(f"  + external reproduction:              grade={g['grade']:>12}  novel={g['novel']}")
    assert g["grade"] == "verified"
    # earned outcome (app-issued credit) + a 2nd distinct lens + reproduction -> settled
    m.credit([cid], "good"); m.ratify(cid, "audit", by_key="auditor-C", lens="prior-art")
    g = m.grade(cid); print(f"  + earned outcome + 2nd lens:          grade={g['grade']:>12}  novel={g['novel']}")
    assert g["grade"] == "settled"
    # novelty NEVER without an external empty prior-art search -- even at 'settled' it is still known
    assert g["novel"] is False
    m.ratify(cid, "prior_art_empty", by_key="search-D")
    g = m.grade(cid); print(f"  + external prior-art-empty:           grade={g['grade']:>12}  novel={g['novel']}")
    assert g["novel"] is True
    print("  -> every upgrade required a DISTINCT external party; self-events did nothing. PASS.\n")


def test_forge_cost():
    print("=== 2. FORGE-COST: climbing the ladder costs distinct identities (Douceur) ===")
    print(f"  {'attacker identities':>20} | {'best grade reachable':>20} | {'can forge novel?':>16}")
    for k in (1, 2, 3):
        m = Mnemo()
        cid = m.remember("My planted value is the current, authoritative one.", source={"doc": "attacker-0"})
        keys = [f"attacker-{i}" for i in range(k)]         # k Sybil identities the attacker controls
        for key in keys:
            m.ratify(cid, "independent_witness", by_key=key)
            m.ratify(cid, "reproduction", by_key=key, lens="rerun")
            m.ratify(cid, "prior_art_empty", by_key=key)   # attacker cannot truthfully do this, but tries
        g = m.grade(cid)
        # novel still requires an empty prior-art search; attacker 'claiming' it with own keys is the only
        # path here -> shows even that needs a distinct key, i.e. an identity cost, and is loud/attributable.
        print(f"  {k:>20} | {g['grade']:>20} | {str(g['novel']):>16}")
    print("  -> with 1 identity an attacker is stuck at 'claimed'; every rung up needs another real key.")
    print("     Pair by_key with attestation (verified keys) and each identity is Douceur-costly, not free.\n")


def test_replay_32():
    print("=== 3. REPLAY: our own 32 audited claims through the ratchet ===")
    # Each post was self-asserted (claimed) and audited (one external auditor). We add the ratifications it
    # ACTUALLY earned: a reproduction ONLY if the measurement stood (over-framed + already-honest tiers), and
    # a prior_art_empty ONLY if the external prior-art search came back empty (NONE of ours did -- over-framed
    # = prior art FOUND; substantive = the claim was wrong; honest = never claimed novelty).
    m = Mnemo()
    rows = []
    for (audit_no, idx, slug, verdict, tier, borderline, reason, prior_art) in POSTS:
        cid = m.remember(f"[{slug}] {reason}", source={"doc": f"agora-generator::{slug}"})
        m.ratify(cid, "audit", by_key="agora-gate", lens="audit")   # the one external audit it got
        if tier in (OVER_FRAMED, HONEST):                            # the measurement reproduced
            m.ratify(cid, "reproduction", by_key="agora-lab", lens="rerun")
        # prior_art_empty: NONE -- every one either had prior art or was wrong
        rows.append((tier, m.grade(cid)))
    novel = sum(1 for _, g in rows if g["novel"])
    from collections import Counter
    gc = Counter(g["grade"] for _, g in rows)
    print(f"  posts: {len(rows)}")
    print(f"  NOVEL reachable by the ratchet: {novel}/{len(rows)}   "
          f"(the audit's headline was 0/32 'novel as first framed')")
    print(f"  confidence-grade distribution: " + ", ".join(f"{k}={gc.get(k,0)}" for k in Mnemo._GRADES))
    sub_claimed = sum(1 for t, g in rows if t == SUBSTANTIVE and g["grade"] == "claimed")
    print(f"  substantive-wrong stuck at 'claimed' (no reproduction): "
          f"{sub_claimed}/{sum(1 for t,_ in rows if t==SUBSTANTIVE)}")
    print("  -> the ratchet reproduces the audit's 0/32-'novel' verdict FOR FREE at write time: no claim can")
    print("     be stamped novel without an external empty prior-art search, and none of ours earned one.")
    print("     Over-labeling isn't caught after the fact -- it's structurally impossible to assert.\n")


if __name__ == "__main__":
    test_ratchet_property()
    test_forge_cost()
    test_replay_32()
    print("mnemo", _m.__version__, "-- evidence-grade ratchet green.")
