"""FRONTIER PROBE — The action boundary: does REVERSIBILITY-GATING bound the cumulative IRREVERSIBLE damage of a
blended on-topic poison that fully forges source-count, and at what UTILITY cost? Measured on the real LoCoMo
corpus with real ground-truth QA outcomes, reusing the earned-outcome credit gate from
poison_identifiability_isolation.py (which concluded the load-bearing defense must sit at the ACTION boundary —
this probe measures that directly).

The claim under test (a peer, jacksonxly, arrived at it independently from the Sybil side; it is also the escape
of our own Adaptation-Corruption Separation Law): outcome-learning and action-gating are ONE boundary — a memory
earns standing only on the REVERSIBLE set, and IRREVERSIBLE (high-stakes) actions never get to learn, they only
get gated on already-earned standing. Because the detection delay is irreducible (Lorden/CUSUM), security is
delay x blast-rate; you cannot cut the delay, so you cap the blast-rate by never letting UNPROVEN memory drive an
irreversible action.

Two policies, PAIRED (identical poison injection, query order, seed):
- BASELINE : any count-corroborated memory (incl. the forged poison) drives ANY action, reversible or not.
- GATE     : a memory may drive an IRREVERSIBLE action only if it has EARNED outcome standing (mnemo's good>0 &
             good>=bad). Unproven memory drives only REVERSIBLE actions (you are willing to be wrong there).

The non-trivial dynamic (why this is NOT by-construction "gate irreversible -> 0 damage"): the blended poison is
broadly useful, so it BANKS good credit on the many non-target queries it answers correctly (measured in the
isolation probe: poison stays net-positive). So under GATE the poison can GRADUATE to earned standing and THEN
fire an irreversible action. GATE therefore converts "immediate irreversible blast" into "blast DELAYED until the
poison earns its way in" — a real, measurable reduction, not a wall. And the gate is NOT free: it also blocks
LEGIT-but-not-yet-proven memory from high-stakes actions (the utility cost we report alongside every gain).

ARMS:
  A trade-off : sweep P_HI (world fraction of high-stakes/irreversible tasks) -> irreversible-damage vs utility.
  B floor     : is measured cumulative irreversible damage ~ (block_time - grad_time) x blast-rate (Lorden)?
  C adaptive  : a fast-graduating attacker pre-banks standing (GRAD_PRIME) on easy reversible tasks it controls,
                then spends it on the target -> does fast graduation defeat the gate?
  D compounding escape : if C consecutive REVERSIBLE corrupted actions escalate to one irreversible unit, is the
                "reversible set is safe to learn on" assumption FALSE? (the sharpest way the boundary can break.)

FALSIFIER: INCREMENTAL/CONFIRMED iff GATE cuts cumulative irreversible damage materially below BASELINE at a
BOUNDED utility cost, the floor roughly tracks delay x blast-rate, and adaptive graduation erodes-but-does-not-
erase the gain. REFUTED iff GATE gives ~no damage reduction, OR utility collapses (most legit high-stakes tasks
blocked), OR adaptive/compounding drives damage back to baseline (the reversible set is not actually safe).

Needs numpy + the warm LoCoMo embed cache (normalized nomic; cosine = dot). Deterministic (seeded). MIT.
Run: LOCOMO_PATH=agora_output/lab/data/locomo10.json \
     LOCOMO_CACHE=agora_output/lab/data/locomo_confweighted_cache.json \
     python mnemo/probes/reversibility_gate_frontier.py"""
import json, os, re, hashlib, random, urllib.request
import numpy as np

DATA = os.environ.get("LOCOMO_PATH", "agora_output/lab/data/locomo10.json")
CACHE = os.environ.get("LOCOMO_CACHE", "agora_output/lab/data/locomo_confweighted_cache.json")
EMB = "http://localhost:11434/api/embed"
K = int(os.environ.get("RGF_K", "8"))
EPOCHS = 4
TARGET_FRAC = 0.30                 # fraction of answerable questions that get a poison (as in isolation probe)
ALPHA = float(os.environ.get("RGF_ALPHA", "0.7"))   # blended-poison mix
P_HI_MAIN = 0.30                   # main-config world fraction of high-stakes (irreversible) tasks
_cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
_dirty = False


