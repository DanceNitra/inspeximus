"""reinforce_accuracy_ablation.py -- what does recall(reinforce=True) actually buy, and what does it cost?

`reinforce=True` is the shipped default of `Inspeximus.recall()`. It turns a read into a write: every
returned hit gets `value += 0.25 * relevance` and its decay clock reset (core.py ~5730), an episodic
record can graduate to semantic (~5766), and `value` is a multiplier in the ranking
(`score = sim * (1 + log1p(effective_value)) * ...`, ~5504). This probe MEASURES that default. It does
not change it, and it does not change ranking.

WHAT WAS ALREADY KNOWN, AND IS NOT RE-DERIVED HERE
    The COLD case is published. README (the 1.15.0 correction) and CHANGELOG both record reinforcement as
    a confound that DEPRESSES benchmark recall on a cold query stream -- "up to ~0.10 at low k" -- when
    many queries are swept against one store. Re-running that and reporting a null would be a
    re-derivation, so the cold stream is carried here only as a CONTROL, explicitly labelled as a known
    negative, and its job is to show the harness reproduces the published sign.

WHAT IS GENUINELY UNMEASURED, AND IS THE PRIMARY ARM
    The WARM store -- the case the design was actually for. Reinforcement's claim is "was-it-useful
    outranks merely-similar", which can only pay off once a store has absorbed a query stream and the
    accrued value carries a real popularity signal. So the primary experiment TRAINS on a query stream
    (reinforce=True vs reinforce=False -- the only difference between arms) and then EVALUATES on
    HELD-OUT questions that never appeared in training, with `reinforce=False` on BOTH arms so the
    measurement itself is a pure read. Treatment = the training regime; measurement = identical.
    Reported with a cluster-bootstrap 95% CI over seeds, under two test weightings (uniform over the
    corpus, and Zipf-weighted to match the workload the store was warmed on).

WHY THE BASELINE GUARD EXISTS (this is the whole reason the file is shaped this way)
    A previous attempt reported accuracy 0.0056 -> 0.0035 and nearly published "reinforcement buys
    nothing". Chance on that corpus was 1/300 = 0.0033, so BOTH ARMS WERE ON THE FLOOR and the delta
    measured nothing at all. So before ANY verdict is emitted, `assert_baseline_is_measurable()` runs on
    the control arm and ABORTS rather than reporting a delta; the chance level is printed next to every
    accuracy, always. A second guard, `assert_reinforcement_is_active()`, aborts if the reinforce=True
    arm did not actually accrue more value than the control -- otherwise the ablation would be comparing
    an arm against a copy of itself and would report a confident 0.000 for the wrong reason.

THE ORDER-DIVERGENCE CLAIM, DECOMPOSED
    Our 2026-07-28 note says "reordering the same query set changes 49-90% of answers". That figure
    conflates three separable properties, and only one of them is reinforcement's doing:
      (a) run-to-run determinism   -- same store, same query, repeated. Already fixed and covered by
                                      tests/test_recall_is_deterministic.py. Expected ~0.
      (b) insert-order sensitivity -- same records, different INSERT order. STRUCTURALLY EXCLUDED ON
                                      PURPOSE: core.py ~5584 sorts `(-score, -insertion_position)`, a
                                      deliberate newest-first tie-break that `tie_recent` is built on.
                                      Whatever this arm reports is BY DESIGN, not a defect.
      (c) read-purity              -- same records, same insert order, different QUERY order. This is
                                      what `reinforce` causes and the only fixable one.
      (d) write-path read-purity   -- `admit()` (core.py ~7002) and `remember_dedup()` (~2561) both call
                                      `recall(t, k=1)` with reinforcement ON, so a write-admission check
                                      that ADMITS NOTHING still mutates ranking state.
    The probe reports all four and names which one carries the figure. If every arm comes in at <= 5%,
    the claim does not reproduce and the probe says so in as many words.

CORPORA
    synthetic -- built in-process, zero dependencies, no network, runs anywhere. Conversational
                 utterances; a question supplies the speaker and the topic and NOTHING else, so there is
                 no difficulty knob to tune. Some (speaker, topic) cells hold one record and are uniquely
                 answerable, some hold several and are genuinely ambiguous -- the regime where a
                 popularity prior could help if it helps anywhere.
    locomo    -- the real external corpus (LOCOMO conversations, human questions, labelled gold evidence
                 turns), used when the data file is discoverable (--locomo PATH, $INSPEXIMUS_LOCOMO_JSON,
                 or a relative candidate). Lexical mode, so still zero-dependency and LLM-free.

RUN:  python probes/reinforce_accuracy_ablation.py
      python probes/reinforce_accuracy_ablation.py --locomo /path/to/locomo10.json --seeds 8
"""

import argparse
import bisect
import json
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from inspeximus import Inspeximus  # noqa: E402

RESULT_PATH = os.path.join(HERE, "reinforce_accuracy_ablation.result.json")

K = 5                 # top-k for hit@k
ZIPF_A = 1.3          # the exponent used in the 2026-07-28 order-divergence measurement
ORDERS = 8            # permutations compared in every divergence arm (matches that measurement)
RETRACT_BELOW = 0.05  # if every divergence arm lands at or under this, the 49-90% claim does not reproduce

# Guard thresholds. A control arm must clear ALL of these to be a usable baseline.
MIN_RATIO_OVER_CHANCE = 10.0   # accuracy must be >= 10x the chance level
MIN_ABSOLUTE = 0.05            # ...and not a rounding artefact of a tiny chance level
MIN_Z = 5.0                    # ...and >= 5 binomial sigma above chance
CEILING = 0.99                 # a saturated control leaves no headroom to detect an improvement


class BaselineGuardError(AssertionError):
    """The measurement is not interpretable. Raised INSTEAD of reporting a delta."""


# --------------------------------------------------------------------------------------- guards


