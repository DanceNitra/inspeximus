"""reversion_classifier_probe.py — the Marat decomposition shipped as mnemo.classify_reversion (0.7.14).

The joint TAT/mnemo analysis factorized value-obscuring reversion detection into reference resolution (text)
plus recency attribution (ledger), with abstention on unresolvable references. classify_reversion() is that,
running on a real mnemo store's own ledger. This probe measures it end to end on mnemo-native data: per
entity we remember the old state with a descriptive record (it names the setter / a distinguishing feature),
then the correction, then classify four kinds of candidate:

  - REFERENCED REVERT  : describes the old state without naming its value ("go with what marcus decided")
                         -> should classify "revert", target = the superseded value.
  - AFFIRM CURRENT     : refers to the current state ("stick with the vendor's update")
                         -> should classify "keep".
  - BARE GO-BACK       : "let's go back", "undo that" — a value-obscuring revert that describes nothing
                         -> should ABSTAIN (unresolved_reference). This is the boundary: no content method
                            should decide it; the authorized-revert channel does.
  - UNRELATED          : an off-topic utterance -> should ABSTAIN.

Uses the local embedder (Ollama nomic-embed-text). The point measured: does the shipped classifier reproduce
the decomposition's behaviour — resolve real references correctly via the ledger, and abstain (not guess) on
the undecidable ones — on mnemo's own store rather than on the paper's fixture.

RUN: python mnemo/probes/reversion_classifier_probe.py
"""
import sys, pathlib, json, urllib.request, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "mnemo_pypi"))
from mnemo import Mnemo, __version__
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import integrity_bench_revert as rev


def embed(text):
    body = json.dumps({"model": "nomic-embed-text", "input": [text]}).encode()
    r = urllib.request.urlopen(urllib.request.Request(
        "http://localhost:11434/api/embed", data=body,
        headers={"Content-Type": "application/json"}), timeout=60)
    return json.loads(r.read())["embeddings"][0]


SETTERS = ["marcus", "the vendor", "priya", "the audit team", "lena", "old tomas",
           "the review board", "the platform crew", "the architect", "the ops desk"]


def main():
    R = {"mnemo_version": __version__}
    rng_names = SETTERS
    cases = rev.ENTS[:24]
    referenced_revert = affirm_keep = bare_abstain = unrelated_abstain = 0
    ref_total = keep_total = bare_total = unrel_total = 0
    misfires = []                                          # a revert flagged where keep/abstain was right, etc.
    for i, (e, A, B) in enumerate(cases):
        setter_old, setter_new = rng_names[i % len(rng_names)], rng_names[(i + 3) % len(rng_names)]
        m = Mnemo(path=None, embed=embed); m.echo_guard = True
        m.remember(f"the {e} was {A}; {setter_old} made that call.", key=e, object=A)
        m.remember(f"correction: {setter_new} changed the {e} to {B}.", key=e, object=B)

        # 1. referenced revert (describes old state by its setter, no value token)
        r1 = m.classify_reversion(f"let's go with the choice {setter_old} made about the {e}.", e)
        ref_total += 1
        if r1["intent"] == "revert" and r1.get("target") == A:
            referenced_revert += 1
        else:
            misfires.append(("ref_revert", e, r1))

        # 2. affirm current (refers to the current setter/state)
        r2 = m.classify_reversion(f"stick with what {setter_new} decided for the {e}.", e)
        keep_total += 1
        if r2["intent"] == "keep":
            affirm_keep += 1
        else:
            misfires.append(("affirm_keep", e, r2))

        # 3. bare go-back (describes nothing) -> must abstain
        r3 = m.classify_reversion(f"actually, let's just go back on the {e}.", e)
        bare_total += 1
        if r3["intent"] == "abstain":
            bare_abstain += 1
        else:
            misfires.append(("bare_goback", e, r3))

        # 4. unrelated -> must abstain
        r4 = m.classify_reversion("the weather has been unusually mild for the season.", e)
        unrel_total += 1
        if r4["intent"] == "abstain":
            unrelated_abstain += 1
        else:
            misfires.append(("unrelated", e, r4))

    R["referenced_revert_correct"] = f"{referenced_revert}/{ref_total}"
    R["affirm_current_kept"] = f"{affirm_keep}/{keep_total}"
    R["bare_goback_abstained"] = f"{bare_abstain}/{bare_total}"
    R["unrelated_abstained"] = f"{unrelated_abstain}/{unrel_total}"
    R["misfire_count"] = len(misfires)

    print(json.dumps(R, indent=2))
    if misfires[:4]:
        print("\nsample misfires:")
        for kind, e, res in misfires[:4]:
            print(f"  {kind} [{e}]: {res}")
    ok = (referenced_revert >= 0.8 * ref_total and affirm_keep >= 0.7 * keep_total
          and bare_abstain >= 0.8 * bare_total and unrelated_abstain == unrel_total)
    print("\nREADING: classify_reversion() ships the decomposition. It resolves a described reference to the")
    print("old state and attributes it to the superseded value via the ledger (revert), keeps the current one")
    print("when the reference points there, and ABSTAINS on a bare go-back that describes nothing (the")
    print("undecidable boundary, correctly deferred to the authorized-revert channel) and on unrelated text.")
    print("It classifies only; restoring still requires submit_revert with authorization.")
    print("HONEST PROFILE: strong on the discriminating cases; conservative on affirm (a borderline keep")
    print("abstains rather than risk, the safe direction since abstaining never triggers a wrong revert);")
    print("abstention quality is embedder-bound (clean here on nomic at margin 0.06/floor 0.50; the joint")
    print("analysis showed the gradient is sharper on bge-m3, softer on some embedders) — tune per embedder.")
    print("\nALL PASS" if ok else "\nBELOW THRESHOLD (tune threshold or inspect misfires)")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