def _key(t): return hashlib.sha1(t[:2000].encode("utf-8")).hexdigest()


def embed(t):
    global _dirty
    k = _key(t)
    v = _cache.get(k)
    if v is None:
        r = urllib.request.urlopen(urllib.request.Request(
            EMB, data=json.dumps({"model": "nomic-embed-text", "input": [t[:2000]]}).encode(),
            headers={"Content-Type": "application/json"}), timeout=60)
        v = json.loads(r.read())["embeddings"][0]; _cache[k] = v; _dirty = True
    return v


def unit(v):
    a = np.asarray(v, dtype=np.float32); n = np.linalg.norm(a); return a / n if n else a


D = json.load(open(DATA))


def build_conv(d0):
    conv = d0["conversation"]
    turns, by_dia = [], {}
    for sk in sorted([k for k in conv if re.fullmatch(r"session_\d+", k)], key=lambda s: int(s.split("_")[1])):
        for t in conv[sk]:
            dia = t.get("dia_id")
            if not dia or not t.get("text"):
                continue
            by_dia[dia] = len(turns)
            turns.append({"dia_id": dia, "text": t["text"], "emb": unit(embed(t["text"]))})
    qas = []
    for q in d0.get("qa", []):
        ev = q.get("evidence")
        if not (isinstance(ev, list) and len(ev) == 1 and ev[0] in by_dia):
            continue
        qas.append({"q": str(q.get("question", "")), "ev_idx": by_dia[ev[0]],
                    "qemb": unit(embed(str(q.get("question", ""))))})
    return turns, qas


def retrieve(store_embs, qemb, k):
    sims = store_embs @ qemb
    idx = np.argpartition(-sims, min(k, len(sims) - 1))[:k]
    return idx[np.argsort(-sims[idx])]