def assert_baseline_is_measurable(label, hits, n, chance, verbose=True):
    """ABORT unless the control arm is decisively above chance; FLAG it if it is at the ceiling.

    The floor is fatal: a delta between two arms that are both indistinguishable from guessing measures
    nothing, and reporting one is exactly the mistake this file exists to prevent. The ceiling is not
    fatal but is not interpretable either -- a control at 1.0000 has no headroom for an improvement to
    show up in, so the metric is marked `interpretable: false` and excluded from the verdicts rather
    than silently reported as "no effect".

    Returns the diagnostic it checked, so the result file records exactly what was asserted. `chance`
    is ALWAYS printed -- the previous attempt's error was invisible precisely because the chance level
    was never put next to the accuracy.
    """
    if n <= 0:
        raise BaselineGuardError("%s: no events scored (n=0); nothing was measured" % label)
    if not (0.0 < chance < 1.0):
        raise BaselineGuardError("%s: chance level %r is not a probability" % (label, chance))
    acc = hits / float(n)
    sigma = (chance * (1.0 - chance) / n) ** 0.5
    z = (acc - chance) / sigma if sigma > 0 else 0.0
    ratio = acc / chance
    diag = {"label": label, "accuracy": round(acc, 4), "chance": round(chance, 6),
            "ratio_over_chance": round(ratio, 2), "z_vs_chance": round(z, 2), "n": n,
            "interpretable": True,
            "thresholds": {"min_ratio": MIN_RATIO_OVER_CHANCE, "min_absolute": MIN_ABSOLUTE,
                           "min_z": MIN_Z, "ceiling": CEILING}}
    floor_reasons = []
    if ratio < MIN_RATIO_OVER_CHANCE:
        floor_reasons.append("accuracy %.4f is only %.1fx chance %.6f (need >= %.0fx)"
                             % (acc, ratio, chance, MIN_RATIO_OVER_CHANCE))
    if acc < MIN_ABSOLUTE:
        floor_reasons.append("accuracy %.4f is below the absolute floor %.2f" % (acc, MIN_ABSOLUTE))
    if z < MIN_Z:
        floor_reasons.append("accuracy %.4f is only %.1f binomial sigma above chance %.6f (need >= %.1f)"
                             % (acc, z, chance, MIN_Z))
    if acc > CEILING:
        diag["interpretable"] = False
        diag["not_interpretable"] = ("control accuracy %.4f is at the ceiling (> %.2f): no headroom for "
                                     "an improvement to show up in, so this metric is excluded from the "
                                     "verdicts" % (acc, CEILING))
    if verbose:
        print("    GUARD %-36s control=%.4f chance=%.6f ratio=%.1fx z=%.1f n=%d%s"
              % (label, acc, chance, ratio, z, n, "" if diag["interpretable"] else "  [AT CEILING -> EXCLUDED]"))
    if floor_reasons:
        diag["failed"] = floor_reasons
        diag["interpretable"] = False
        raise BaselineGuardError(
            "BASELINE NOT MEASURABLE for %s -- refusing to report a delta.\n"
            "  control accuracy = %.4f   chance = %.6f   n = %d\n"
            "  %s\n"
            "  A delta between two arms that are both indistinguishable from guessing measures nothing."
            % (label, acc, chance, n, "\n  ".join(floor_reasons)))
    return diag


def assert_reinforcement_is_active(label, value_on, value_off, verbose=True):
    """Abort unless the reinforce=True arm really accrued more value than the control.

    Without this the probe could compare an arm against an identical copy of itself and report a
    confident 0.000 delta -- a green result produced by the mechanism never firing.
    """
    if verbose:
        print("    GUARD %-36s max value on=%.2f off=%.2f" % (label + " [mechanism]", value_on, value_off))
    if not value_on > value_off:
        raise BaselineGuardError(
            "REINFORCEMENT DID NOT FIRE for %s -- refusing to report a delta.\n"
            "  max value with reinforce=True  = %.4f\n"
            "  max value with reinforce=False = %.4f\n"
            "  The two arms are the same experiment; any delta would be noise, not an ablation."
            % (label, value_on, value_off))
    return {"max_value_reinforce_true": round(value_on, 4),
            "max_value_reinforce_false": round(value_off, 4)}


# ------------------------------------------------------------------------------------- corpora

_NAMES = ["Caroline", "Melanie", "Joanna", "Nate", "Angela", "Jon", "Calvin", "Diego",
          "Priya", "Ruth", "Omar", "Lena"]
_TOPICS = [
    ("pottery class", "the kiln kept cracking my glaze"),
    ("rescue greyhound", "she still will not climb the stairs"),
    ("night shift", "the corridor lights hum all evening"),
    ("half marathon", "my knee gave out near the bridge"),
    ("family piano", "the buyer haggled for a full hour"),
    ("garden fence", "the posts had rotted through at the base"),
    ("law school", "the evening seminars run until ten"),
    ("sea kayaking", "the swell was worse than the forecast"),
    ("bakery loft", "the ovens warm the floorboards all night"),
    ("animal shelter", "the intake room is always full"),
    ("accounting job", "the quarterly close finally broke me"),
    ("welding course", "the arc burned straight through my sleeve"),
    ("second hand cello", "the bridge was warped when it arrived"),
    ("ridge trail", "the cairns vanish above the treeline"),
    ("coffee cart", "the grinder jams in humid weather"),
    ("foster kittens", "they sleep in the laundry basket"),
]
_PLACES = ["Bristol", "Tallinn", "Oaxaca", "Perth", "Krakow", "Bergen", "Nairobi", "Hobart",
           "Cork", "Sapporo"]
_MONTHS = ["January", "March", "April", "June", "July", "September", "October", "November"]
_ASIDES = ["Honestly it has been a strange year.", "I keep meaning to tell you about it.",
           "Anyway that is roughly where things stand.", "It is not what I expected at all.",
           "You would have laughed if you had seen it.", "I still think about it most mornings."]
# Several distinct phrasings per record. Without them the held-out split would be impossible: a train
# question and its test question must differ in wording but share a gold record, so accrued value has
# to TRANSFER across queries to help.
_QUESTION_FORMS = [
    "What did {name} say about the {topic}?",
    "Tell me about {name} and the {topic}.",
    "{name} mentioned the {topic} once - what was that?",
    "Was there something with {name} regarding the {topic}?",
]


def build_synthetic_corpus(seed, n_records=300):
    """Return (records, questions) where questions is a list of (text, gold_index).

    A record is a conversational utterance carrying a speaker, topic, place, month, year, a concrete
    detail and an aside. A question supplies the speaker and the topic and nothing else; the
    distinguishing tokens are always all withheld, so there is no difficulty parameter to tune.
    """
    rng = random.Random(seed)
    records, questions = [], []
    for i in range(n_records):
        name = rng.choice(_NAMES)
        topic, detail = rng.choice(_TOPICS)
        place, month = rng.choice(_PLACES), rng.choice(_MONTHS)
        year, aside = rng.choice([2021, 2022, 2023]), rng.choice(_ASIDES)
        records.append("%s: About the %s in %s last %s %d - %s. %s"
                       % (name, topic, place, month, year, detail, aside))
        for form in _QUESTION_FORMS:
            questions.append((form.format(name=name, topic=topic), i))
    return records, questions


def build_floor_corpus(seed, n_records=300):
    """A corpus on which retrieval CANNOT work -- the fixture the guard has to reject.

    Every record carries the same token multiset plus a unique tag token that no question ever
    mentions, so every record scores identically for every question and the deterministic tie-break
    hands the same record to every query. Accuracy therefore sits at the chance level -- the shape of
    the corpus the previous attempt drew a conclusion from.
    """
    rng = random.Random(seed)
    shared = "the memory record entry note item line about a general topic here"
    records = ["%s tag%05d" % (shared, i) for i in range(n_records)]
    order = list(range(n_records))
    rng.shuffle(order)
    return records, [(shared, i) for i in order]


