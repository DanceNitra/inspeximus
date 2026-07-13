"""The OPT-IN coherence gate (`Mnemo.coherence_gate`), measured as a shipped feature.

A corroborating link only counts toward the >=2-distinct-source corroboration bar if its witness is actually
COHERENT with the claim (embedder cosine if an embed fn is set, else lexical token-Jaccard) >= the threshold.
This closes the LAZY forged-source residual (a poison that clears the source COUNT with off-topic filler
witnesses no longer corroborates). HONEST LIMIT (textbook adaptive-attack / common-mode territory --
Carlini-Wagner 2017, Knight-Leveson 1986, PoisonedRAG 2402.07867): it RAISES the forger's bar to on-topic
witnesses, it does not close the residual. Default OFF -> zero behavior change.

Four cases, real mnemo (`_corroborated`), no embedder (lexical coherence):
  1. genuine recovery  (on-topic witnesses, 2 real sources)  -> corroborated OFF and ON  (no false-withhold)
  2. LAZY forgery      (off-topic filler, 2 forged sources)  -> corroborated OFF, BLOCKED ON  (the win)
  3. SOPHISTICATED forgery (on-topic witnesses, 2 forged src) -> corroborated OFF and ON  (the honest limit)

FALSIFIER: if the gate blocked the genuine recovery (case 1 ON) or failed to block the lazy forgery (case 2 ON),
the feature would be either too costly or useless. Neither holds.

Deterministic, zero-dependency. MIT. Part of Agora / mnemo.
Run:  python mnemo/probes/coherence_gate_demo.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from mnemo import Mnemo

CLAIM = "billing api uses oauth2 client credentials for production auth"
ON_TOPIC = ["oauth2 client credentials are the production auth for the billing api",
            "the billing api authenticates with oauth2 client credentials in prod"]
OFF_TOPIC = ["the weather in helsinki was mild and grey all week",
             "quarterly budget review meeting moved to next thursday afternoon"]


def _make(coherence_gate, witness_texts, sources):
    """Build a store: a claim P with 2 corroborating links (given witness texts + source docs). Return whether
    the real mnemo corroboration check (with the given coherence_gate) counts P as corroborated."""
    m = Mnemo(os.path.join(tempfile.mkdtemp(), "c.jsonl"))
    m.coherence_gate = coherence_gate
    P = m.remember(CLAIM, source={"doc": "origin"})
    links = [m.remember(t, source={"doc": s}) for t, s in zip(witness_texts, sources)]
    by = {x["id"]: x for x in m.items}
    by[P]["links"] = links
    return m._corroborated(by[P], by)


def main():
    print("=== mnemo coherence_gate (opt-in): does a corroborating witness have to be ABOUT the claim? ===\n")
    thr = 0.18
    cases = [
        ("genuine recovery (on-topic, 2 real sources)", ON_TOPIC, ["indep-lab", "indep-forum"]),
        ("LAZY forgery (off-topic filler, 2 forged sources)", OFF_TOPIC, ["forged-a", "forged-b"]),
        ("SOPHISTICATED forgery (on-topic, 2 forged sources)", ON_TOPIC, ["forged-a", "forged-b"]),
    ]
    print(f"{'case':52} | gate OFF | gate ON (thr={thr})")
    results = {}
    for name, wits, srcs in cases:
        off = _make(None, wits, srcs)
        on = _make(thr, wits, srcs)
        results[name] = (off, on)
        tag = ""
        if off and not on:
            tag = "  <-- BLOCKED by coherence (off-topic witnesses don't count)"
        elif off and on and "SOPHISTICATED" in name:
            tag = "  <-- honest LIMIT: on-topic forgery still passes"
        print(f"{name:52} |  {str(off):5}  |  {str(on):5}{tag}")

    genuine = results["genuine recovery (on-topic, 2 real sources)"]
    lazy = results["LAZY forgery (off-topic filler, 2 forged sources)"]
    soph = results["SOPHISTICATED forgery (on-topic, 2 forged sources)"]

    # --- self-check (the falsifier) ---
    assert genuine == (True, True), "coherence gate must NOT block a genuine on-topic recovery"
    assert lazy == (True, False), "coherence gate must block the LAZY forgery (off-topic witnesses)"
    assert soph == (True, True), "on-topic forgery is the honest limit -- it still passes (raises the bar, no wall)"

    print("\nVERDICT: with coherence_gate ON, an off-topic filler forgery no longer corroborates -- its witnesses")
    print("aren't about the claim, so they don't count toward the >=2-distinct-source bar -- while a genuine")
    print("on-topic recovery is untouched. It does NOT close the residual: an attacker who writes on-topic forged")
    print("witnesses still passes (adaptive-attack / common-mode limit, cite Carlini-Wagner / Knight-Leveson /")
    print("PoisonedRAG). Default OFF; a defense-in-depth layer that raises the forger's bar, not a wall.")


if __name__ == "__main__":
    main()
