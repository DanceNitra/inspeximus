"""How good is `infer_lineage` actually? Precision and recall against constructed ground truth.

1.49.0 shipped the inference with two numbers and a hole. Measured on our own 27,290-record deployment we
knew the FIRING RATE (~22%) and a null-model DISCRIMINATION (61.6% vs a 50% chance line) -- but not
precision or recall, because a real corpus has no ground truth for "was this write actually derived from
that recall". This builds a corpus where the answer is known by construction and measures both.

DESIGN, and the part that decides whether the test means anything:

  * DERIVED writes are built from what a recall actually returned -- content words drawn from the recalled
    records and recombined. A `reuse` parameter controls how much of the parent's wording survives, from a
    near-quote (0.9) to a heavy rewrite (0.3). That axis is where the inference should break, so it is swept
    rather than fixed at a flattering value.
  * NON-DERIVED writes are the hard case on purpose: new facts about the SAME domain, using the same
    vocabulary, written straight after the same recall. A test whose negatives are off-topic would score
    beautifully and prove nothing -- vocabulary overlap is exactly what fooled the raw threshold.

Reported: precision, recall and F1 per threshold, plus the reuse level at which recall collapses. Run it
with `python probes/infer_lineage_precision.py`.
"""
import random
import statistics
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from inspeximus import Inspeximus                                              # noqa: E402

SEED = 11
DOMAIN = ("billing api authenticates oauth2 tokens keycloak tenant service gateway latency retry quota "
          "webhook payload signature rotation scope refresh grant issuer audience claim session").split()
SUBJECTS = ["billing", "gateway", "webhook", "session", "quota", "issuer", "payload", "scope"]


def _domain_fact(rng, subject):
    """An independent observation in the same domain — the negative class."""
    body = rng.sample(DOMAIN, 8)
    return f"the {subject} {' '.join(body)}"


def _derived_from(rng, parents_text, reuse):
    """A summary built out of what the recall returned, keeping `reuse` of the parent's content words."""
    pw = [w for w in parents_text.lower().split() if w.isalpha() and len(w) > 2]
    if not pw:
        return None
    keep = max(4, int(len(set(pw)) * reuse))
    taken = rng.sample(sorted(set(pw)), min(keep, len(set(pw))))
    filler = rng.sample(DOMAIN, max(0, 10 - len(taken)))
    words = taken + filler
    rng.shuffle(words)
    return "summary: " + " ".join(words)


def run(threshold, reuse, n_pairs=120):
    rng = random.Random(SEED)
    m = Inspeximus(path=str(Path(tempfile.mkdtemp()) / "m.json"), infer_lineage=threshold)
    # seed the store so the null baseline is a real same-domain population, not an empty one
    for s in SUBJECTS:
        for _ in range(6):
            m.remember(_domain_fact(rng, s))

    truth, stamped = [], []
    for i in range(n_pairs):
        subject = SUBJECTS[i % len(SUBJECTS)]
        hits = m.recall(subject, k=4) or []
        if not hits:
            continue
        parents_text = " ".join(h.get("text", "") for h in hits)

        want_derived = (i % 2 == 0)
        text = (_derived_from(rng, parents_text, reuse) if want_derived
                else _domain_fact(rng, subject))
        if not text:
            continue
        rid = m.remember(text)
        rec = next(r for r in m.items if r["id"] == rid)
        truth.append(want_derived)
        stamped.append(bool(rec.get("derived_from")))

    tp = sum(1 for t, s in zip(truth, stamped) if t and s)
    fp = sum(1 for t, s in zip(truth, stamped) if not t and s)
    fn = sum(1 for t, s in zip(truth, stamped) if t and not s)
    prec = tp / (tp + fp) if tp + fp else float("nan")
    rec_ = tp / (tp + fn) if tp + fn else float("nan")
    f1 = (2 * prec * rec_ / (prec + rec_)) if tp and (prec + rec_) else 0.0
    return {"n": len(truth), "tp": tp, "fp": fp, "fn": fn,
            "precision": prec, "recall": rec_, "f1": f1}


def main():
    print("infer_lineage — precision/recall vs constructed ground truth")
    print("negatives are same-domain, same-vocabulary writes issued right after the same recall\n")
    for reuse in (0.9, 0.7, 0.5, 0.3):
        print(f"--- parent wording retained in the derivative: {reuse:.0%} ---")
        print(f"  {'thresh':>7}{'n':>6}{'TP':>5}{'FP':>5}{'FN':>5}{'precision':>11}{'recall':>9}{'F1':>7}")
        for t in (0.05, 0.10, 0.20, 0.30, 0.40):
            r = run(t, reuse)
            print(f"  {t:>7.2f}{r['n']:>6}{r['tp']:>5}{r['fp']:>5}{r['fn']:>5}"
                  f"{r['precision']:>11.3f}{r['recall']:>9.3f}{r['f1']:>7.3f}")
        print()


if __name__ == "__main__":
    main()