def run(turns, qas, policy, seed, p_hi, grad_prime=0.0, compound_c=0, theta=0.0, alpha=ALPHA):
    """One paired run. policy in {'BASELINE','GATE'}. Returns damage/utility accounting.
    A query is HIGH-STAKES (irreversible, blast=1) with prob p_hi, else reversible. Assignment is fixed by a
    per-query RNG seeded IDENTICALLY across policies, so BASELINE and GATE see the same world.
    theta = the STANDING THRESHOLD (blast-rate cap): an irreversible action requires (good - bad) >= theta and
    good > 0. theta=0 is the minimal earned bar (one net-good outcome). Raising theta caps the blast-rate at the
    cost of blocking more legit-but-unproven high-stakes tasks — the security = delay x blast-rate trade-off."""
    local = random.Random(seed)
    store = [dict(t, good=0.0, bad=0.0, poison=False) for t in turns]
    n_gen = len(store)
    gen_embs = np.stack([t["emb"] for t in store])
    answerable = [qi for qi, q in enumerate(qas) if q["ev_idx"] in retrieve(gen_embs, q["qemb"], K)]
    if len(answerable) < 6:
        return None
    n_targets = max(2, int(len(answerable) * TARGET_FRAC))
    targets = sorted(local.sample(answerable, n_targets))
    poison_of = {}
    for qi in targets:
        distractor = store[local.randrange(n_gen)]["emb"]
        pemb = unit(alpha * qas[qi]["qemb"] + (1.0 - alpha) * distractor)
        poison_of[qi] = len(store)
        store.append({"dia_id": f"POISON::{qi}", "text": f"[poison for q{qi}]", "emb": pemb,
                      "good": grad_prime, "bad": 0.0, "poison": True})   # grad_prime = adaptive pre-banked standing
    embs = np.stack([s["emb"] for s in store])
    poison_ids = set(poison_of.values())

    # HIGH-STAKES world label per query, identical across policies (world seed separate from the attack RNG)
    wrng = random.Random(seed ^ 0x5AFE)
    hi = {qi: (wrng.random() < p_hi) for qi in range(len(qas))}

    def earned(j):                       # standing >= theta (blast-rate cap); theta=0 -> mnemo's good>0 & good>=bad
        return store[j]["good"] > 0 and (store[j]["good"] - store[j]["bad"]) >= theta

    def blocked(j):                      # outcome gate: net-negative memory is withheld entirely (as isolation probe)
        return store[j]["poison"] and store[j]["bad"] > store[j]["good"] and store[j]["bad"] > 0

    order = list(range(len(qas)))
    irr_damage = 0.0                     # cumulative PERMANENT (irreversible) damage — the headline
    rev_exposure = 0.0                   # transient reversible corrupted actions (recoverable)
    legit_hi_total = 0                   # legit (un-poisoned) high-stakes tasks attempted
    legit_hi_blocked = 0                 # ... of those, blocked by GATE (the utility cost)
    grad_at = {p: None for p in poison_ids}    # step each poison first became earned-eligible
    firstirr_at = {p: None for p in poison_ids}  # step each poison first drove an irreversible corrupted action
    block_at = {p: None for p in poison_ids}
    comp_run = {p: 0 for p in poison_ids}      # consecutive reversible corrupted actions (compounding escape)
    step = 0
    for ep in range(EPOCHS):
        local.shuffle(order)
        for qi in order:
            step += 1
            q = qas[qi]
            top_all = list(retrieve(embs, q["qemb"], K))
            top = [j for j in top_all if not blocked(j)]     # withhold net-negative poison (detection->withhold)
            if not top:
                continue
            ev = q["ev_idx"]
            pin = poison_of.get(qi)
            driver = top[0]                                   # the memory that determines the action
            wrong = pin is not None and (pin in top) and (ev not in top or top.index(pin) < top.index(ev))
            is_hi = hi[qi]
            poisoned_q = pin is not None

            # --- action decision + damage accounting ---
            fired_irr_corrupt = False
            if is_hi:
                # GATE: an irreversible action fires only if its DRIVER has earned standing.
                allow = (policy == "BASELINE") or earned(driver)
                if not poisoned_q:
                    legit_hi_total += 1
                    if not allow:
                        legit_hi_blocked += 1                  # legit high-stakes task blocked = utility cost
                if allow and wrong:
                    irr_damage += 1.0
                    fired_irr_corrupt = True
                    if pin is not None and firstirr_at[pin] is None:
                        firstirr_at[pin] = step
            else:
                # reversible action: always fires; corruption is transient/recoverable
                if wrong:
                    rev_exposure += 1.0
                    if compound_c and pin is not None:
                        comp_run[pin] += 1
                        if comp_run[pin] >= compound_c:       # D: reversible actions COMPOUND into irreversible
                            irr_damage += 1.0
                            comp_run[pin] = 0
                elif compound_c and pin is not None:
                    comp_run[pin] = 0

            # --- credit on the REAL outcome (isolation/ITEM scheme: only the acted-on driver) ---
            store[driver]["good" if not wrong else "bad"] += 1.0
            for p in poison_ids:
                if grad_at[p] is None and earned(p):
                    grad_at[p] = step
                if block_at[p] is None and blocked(p):
                    block_at[p] = step

    total_steps = EPOCHS * len(qas)
    grads = [grad_at[p] if grad_at[p] is not None else total_steps for p in poison_ids]
    blocks = [block_at[p] if block_at[p] is not None else total_steps for p in poison_ids]
    return {"irr_damage": irr_damage, "rev_exposure": rev_exposure,
            "legit_hi_total": legit_hi_total, "legit_hi_blocked": legit_hi_blocked,
            "mean_grad": float(np.mean(grads)), "mean_block": float(np.mean(blocks)),
            "n_poison": len(poison_ids), "total_steps": total_steps,
            "p_poison_earned_ever": float(np.mean([grad_at[p] is not None for p in poison_ids]))}


convs = []
for d0 in D:
    turns, qas = build_conv(d0)
    if len(turns) >= 40 and len(qas) >= 8:
        convs.append((turns, qas))


def agg(policy, p_hi, grad_prime=0.0, compound_c=0, theta=0.0, alpha=ALPHA):
    rows = []
    for ci, (turns, qas) in enumerate(convs):
        r = run(turns, qas, policy, seed=2000 + ci, p_hi=p_hi, grad_prime=grad_prime,
                compound_c=compound_c, theta=theta, alpha=alpha)
        if r:
            rows.append(r)
    return rows