def _locomo_candidates(explicit):
    cands = [explicit, os.environ.get("INSPEXIMUS_LOCOMO_JSON"),
             os.path.join(ROOT, "agora_output", "lab", "data", "locomo10.json"),
             os.path.join(ROOT, "..", "agora", "agora_output", "lab", "data", "locomo10.json"),
             os.path.join(ROOT, "..", "agora_output", "lab", "data", "locomo10.json")]
    for c in cands:
        if c and os.path.isfile(c):
            return os.path.abspath(c)
    return None


def load_locomo(path, max_convs=10):
    """Return [(label, records, questions)] per LOCOMO conversation.

    A record is one conversation turn ("<speaker>: [<date>] <text>"); a question is a human-written
    LOCOMO question; the gold is the list of record indices for the LOCOMO-labelled evidence turns.
    Category 5 (adversarial / unanswerable) is excluded, as in our other LOCOMO probes.
    """
    data = json.load(open(path, encoding="utf-8"))[:max_convs]
    out = []
    for ci, sample in enumerate(data):
        conv = sample["conversation"]
        turn_ids, records = [], []
        for skey in sorted([k for k in conv if k.startswith("session_") and not k.endswith("date_time")],
                           key=lambda s: int(s.split("_")[1])):
            date = conv.get(skey + "_date_time", "")
            for t in conv[skey]:
                body = t.get("text", "") or t.get("clean_text", "")
                if not body.strip():
                    continue
                turn_ids.append(str(t.get("dia_id") or t.get("id") or ""))
                records.append("%s: [%s] %s" % (t.get("speaker", ""), date, body) if date
                               else "%s: %s" % (t.get("speaker", ""), body))
        pos = {tid: i for i, tid in enumerate(turn_ids)}
        questions = []
        for q in sample.get("qa", []):
            ev = q.get("evidence") or []
            if not ev or q.get("category") == 5:
                continue
            gold = sorted({pos[str(e)] for e in ev if str(e) in pos})
            if gold:
                questions.append((q["question"], gold))
        if len(records) >= 50 and len(questions) >= 20:
            out.append(("locomo_conv%d" % ci, records, questions))
    return out


# ------------------------------------------------------------------------------------ workloads


def gold_key(q):
    g = q[1]
    return tuple(g) if isinstance(g, (list, tuple)) else (g,)


def zipf_weights(keys, rng, a=ZIPF_A):
    """Assign Zipf(a) weights to gold groups on a SHUFFLED rank order (so 'hot' is not corpus order).

    The keys are SORTED before the shuffle: callers pass a set, and seeding the shuffle from a set's
    iteration order would make the weight assignment depend on something other than the seed.
    """
    ks = sorted(keys)
    rng.shuffle(ks)
    return {k: 1.0 / ((i + 1) ** a) for i, k in enumerate(ks)}


def weighted_stream(questions, weights, rng, n_events):
    """Draw n_events questions, weighting each GOLD GROUP by `weights` and picking uniformly among the
    distinct phrasings inside the group. Weighting by gold (not by question) is what makes the
    popularity signal real: a record drawn often is often the right answer, reached through several
    different wordings, so accrued value has to transfer across queries to help."""
    groups = {}
    for q in questions:
        groups.setdefault(gold_key(q), []).append(q)
    keys = sorted(groups)
    cum, tot = [], 0.0
    for k in keys:
        tot += weights.get(k, 0.0)
        cum.append(tot)
    if tot <= 0:
        return [questions[rng.randrange(len(questions))] for _ in range(n_events)]
    out = []
    for _ in range(n_events):
        g = groups[keys[bisect.bisect_left(cum, rng.random() * tot)]]
        out.append(g[rng.randrange(len(g))])
    return out


# ------------------------------------------------------------------------------------- the arms


def build_store(records, order=None):
    """Fresh in-memory store. `order` permutes the INSERT order; the returned map is id -> the index in
    the ORIGINAL `records` list, so gold identity survives any permutation."""
    store = Inspeximus(path=None)
    idx = {}
    for pos in (order if order is not None else range(len(records))):
        idx[store.remember(records[pos])] = pos
    return store, idx


def _score(hits, idx, gold_set, k=K):
    got = [idx.get(h.get("id")) for h in hits]
    top = got[0] if got else None
    return (1 if top in gold_set else 0,
            1 if (gold_set & {g for g in got[:k] if g is not None}) else 0,
            top)


def run_stream(store, idx, stream, reinforce, k=K, score=True):
    """Run an event stream against an EXISTING store. Returns (hit1_flags, hitk_flags)."""
    h1, hk = [], []
    for text, gold in stream:
        gs = set(gold) if isinstance(gold, (list, tuple)) else {gold}
        hits = store.recall(text, k=k, reinforce=reinforce) or []
        if score:
            a, b, _ = _score(hits, idx, gs, k)
            h1.append(a)
            hk.append(b)
    return h1, hk


def max_value(store):
    return max((float(it.get("value") or 0.0) for it in store.items), default=0.0)


# ------------------------------------------------------------------------------------ statistics


def cluster_bootstrap_ci(per_seed, iters=10000, seed=12345, alpha=0.05):
    """95% CI for the paired delta, resampling SEEDS (the independent unit) with replacement.

    per_seed: list of (weight_on, weight_off, denom) per seed. Pure Python, zero dependencies.
    """
    per_seed = [p for p in per_seed if p[2] > 0]
    if not per_seed:
        return {"delta": None, "ci95": None, "n_seeds": 0}
    rng = random.Random(seed)
    n_seeds = len(per_seed)

    def pooled(sample):
        d = sum(s[2] for s in sample)
        return (sum(s[0] for s in sample) - sum(s[1] for s in sample)) / d if d else 0.0

    point = pooled(per_seed)
    if n_seeds == 1:
        return {"delta": round(point, 5), "ci95": None, "n_seeds": 1,
                "note": "one seed: no cluster resampling possible"}
    draws = sorted(pooled([per_seed[rng.randrange(n_seeds)] for _ in range(n_seeds)])
                   for _ in range(iters))
    lo, hi = draws[int(alpha / 2 * iters)], draws[min(iters - 1, int((1 - alpha / 2) * iters))]
    per = [(s[0] - s[1]) / s[2] for s in per_seed]
    return {"delta": round(point, 5), "ci95": [round(lo, 5), round(hi, 5)], "n_seeds": n_seeds,
            "seeds_favouring_reinforce": sum(1 for d in per if d > 0),
            "seeds_favouring_control": sum(1 for d in per if d < 0),
            "seeds_tied": sum(1 for d in per if d == 0),
            "significant_at_95": bool(lo > 0 or hi < 0)}


# ---------------------------------------------------------------------- PRIMARY: the warm store


ORACLE_BUMP = 0.25    # the maximum per-hit bump recall applies (0.25 * relevance, relevance <= 1)


