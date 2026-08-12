"""Does our >=2 distinct-source bar survive two links that are ECHOES of ONE upstream chain?

yun520-1 raised this on DeepSeek-V3#1462 and credited our "≥2 distinct-source links" wording with
capturing it: corroborating sources must point to DIFFERENT upstream evidence chains (different
documents, sessions, observers), because otherwise "2 links are just 1 belief's echo".

Reading the code first suggested the credit is partly misplaced. `_distinct_canonical_sources` counts
canonicalised `source.doc` STRINGS and never consults lineage, so two records DERIVED FROM ONE PARENT
but carrying different source strings should count as two independent witnesses. That is a reading,
not a measurement, so this probe builds the case and asks the shipped gate.

Four cases, and the two controls are the point:

  A  genuinely independent  -> two records, two unrelated sources          EXPECT corroborated
  B  the echo               -> two records, different source strings, both derived_from ONE parent
  C  naming sybil           -> two records, "Reuters" and "reuters.com"     EXPECT not corroborated
                              (entity resolution already collapses these -- the rail that DOES work)
  D  single witness         -> one link                                     EXPECT not corroborated

C and D are positive controls: if either comes back corroborated the instrument is wrong and B's
result means nothing. A is the negative control: if it comes back UNcorroborated the gate is simply
broken and B is not evidence of anything either.

Run:  python probes/corroboration_counts_an_echo_as_two_witnesses.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus  # noqa: E402


def _store():
    return Inspeximus(path=os.path.join(tempfile.mkdtemp(), "m.json"))


def _verdict(m, rec_id, strict=False):
    by_id = {r["id"]: r for r in m.items}
    rec = by_id[rec_id]
    return Inspeximus._is_corroborated(rec, by_id, strict=strict)


def case_A_genuinely_independent():
    """Two witnesses, two unrelated upstream chains. The gate must say corroborated."""
    m = _store()
    a = m.remember("The staging DB is db-7", source={"doc": "runbook.md"})
    b = m.remember("The staging DB is db-7", source={"doc": "incident-4412"})
    claim = m.remember("The staging DB is db-7", source={"doc": "summary"})
    claim_rec = next(r for r in m.items if r["id"] == claim)
    claim_rec["links"] = [a, b]
    return _verdict(m, claim)


def case_B_the_echo():
    """Two witnesses that are both DERIVED FROM one parent, with different source strings.

    This is yun520-1's case stated in our own data model: one upstream belief, restated twice, each
    restatement carrying its own document name.
    """
    m = _store()
    parent = m.remember("Original memo: the staging DB is db-7", source={"doc": "memo-original"})
    a = m.remember("Restated: staging DB is db-7", source={"doc": "slack-thread"}, derived_from=[parent])
    b = m.remember("Restated again: staging DB is db-7", source={"doc": "wiki-copy"}, derived_from=[parent])
    claim = m.remember("The staging DB is db-7", source={"doc": "summary"})
    claim_rec = next(r for r in m.items if r["id"] == claim)
    claim_rec["links"] = [a, b]
    return _verdict(m, claim)


def case_C_naming_sybil():
    """Two names for ONE source. Entity resolution should collapse them to one witness."""
    m = _store()
    a = m.remember("The staging DB is db-7", source={"doc": "Reuters"})
    b = m.remember("The staging DB is db-7", source={"doc": "reuters.com"})
    claim = m.remember("The staging DB is db-7", source={"doc": "summary"})
    claim_rec = next(r for r in m.items if r["id"] == claim)
    claim_rec["links"] = [a, b]
    return _verdict(m, claim)


def case_D_single_witness():
    m = _store()
    a = m.remember("The staging DB is db-7", source={"doc": "runbook.md"})
    claim = m.remember("The staging DB is db-7", source={"doc": "summary"})
    claim_rec = next(r for r in m.items if r["id"] == claim)
    claim_rec["links"] = [a]
    return _verdict(m, claim)


def main():
    results = {
        "A_genuinely_independent": case_A_genuinely_independent(),
        "B_echo_of_one_parent": case_B_the_echo(),
        "C_naming_sybil": case_C_naming_sybil(),
        "D_single_witness": case_D_single_witness(),
    }
    for k, v in results.items():
        print(f"  {k:<26} corroborated = {v}")

    controls_ok = (results["A_genuinely_independent"] is True
                   and results["C_naming_sybil"] is False
                   and results["D_single_witness"] is False)
    print()
    print(f"  controls hold (A True, C False, D False): {controls_ok}")
    if not controls_ok:
        print("  VERDICT: instrument is not trustworthy; B proves nothing.")
        verdict = "INSTRUMENT-FAILED"
    elif results["B_echo_of_one_parent"]:
        print("  VERDICT: an ECHO OF ONE PARENT counts as two independent witnesses.")
        print("           yun520-1 is right and our wording does NOT capture it: the default rail")
        print("           counts source STRINGS and never consults lineage.")
        verdict = "ECHO-PASSES-THE-GATE"
    else:
        print("  VERDICT: the echo is rejected; lineage is already consulted.")
        verdict = "ECHO-REJECTED"

    out = {"results": results, "controls_ok": controls_ok, "verdict": verdict}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "corroboration_counts_an_echo_as_two_witnesses.result.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    print(f"\n  wrote {os.path.basename(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