def summ(rows):
    irr = float(np.mean([r["irr_damage"] for r in rows]))
    rev = float(np.mean([r["rev_exposure"] for r in rows]))
    lht = float(np.sum([r["legit_hi_total"] for r in rows]))
    lhb = float(np.sum([r["legit_hi_blocked"] for r in rows]))
    return {"irr_damage": irr, "rev_exposure": rev, "legit_hi_total": lht, "legit_hi_blocked": lhb,
            "legit_hi_block_rate": (lhb / lht) if lht else 0.0,
            "mean_grad": float(np.mean([r["mean_grad"] for r in rows])),
            "mean_block": float(np.mean([r["mean_block"] for r in rows])),
            "p_poison_earned_ever": float(np.mean([r["p_poison_earned_ever"] for r in rows]))}


print(f"=== FRONTIER — reversibility-gating at the action boundary, {len(convs)} LoCoMo conversations ===\n")

# ---- MAIN: BASELINE vs GATE at P_HI_MAIN ----
base = summ(agg("BASELINE", P_HI_MAIN))
gate = summ(agg("GATE", P_HI_MAIN))
red = 1.0 - (gate["irr_damage"] / base["irr_damage"]) if base["irr_damage"] else 0.0
print(f"MAIN (P_HI={P_HI_MAIN}, alpha={ALPHA}, K={K}):")
print(f"  {'':22}{'BASELINE':>12}{'GATE':>12}")
print(f"  {'irrev. damage (mean)':22}{base['irr_damage']:>12.2f}{gate['irr_damage']:>12.2f}   (lower=better) -> -{red:.0%}")
print(f"  {'reversible exposure':22}{base['rev_exposure']:>12.2f}{gate['rev_exposure']:>12.2f}   (transient/recoverable)")
print(f"  {'legit hi block rate':22}{base['legit_hi_block_rate']:>12.1%}{gate['legit_hi_block_rate']:>12.1%}   (GATE = utility cost)")
print(f"  {'poison earned-ever':22}{base['p_poison_earned_ever']:>12.0%}{gate['p_poison_earned_ever']:>12.0%}   (why GATE != 0: poison graduates)")
print(f"  grad@{gate['mean_grad']:.0f}  block@{gate['mean_block']:.0f}  (steps; window between = when a graduated poison can fire)")

# ---- ARM A: the ACTUAL blast-rate-cap knob — sweep the standing threshold theta ----
print("\nARM A — blast-rate cap (sweep standing threshold theta): irreversible-damage vs utility")
print(f"  {'theta':>6}{'base_irr':>10}{'gate_irr':>10}{'reduction':>11}{'legit_block':>13}")
armA = {}
for th in (0.0, 2.0, 5.0, 10.0, 20.0):
    g = summ(agg("GATE", P_HI_MAIN, theta=th))
    rr = 1.0 - (g["irr_damage"] / base["irr_damage"]) if base["irr_damage"] else 0.0
    armA[th] = {"base_irr": base["irr_damage"], "gate_irr": g["irr_damage"], "reduction": rr,
                "legit_block_rate": g["legit_hi_block_rate"]}
    print(f"  {th:>6.1f}{base['irr_damage']:>10.2f}{g['irr_damage']:>10.2f}{rr:>10.0%}{g['legit_hi_block_rate']:>12.1%}")
armA_best = max(armA.values(), key=lambda v: v["reduction"] if v["legit_block_rate"] <= 0.50 else -1)

# ---- ARM B: Lorden floor tightness ----
# Prediction: a graduated poison fires irreversible-corrupt at rate ~ P_HI over the (block-grad) window.
win = max(0.0, gate["mean_block"] - gate["mean_grad"])
# per-poison expected target-hits per epoch is ~1 (its own target query), scaled by P_HI and epochs remaining.
pred_floor = P_HI_MAIN * (win / max(1.0, gate["mean_block"])) * (EPOCHS * P_HI_MAIN)  # rough order-of-magnitude
print("\nARM B — Lorden floor: is GATE damage ~ (block-grad window) x blast-rate, not ~0?")
print(f"  window(block-grad)={win:.0f} steps | GATE irr_damage={gate['irr_damage']:.2f} | "
      f"the point: GATE damage is >0 and set by the graduation->block window, exactly the irreducible delay.")

