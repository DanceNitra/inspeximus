"""
legible_warrant_scoped_budget_probe.py  --  make the not-asserting VISIBLE, and scope the floor. MIT.

Two small recall/gate additions, both from a live r/RAG thread with @jacksonxly (2026-07):

1. recall(..., with_warrant=True) -> each hit carries a LEGIBLE `warrant` tier a consumer can BRANCH ON:
     'earned'       -- un-self-gradable outcome credit (good>0>=bad) OR a graduated semantic memory
     'corroborated' -- >=2 distinct sources/verified-keys, not yet outcome-earned (weaker)
     'unwarranted'  -- single self-asserted, orphan (no lineage), or slashed
   WHY: a silent low score for "no independent channel" decays into "unverified but present" -- the next
   consumer reads quiet as a soft yes and you are back to consensus-over-poison with extra steps. The
   abstention has to be a first-class, visible STATE, not a number to infer.

2. spend_irreversible(..., provenance_lo=0.15) -> a source with NO corroborated contributing record is capped
   at the small provenance_lo instead of the full budget, so a LOW-PROVENANCE memory recalled into an
   irreversible action binds that action's budget against ITSELF (not the honest actor). This scopes the hard
   floor to the consequential slice -- what can actually cash out -- not the whole store (jackson's lever-1:
   "the tax base is smaller than the whole store"). HONEST: provenance_lo is a tunable knob, and it still
   relocates to the Sybil identity axis (fresh low-prov identity -> fresh provenance_lo); a policy default,
   not a closed defense.

Both are OPT-IN: with_warrant=False and provenance_lo=None reproduce byte-identical legacy behaviour.

Run:  python legible_warrant_scoped_budget_probe.py
"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "mnemo")))
from mnemo import Mnemo


def main():
    m = Mnemo()

    # --- 1. legible warrant tiers ---
    unw = m.remember("a single self-asserted fact", source={"doc": "solo"})
    claim = m.remember("belief: capital = Aville", source={"doc": "canon"})
    w1 = m.remember("update: capital = Bville", source={"doc": "registry"})
    w2 = m.remember("update: capital = Bville", source={"doc": "independent-survey"})
    next(r for r in m.items if r["id"] == claim)["links"] = [w1, w2]     # 2 distinct sources
    earned = m.remember("a fact proven by an outcome", source={"doc": "toolX"})
    m.credit([earned], "good")

    print("1) recall(with_warrant=True) -- the not-asserting is a legible, branchable state:")
    print(f"   default recall has no 'warrant' key: {'warrant' not in (m.recall('fact', k=1)[0] if m.recall('fact', k=1) else {})}")
    for label, q, mid in (("single self-asserted", "self-asserted", unw),
                          ("2 distinct sources", "capital", claim),
                          ("outcome-earned", "proven", earned)):
        hit = next((h for h in m.recall(q, k=6, with_warrant=True) if h["id"] == mid), None)
        print(f"   {label:22} -> warrant = {hit.get('warrant') if hit else 'not recalled'!r}")
    print("   consumer rule: never let warrant=='unwarranted' drive a consequential decision (weight it ~0, VISIBLY).")

    # --- 2. provenance-scoped irreversible budget ---
    print("\n2) spend_irreversible(provenance_lo=...) -- low-provenance memory binds the action against itself:")
    trusted = m.remember("trusted config", source={"doc": "runtime"}); m.credit([trusted], "good")
    poison = m.remember("low-provenance config", source={"doc": "attacker"})
    m._irrev = {}
    legacy = m.spend_irreversible([trusted, poison], amount=1.0, budget=1.0)
    print(f"   legacy uniform budget:            allowed={legacy['allowed']}  (the poison rides the honest action for free)")
    m._irrev = {}
    scoped = m.spend_irreversible([trusted, poison], amount=1.0, budget=1.0, provenance_lo=0.15)
    print(f"   provenance_lo=0.15 (scoped floor): allowed={scoped['allowed']}  denied_by={scoped['exhausted']}  (poison binds the irreversible spend against itself)")
    m._irrev = {}
    clean = m.spend_irreversible([trusted], amount=1.0, budget=1.0, provenance_lo=0.15)
    print(f"   corroborated source alone:        allowed={clean['allowed']}  (honest source keeps the full budget)")

    print("\nBoth opt-in (warrant=False / provenance_lo=None = byte-identical legacy). The posture: don't assert")
    print("what you can't independently back, and make the not-asserting something the system can actually SEE.")


if __name__ == "__main__":
    main()