def _oracle_warm(store, idx, stream, bump=ORACLE_BUMP):
    """Warm a store the way reinforcement WISHES it could: bump value only on the record that was
    actually RIGHT, by the same magnitude recall uses at full relevance.

    This is not a proposed change and nothing in the library does it. It exists to separate two very
    different explanations of a negative result: 'a popularity prior in the value multiplier is
    harmful' versus 'the prior is fine, but recall estimates it from what it RETURNED rather than from
    what was CORRECT, so it amplifies its own prior beliefs'. Only the second leaves a product move on
    the table (route the bump through the outcome signal, i.e. credit(), instead of the read path).

    `idx` maps record id -> index in the original `records` list; gold is expressed in THOSE indices,
    so the inverse map is required. The first version of this function looked records up by gold index
    in an id-keyed dict, matched nothing, bumped nothing, and reported a flawless +0.0000 with a CI of
    [0, 0] -- which is why `assert_reinforcement_is_active` is now also run on this arm.
    """
    pos2rec = {}
    for rec in store._items:
        p = idx.get(rec.get("id"))
        if p is not None:
            pos2rec[p] = rec
    for _text, gold in stream:
        for g in (gold if isinstance(gold, (list, tuple)) else [gold]):
            rec = pos2rec.get(g)
            if rec is not None:
                rec["value"] = float(rec.get("value") or 0.0) + bump


def warm_heldout(records, questions, seeds, train_events, chance1, chancek, label, verbose=True):
    """PRIMARY ARM. Train on a query stream, evaluate on HELD-OUT questions with a pure read.

    Per seed: split the questions 50/50 into a TRAIN pool and a TEST set (a test question is never
    asked during training). Warm the store with `train_events` draws from the TRAIN pool, Zipf-weighted
    over gold records. Then ask every TEST question ONCE with reinforce=False on ALL arms, so the arms
    differ only in the state training left behind. Scored under two weightings:
      uniform -- every test question counts once (does warming help the corpus as a whole?)
      zipf    -- each test question weighted by its gold record's training weight (does warming help
                 on the workload the store was actually warmed on?)
    Three arms: reinforce=True, reinforce=False (control), and an ORACLE that bumps only the gold
    record -- the upper bound on what a perfectly-estimated popularity prior could buy here.
    """
    acc = {w: {m: [] for m in ("hit1", "hitk")} for w in ("uniform", "zipf")}
    oracle = {w: {m: [] for m in ("hit1", "hitk")} for w in ("uniform", "zipf")}
    # control accumulators: [sum(w*hit1), sum(w*hitk), sum(w), sum(w^2)] -- the last two give Kish's
    # effective sample size, because under Zipf weights the row count wildly overstates the evidence.
    ctrl = {w: [0.0, 0.0, 0.0, 0.0] for w in ("uniform", "zipf")}
    v_on = v_off = v_oracle = 0.0
    n_test = n_train_pool = 0
    cover_hit = cover_n = 0      # can a popularity prior transfer at all? (see transfer_coverage below)
    t0 = time.time()
    for s in seeds:
        rng = random.Random(9000 + s)
        qs = list(questions)
        rng.shuffle(qs)
        cut = len(qs) // 2
        train_pool, test_set = qs[:cut], qs[cut:]
        n_train_pool, n_test = len(train_pool), len(test_set)
        weights = zipf_weights({gold_key(q) for q in questions}, rng)
        train = weighted_stream(train_pool, weights, rng, train_events)
        # A popularity prior can only transfer to a HELD-OUT question when that question's gold record
        # was also the answer to some TRAINING question. Where a gold record has exactly one question in
        # the whole corpus, the 50/50 split puts it wholly on one side, no prior about it can exist at
        # evaluation time, and the only thing training can do to that question is add noise. This
        # fraction is the ceiling on how much ANY popularity prior -- reinforcement's or the oracle's --
        # could possibly buy, and it is what separates the two corpora.
        trained_golds = {gold_key(q) for q in train}
        cover_hit += sum(1 for q in test_set if gold_key(q) in trained_golds)
        cover_n += len(test_set)

        per_arm = {}
        for arm in ("on", "off", "oracle"):
            store, idx = build_store(records)
            if arm == "oracle":
                _oracle_warm(store, idx, train)         # gold-only bump; no recall, no read side effect
            else:
                run_stream(store, idx, train, arm == "on", score=False)
            if arm == "on":
                v_on = max(v_on, max_value(store))
            elif arm == "off":
                v_off = max(v_off, max_value(store))
            else:
                v_oracle = max(v_oracle, max_value(store))
            flags1, flagsk = [], []
            for text, gold in test_set:                 # pure read on ALL arms
                gs = set(gold) if isinstance(gold, (list, tuple)) else {gold}
                a, b, _ = _score(store.recall(text, k=K, reinforce=False) or [], idx, gs)
                flags1.append(a)
                flagsk.append(b)
            per_arm[arm] = (flags1, flagsk)

        wq = [weights.get(gold_key(q), 0.0) for q in test_set]
        for wname, ws in (("uniform", [1.0] * len(test_set)), ("zipf", wq)):
            denom = sum(ws)
            for metric, i in (("hit1", 0), ("hitk", 1)):
                off = sum(w * f for w, f in zip(ws, per_arm["off"][i]))
                acc[wname][metric].append((sum(w * f for w, f in zip(ws, per_arm["on"][i])), off, denom))
                oracle[wname][metric].append(
                    (sum(w * f for w, f in zip(ws, per_arm["oracle"][i])), off, denom))
            ctrl[wname][0] += sum(w * f for w, f in zip(ws, per_arm["off"][0]))
            ctrl[wname][1] += sum(w * f for w, f in zip(ws, per_arm["off"][1]))
            ctrl[wname][2] += denom
            ctrl[wname][3] += sum(w * w for w in ws)

    out = {"design": "train on a Zipf query stream (reinforce=True vs reinforce=False vs a gold-only "
                     "ORACLE bump), then evaluate HELD-OUT questions with reinforce=False on all three "
                     "arms, so the arms differ only in the state training left behind",
           "train_events_per_seed": train_events, "train_pool": n_train_pool, "test_questions": n_test,
           "transfer_coverage": {
               "value": round(cover_hit / cover_n, 4) if cover_n else None,
               "meaning": "fraction of HELD-OUT questions whose gold record was also the answer to some "
                          "TRAINING question. This is the ceiling on what any popularity prior could buy: "
                          "for the remaining questions no prior about the right record can exist at "
                          "evaluation time, so training can only add noise."},
           "seconds": round(time.time() - t0, 1), "weightings": {}}
    # ---- GUARDS. Nothing below is reported if either fires. ----
    for wname in ("uniform", "zipf"):
        # The guard runs on the WEIGHTED control accuracy. Under Zipf weights the row count wildly
        # overstates the evidence, so the guard's n is Kish's effective sample size (sum w)^2 / sum w^2,
        # which for uniform weights reduces exactly to the row count.
        s_h1, s_hk, s_w, s_w2 = ctrl[wname]
        eff_n = max(1, int(round((s_w * s_w) / s_w2))) if s_w2 > 0 else 0
        g1 = assert_baseline_is_measurable("%s warm/%s hit@1" % (label, wname),
                                           (s_h1 / s_w) * eff_n if s_w else 0, eff_n, chance1, verbose)
        gk = assert_baseline_is_measurable("%s warm/%s hit@%d" % (label, wname, K),
                                           (s_hk / s_w) * eff_n if s_w else 0, eff_n, chancek, verbose)
        rec = {"guard_hit1": g1, "guard_hitk": gk, "effective_n": eff_n}
        for metric in ("hit1", "hitk"):
            per = acc[wname][metric]
            tot = sum(p[2] for p in per)
            rec[metric] = {"reinforce_true": round(sum(p[0] for p in per) / tot, 4) if tot else None,
                           "reinforce_false": round(sum(p[1] for p in per) / tot, 4) if tot else None,
                           **cluster_bootstrap_ci(per)}
            po = oracle[wname][metric]
            rec["oracle_" + metric] = {
                "gold_only_bump": round(sum(p[0] for p in po) / tot, 4) if tot else None,
                "reinforce_false": round(sum(p[1] for p in po) / tot, 4) if tot else None,
                **cluster_bootstrap_ci(po),
                "note": "upper bound: value bumped ONLY on the record that was right. Not a proposal and "
                        "not in the library -- it separates 'the prior is harmful' from 'the prior is "
                        "estimated from what was returned rather than what was correct'."}
        out["weightings"][wname] = rec
        if verbose:
            print("    warm/%-7s hit@1 on=%.4f off=%.4f delta %+0.4f CI95 %s | hit@%d on=%.4f off=%.4f "
                  "delta %+0.4f CI95 %s"
                  % (wname, rec["hit1"]["reinforce_true"], rec["hit1"]["reinforce_false"],
                     rec["hit1"]["delta"], rec["hit1"]["ci95"], K,
                     rec["hitk"]["reinforce_true"], rec["hitk"]["reinforce_false"],
                     rec["hitk"]["delta"], rec["hitk"]["ci95"]))
            print("    warm/%-7s ORACLE (gold-only bump) hit@1=%.4f delta %+0.4f CI95 %s | hit@%d=%.4f "
                  "delta %+0.4f CI95 %s"
                  % (wname, rec["oracle_hit1"]["gold_only_bump"], rec["oracle_hit1"]["delta"],
                     rec["oracle_hit1"]["ci95"], K, rec["oracle_hitk"]["gold_only_bump"],
                     rec["oracle_hitk"]["delta"], rec["oracle_hitk"]["ci95"]))
    out["guard_mechanism"] = assert_reinforcement_is_active("%s warm" % label, v_on, v_off, verbose)
    out["guard_mechanism_oracle"] = assert_reinforcement_is_active(
        "%s warm oracle" % label, v_oracle, v_off, verbose)
    return out


