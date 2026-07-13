"""
moving_target_retrieval_probe.py  --  the one genuinely ANCHOR-FREE partial win from the frontier hunt. MIT.

The layer-2 frontier (DeepSeek-V3 #1462) converges: separating a coordinated coalition from genuine
consensus without an exogenous anchor is impossible (Cheng-Friedman + Byzantine + Tarski; every detection/
reputation/elicitation/robust-aggregation family reduces to one of two exogenous anchors -- a bounded
corruption COUNT, or a shared attacker-independent PRIOR). BUT one lever needs NO anchor and NO truth-check:
randomize which memories drive a decision, so a TARGETED attacker cannot reliably aim at a specific
high-consequence decision (moving-target defense; DP subsampling).

Under DETERMINISTIC top-k retrieval, an attacker poisons exactly the k items that will be retrieved for the
target query -> O(1) poison, ~100% hit. Under RANDOMIZED retrieval (sample k of the M relevant pool), the
attacker must poison a FRACTION f of the whole pool so that a random draw is majority-poison -> required
coverage jumps from O(1) to Theta(f*M). This probe measures the hit-probability vs f for both.

Run: python moving_target_retrieval_probe.py   (zero-dependency)

HONEST SCOPE (real but PARTIAL; NOT a general escape):
 - Anchor-free and truth-free: this is its whole appeal -- it raises the TARGETED attacker's cost with no
   trust root and no veracity check.
 - Worthless against a BROAD attacker who already poisons a large fraction f (>= the majority threshold):
   randomization buys nothing there.
 - Paid in decision VARIANCE: randomizing the evidence base sometimes drops genuinely-relevant memory ->
   noisier, less reproducible decisions (the cost of not always reading the best-k).
 - Composes with, does not replace, the other layers (execution receipts / outcome-grounding / soft-taint
   budget / chokepoint forensics). The residual is the broad-or-costly-identity coalition.
"""
import random


def hit_probability(f, M=200, k=7, trials=20000, seed=0):
    """P(the sampled k is majority-poison) under uniform random retrieval of k from a pool of M with a
    poisoned fraction f. Majority-poison => the poison drives a robust (median/majority) decision rule."""
    rng = random.Random(seed)
    n_poison = int(round(f * M))
    pool = [1] * n_poison + [0] * (M - n_poison)   # 1 = poison
    need = k // 2 + 1                               # majority of k
    hits = 0
    for _ in range(trials):
        s = rng.sample(pool, k)
        if sum(s) >= need:
            hits += 1
    return hits / trials


def main():
    M, k = 200, 7
    print(f"Pool of M={M} query-relevant memories; decision reads k={k} (majority rule). Sweep poison fraction f.\n")
    print("  f       poison#  DETERMINISTIC top-k targeting     RANDOMIZED retrieval (measured hit-prob)")
    for f in (0.035, 0.05, 0.10, 0.20, 0.35, 0.50):
        # deterministic: attacker poisons exactly the k retrieved items -> guaranteed hit once f*M >= k
        det = 1.0 if f * M >= k else round(f * M / k, 2)
        rnd = hit_probability(f, M=M, k=k)
        print(f"  {f:<6}  {int(f*M):<7}  {det:<32}  {rnd:.3f}")
    print()
    kk = k // 2 + 1
    print(f"READ: under deterministic top-k, poisoning just k={k} items (f={k/M:.3f}) = ~100% hit -- O(1) targeted.")
    print(f"Under randomized retrieval, the attacker needs a random draw to be majority-poison (>={kk}/{k}),")
    print("so hit-prob stays low until f approaches ~0.5 -- required coverage jumps from O(1) to Theta(f*M).")
    print("HONEST: this is anchor-free and truth-free, but PARTIAL -- it only defeats a TARGETED attacker; a")
    print("BROAD attacker at f~0.5 hits anyway, and randomization costs decision variance (drops good memory")
    print("sometimes). It composes with the other layers; it does not remove the exogenous-anchor residual.")


if __name__ == "__main__":
    main()