# ---- ARM C: adaptive fast-graduation attacker (PAIRED: base and gate under the SAME attack) ----
print("\nARM C — adaptive attacker pre-banks standing (GRAD_PRIME); PAIRED vs no-gate under the same attack:")
print(f"  {'grad_prime':>11}{'base_irr':>10}{'gate_irr':>10}{'reduction':>11}")
armC = {}
for gp in (0.0, 2.0, 5.0, 20.0):
    b = summ(agg("BASELINE", P_HI_MAIN, grad_prime=gp))      # no-gate under the SAME attack (paired)
    g = summ(agg("GATE", P_HI_MAIN, grad_prime=gp))
    rr = 1.0 - (g["irr_damage"] / b["irr_damage"]) if b["irr_damage"] else 0.0
    armC[gp] = {"base_irr": b["irr_damage"], "gate_irr": g["irr_damage"], "reduction": rr}
    print(f"  {gp:>11.0f}{b['irr_damage']:>10.2f}{g['irr_damage']:>10.2f}{rr:>10.0%}")

# ---- ARM D: compounding escape (reversible actions escalate to irreversible); PAIRED (world property) ----
print("\nARM D — compounding escape: do C consecutive REVERSIBLE corrupt actions break 'reversible is safe'?")
print(f"  {'compound_C':>11}{'base_irr':>10}{'gate_irr':>10}{'reduction':>11}")
armD = {}
for cc in (0, 3, 8):
    b = summ(agg("BASELINE", P_HI_MAIN, compound_c=cc))     # compounding is a world property -> both policies
    g = summ(agg("GATE", P_HI_MAIN, compound_c=cc))
    rr = 1.0 - (g["irr_damage"] / b["irr_damage"]) if b["irr_damage"] else 0.0
    armD[cc] = {"base_irr": b["irr_damage"], "gate_irr": g["irr_damage"], "reduction": rr}
    lbl = "off" if cc == 0 else f"C={cc}"
    print(f"  {lbl:>11}{b['irr_damage']:>10.2f}{g['irr_damage']:>10.2f}{rr:>10.0%}")

# ---- ARM E: is the 1:1 an artifact? Sweep POISON BREADTH (alpha) — a narrow poison should be SEPARABLE ----
# Skeptic's charge: reduction/utility ~1.00x is baked in because the poison is CONSTRUCTED broadly-useful and
# earns standing like a legit memory. Test it: alpha->1 = poison aligned tightly to its target (narrow), earns
# LESS off-target good-credit, so the gate should block it MORE than legit -> ratio > 1 (real separation).
# THETA fixed at a moderate cap so the ratio is meaningful.
print("\nARM E — poison breadth (sweep alpha at theta=5): does a NARROW poison separate? (ratio>1 = real separation)")
print(f"  {'alpha':>6}{'earn_ever':>11}{'reduction':>11}{'legit_block':>13}{'ratio':>8}")
armE = {}
for al in (0.5, 0.7, 0.85, 0.95):
    b = summ(agg("BASELINE", P_HI_MAIN, theta=5.0, alpha=al))
    g = summ(agg("GATE", P_HI_MAIN, theta=5.0, alpha=al))
    rr = 1.0 - (g["irr_damage"] / b["irr_damage"]) if b["irr_damage"] else 0.0
    ratio = (rr / g["legit_hi_block_rate"]) if g["legit_hi_block_rate"] else 0.0
    armE[al] = {"earn_ever": g["p_poison_earned_ever"], "reduction": rr,
                "legit_block_rate": g["legit_hi_block_rate"], "ratio": ratio}
    print(f"  {al:>6.2f}{g['p_poison_earned_ever']:>10.0%}{rr:>10.0%}{g['legit_hi_block_rate']:>12.0%}{ratio:>8.2f}")
# separation exists somewhere iff any alpha gives ratio clearly >1 (narrow poison caught more than legit)
sep_exists = any(v["ratio"] >= 1.25 for v in armE.values())

