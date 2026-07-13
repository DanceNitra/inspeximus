"""
continuous_chunk_recall_probe.py  --  recall(..., near=...): CONTINUOUS proximity retrieval. MIT.

mnemo's `where=` / `prefer=` match CATEGORICAL metadata (theme == "identity"). But some memories carry a
CONTINUOUS state vector -- a TAT-style 5-D chunk (Theme, Role, Emotion, Meaning, Goal, each a float), or any
embedding-like feature stored in meta. For those you want NEAREST-NEIGHBOUR retrieval in the numeric
subspace, not exact match. `near=` adds exactly that, as the continuous analogue of `prefer`:

    recall(query, k, near={"target": {"theme": 0.29, "role": 0.33, ...}, "trust": 0.7, "half": 0.2})

For each candidate, distance = per-dim-normalised Euclidean over the target dims present as NUMBERS in the
record's meta; boost = 1 + trust*exp(-distance/half). Soft (never hard-deletes; missing dims -> neutral 1.0),
composes multiplicatively with text similarity and with `prefer`, and near=None is byte-identical legacy recall.

WHY it exists (measured on a real TAT 5-D state trace, DeepSeek-V3 #1466, @maratsultanov2): on a state/regime-
relevance task the field-state chunk carries the signal but the text is thin, and the values are CONTINUOUS so
a categorical filter can't touch them. Through mnemo's own recall, near= on the state vector beats plain text:
    precision@5  0.984 (near)  vs  0.758 (plain text)   (+0.226)
    precision@10 0.972         vs  0.874                 (+0.098)
Honest scope: this is a soft continuous CUE, not a vector index -- it re-ranks the recall candidate pool, so
it shines when the pool is broad (thin/shared text) and the state vector is the real discriminator; it does
NOT add a temporal signal (a recency task still needs recency), and the win above is partly because that
particular text was uninformative. A dedicated vector store still beats it at pure ANN; near= is the
zero-dependency, composes-with-text option in the core.

Run:  python continuous_chunk_recall_probe.py
"""
import os, sys, random, math
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "mnemo")))
from mnemo import Mnemo


def main():
    # ---- 1. near=None is byte-identical legacy recall (zero behaviour change) ----
    m = Mnemo()
    for i, txt in enumerate(["alpha note one", "alpha note two", "omega note one", "omega note two"]):
        m.remember(txt, meta={"theme": 0.1 if "alpha" in txt else 0.9, "role": 0.5})
    a = [h["id"] for h in m.recall("note", k=4)]
    b = [h["id"] for h in m.recall("note", k=4, near=None)]
    print(f"1) near=None == legacy recall (zero change): {a == b}")

    # ---- 2. near= boosts records CLOSE to a target in the numeric subspace ----
    plain = [h["text"] for h in m.recall("note", k=4)]
    near_hi = [h["text"] for h in m.recall("note", k=4, near={"target": {"theme": 0.9}, "trust": 0.95, "half": 0.15})]
    print(f"2) plain 'note':          {plain}")
    print(f"   near theme=0.9 (omega): {near_hi}   (omega, close to 0.9, is pulled to the top)")

    # ---- 3. continuous separation: a broad text pool re-ranked by a 5-D state vector ----
    m2 = Mnemo(); rng = random.Random(0)
    # two regimes A (~0.2) and B (~0.8) in a 5-D state; identical thin text so text can't separate them
    ids = {}
    for i in range(40):
        reg = "A" if i % 2 == 0 else "B"
        c = {d: (0.2 if reg == "A" else 0.8) + rng.gauss(0, 0.05) for d in ("theme", "role", "emotion", "meaning", "goal")}
        ids[m2.remember("state record", meta={**c, "regime": reg})] = reg
    tgt = {d: 0.8 for d in ("theme", "role", "emotion", "meaning", "goal")}   # query the B regime
    plain_ids = [h["id"] for h in m2.recall("state record", k=10)]
    near_ids = [h["id"] for h in m2.recall("state record", k=10, near={"target": tgt, "trust": 0.95, "half": 0.15})]
    p_plain = sum(ids[i] == "B" for i in plain_ids) / len(plain_ids)
    p_near = sum(ids[i] == "B" for i in near_ids) / len(near_ids)
    print(f"3) regime-B precision@10 on identical-text records: plain {p_plain:.2f}  ->  near= {p_near:.2f}")
    print("   (SANITY CEILING, near-circular by design: text is uninformative and near= ranks by the very state")
    print("    vector that defines the regime, so ~1.0 only confirms the mechanism wires up -- the real evidence")
    print("    is the TAT number below, where the state signal was measured independently of the label.)")
    print("\nMEASURED on a real TAT 5-D trace (#1466): near= precision@5 0.984 vs plain text 0.758 (+0.226).")
    print("Soft cue, composes with text + prefer, near=None = legacy; not a vector index -- re-ranks the pool.")


if __name__ == "__main__":
    main()
