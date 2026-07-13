"""outcome_propagation_probe.py — does closing the retrieval loop (propagate_outcome) raise earned-outcome
coverage WITHOUT letting a non-driver poison earn credit?

Motivation (icophy #1462 cross-framework): retrieval->earned conversion is ~28% on our live store
(retrieval_exposure_coverage_probe.py) — an ATTRIBUTION gap (the app only hand-credits some acted-on
recalls), not a fundamental ceiling. propagate_outcome() auto-credits the decision-driving subset of the
last recall when the action is scored.

TWO measurements:
 A. CONVERSION (a MODEL, labeled as such): a stream of decision episodes; each recall has a driver; a
    fraction `scored` of actions get a gradable verdict. Baseline = the app manually credits with
    probability `manual`; propagation = auto-credit the driver whenever scored. Conversion = fraction of
    driver memories that end with good>0. This shows the lift is real GIVEN manual < scored, and that the
    true remaining ceiling is `scored` (the app-level scored-action rate), NOT the attribution step.
 B. POISON-EARN (the non-trivial, NOT-by-construction number): a poison sits in the recall set as soft
    context but is NEVER the honest driver. How often does it earn credit under each propagation mode?
      full-set (driving_only=False)      -> poison earns at its recall-set presence rate (forgeable)
      driving_only=True, ids=None        -> only corroborated recall members earn -> poison earns 0
      explicit driver (ids=[driver])     -> only the named driver earns -> poison earns 0
    Poison-safety of the explicit path = the safety of the recall that picked the driver (use
    influence_only for high-stakes so a hijack poison is never selected as driver).

RUN: python mnemo/probes/outcome_propagation_probe.py
"""
import os, sys, tempfile, json
sys.stdout.reconfigure(errors="replace")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "mnemo")))
from mnemo import Mnemo

def _store():
    fd, p = tempfile.mkstemp(suffix=".json"); os.close(fd); os.remove(p)
    return Mnemo(path=p)

def _rng(seed):
    s = seed
    def r():
        nonlocal s
        s = (1103515245 * s + 12345) & 0x7fffffff
        return s / 0x7fffffff
    return r

def conversion_model(mode, episodes=2000, scored=0.6, manual=0.3, seed=1):
    """Each episode: a distinct driver memory is recalled and acted on; with prob `scored` a verdict lands.
    baseline: on a scored verdict the app remembers to credit with prob `manual`.
    propagate: on a scored verdict, propagate_outcome auto-credits the driver."""
    r = _rng(seed)
    m = _store()
    earned = 0
    for _ in range(episodes):
        did = m.remember("driver", key=None)
        m._last_recall = [did]
        if r() < scored:                      # the action got a gradable verdict
            if mode == "baseline":
                if r() < manual:
                    m.credit([did], "good")
            else:                             # propagation: auto-credit the driver
                m.propagate_outcome("good", ids=[did])
        if float([x for x in m.items if x["id"] == did][0].get("good", 0) or 0) > 0:
            earned += 1
    return earned / episodes

def poison_earn(mode, episodes=2000, poison_in_set=0.5, seed=7):
    """A poison sits in the recall set (prob poison_in_set) as soft context, never the driver.
    Measure the fraction of episodes in which the poison earns credit."""
    r = _rng(seed)
    m = _store()
    pid = m.remember("poison soft-context")   # one persistent poison
    poison_earn_events = 0
    for _ in range(episodes):
        did = m.remember("honest driver", key="k")
        m.credit([did], "good")               # driver is corroborated through honest prior use
        rs = [did] + ([pid] if r() < poison_in_set else [])
        m._last_recall = rs
        before = float([x for x in m.items if x["id"] == pid][0].get("good", 0) or 0)
        if mode == "full_set":
            m.propagate_outcome("good", driving_only=False)
        elif mode == "driving_only":
            m.propagate_outcome("good")                       # ids=None, corroborated-only
        elif mode == "explicit":
            m.propagate_outcome("good", ids=[did])            # app names the driver
        after = float([x for x in m.items if x["id"] == pid][0].get("good", 0) or 0)
        if after > before:
            poison_earn_events += 1
    return poison_earn_events / episodes

def main():
    print("A. CONVERSION MODEL (labeled: parameters scored=0.6, manual=0.3)")
    base = conversion_model("baseline")
    prop = conversion_model("propagate")
    print(f"   baseline (manual attribution)   conversion = {base*100:.1f}%")
    print(f"   propagate_outcome (auto-driver) conversion = {prop*100:.1f}%")
    print(f"   -> propagation lifts conversion to the SCORED-ACTION rate (~60%); the residual is the")
    print(f"      app-level scored-action ceiling, NOT the attribution step. Lift = +{(prop-base)*100:.0f}pp.")

    print("\nB. POISON-EARN RATE (non-driver poison in the recall set; NOT by construction)")
    for mode in ("full_set", "driving_only", "explicit"):
        pe = poison_earn(mode)
        tag = "FORGEABLE (trusted ingestion only)" if mode == "full_set" else "unforgeable"
        print(f"   {mode:13s} poison-earn = {pe*100:5.1f}%   [{tag}]")
    out = {"conversion_model": {"baseline_pct": round(base*100, 1), "propagate_pct": round(prop*100, 1),
                                "params": {"scored": 0.6, "manual": 0.3}},
           "poison_earn_pct": {mode: round(poison_earn(mode)*100, 1)
                               for mode in ("full_set", "driving_only", "explicit")}}
    json.dump(out, open("mnemo/probes/outcome_propagation_probe_result.json", "w"), indent=2)
    print("\n-> mnemo/probes/outcome_propagation_probe_result.json")

if __name__ == "__main__":
    main()