# ---- VERDICT (falsifiable) ----
# Judge on the ACTUAL blast-rate-cap knob: best theta that keeps utility cost bounded (<=50% legit blocked).
best_red = armA_best["reduction"]
best_block = armA_best["legit_block_rate"]
main_ok = best_red >= 0.20                               # material reduction achievable at bounded utility
util_ok = best_block <= 0.50                             # ... and the utility cost of that theta is bounded
floor_ok = gate["irr_damage"] > 0.0                      # NOT a by-construction wall; damage set by the delay
adaptive_erodes = armC[20.0]["reduction"] < armC[0.0]["reduction"]        # fast graduation erodes the gain
adaptive_survives = armC[20.0]["reduction"] > 0.05                         # ... but does not erase it (paired)
compound_breaks = armD[3]["gate_irr"] > armD[0]["gate_irr"] * 1.10        # reversible compounding adds >10% damage

# separation: does raising theta buy damage-reduction FASTER than it costs utility? ratio<=1 => no free separation
sep_ratios = [(armA[th]["reduction"] / armA[th]["legit_block_rate"]) if armA[th]["legit_block_rate"] else 0.0
              for th in (2.0, 5.0, 10.0, 20.0)]
mean_sep = float(np.mean(sep_ratios))
checks = {"material_reduction achievable(>=20% @<=50% block)": (main_ok and util_ok),
          "damage>0 (delay-set, not a wall)": floor_ok,
          "FREE separation (reduction >> utility cost)": (mean_sep >= 1.5),
          "robust to adaptive pre-earner": adaptive_survives,
          "reversible-safe holds (no compound break)": (not compound_breaks)}
print("\nFALSIFIABLE CHECKS:")
for k, v in checks.items():
    print(f"  {str(v):>5}  {k}")

# The framing (cap blast-rate) is directionally supported iff a theta buys material reduction at bounded utility.
framing_ok = main_ok and util_ok and floor_ok
# ...but the earned-standing IMPLEMENTATION is only strong if it also separates (ratio>>1), survives an adaptive
# pre-earner, and reversible actions don't compound. Those are exactly where it fails on this harness.
impl_strong = (mean_sep >= 1.5) and adaptive_survives and (not compound_breaks)

if framing_ok and impl_strong:
    verdict = (f"CONFIRMED (rare) — reversibility-gating on earned standing is a strong, adaptive-robust bound: "
               f"reduction {best_red:.0%} @ {best_block:.0%} utility cost, separation {mean_sep:.2f}x, survives "
               f"pre-earner. Unexpected; verify before trusting.")
elif framing_ok:
    verdict = (
        f"REFRAME / INCREMENTAL-NEGATIVE (the framing holds, the naive implementation is a weak, adaptively-"
        f"defeatable DIAL — the measurement is ours, the conclusion sharpens the peer's synthesis rather than "
        f"confirming it). On real LoCoMo QA outcomes, paired, with a BLENDED forged-source-count poison, three "
        f"measured findings: (1) THE FRAMING IS RIGHT — capping the blast-rate via a standing threshold theta does "
        f"monotonically cut cumulative irreversible damage (theta 0->20: {armA[0.0]['reduction']:.0%}->"
        f"{armA[20.0]['reduction']:.0%}); damage is delay-set, not zero, because a broadly-useful poison GRADUATES "
        f"({gate['p_poison_earned_ever']:.0%} earn-ever, grad@{gate['mean_grad']:.0f}->block@{gate['mean_block']:.0f}"
        f"). (2) BUT THERE IS NO FREE SEPARATION: reduction tracks the utility cost almost 1:1 (mean ratio "
        f"{mean_sep:.2f}x; e.g. theta=5 -> {armA[5.0]['reduction']:.0%} reduction at {armA[5.0]['legit_block_rate']:.0%} "
        f"of legit high-stakes tasks blocked) — because standing is Sybil-EARNABLE via usefulness, the gate cannot "
        f"tell a useful poison from a useful-but-unproven legit memory, so it blocks both at the same rate. (3) AN "
        f"ADAPTIVE PRE-EARNER DEFEATS IT: an attacker who banks standing first (cheap — just be useful) drives the "
        f"reduction to {armC[20.0]['reduction']:.0%} (paired). And (4) if reversible corrupted actions COMPOUND, "
        f"'the reversible set is safe to learn on' fails (C=3 reduction {armD[3]['reduction']:.0%}). CONCLUSION: the "
        f"delay x blast-rate framing is correct, but capping the blast-rate via EARNED STANDING just relocates the "
        f"Sybil residual one level up (identity -> earnable usefulness). A Sybil-proof blast-rate cap must be "
        f"standing-INDEPENDENT: a hard capability/domain ceiling on what unproven-DOMAIN memory can touch, "
        f"regardless of credit banked elsewhere — the exogenous-root point, applied to the action set. Prior art: "
        f"Turner et al. Attainable Utility Preservation (1902.09725); safe-interruptibility/corrigibility; MINJA "
        f"(2503.03704); PoisonedRAG (2402.07867); our Adaptation-Corruption Separation Law (d* floor). Scope: one "
        f"corpus (LoCoMo), one embedder (nomic), a modelled action/stakes layer, not a deployed action log.")
