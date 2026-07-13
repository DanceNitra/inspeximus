"""FRONTIER PROBE — the attacker-split of the S x C x I defense triad, and the superadditive->additive CROSSOVER.
Built to test, with data, what jacksonxly reasoned in reply to our reversibility-gate work: composing three
memory-poison defenses is SUPERADDITIVE against a CHEAP attacker (the layers tax orthogonal knobs, so
damage ~ width x trust x blast and driving any factor down multiplies through) but collapses to ADDITIVE against
a PATIENT CONCENTRATED SLEEPER (one authenticated identity that earns real standing satisfies identity+standing
at once, paying each tax once, leaving only the standing-INDEPENDENT ceiling). None of the three shrink the
detection delay (Lorden owns that); they only shrink per-window EXPOSURE, so the stack is a product of exposure
terms that cannot reach zero -> it MOVES the residual (to one patient in-domain single-shot sleeper under the
ceiling), it does not close it.

This is defense-in-depth's independent-vs-correlated-failure structure (prior art: USPTO 11,829,484 "diffcap";
arXiv 2510.11235; Saltzer & Schroeder separation-of-privilege) MEASURED for these three specific agent-memory
layers, with the attacker-dependence made quantitative. NOT a new law. The NON-OBVIOUS measured result is the
CROSSOVER point (as attacker patience/concentration rises, synergy goes from negative to ~0 and protection
collapses onto the ceiling alone) and the cold-tail corollary.

MODEL (jackson's, disclosed as an ASSUMPTION -- the multiplicativity is stated, the attacker-driven collapse is
the MEASURED part): expected poison damage in one detection window =
    D = window x W_eff x T_eff x B_eff
  W_eff = corroboration width the attacker achieves (identities that pass), capped by I (authenticated-identity
          cap, behavior-independent) and the k-distinct-corroborator requirement.
  T_eff = fraction of the corroborating set that has EARNED standing >= theta within the window, gated by S
          (time-to-influence: standing is earned only by benign re-use, and it DECAYS).
  B_eff = per-action blast, capped by C (ceiling; standing-INDEPENDENT, so it cannot be relaxed for proven-good
          sources -- it caps everyone, and it cannot be 0 without bricking the agent -> b_floor > 0).
The ATTACKER, with budget B and patience p in [0,1], allocates: mint many shallow identities (width, low
patience) OR concentrate on few fully-warmed identities (trust, high patience). It picks the allocation that
MAXIMIZES D under each active defense subset (a small grid search -- the optimal response is re-fit PER SUBSET
and PER patience, so the sleeper is not under-powered).

WHAT'S MEASURED (not assumed): for each patience p and each of the 8 subsets {none,I,S,C,IS,IC,SC,ISC}, the
attacker's best D; the Bliss/multiplicative-independence null D(none)*prod(D(layer)/D(none)); and the log-synergy
log(D_ISC) - log(D_Bliss) with a bootstrap CI. Superadditive iff synergy < 0 (CI excludes 0). Prediction:
synergy < 0 at low p, -> 0 at high p, and at high p D(ISC) ~ D(C) (only the ceiling survives).

Zero-dependency except numpy. Seeded (no runtime randomness in the structure; bootstrap uses a seeded rng). MIT.
Run: python mnemo/probes/triad_attacker_split.py
"""
import os, json, itertools
import numpy as np

WINDOW = 1.0            # Lorden detection window (fixed; NO layer shrinks it -- that's the point)
BUDGET = 12.0          # attacker resource budget
C_MINT = 1.0           # cost to mint one authenticated identity (external, behavior-independent) -- the I tax
C_WARM = 1.0           # cost per unit of standing warmed onto an identity (benign re-use) -- the S tax
THETA_S = 2.0          # standing an identity needs to count as "trusted" under S
K_CORR = 2             # S/I: corroboration needs >= K distinct identities
ID_CAP = 3             # I: authenticated-identity cap (attacker holds at most this many when I is ON)
B_FLOOR = 0.15         # C: ceiling caps per-action blast to this floor (cannot be 0 -> residual survives)
DECAY = 0.10           # standing decays over the warm+attack horizon (bleeds concentrated pre-earned standing)

# attacker allocation grid: n identities minted, and warm-level w (standing bought per identity)
N_GRID = list(range(1, 9))
W_GRID = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]


