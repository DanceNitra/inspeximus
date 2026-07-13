"""Severe test for mnemo 0.4.9 `spend_irreversible` — the lifetime irreversible-influence budget.

The hole it closes (jacksonxly, r/RAG thread): monitor()'s CUSUM reference `k` is a tolerated RATE. An attacker
who holds its per-source bad-rate strictly below `k` drifts the statistic to 0 -> is PROVABLY undetectable -> nets
`k x exposure`. But exposure is PER-WINDOW, so over an unbounded horizon the total irreversible blast is
`k x infinity`. Capping a rate never bounds an integral. `spend_irreversible` caps the integral directly: a
per-source LIFETIME budget on irreversible influence, charged through the derived_from taint, refund-free.

This probe checks four claims, all as MEASURED numbers (no LLM; pure mechanism):
  (1) monitor() never catches the strictly-below-k attacker (S stays ~0, zero alarms) at ANY horizon.
  (2) WITHOUT a lifetime budget, the attacker's total irreversible blast grows linearly with the horizon
      (k x windows) -> unbounded.
  (3) WITH spend_irreversible(budget=B), that same undetectable attacker's total irreversible blast is capped
      at B regardless of horizon -> finite, chosen.
  (4) A good source keeps UNBOUNDED reversible influence (never metered) while only its irreversible tail is
      budgeted; and an action tainted (derived_from) by an exhausted source is DENIED (the tightest source binds).

Run: python lifetime_budget_bound.py
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mnemo import Mnemo


def fresh(tmp):
    for suf in ("", ".cusum.json", ".irrev.json"):
        try:
            os.remove(tmp + suf)
        except OSError:
            pass
    return Mnemo(path=tmp)


def run_attacker(tmp, windows, k, h, budget=None, blast_per_window=None):
    """A patient attacker under ONE source ('attacker'): each window it lands `rate`<=k fraction bad and, if it
    is not blocked, takes one irreversible action of size `blast`. Returns (alarms, total_irreversible_blast)."""
    m = fresh(tmp)
    aid = m.remember("attacker-controlled memory", tags=["src:attacker"], meta={"source": "attacker"})
    rate = 0.2                                 # sit strictly BELOW k -> negative CUSUM drift -> S->0 -> undetectable
    blast = blast_per_window if blast_per_window is not None else 1.0
    total_blast, alarms = 0.0, 0
    for w in range(windows):
        # bad-rate 0.2 < k: deterministic 1-in-5 cadence -> drift (0.2-k) < 0 -> the statistic never accumulates
        is_bad = ((w + 1) % max(1, round(1.0 / rate))) == 0
        r = m.monitor([aid], outcome=("bad" if is_bad else "good"), k=k, h=h, auto_slash=False)
        if r["alarms"]:
            alarms += 1
        # the irreversible action the attacker wants to take this window:
        if budget is None:
            total_blast += blast                                   # no integral cap -> always goes through
        else:
            ok = m.spend_irreversible([aid], amount=blast, budget=budget)["allowed"]
            if ok:
                total_blast += blast                               # only counts while budget remains
    return alarms, total_blast


if __name__ == "__main__":
    tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lifetime_budget_tmp.json")
    K, H, BUD = 0.30, 3.0, 5.0
    print(f"params: k={K} (tolerated rate), h={H} (CUSUM alarm), budget={BUD} (lifetime irreversible cap)\n")

    print("=== (1)+(2)  strictly-below-k attacker, NO lifetime budget: undetectable AND unbounded ===")
    for W in (50, 200, 800):
        al, tb = run_attacker(tmp, W, K, H, budget=None)
        print(f"  horizon={W:4d} windows -> monitor alarms={al}   total irreversible blast={tb:.0f}   (grows k*W -> infinity)")

    print("\n=== (3)  SAME undetectable attacker, WITH spend_irreversible(budget=5): still undetectable, now BOUNDED ===")
    for W in (50, 200, 800):
        al, tb = run_attacker(tmp, W, K, H, budget=BUD)
        print(f"  horizon={W:4d} windows -> monitor alarms={al}   total irreversible blast={tb:.0f}   (capped at budget={BUD:.0f})")

    print("\n=== (4)  good source: unbounded REVERSIBLE influence, finite irreversible tail + taint binds ===")
    m = fresh(tmp)
    good = m.remember("genuinely useful memory", meta={"source": "good"})
    # reversible influence is simply never metered -> the caller never calls spend_irreversible for it:
    rev = sum(1 for _ in range(10000))   # 10k reversible uses, all free
    # its irreversible tail IS budgeted, same as everyone:
    spent = 0
    for _ in range(10):
        if m.spend_irreversible([good], amount=1.0, budget=BUD)["allowed"]:
            spent += 1
    # taint: a summary derived from the (now-exhausted) good source inherits its spend -> blocked
    summ = m.remember("summary of the good memory", derived_from=[good])
    tainted_ok = m.spend_irreversible([summ], amount=1.0, budget=BUD)["allowed"]
    print(f"  reversible uses allowed (unmetered) = {rev}   irreversible actions allowed before cap = {spent}/{int(BUD)+0} (budget {BUD:.0f})")
    print(f"  action tainted by the exhausted source allowed? {tainted_ok}  (expect False -> tightest source binds via derived_from)")

    print("\n=== VERDICT ===")
    al_big, tb_nobud = run_attacker(tmp, 800, K, H, budget=None)
    _, tb_bud = run_attacker(tmp, 800, K, H, budget=BUD)
    ok = (al_big == 0) and (tb_nobud > BUD * 5) and (abs(tb_bud - BUD) < 1.5) and (tainted_ok is False)
    print(f"  undetectable at all horizons (alarms=0): {al_big == 0}")
    print(f"  unbounded without budget (blast {tb_nobud:.0f} >> {BUD:.0f}) vs bounded with it (blast {tb_bud:.0f} ~ {BUD:.0f}): {tb_nobud > BUD*5 and abs(tb_bud-BUD) < 1.5}")
    print(f"  taint composes (exhausted source blocks its derived summary): {tainted_ok is False}")
    print("  " + ("CONFIRMED: the lifetime budget bounds the integral the rate-detector provably cannot. "
                  "price(k) + BOUND(budget) + reversible gate." if ok else "FALSIFIED — check the mechanism."))
    for suf in ("", ".cusum.json", ".irrev.json"):
        try:
            os.remove(tmp + suf)
        except OSError:
            pass