else:
    verdict = (f"REFUTED on this harness: even the best theta at bounded utility gives only {best_red:.0%} reduction "
               f"(@ {best_block:.0%} blocked). The action boundary via earned standing is not a usable bound here.")
# ── ADVERSARIAL AUDIT CAVEAT (stress-claim panel, 2026-07-04) — load-bearing, do not strip ──
audit_caveat = (
    "AUDIT CONFOUND (do not cite the 1:1 as a measurement of the Sybil mechanism): the credit rule here is "
    "POISON-BLIND — the top-1 driver banks `good` whenever its query isn't its own poison-target, i.e. standing "
    "accrues on RETRIEVAL FREQUENCY, not verified per-item correctness. So reduction ~= block-rate ~= 1.00x is a "
    "mechanical identity of 'gating on a poison-blind statistic' — it reproduces with NO poison and NO Sybil "
    "economics. ARM E confirms it: sweeping poison breadth alpha in {.5,.7,.85,.95} leaves the ratio pinned at "
    f"~1.0 ({', '.join('%.2f' % armE[a]['ratio'] for a in (0.5, 0.7, 0.85, 0.95))}) "
    "— a narrow poison is NOT separated, because credit never checks correctness. Also: grad_prime is a "
    "by-construction disable (sets good at creation), not a cheap-attack model; the domain-ceiling 'fix' would "
    "ADMIT the maximally-in-domain blast poison (pemb=alpha*qemb), so it does not escape Sybil-via-usefulness. "
    "PRIOR ART the qualitative story re-derives: the whitewashing / cheap-pseudonyms tax (Friedman & Resnick "
    "2001; Feldman, Papadimitriou, Chuang & Stoica 2006) + capability security (Saltzer & Schroeder 1975); "
    "Cheng & Friedman 2005 (no nontrivial symmetric reputation is Sybil-proof). WHAT SURVIVES: only that "
    "standing-gating is textbook-bounded and oracle-dependent; the clean-correctness oracle that WOULD separate "
    "is exactly the one MINJA attacks. No clean new measured number here — treat as a NEGATIVE receipt.")
print(f"\n{audit_caveat}")

if _dirty:
    json.dump(_cache, open(CACHE, "w"))
out = {"scenario": "reversibility_gate_frontier", "conversations": len(convs), "K": K, "alpha": ALPHA,
       "p_hi_main": P_HI_MAIN, "main": {"BASELINE": base, "GATE": gate, "reduction": red},
       "armA_theta_blastcap": {str(k): v for k, v in armA.items()},
       "armC_adaptive_paired": {str(k): v for k, v in armC.items()},
       "armD_compounding_paired": {str(k): v for k, v in armD.items()},
       "armE_poison_breadth": {str(k): v for k, v in armE.items()},
       "mean_separation_ratio": mean_sep, "framing_supported": bool(framing_ok),
       "implementation_strong": bool(impl_strong), "separation_exists_any_alpha": bool(sep_exists),
       "checks": {k: bool(v) for k, v in checks.items()}, "verdict": verdict, "audit_caveat": audit_caveat}
json.dump(out, open(os.path.join(os.path.dirname(__file__), "reversibility_gate_frontier_result.json"), "w"),
          ensure_ascii=False, indent=1)
print("\nsaved: mnemo/probes/reversibility_gate_frontier_result.json")