# ------------------------------------------------------------- CONTROL: the published cold stream


def cold_stream_control(records, questions, seeds, chance1, chancek, label, verbose=True):
    """CONTROL, NOT A FINDING. Each question asked once against one store, scored inline.

    This is the case README/CHANGELOG already publish as a confound that depresses recall. It is run
    to show the harness reproduces the published SIGN, and is labelled a known negative everywhere it
    appears. A null here would be a re-derivation, not a result.
    """
    pairs = {"hit1": [], "hitk": []}
    ctrl1 = ctrlk = n_tot = 0
    v_on = v_off = 0.0
    t0 = time.time()
    for s in seeds:
        rng = random.Random(1000 + s)
        stream = list(questions)
        rng.shuffle(stream)
        res = {}
        for arm, reinforce in (("on", True), ("off", False)):
            store, idx = build_store(records)
            res[arm] = run_stream(store, idx, stream, reinforce)
            if arm == "on":
                v_on = max(v_on, max_value(store))
            else:
                v_off = max(v_off, max_value(store))
        pairs["hit1"].append((sum(res["on"][0]), sum(res["off"][0]), len(stream)))
        pairs["hitk"].append((sum(res["on"][1]), sum(res["off"][1]), len(stream)))
        ctrl1 += sum(res["off"][0])
        ctrlk += sum(res["off"][1])
        n_tot += len(stream)
    g1 = assert_baseline_is_measurable("%s cold hit@1" % label, ctrl1, n_tot, chance1, verbose)
    gk = assert_baseline_is_measurable("%s cold hit@%d" % (label, K), ctrlk, n_tot, chancek, verbose)
    mech = assert_reinforcement_is_active("%s cold" % label, v_on, v_off, verbose)
    out = {"status": "KNOWN NEGATIVE -- already published (README 1.15.0 correction / CHANGELOG): "
                     "reinforcement depresses recall on a cold query stream. Carried as a control that "
                     "the harness reproduces the published sign, NOT as a new finding.",
           "events_per_seed": n_tot // max(1, len(seeds)), "seconds": round(time.time() - t0, 1),
           "guard_hit1": g1, "guard_hitk": gk, "guard_mechanism": mech}
    for metric in ("hit1", "hitk"):
        per = pairs[metric]
        tot = sum(p[2] for p in per)
        out[metric] = {"reinforce_true": round(sum(p[0] for p in per) / tot, 4),
                       "reinforce_false": round(sum(p[1] for p in per) / tot, 4),
                       **cluster_bootstrap_ci(per)}
    if verbose:
        print("    cold      hit@1 on=%.4f off=%.4f delta %+0.4f CI95 %s | hit@%d on=%.4f off=%.4f "
              "delta %+0.4f CI95 %s"
              % (out["hit1"]["reinforce_true"], out["hit1"]["reinforce_false"], out["hit1"]["delta"],
                 out["hit1"]["ci95"], K, out["hitk"]["reinforce_true"], out["hitk"]["reinforce_false"],
                 out["hitk"]["delta"], out["hitk"]["ci95"]))
    return out


# --------------------------------------------------- DECOMPOSITION of the order-divergence claim


def _divergence(answer_maps):
    ref = answer_maps[0]
    changed = total = 0
    for other in answer_maps[1:]:
        for key, val in ref.items():
            if key in other:
                total += 1
                changed += 1 if other[key] != val else 0
    return {"n_compared": total, "changed": changed,
            "divergence": round(changed / total, 4) if total else None}


def arm_run_to_run(records, questions, repeats=ORDERS, sample=60, seed=7):
    """(a) Same records, same insert order, same query order -- repeated. Expected ~0: fixed and
    covered by tests/test_recall_is_deterministic.py. reinforce=False so the read is pure."""
    rng = random.Random(seed)
    qs = questions if len(questions) <= sample else rng.sample(questions, sample)
    maps = []
    for _ in range(repeats):
        store, idx = build_store(records)
        m = {}
        for text, gold in qs:
            gs = set(gold) if isinstance(gold, (list, tuple)) else {gold}
            m[(text, gold_key((text, gold)))] = _score(store.recall(text, k=K, reinforce=False) or [],
                                                       idx, gs)[2]
        maps.append(m)
    r = _divergence(maps)
    r["property"] = "run-to-run determinism (same everything, repeated)"
    r["expected"] = "~0 -- already fixed; see tests/test_recall_is_deterministic.py"
    return r