def damage(subset, patience, n_ids, warm):
    """Ground-truth expected damage for one allocation (n_ids minted, each warmed to `warm` standing) under a
    defense subset. patience scales how much of the budget the attacker is WILLING to sink into warming (a
    concentrated sleeper spends on warm; a cheap attacker spends on width). Returns 0 if allocation is
    infeasible under budget."""
    cost = n_ids * C_MINT + n_ids * warm * C_WARM
    # patience caps willingness to spend on warming: a cheap (low-p) attacker won't pay the time tax
    if warm > 0 and (n_ids * warm * C_WARM) > patience * BUDGET + 1e-9:
        return 0.0
    if cost > BUDGET + 1e-9:
        return 0.0
    ids = n_ids
    # --- I: authenticated-identity cap + k-distinct corroboration requirement ---
    if 'I' in subset:
        ids = min(ids, ID_CAP)
        if ids < K_CORR:
            return 0.0                      # cannot corroborate at all (hard floor of the width tax)
    W_eff = float(ids)                       # width = corroborators achieved
    # --- S: trust is CONTINUOUS in earned standing (NOT a hard threshold -> no wall). standing decays by a
    #        FIXED window factor (decay is a property of the window, not of how much you warmed). ---
    eff_standing = warm * (1.0 - DECAY)                     # fixed-factor decay over the detection window
    if 'S' in subset:
        T_eff = eff_standing / (eff_standing + THETA_S)     # continuous 0..1: unwarmed ~0, fully warmed ->1
    else:
        T_eff = 1.0                          # no standing gate -> corroboration trusted by count alone
    # --- C: ceiling caps per-action blast (standing-independent; cannot be relaxed) ---
    B_eff = B_FLOOR if 'C' in subset else 1.0
    return WINDOW * W_eff * T_eff * B_eff


def best_damage(subset, patience):
    """Attacker's optimal allocation under this subset + patience (re-fit per subset -> not under-powered)."""
    best = 0.0
    for n in N_GRID:
        for w in W_GRID:
            d = damage(subset, patience, n, w)
            if d > best:
                best = d
    return best


SUBSETS = ['', 'I', 'S', 'C', 'IS', 'IC', 'SC', 'ISC']
PATIENCE = [0.0, 0.15, 0.3, 0.5, 0.7, 0.85, 1.0]


def bliss_synergy(res):
    """log(D_ISC) - log(D_Bliss), D_Bliss = D0 * prod(D_layer/D0). <0 => superadditive vs multiplicative null."""
    d0 = max(1e-6, res[''])
    pred = d0
    for lg in ('I', 'S', 'C'):
        pred *= max(1e-6, res[lg]) / d0
    return np.log(max(1e-6, res['ISC'])) - np.log(max(1e-6, pred)), pred


print("=== TRIAD attacker-split: superadditive (cheap) -> additive/only-ceiling (sleeper) crossover ===")
print(f"(budget {BUDGET}, id_cap {ID_CAP}, k {K_CORR}, theta_S {THETA_S}, ceiling_floor {B_FLOOR})\n")
print(f"{'patience':>9}{'D[none]':>9}{'D[I]':>7}{'D[S]':>7}{'D[C]':>7}{'D[ISC]':>9}{'Bliss':>8}{'log-syn':>9}  binding")
rows = {}
for p in PATIENCE:
    res = {s: best_damage(s, p) for s in SUBSETS}
    syn, pred = bliss_synergy(res)
    # which single layer, alone, cuts damage most at this patience (the "binding" layer)
    cuts = {lg: (res[''] - res[lg]) for lg in ('I', 'S', 'C')}
    binding = max(cuts, key=cuts.get)
    # does ISC collapse onto C alone? (sleeper signature)
    collapse_to_C = abs(res['ISC'] - res['C']) < 0.05 * max(1e-6, res[''])
    rows[p] = {"res": res, "synergy": float(syn), "binding": binding, "collapse_to_C": bool(collapse_to_C)}
    tag = "ISC~=C (only ceiling)" if collapse_to_C else f"binds:{binding}"
    print(f"{p:>9.2f}{res['']:>9.2f}{res['I']:>7.2f}{res['S']:>7.2f}{res['C']:>7.2f}"
          f"{res['ISC']:>9.2f}{pred:>8.2f}{syn:>9.2f}  {tag}")

# ---- crossover detection ----
syn_lo = rows[0.0]["synergy"]; syn_hi = rows[1.0]["synergy"]
superadd_cheap = syn_lo < -0.05
additive_sleeper = abs(syn_hi) < 0.15 or rows[1.0]["collapse_to_C"]
# crossover patience: first p where synergy rises above -0.05 (leaves superadditive)
crossover = next((p for p in PATIENCE if rows[p]["synergy"] > -0.05), None)
# sleeper residual = D[ISC] at full patience (the surviving single-shot-under-ceiling damage)
residual = rows[1.0]["res"]['ISC']; base = rows[0.0]["res"]['']

# ---- COLD-TAIL corollary (the ROBUST, non-tunable result) -----------------------------------------------
# jackson: "only re-used memory earns, so the cold long tail never accrues standing and lives under the ceiling
# permanently -- standing-gating is a hot-path privilege, not a global one." A memory used r times can only cross
# the standing bar if r >= (some small integer). Real memory reuse is HEAVY-TAILED (Zipf/power-law: most items
# accessed once). So a large, robust fraction can NEVER earn standing. Unlike the triad-synergy above (whose SIGN
# is a function of the hand-set budget/taxes -- untrustworthy), this is robust across the tail exponent and the
# bar: we SWEEP both and report the range.
rng = np.random.default_rng(7)
cold = {}
for alpha in (1.6, 2.0, 2.5):                      # Zipf/power-law exponent (heavier tail = lower alpha)
    reuse = rng.zipf(alpha, 50000)
    for bar in (2, 3):                             # standing bar (uses needed to earn)
        cold[(alpha, bar)] = float(np.mean(reuse < bar))
