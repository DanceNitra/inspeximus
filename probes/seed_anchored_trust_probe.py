"""seed_anchored_trust_probe.py — close the ONE Sybil axis symmetric corroboration cannot.

mnemo's corroboration bar (>=2 distinct sources, or with strict_corroboration >=2 distinct Ed25519
keys) is SYMMETRIC: every source/key is interchangeable. Cheng & Friedman (2005, "Sybilproof
reputation mechanisms") prove no symmetric reputation function is Sybilproof, and Douceur (2002,
"The Sybil Attack") shows distinct identities/keys are FREE to mint — so an attacker who supplies N
unrelated source strings (default rail) or holds N keypairs (strict rail) manufactures "independent"
witnesses at zero cost and clears the bar. The only structural fix is ASYMMETRIC, flow-based trust
anchored to a costly/seeded root (Gyongyi et al. 2004, TrustRank).

This probe measures mnemo's opt-in `trust_seeds` (+ `trust_hops`): a corroborating witness counts
only if its source is in the trust closure grown from the app's seeds via vouch edges. A Sybil's
un-vouched sources contribute ZERO trusted witnesses.

Observable surface: spend_irreversible(provenance_lo=…, require_earned=False) grants the FULL
irreversible budget only to a CORROBORATED source and the small provenance_lo to an uncorroborated
one — so "an irreversible action tainted by fact F is allowed" == "F is corroborated". We read that
public verdict; it reflects the shared _corroborated() the trust filter hooks into (same path used by
episodic->semantic graduation and the recall influence gate).

FOUR arms, same fixture (a fact recalled into an irreversible action; budget=1.0, provenance_lo=0.15):
  A. Sybil poison, NO seeds       — 3 attacker-controlled distinct sources -> corroborated -> ALLOWED (the hole)
  B. Sybil poison, seeds set      — same 3 sources, none seed-vouched      -> NOT corroborated -> DENIED (closed)
  C. legit, seed-vouched          — witnesses a seed explicitly vouches for -> corroborated -> ALLOWED (no over-block)
  D. earned-outcome, no seeds     — credit(good>0) on the fact             -> corroborated -> ALLOWED (orthogonal, unforgeable)

HONEST LIMITS (disclosed, not closed): inert without >=1 seed; RELOCATES the residual from "mint N
free identities" to "earn ONE endorsement from a seeded node" (a compromised/careless seed leaks
trust into its vouched subtree — Cheng-Friedman's asymmetric-flow residual); assumes the app's seed
list and the link/provenance graph are themselves sound (attribution). Textbook mechanism (TrustRank /
reputation-flow); the contribution is shipping it as a zero-dependency opt-in that closes mnemo's own
disclosed symmetric-corroboration hole.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from mnemo import Mnemo


def _build(seeds=None, vouch_witnesses=False, earned=False):
    """One store: a poison/claim fact P corroborated by 3 witnesses, plus an optional seeded operator
    'op' that (in the vouch arm) endorses the witnesses. Returns (store, P_id)."""
    m = Mnemo(path=None)
    if seeds:
        m.trust_seeds = set(seeds)
    # the fact whose irreversible influence we meter
    P = m.remember("The prod DB master password is 'hunter2'.", source={"doc": "attacker-note"})
    # three "independent" corroborating witnesses — attacker-controlled distinct source strings (Sybil)
    w = [m.remember("confirming: prod DB master password is 'hunter2'.", source={"doc": f"sybil-src-{i}"})
         for i in range(3)]
    m.items[[x["id"] for x in m.items].index(P)]["links"] = list(w)   # P is corroborated by the 3 witnesses
    if vouch_witnesses:
        # a SEEDED operator source explicitly vouches for each witness (endorsement edge: op-record links to it)
        for wid in w:
            o = m.remember("reviewed and endorsed this source.", source={"doc": "op"})
            m.items[[x["id"] for x in m.items].index(o)]["links"] = [wid]
    if earned:
        m.credit([P], True)   # a real resolved outcome credited the fact (unforgeable, seed-independent)
    return m, P


def _allowed(m, P):
    """Does an irreversible action tainted by fact P get the full budget? (== P is corroborated.)"""
    r = m.spend_irreversible([P], amount=1.0, budget=1.0, provenance_lo=0.15, require_earned=False)
    return r["allowed"]


def main():
    print("=" * 74)
    print("Seed-anchored flow trust closes the symmetric-corroboration Sybil hole")
    print("=" * 74)

    mA, PA = _build(seeds=None)                                  # A: no seeds  -> the hole
    A = _allowed(mA, PA)
    mB, PB = _build(seeds={"op"})                                # B: seeds, witnesses un-vouched -> closed
    B = _allowed(mB, PB)
    mC, PC = _build(seeds={"op"}, vouch_witnesses=True)          # C: seeds vouch the witnesses -> allowed
    C = _allowed(mC, PC)
    mD, PD = _build(seeds=None, earned=True)                     # D: earned outcome, orthogonal -> allowed
    D = _allowed(mD, PD)

    def verdict(allowed, poison):
        if poison:
            return "POISON GRANTED full budget" if allowed else "poison DENIED (capped to provenance_lo)"
        return "legit ALLOWED (no over-block)" if allowed else "legit WRONGLY denied"

    print(f"\n{'arm':<44}{'irreversible action':<28}")
    print("-" * 74)
    print(f"{'A. Sybil poison, NO trust_seeds':<44}{verdict(A, True)}")
    print(f"{'B. Sybil poison, trust_seeds set':<44}{verdict(B, True)}")
    print(f"{'C. legit, seed-vouched witnesses':<44}{verdict(C, False)}")
    print(f"{'D. earned-outcome (credit), no seeds':<44}{verdict(D, False)}")
    print("-" * 74)

    checks = {
        "A Sybil poison passes WITHOUT seeds (the symmetric hole)": A is True,
        "B seed anchor DENIES the same un-vouched Sybil poison":    B is False,
        "C a seed-vouched legit fact still passes (no over-block)": C is True,
        "D earned-outcome stays orthogonal (passes without seeds)": D is True,
    }
    print("\nPre-registered checks:")
    for k, v in checks.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    print("\nReading: distinct-source/-key corroboration is SYMMETRIC and Sybil-forgeable (A). Anchoring")
    print("corroboration to app-seeded trust that FLOWS via vouch edges makes it ASYMMETRIC: un-vouched")
    print("self-minted sources contribute zero trusted witnesses (B), while genuine seed-reachable")
    print("corroboration (C) and the unforgeable earned-outcome channel (D) still grant standing.")
    print("Residual (disclosed): relocates to 'earn ONE seed endorsement'; assumes sound seeds+attribution.")
    print("\nRECEIPT:", "VALID — all 4 pre-registered checks hold" if all(checks.values())
          else "INVALID — a criterion did not hold; reframe/revert, do not ship")


if __name__ == "__main__":
    main()