def arm_insert_permutation(records, questions, perms=ORDERS, sample=None, seed=7):
    """(b) Same records, DIFFERENT INSERT ORDER, same query order, reinforce=False.

    BY DESIGN, NOT A DEFECT: core.py ~5584 sorts (-score, -insertion_position), a deliberate
    newest-first tie-break that `tie_recent` is built on. Reported so the 49-90% figure can be
    apportioned, and explicitly excluded from the read-purity total.
    """
    rng = random.Random(seed)
    qs = questions if (sample is None or len(questions) <= sample) else rng.sample(questions, sample)
    maps = []
    for p in range(perms):
        order = list(range(len(records)))
        if p:
            rng.shuffle(order)
        store, idx = build_store(records, order=order)
        m = {}
        for text, gold in qs:
            gs = set(gold) if isinstance(gold, (list, tuple)) else {gold}
            m[(text, gold_key((text, gold)))] = _score(store.recall(text, k=K, reinforce=False) or [],
                                                       idx, gs)[2]
        maps.append(m)
    r = _divergence(maps)
    r["property"] = "insert-order sensitivity (same records, different write order)"
    r["by_design"] = ("core.py ~5584 tie-breaks on (-score, -insertion_position): newest-first. This is "
                      "the documented policy `tie_recent` builds on, not a defect, and it is NOT part of "
                      "the read-purity total.")
    return r


def arm_query_order(records, questions, reinforce, orders=ORDERS, sample=None, seed=7):
    """(c) Same records, same insert order, DIFFERENT QUERY ORDER. This is read-purity: the only one
    reinforcement causes, and the only fixable one."""
    rng = random.Random(seed)
    qs = questions if (sample is None or len(questions) <= sample) else rng.sample(questions, sample)
    maps = []
    for _ in range(orders):
        stream = list(qs)
        rng.shuffle(stream)
        store, idx = build_store(records)
        m = {}
        for text, gold in stream:
            gs = set(gold) if isinstance(gold, (list, tuple)) else {gold}
            m[(text, gold_key((text, gold)))] = _score(store.recall(text, k=K, reinforce=reinforce) or [],
                                                       idx, gs)[2]
        maps.append(m)
    r = _divergence(maps)
    r["property"] = "read-purity (same store, different QUERY order), reinforce=%s" % reinforce
    return r