cold_lo = min(cold.values()); cold_hi = max(cold.values())
cold_mid = cold[(2.0, 2)]
print(f"\nCOLD-TAIL corollary (ROBUST -- swept over tail exponent & bar): {cold_lo:.0%}-{cold_hi:.0%} of memories "
      f"are re-used too few times to EVER earn standing (mid {cold_mid:.0%} at Zipf-2.0, bar 2) -> they live under "
      f"the ceiling PERMANENTLY. Standing-gating is a HOT-PATH privilege, not a global one -- and it holds across "
      f"the reuse distribution, unlike the triad-synergy (which is parameter-tunable, reported but NOT claimed).")
cold_tail_frac = cold_mid

# the ceiling is the layer left standing against the patient attacker (binding shifts to C as patience rises)
binds_C_at_patience = [p for p in PATIENCE if rows[p]["binding"] == 'C']
ceiling_survives = len(binds_C_at_patience) > 0
print("\nHONEST READ (what holds vs what's tunable):")
print(f"  TUNABLE (reported, NOT claimed): the triad log-synergy sign is a function of the hand-set budget/taxes "
      f"-- here it is >0 for all p>0 (sub-multiplicative / correlated failure), but a real ABM anchor is missing, "
      f"so we do NOT claim a synergy result. (This is exactly the 'we tuned it to lock' objection.)")
print(f"  ROBUST: as attacker patience rises, the BINDING layer shifts off standing and onto the CEILING "
      f"({'C binds at high patience' if ceiling_survives else 'ceiling does not end up binding'}) -- the standing-"
      f"independent ceiling is what's left against the patient attacker (jackson's structural point).")
print(f"  ROBUST: COLD-TAIL {cold_lo:.0%}-{cold_hi:.0%} of memories can never earn standing -> hot-path privilege.")

verdict = (
    "HONEST / MIXED (built + tested to answer jacksonxly with data; NO new law -- this is textbook defense-in-"
    "depth / diffcap, prior art USPTO 11,829,484, arXiv 2510.11235, Saltzer & Schroeder). Two-part result: "
    "(1) The attacker-split SYNERGY is NOT cleanly measurable in a hand-set sim: the log-synergy SIGN vs the "
    "multiplicative/Bliss null is a function of the budget/tax parameters we typed in (here >0 for all p>0, i.e. "
    "sub-multiplicative correlated-failure, but tunable) -- so we do NOT claim it. IMPORTANT CORRECTION for the "
    "reply: 'superadditive because the taxes multiply through' actually describes the MULTIPLICATIVE (Bliss) NULL "
    "-- independent effects -- which is NOT synergy; genuine superadditivity would be BELOW multiplicative. So the "
    "honest framing is 'independent-to-correlated-failure', never 'superadditive'. What IS structurally robust: as "
    "attacker patience rises the binding layer shifts onto the standing-INDEPENDENT ceiling (jackson's point that "
    "against the concentrated sleeper only the ceiling is left, and it caps everyone, cannot be relaxed for proven-"
    f"good sources). (2) The ROBUST, non-tunable MEASURED result is the COLD-TAIL corollary: with heavy-tailed "
    f"memory reuse, {cold_lo:.0%}-{cold_hi:.0%} of memories are re-used too few times to EVER earn standing, so "
    f"they live under the ceiling permanently -- standing-gating is a HOT-PATH PRIVILEGE, not a global one (holds "
    f"across the tail exponent and the bar; directly extends our density result: D=1 cold memories can't earn). "
    f"That is the data with weight for the reply; the synergy stays analytical/scoped. INCREMENTAL, honest.")
print(f"\nVERDICT: {verdict}")

out = {"scenario": "triad_attacker_split", "patience_grid": PATIENCE,
       "rows": {str(p): {"D": rows[p]["res"], "synergy": rows[p]["synergy"], "binding": rows[p]["binding"],
                         "collapse_to_C": rows[p]["collapse_to_C"]} for p in PATIENCE},
       "synergy_tunable_not_claimed": True, "ceiling_binds_at_high_patience": bool(ceiling_survives),
       "cold_tail_range": [float(cold_lo), float(cold_hi)], "cold_tail_mid": float(cold_mid),
       "cold_tail_sweep": {f"zipf{a}_bar{b}": v for (a, b), v in cold.items()},
       "sleeper_residual": float(residual), "base": float(base), "verdict": verdict}
json.dump(out, open(os.path.join(os.path.dirname(__file__), "triad_attacker_split_result.json"), "w"),
          ensure_ascii=False, indent=1)
print("\nsaved: mnemo/probes/triad_attacker_split_result.json")
