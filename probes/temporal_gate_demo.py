"""The OPT-IN temporal gate (`Mnemo.temporal_gate`), measured as a shipped feature. Suggested by hannune (r/RAG).

A corroborating link proves independence of source, never independence of TIMING. Genuinely independent sources
rarely write within seconds of each other; a coordinated forgery writes its witnesses in a burst. `temporal_gate`
(opt-in, `m.temporal_gate = 60.0` seconds; default None -> zero behavior change) collapses CO-ARRIVING witnesses
(timestamps within the window of each other) to one anchor before the >=2-distinct-source count -- exactly as
_distinct_sources collapses one canonical source, but on TIME. So a 2-witness burst counts as one; two witnesses
spread out in time count as two.

Three cases, real mnemo (`_corroborated`), timestamps set explicitly:
  1. genuine recovery, witnesses SPREAD OUT (hours apart, 2 sources)   -> corroborated OFF and ON  (untouched)
  2. BURST forgery, 2 witnesses co-arrive within the window            -> corroborated OFF, BLOCKED ON  (the win)
  3. PATIENT forgery, 2 witnesses spaced BEYOND the window             -> corroborated OFF and ON  (honest limit)

FALSIFIER: if the gate blocked the spread-out genuine recovery (case 1 ON) or failed to block the burst (case 2
ON), it would be useless or too costly. Neither holds. Case 3 is the honest limit: a patient attacker who spaces
writes out defeats a timing signal (patience buys past it -- the sleeper again).

Deterministic, zero-dependency. MIT. Part of Agora / mnemo.
Run:  python mnemo/probes/temporal_gate_demo.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from mnemo import Mnemo

WINDOW = 60.0   # seconds; witnesses arriving within this of each other collapse to one anchor


def _build(temporal_gate, witness_offsets, sources):
    """A store: claim P at t=1000 with 2 corroborating links whose ts = 1000 + offset (seconds), from the given
    source docs. Returns whether real mnemo counts P as corroborated under the given temporal_gate."""
    m = Mnemo(os.path.join(tempfile.mkdtemp(), "t.jsonl"))
    m.temporal_gate = temporal_gate
    P = m.remember("contested value under override", source={"doc": "origin"})
    links = [m.remember(f"witness {i}", source={"doc": s}) for i, s in enumerate(sources)]
    by = {x["id"]: x for x in m.items}
    by[P]["ts"] = 1000.0
    for lid, off in zip(links, witness_offsets):
        by[lid]["ts"] = 1000.0 + off
    by[P]["links"] = links
    return m._corroborated(by[P], by)


def main():
    print("=== mnemo temporal_gate (opt-in): do corroborating witnesses have to arrive INDEPENDENTLY in time? ===\n")
    cases = [
        ("genuine recovery, witnesses SPREAD OUT (0s, 3h)", [0.0, 10800.0], ["indep-lab", "indep-forum"]),
        ("BURST forgery, 2 witnesses co-arrive (0s, 5s)", [0.0, 5.0], ["forged-a", "forged-b"]),
        ("PATIENT forgery, witnesses spaced (0s, 2h)", [0.0, 7200.0], ["forged-a", "forged-b"]),
    ]
    print(f"{'case':50} | gate OFF | gate ON ({int(WINDOW)}s)")
    results = {}
    for name, offs, srcs in cases:
        off = _build(None, offs, srcs)
        on = _build(WINDOW, offs, srcs)
        results[name] = (off, on)
        tag = "  <-- BLOCKED: co-arriving witnesses collapse to one anchor" if (off and not on) else \
              ("  <-- honest LIMIT: patient (spaced) forgery still passes" if (off and on and "PATIENT" in name) else "")
        print(f"{name:50} |  {str(off):5}  |  {str(on):5}{tag}")

    # --- self-check (the falsifier) ---
    assert results["genuine recovery, witnesses SPREAD OUT (0s, 3h)"] == (True, True), \
        "temporal gate must NOT block a spread-out genuine recovery"
    assert results["BURST forgery, 2 witnesses co-arrive (0s, 5s)"] == (True, False), \
        "temporal gate must block a co-arrival burst (collapses to one anchor)"
    assert results["PATIENT forgery, witnesses spaced (0s, 2h)"] == (True, True), \
        "patient (spaced) forgery is the honest limit -- a timing signal can't catch it"

    print("\nVERDICT: with temporal_gate ON, two witnesses that CO-ARRIVE (within the window) collapse to one anchor")
    print("and no longer clear the >=2-source bar, while genuinely independent, time-separated witnesses are")
    print("untouched. HONEST LIMIT: a PATIENT attacker who spaces the forged writes beyond the window defeats it")
    print("(coordinated-burst / Sybil-timing detection; patience buys past a timing signal -- the sleeper). A soft,")
    print("DECORRELATED layer (timing is orthogonal to source count and content coherence), not a wall. Suggested")
    print("by hannune (r/RAG); its value is exactly the decorrelation the attacker leaves you. Default OFF.")


if __name__ == "__main__":
    main()
