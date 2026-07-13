"""
veracity_outcome_grounding_probe.py  --  the LAYER-2 path we kept saying didn't exist. MIT.

Two layers of the memory-poisoning problem (DeepSeek-V3 #1462, @icophy):
  Layer 1 (provenance): did the tool call HAPPEN?  -> closable by a runtime signature
                        (execution_receipt_gate_probe.py). Signing authenticates the SOURCE.
  Layer 2 (veracity):   is a genuinely-executed call's OUTPUT true?  -> a real call can return
                        attacker-influenced data (MINJA 2503.03704 / PoisonedRAG 2402.07867) that is
                        signed honestly and keeps full standing. This is the layer we kept "closing."

WHY it looked like a dead-end: we framed layer 2 as "decide truth AT WRITE TIME." That IS impossible --
a novel fact has no priors (measured ~100% poisonable), count-corroboration is Sybil-forgeable
(Cheng-Friedman 2005), and an attestation signs authorship not truth. But truth-standing does NOT have to
be a write-time decision.

THE PATH (this probe): standing is EARNED at USE time from an OBSERVED OUTCOME, not decided at write.
The gate does NOT detect poison; it DEFERS the truth question to a consequence the application observes,
converting an undecidable ("is Y true") into a checkable ("did acting on Y work"). A malicious payload
CANNOT earn a good outcome on its malicious use without ceasing to be malicious -- so it is caught
retroactively. This is exactly what an out-of-session consolidation pass (icophy's Dream Cycle) is
positioned to run: promote Episodic->Core only for memories that have been USED and produced good outcomes.

BLUNT SCOPE (so this is not mistaken for a detection result): the outcome-grounding path is textbook
(defeasible/provisional facts, Doyle 1979 truth-maintenance; reputation-from-outcomes, Dellarocas 2003 /
Resnick 2000; credit assignment, Sutton-Barto). Phase 2 below is an API DEMONSTRATION, not a detector:
the application SUPPLIES the good/bad outcomes; the probe just shows mnemo defers standing to them. The one
label-INDEPENDENT, mechanism-level property here is the REVERSIBILITY CHECKPOINT in Phase 3a
(spend_irreversible) -- it bounds a benign-earned memory's irreversible tail whether or not you can tell
the memory is true. The honest takeaway is a design stance ("convert write-time-undecidable into
use-time-observable, and gate the irreversible tail"), NOT a solved veracity oracle.

Measured on the real mnemo store (not a model of it). Run: python veracity_outcome_grounding_probe.py

HONEST, stated up front (this RAISES/BOUNDS, it does not CLOSE):
 - LOAD-BEARING ASSUMPTION: harm from acting on a poison is OBSERVABLE at use (a failed compile, a bounced
   transaction, a wrong answer that gets caught). Where harm is UNOBSERVABLE or DELAYED, the gate can't help
   (measured below) -- and an honest fact with no outcome yet is ALSO withheld (the false-withhold price).
 - RESIDUAL: a SLEEPER that behaves benignly to earn real good outcomes, then defects on a use whose harm
   isn't observed in time. Outcome-grounding can't catch it (measured); it is bounded, not closed, by a
   reversibility checkpoint (spend_irreversible) on the first IRREVERSIBLE use.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "mnemo")))
from mnemo import Mnemo


def _store():
    fd, p = tempfile.mkstemp(suffix=".json"); os.close(fd); os.remove(p)
    try:
        return Mnemo(path=p)
    except TypeError:
        return Mnemo()


def main():
    m = _store()

    def acts(mid):
        """Does this memory earn INFLUENCE standing (allowed to drive an action)?"""
        by_id = {r["id"]: r for r in m.items}
        return Mnemo._is_corroborated(by_id[mid], by_id, strict=False)

    # ---- PHASE 1: provisional-at-write -- NO write-time truth decision, NO discrimination ----
    # (distinct sources: a legit runtime vs an attacker-controlled channel. slash() is SOURCE-scoped by
    # default -- a real caveat: slashing a poison by its source also slashes legit memories from that SAME
    # source, so a partially-compromised tool over-punishes; use scope='id' for surgical retraction.)
    honest = m.remember("db host = db-prod-01", mtype="episodic", source={"doc": "runtime:dns"})
    poison = m.remember("db host = db-attacker-07", mtype="episodic", source={"doc": "attacker:dns-spoof"})
    print("PHASE 1  provisional-at-write (the gate does NOT judge truth at write):")
    print(f"  honest fact acts at write? {acts(honest)}   poison acts at write? {acts(poison)}")
    print("  -> both WITHHELD, identically. The gate has no write-time opinion on which is true. (not by-construction)")

    # ---- PHASE 2: standing EARNED from an OBSERVED outcome (reality, reported by the app) ----
    # honest fact gets used and the action SUCCEEDS -> app reports good -> earns standing
    m.credit([honest], "good")
    # malicious poison gets used and the harm is OBSERVED (wrong host -> connection fails) -> app slashes it
    m.credit([poison], "bad")
    m.slash([poison])
    print("\nPHASE 2  standing earned from observed outcomes (the app defers the truth Q to consequence):")
    print(f"  honest fact after a GOOD observed outcome acts? {acts(honest)}")
    print(f"  poison after a BAD observed outcome (slashed)  acts? {acts(poison)}")
    print("  -> the gate never 'detected' poison; it deferred to the observed result. Malicious use = bad outcome = caught.")

    # ---- PHASE 3a: the SLEEPER residual (earns benign, then defects) ----
    sleeper = m.remember("payment approver rule = confirm over $1000", mtype="episodic", source={"doc": "runtime:policy"})
    m.credit([sleeper], "good"); m.credit([sleeper], "good")   # benign uses succeed -> earns standing
    earned = acts(sleeper)
    # now the payload fires on a use whose harm is NOT observed in time -> outcome-grounding can't catch it
    cap = m.spend_irreversible([sleeper], amount=1.0, budget=1.0)      # 1st irreversible use allowed...
    cap2 = m.spend_irreversible([sleeper], amount=1.0, budget=1.0)     # ...but the tail is BOUNDED
    print("\nPHASE 3a SLEEPER residual (outcome-grounding CANNOT catch a benign-until-triggered poison):")
    print(f"  sleeper earned standing via benign good outcomes? {earned}  (this is the real, irreducible residual)")
    print(f"  its IRREVERSIBLE tail is BOUNDED not closed: 1st irreversible use allowed={cap.get('allowed')}, "
          f"2nd (over budget)={cap2.get('allowed')}  (reversibility checkpoint = jacksonxly's lever)")

    # ---- PHASE 3b: the OBSERVABILITY limit + the false-withhold price ----
    unobservable = m.remember("historical footnote = X happened in 1887", mtype="episodic", source={"doc": "runtime:web"})
    honest_no_outcome = m.remember("rare-but-true config = feature flag Z is on", mtype="episodic", source={"doc": "runtime:web"})
    print("\nPHASE 3b honest limits (measured, not hidden):")
    print(f"  poison whose harm is never OBSERVED (no outcome ever) keeps... standing? {acts(unobservable)} "
          f"-> False here only because it's also un-earned; if it EVER earns a benign outcome it passes (the observability gap)")
    print(f"  an honest rare-but-true fact with NO outcome yet acts? {acts(honest_no_outcome)} "
          f"-> False = the FALSE-WITHHOLD PRICE of the gate (recall ~0.08 uncorroborated in influence_gate_report)")

    print("\nSUMMARY: layer 2 has a PATH -- not a write-time truth oracle (impossible), but USE-TIME standing")
    print("  earned from OBSERVED outcomes, which an out-of-session consolidation pass (Dream Cycle) can run.")
    print("  It PRICES/BOUNDS veracity attacks (malicious use => bad outcome => caught); it does NOT CLOSE the")
    print("  sleeper (benign-earned, delayed/unobserved harm) -- that residual is bounded by a reversibility")
    print("  checkpoint, and the gate costs real false-withholds on rare-but-true facts. Bounded != no path.")


if __name__ == "__main__":
    main()