def arm_admit_write_path(records, questions, sample=80, n_admits=400, seed=7, repeats=5):
    """(d) `admit()` and `remember_dedup()` call recall(t, k=1) with reinforcement ON.

    The candidates are EXACT COPIES of stored records, so a correct dedup rejects every one of them and
    the store's contents never change. Any answer that moves afterwards moved because a write-admission
    check that stored nothing reinforced what it checked against.

    MEASURED 2026-08-05, AND IT RETRACTS THIS ARM'S HEADLINE: the divergence this arm reported was the
    CLOCK, not the write path. A time-matched control store -- built beside the treated one, read
    interleaved with it, never handed to admit() at all -- moves the same answers. On locomo_conv2 the
    raw figure is 0.2500 and the control is 0.2500, so the excess attributable to admit() is 0.0000; on
    conv1 and conv3 the excess is likewise 0.0000. Nothing was admitted, no stored field changed, and
    the order of `store.items` was identical before and after. `recall()` recomputes decay from
    wall-clock age, so about a second of elapsed time is enough to reorder records that are tied to
    within the API's own 3-decimal score resolution.
    Read `divergence_excess_over_time_control`. The raw `divergence` is kept only so the correction
    stays visible; on its own it measures how long the machine took.
    THE EARLIER CLAIM, now unsupported: some copies are NOT rejected. `admit()` looks for the
    duplicate with `recall(t, k=1)`, and on a reinforced ranking a high-value hub can outrank the
    byte-identical record, so the similarity test is run against the wrong neighbour and an exact
    duplicate is appended. That is a write-correctness defect caused by read-path reinforcement, and it
    is reported here as `n_admitted` / `exact_duplicate_texts_after`.

    Because those admissions change the store's CONTENTS, the raw divergence below is confounded by
    them. `divergence_excluding_new_records` is the clean read-purity figure: it counts only the
    answers that moved from one PRE-EXISTING record to another (a newly appended record is absent from
    `idx`, so any move onto one is excluded).
    """
    rng = random.Random(seed)
    qs = questions if len(questions) <= sample else rng.sample(questions, sample)

    def _trial():
        """ONE paired trial: a treated store and a time-matched twin, read interleaved.

        The twin is built HERE, beside the treated store, not after the admits. Both must share a
        timeline -- recall() recomputes decay from wall-clock age, so a control built later is younger
        and crosses its integer-second boundaries at different moments. Built after the fact it
        reported 0.0000 and manufactured an "attributable to admit()" that is not there.
        """
        store, idx = build_store(records)
        ctl_store, ctl_idx = build_store(records)
        before, ctl_before = {}, {}
        for text, gold in qs:
            gs = set(gold) if isinstance(gold, (list, tuple)) else {gold}
            before[text] = _score(store.recall(text, k=K, reinforce=False) or [], idx, gs)[2]
            ctl_before[text] = _score(ctl_store.recall(text, k=K, reinforce=False) or [], ctl_idx, gs)[2]
        n_before, v_before = len(store.items), max_value(store)
        admitted = 0
        t0 = time.time()
        for _ in range(n_admits):
            if store.admit(records[rng.randrange(len(records))]).get("admitted"):
                admitted += 1
        elapsed = time.time() - t0
        # INTERLEAVED, one query at a time across both stores. Run as two separate passes, the control's
        # reads land later than the treated store's, and since the quantity under test IS a clock effect
        # that skew is measured as if it were the treatment.
        ctl_changed = changed = changed_clean = clean_n = 0
        for text, gold in qs:
            gs = set(gold) if isinstance(gold, (list, tuple)) else {gold}
            now = _score(store.recall(text, k=K, reinforce=False) or [], idx, gs)[2]
            ctl_now = _score(ctl_store.recall(text, k=K, reinforce=False) or [], ctl_idx, gs)[2]
            ctl_changed += (ctl_now != ctl_before[text])
            changed += (now != before[text])
            if now is not None:            # None == a record appended by admit(), not a pre-existing one
                clean_n += 1
                changed_clean += (now != before[text])
        texts = [it["text"] for it in store.items]
        return {"changed": changed, "ctl_changed": ctl_changed, "admitted": admitted,
                "n_before": n_before, "n_after": len(store.items), "elapsed": elapsed,
                "v_before": v_before, "v_after": max_value(store),
                "dupes": len(texts) - len(set(texts)),
                "changed_clean": changed_clean, "clean_n": clean_n}

    # REPEATED, because one trial cannot resolve this. The excess is a difference between two noisy
    # counts on the same grain, and on locomo_conv2 five consecutive trials of identical code gave
    # excesses 0.0250 / 0.0125 / 0.0000 / 0.0000 / 0.0000 -- so a single trial reported "attributable
    # to admit()" one time in five with nothing to attribute. A threshold cannot fix that; only
    # repetition can. The flag below therefore asks for a SIGN, not a size: every trial positive.
    trials = [_trial() for _ in range(max(1, int(repeats)))]
    n_q = len(qs)
    raws = [t["changed"] / n_q for t in trials] if n_q else []
    ctls = [t["ctl_changed"] / n_q for t in trials] if n_q else []
    excesses = [max(0.0, r - c) for r, c in zip(raws, ctls)]
    positive = sum(1 for r, c in zip(raws, ctls) if r - c > 1e-9)

    def _median(xs):
        if not xs:
            return None
        ys = sorted(xs)
        return ys[len(ys) // 2] if len(ys) % 2 else (ys[len(ys) // 2 - 1] + ys[len(ys) // 2]) / 2.0

    last = trials[-1]
    admitted = sum(t["admitted"] for t in trials)
    return {"property": "write-path read-purity: admit() calls recall(t, k=1) (core.py ~7002; "
                        "remember_dedup() the same at ~2561). Read "
                        "`divergence_excess_over_time_control`, NOT `divergence`: an untouched store "
                        "moves on elapsed time alone, and the raw figure is mostly that.",
            "n_trials": len(trials),
            "per_trial_divergence": [round(r, 4) for r in raws],
            "per_trial_time_control": [round(c, 4) for c in ctls],
            "per_trial_excess": [round(e, 4) for e in excesses],
            "trials_with_positive_excess": positive,
            "divergence": round(_median(raws), 4) if raws else None,
            "time_control_divergence": round(_median(ctls), 4) if ctls else None,
            "divergence_excess_over_time_control": round(_median(excesses), 4) if excesses else None,
            "attributable_resolution": round(1.0 / n_q, 4) if n_q else None,
            # Every trial positive, not a median above some threshold. With 5 trials that is p = 1/32
            # under a symmetric null, and it is the only reading that survived five repeats of code
            # whose true excess is zero.
            "attributable_to_admit": bool(trials and positive == len(trials)),
            "seconds_admitting": round(sum(t["elapsed"] for t in trials), 2),
            "time_control_changed": last["ctl_changed"],
            "n_admit_calls": n_admits * len(trials), "n_admitted": admitted,
            "all_rejected_as_duplicates": admitted == 0,
            "dedup_failure": ("admit() appended %d byte-identical duplicates: on a reinforced ranking a "
                              "high-value hub outranks the identical record, so recall(t, k=1) hands the "
                              "similarity test the wrong neighbour" % admitted) if admitted else None,
            "store_size_before": last["n_before"], "store_size_after": last["n_after"],
            "exact_duplicate_texts_after": last["dupes"],
            "max_value_before": round(last["v_before"], 3), "max_value_after": round(last["v_after"], 3),
            "n_queries": n_q, "changed": last["changed"],
            "divergence_confounded_by_admissions": admitted > 0,
            "divergence_excluding_new_records": (round(last["changed_clean"] / last["clean_n"], 4)
                                                 if last["clean_n"] else None),
            "n_queries_excluding_new_records": last["clean_n"],
            "control": "a time-matched twin store, built beside the treated one and read interleaved "
                       "with it, is never handed to admit(). Whatever it moves is the clock; only the "
                       "excess over it is evidence about the write path, and only when every trial "
                       "agrees on the sign"}


def decompose_divergence(records, questions, verbose=True, sample=None):
    t0 = time.time()
    arms = {
        "a_run_to_run": arm_run_to_run(records, questions),
        "b_insert_order_BY_DESIGN": arm_insert_permutation(records, questions, sample=sample),
        "c_query_order_reinforce_true": arm_query_order(records, questions, True, sample=sample),
        "c_query_order_reinforce_false": arm_query_order(records, questions, False, sample=sample),
        "d_admit_write_path": arm_admit_write_path(records, questions),
    }
    fixable = arms["c_query_order_reinforce_true"]["divergence"] or 0.0

    def _attributable(v):
        """The divergence an arm may contribute to the headline: for the admit arm that is the EXCESS
        over its time-matched control, never the raw figure. Raw, arm (d) contributed 0.2500 on
        locomo_conv2 that was entirely elapsed time; it happened to sit below arm (b)'s 0.4464 and so
        never became the carrier, but nothing stopped it from doing so on a slower machine."""
        if not isinstance(v, dict):
            return 0.0
        if "divergence_excess_over_time_control" in v:
            return v.get("divergence_excess_over_time_control") or 0.0
        return v.get("divergence") or 0.0

    every = [_attributable(v) for v in arms.values()]
    arms["seconds"] = round(time.time() - t0, 1)
    arms["carrier"] = max((k for k in arms if k not in ("seconds", "carrier")),
                          key=lambda k: _attributable(arms[k]))
    arms["claim_reproduces"] = bool(max(every) > RETRACT_BELOW)
    arms["read_purity_divergence"] = round(fixable, 4)
    arms["note"] = ("the 2026-07-28 '49-90% of answers change on reorder' figure, apportioned. Arm (b) is "
                    "policy, not a defect; the read-purity number is arm (c) with reinforce=True.")
    if not arms["claim_reproduces"]:
        arms["retraction"] = ("every arm is at or below %.2f: the 49-90%% order-divergence claim does NOT "
                              "reproduce on this corpus and should be retracted for it." % RETRACT_BELOW)
    if verbose:
        for name in ("a_run_to_run", "b_insert_order_BY_DESIGN", "c_query_order_reinforce_true",
                     "c_query_order_reinforce_false", "d_admit_write_path"):
            _shown = _attributable(arms[name])
            _note = ("  (raw %.4f, time-control %.4f)"
                     % (arms[name].get("divergence") or 0.0, arms[name].get("time_control_divergence") or 0.0)
                     ) if "divergence_excess_over_time_control" in arms[name] else ""
            print("    divergence %-32s %.4f%s" % (name, _shown, _note))
        print("    carrier=%s  claim_reproduces=%s" % (arms["carrier"], arms["claim_reproduces"]))
    return arms


# ------------------------------------------------------------------------------------- the probe


def evaluate_corpus(label, records, questions, seeds, train_events, verbose=True, divergence_sample=None):
    """Everything the probe measures on one corpus. Raises BaselineGuardError instead of reporting a
    delta whenever the control is not a usable baseline or reinforcement never fired."""
    chance1 = 1.0 / len(records)
    chancek = min(1.0, K * chance1)
    if verbose:
        print("\n  corpus %s: %d records, %d questions, chance hit@1=%.6f hit@%d=%.6f"
              % (label, len(records), len(questions), chance1, K, chancek))
    return {"corpus": label, "n_records": len(records), "n_questions": len(questions),
            "chance_hit1": round(chance1, 6), "chance_hitk": round(chancek, 6), "k": K,
            "primary_warm_heldout": warm_heldout(records, questions, seeds, train_events,
                                                 chance1, chancek, label, verbose),
            "control_cold_stream": cold_stream_control(records, questions, seeds, chance1, chancek,
                                                       label, verbose),
            "divergence_decomposition": decompose_divergence(records, questions, verbose,
                                                             sample=divergence_sample)}


def verdict_for(entry):
    lines = []
    def line(prefix, holder, metric, tag, key=None):
        m, g = holder[key or metric], holder["guard_" + metric]
        if not g.get("interpretable"):
            lines.append("%s %s: EXCLUDED -- %s" % (prefix, tag, g.get("not_interpretable", "guard")))
            return
        if not m.get("ci95"):
            return
        lo, hi = m["ci95"]
        word = ("HELPS" if lo > 0 else "HURTS" if hi < 0 else "NO EFFECT detected")
        lines.append("%s %s: reinforcement %s (%+.4f, CI95 [%+.4f, %+.4f]; control %.4f, chance %.6f)"
                     % (prefix, tag, word, m["delta"], lo, hi, g["accuracy"], g["chance"]))

    for wname, rec in entry["primary_warm_heldout"]["weightings"].items():
        for metric in ("hit1", "hitk"):
            line("PRIMARY", rec, metric, "%s warm/%s/%s" % (entry["corpus"], wname, metric))
            line("ORACLE(upper bound, gold-only bump)", rec, metric,
                 "%s warm/%s/%s" % (entry["corpus"], wname, metric), key="oracle_" + metric)
    for metric in ("hit1", "hitk"):
        line("CONTROL(known negative)", entry["control_cold_stream"], metric,
             "%s cold/%s" % (entry["corpus"], metric))
    tc = entry["primary_warm_heldout"]["transfer_coverage"]["value"]
    lines.append("TRANSFER CEILING %s: %.1f%% of held-out questions have a gold record that training "
                 "could have carried a prior about; for the rest, warming can only add noise"
                 % (entry["corpus"], 100.0 * (tc or 0.0)))
    d = entry["divergence_decomposition"]
    _d = d["d_admit_write_path"]
    lines.append("DIVERGENCE %s: read-purity(c, reinforce=True)=%.4f  insert-order(b, BY DESIGN)=%.4f  "
                 "run-to-run(a)=%.4f  admit-write-path(d)=%.4f [excess over its time-matched control; "
                 "raw %.4f, control %.4f]  carrier=%s"
                 % (entry["corpus"], d["c_query_order_reinforce_true"]["divergence"] or 0.0,
                    d["b_insert_order_BY_DESIGN"]["divergence"] or 0.0,
                    d["a_run_to_run"]["divergence"] or 0.0,
                    _d.get("divergence_excess_over_time_control") or 0.0,
                    _d.get("divergence") or 0.0, _d.get("time_control_divergence") or 0.0, d["carrier"]))
    if "retraction" in d:
        lines.append("DIVERGENCE %s: %s" % (entry["corpus"], d["retraction"]))
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--records", type=int, default=300)
    ap.add_argument("--train-events", type=int, default=1200)
    ap.add_argument("--locomo", default=None, help="path to locomo10.json (optional)")
    ap.add_argument("--locomo-convs", type=int, default=4)
    ap.add_argument("--locomo-seeds", type=int, default=6)
    ap.add_argument("--no-locomo", action="store_true")
    ap.add_argument("--out", default=RESULT_PATH)
    a = ap.parse_args(argv)

    print("REINFORCEMENT ABLATION -- recall(reinforce=True) vs recall(reinforce=False)")
    print("MEASURE ONLY: this probe does not change the default and does not change ranking.")
    print("PRIMARY = warm store, held-out evaluation. Cold stream = published known negative, control.\n")
    t_all = time.time()
    entries = []

    print("[1] synthetic corpus (zero dependency, runs anywhere)")
    records, questions = build_synthetic_corpus(seed=1, n_records=a.records)
    entries.append(evaluate_corpus("synthetic", records, questions, list(range(a.seeds)),
                                   a.train_events))

    locomo_path = None if a.no_locomo else _locomo_candidates(a.locomo)
    if locomo_path:
        print("\n[2] LOCOMO corpus (real external data): %s" % locomo_path)
        for label, recs, qs in load_locomo(locomo_path, max_convs=a.locomo_convs):
            entries.append(evaluate_corpus(label, recs, qs, list(range(a.locomo_seeds)),
                                           train_events=4 * len(qs)))
    else:
        print("\n[2] LOCOMO corpus: NOT FOUND (pass --locomo PATH or set INSPEXIMUS_LOCOMO_JSON)."
              "\n    The synthetic arm above is complete on its own; LOCOMO is a confirmation on real"
              " external data.")

    verdicts = []
    for e in entries:
        verdicts += verdict_for(e)

    result = {
        "probe": "reinforce_accuracy_ablation",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "inspeximus_version": getattr(__import__("inspeximus"), "__version__", "unknown"),
        "scope": "MEASUREMENT ONLY -- the reinforce default and the ranking are unchanged by this probe",
        "k": K, "zipf_a": ZIPF_A, "orders": ORDERS,
        "prior_art": {
            "cold_stream": "README (1.15.0 correction) and CHANGELOG already publish reinforcement as a "
                           "confound depressing recall on a COLD query stream (~0.10 at low k). The cold "
                           "arm here is a control reproducing that sign, not a new result.",
            "determinism": "tests/test_recall_is_deterministic.py already establishes run-to-run "
                           "determinism and that reinforcement was not its cause.",
            "insert_order": "core.py ~5584 newest-first tie-break is deliberate policy (`tie_recent`), "
                            "so insert-order sensitivity is by design.",
        },
        "guard_thresholds": {"min_ratio_over_chance": MIN_RATIO_OVER_CHANCE,
                             "min_absolute": MIN_ABSOLUTE, "min_z": MIN_Z, "ceiling": CEILING},
        "guard_fired": False,
        "locomo_path": locomo_path,
        "corpora": entries,
        "verdicts": verdicts,
        "seconds_total": round(time.time() - t_all, 1),
    }
    json.dump(result, open(a.out, "w", encoding="utf-8"), indent=1)

    print("\nVERDICTS")
    for v in verdicts:
        print("  " + v)
    print("\nwrote %s  (%.1fs)" % (a.out, result["seconds_total"]))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BaselineGuardError as exc:
        print("\n" + "=" * 78)
        print("PROBE ABORTED -- BASELINE GUARD FIRED")
        print("=" * 78)
        print(exc)
        print("=" * 78)
        sys.exit(2)
