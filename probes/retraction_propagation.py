"""jacksonxly's TEMPORAL integrity invariant, built and measured on shipped mnemo.

Context (r/RAG / r/LangChain thread on our memory-poisoning post). jacksonxly's point: authenticated-but-false
is the corroboration gate working AS SPECIFIED, not a hole -- once genuinely independent origins converge on a
wrong claim, no WRITE-TIME signal can catch it, because correctness is not computable from the record. The only
remaining lever is TIME: treat corroboration as raising CONFIDENCE, never conferring TRUTH; keep every
influence-grant REVERSIBLE; and when a correctness signal lands later (a contradicting outcome, a retraction, a
human correction) let it PROPAGATE to everything that leaned on the claim. He reframes the integrity property
from the impossible "never hold a false belief" to the achievable:

    "No false belief stays LOAD-BEARING past the moment a correctness signal lands."
    = bounded blast radius  +  fast, complete retraction propagation.

We credit jacksonxly for the invariant statement and marintkael for the authenticated-but-false framing; the
security principle underneath is capability REVOCATION / provenance-carried taint (least-privilege + revocation),
which we also credit. We did not invent revocation -- we MEASURE whether mnemo's shipped slash()/restore() over
derived_from taint actually satisfies his invariant, and exactly where it does not.

WHAT WE MEASURE (deterministic; no embedder -- load-bearing := Mnemo._is_corroborated, the recall influence gate):
Build a provenance tree from an authenticated-but-false root P, land ONE correctness signal (slash the root's
source), and check the load-bearing set before / after / after-restore.

  P   root poison, source=SRC_BAD, load-bearing via EARNED outcome credit (the sleeper that banked good)
  A1  summary        derived_from [P]      load-bearing via its OWN earned credit
  A2  consolidation  derived_from [P]      load-bearing via semantic GRADUATION (mtype=semantic)
  B1  meta-summary   derived_from [A1]     DEPTH-2 (transitivity) -- load-bearing via its own credit
  O   orphan copy of A1 with NO derived_from  -- control: lineage stripped, so the retraction cannot find it
  C   derived_from [P] but load-bearing via >=2 DISTINCT-SOURCE corroboration links (the authenticated path)

FINDINGS (self-check asserts the core):
  1. PROPAGATION: one slash([P], scope='source') revokes load-bearing standing on 100% of the provenance-linked
     descendants -- INCLUDING the depth-2 node AND a descendant that independently clears the >=2-distinct-source
     bar -- in a single operation (taint rides transitively through summarization). Bounded blast radius = the
     full provenance subtree, reached at once, not chased node by node.
  2. REVERSIBILITY: restore([P], scope='source') recovers every one of them to its EXACT pre-slash standing --
     so a mistaken/ weaponized retraction is undoable (slash cannot be used to permanently knock out a rival).
  3. CONFIDENCE-NOT-TRUTH: at its peak P grades 'corroborated' / convergence-backed, never 'verified' -- the
     substrate never granted it truth, exactly as the invariant requires.
  ORPHAN BOUNDARY -> INTEGRITY FLOOR (Biba-style, measured): a self-declared derived write (derived=True) that
     resolves no parent earns NO standing (O2 below is load-bearing=False from t0 even WITH credit), so a summary
     that lost its lineage cannot quietly earn influence or survive a retraction; a genuine primary write (O) is
     unaffected. HONEST LIMIT (this is NOT adversary-proof): `derived` is caller-set, so a hostile/careless caller
     that OMITS it is treated as a primary observation and can still earn standing -- it fails OPEN. Prior art:
     Biba integrity (1977), taint-tracking / default-deny; this is an application to the graduation+recall gate,
     not a new idea. A truly adversary-resistant version would INFER derivation from the summarize/consolidate
     call site rather than trust the flag.
  FIXED THIS CYCLE (the link-corroboration hole): a descendant that independently cleared the >=2-distinct-source
     gate used to SURVIVE the retraction, because slash() books accountability (zeroes good, dominates bad,
     revokes graduation) but does not invalidate corroboration LINKS, so the distinct-source path stayed lit.
     Closed by making a landed retraction win: _is_corroborated() (the recall influence gate + graduation bar)
     now returns False for any slash()'d record on EVERY path, incl. distinct-link corroboration. restore()
     clears the flag, so the fix stays fully reversible. C below now falls with the rest of the subtree.

FALSIFIER: if any provenance-descendant stayed load-bearing after the slash (incomplete propagation), or restore
did not recover it, or P ever graded 'verified' from corroboration alone, the invariant would be violated. It is
not. (Before the fix, C stayed load-bearing -- that regression test is the reason C is in the tree.)

Zero-dependency, no network, no embedder. Deterministic. MIT. Part of Agora / mnemo.
Run:  python mnemo/probes/retraction_propagation.py
"""
import os
import sys
import tempfile

# Prefer the in-repo mnemo source (this probe travels with the repo and tests the current shipped code);
# a standalone `pip install agora-mnemo` copy falls through to the installed package.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from mnemo import Mnemo


def _load_bearing(m, rid):
    """Exactly the recall influence gate (recall(influence_only=True)) applied to one record -- no embedder."""
    by_id = {x["id"]: x for x in m.items}
    r = by_id.get(rid)
    return bool(r) and r.get("status") == "active" and Mnemo._is_corroborated(r, by_id)


def _row(m, ids):
    return {name: _load_bearing(m, rid) for name, rid in ids.items()}


def main():
    path = os.path.join(tempfile.mkdtemp(), "retraction.jsonl")
    m = Mnemo(path)

    SRC_BAD = {"doc": "vendor-brief-42"}          # the (authenticated-but-false) origin of the poison
    SRC_X = {"doc": "independent-blog-7"}         # a genuinely independent source (for the link-corroboration path)
    SRC_Y = {"doc": "independent-forum-9"}        # another genuinely independent source

    # --- root poison P: admitted authenticated-but-false, made load-bearing by EARNED outcome credit (sleeper) ---
    P = m.remember("Setting api.retry=0 is the recommended production default.", source=SRC_BAD, mtype="semantic")
    m.credit([P], "good", weight=4.0)             # banked good on many benign queries -> load-bearing, still WRONG

    # --- provenance tree derived from P (taint rides through) ---
    A1 = m.remember("Prod config summary: retries disabled by default.", source=None, derived_from=[P])
    m.credit([A1], "good", weight=3.0)            # the summary earned its OWN standing
    A2 = m.remember("Consolidated ops note: api.retry stays 0.", source=None, derived_from=[P], mtype="semantic")
    #                                              A2 is load-bearing via semantic GRADUATION, not credit
    B1 = m.remember("Runbook (rolled up from the config summary): keep retries off.", source=None, derived_from=[A1])
    m.credit([B1], "good", weight=2.0)            # DEPTH-2 descendant (derived from A1, not P) -- transitivity test

    # --- O: a PRIMARY write with no parents (NOT declared derived) -- a fresh observation legitimately has no
    #     lineage and still earns standing. This is the case the fail-closed rule must NOT punish. ---
    O = m.remember("Prod config summary: retries disabled by default.", source=None)  # no derived_from, derived=False
    m.credit([O], "good", weight=3.0)

    # --- O2: an app-side summary DECLARED a transformation output (derived=True) that named NO parent --
    #     the untrusted LLM-summarize step dropped the lineage. Fail-closed provenance (jacksonxly) -> ORPHAN ->
    #     NO corroboration standing from the start, even WITH earned credit -> nothing to survive a retraction. ---
    O2 = m.remember("Ops summary (LLM-written, lineage lost): retries stay disabled.", source=None, derived=True)
    m.credit([O2], "good", weight=3.0)

    # --- C: a descendant of P that is ALSO independently link-corroborated (>=2 distinct sources) ---
    corr1 = m.remember("Blog: many teams run api.retry=0.", source=SRC_X)
    corr2 = m.remember("Forum: api.retry=0 is common.", source=SRC_Y)
    C = m.remember("Cross-checked: api.retry=0 is standard.", source=None, derived_from=[P])
    # attach independent corroboration links (2 distinct sources) -- the authenticated-but-false path
    by_id = {x["id"]: x for x in m.items}
    by_id[C]["links"] = [corr1, corr2]
    m._save()

    ids = {"P (root)": P, "A1 (summary)": A1, "A2 (graduated)": A2, "B1 (depth-2)": B1,
           "O (primary,no-lin)": O, "O2 (derived orphan)": O2, "C (link-corrob.)": C}
    # every provenance-reached descendant should fall (incl. C, the link-corroborated one, after the fix);
    # O is the only survivor -- it kept no lineage, so the retraction has no edge to travel along.
    prov_reached = ["P (root)", "A1 (summary)", "A2 (graduated)", "B1 (depth-2)", "C (link-corrob.)"]

    print("=== jacksonxly's invariant: 'no false belief stays load-bearing past the correctness signal' ===")
    print("    measured on shipped mnemo -- load-bearing := the recall influence gate (Mnemo._is_corroborated)\n")

    t0 = _row(m, ids)
    grade0 = m.convergence_report(P)
    print("t0  admitted (P authenticated-but-false, banked good; tree derived + earning standing):")
    for k in ids:
        print(f"      {k:20s} load-bearing={t0[k]}")
    print(f"    P evidence grade at peak: '{grade0.get('status')}' "
          f"(lineage_grade='{grade0.get('lineage_grade')}') -- confidence, never 'verified'.")
    print(f"    FAIL-CLOSED: O2 (declared derived=True, named no parent) load-bearing={t0['O2 (derived orphan)']} "
          f"even WITH credit -- an orphan earns NO standing; O (primary, not declared derived) "
          f"load-bearing={t0['O (primary,no-lin)']}, legitimately unaffected.\n")

    # --- ONE correctness signal lands on the root ---
    res = m.slash([P], scope="source")
    t1 = _row(m, ids)
    print(f"t1  correctness signal lands: slash([P], scope='source')  -> revoked {res['slashed']} records in ONE op")
    for k in ids:
        flip = "  <-- revoked" if (t0[k] and not t1[k]) else ("  <-- SURVIVED" if t1[k] else "")
        print(f"      {k:20s} load-bearing={t1[k]}{flip}")

    revoked = [k for k in prov_reached if t0[k] and not t1[k]]
    print(f"\n    PROPAGATION: {len(revoked)}/{len(prov_reached)} provenance-reached descendants revoked "
          f"transitively (incl. depth-2 B1 AND link-corroborated C).")
    print(f"    ORPHAN BOUNDARY (integrity-floor, Biba-style): a SELF-DECLARED derived summary that lost its "
          f"lineage (O2) earns no standing, so nothing to survive; a primary write (O) is untouched.")

    # --- reversibility: the retraction is undoable ---
    m.restore([P], scope="source")
    t2 = _row(m, ids)
    recovered = [k for k in prov_reached if not t1[k] and t2[k]]
    print(f"\nt2  restore([P], scope='source')  -> {len(recovered)}/{len(revoked)} recovered to exact pre-slash standing")
    for k in prov_reached:
        print(f"      {k:20s} load-bearing={t2[k]}")

    # --- ADVERSARY-RESISTANT (store policy): the exact attack the caller-set `derived` flag missed ---
    # An UNDECLARED summary omits derived=True AND has no source. In a normal store it is treated as a PRIMARY
    # write and earns standing (the caller-elective hole the skeptic found). Under strict_provenance the store
    # DENIES it standing with no flag required -- provenance must be SHOWN (a source or resolvable parents), so
    # the untrusted caller no longer holds the switch.
    def _undeclared_summary_lb(strict):
        s = Mnemo(os.path.join(tempfile.mkdtemp(), "s.jsonl"))
        s.strict_provenance = strict
        u = s.remember("Undeclared LLM summary: retries stay disabled.")   # no source, no derived_from, no flag
        s.credit([u], "good", weight=3.0)                                  # even with earned credit
        return _load_bearing(s, u)
    normal_lb = _undeclared_summary_lb(False)
    strict_lb = _undeclared_summary_lb(True)
    print(f"\nADVERSARY-RESISTANT (strict_provenance): an UNDECLARED summary (no flag, no source, no parents) is "
          f"load-bearing={normal_lb} in a normal store vs {strict_lb} under strict_provenance -- the caller")
    print(f"    cannot escape by omitting the flag; absence of SHOWN provenance denies standing by store policy.")

    # --- self-check (the falsifier) ---
    assert normal_lb is True, "baseline: an undeclared no-provenance write is treated as primary in a non-strict store"
    assert strict_lb is False, "ADVERSARY-RESISTANT broke: strict_provenance must deny standing to no-provenance writes"
    non_orphan = [k for k in ids if k != "O2 (derived orphan)"]
    assert all(t0[k] for k in non_orphan), "setup: every non-orphan node must start load-bearing"
    assert t0["O2 (derived orphan)"] is False, "FAIL-CLOSED broke: a declared-derived orphan must earn NO standing"
    assert all(not t1[k] for k in prov_reached), "PROPAGATION incomplete: a provenance descendant survived slash"
    assert t1["O (primary,no-lin)"] is True, "control broke: a PRIMARY no-lineage write must keep standing (not an orphan)"
    assert all(t2[k] for k in prov_reached), "REVERSIBILITY failed: restore did not recover standing"
    assert grade0.get("status") != "verified", "confidence-not-truth: P must never grade 'verified' from corroboration"

    print("\nVERDICT: the invariant HOLDS -- one slash reaches the full transitive provenance subtree (credit,")
    print("graduation, AND link-corroboration all fall), load-bearing -> 0, restore exact. The orphan boundary")
    print("gets a Biba-style INTEGRITY FLOOR at two levels: (1) a self-declared derived write that lost its")
    print("lineage (derived=True) earns NO standing; (2) strict_provenance store policy denies standing to ANY")
    print("write with no shown provenance (no source, no parents) -- so an UNDECLARED summary cannot escape by")
    print("omitting the flag (measured above: primary in a normal store, orphan under strict_provenance). The")
    print("caller no longer holds the switch. Residual: a FAKE source string -> priced by strict_corroboration/")
    print("attestation (a verified key). A primary write with a real source is unaffected in both modes.")


if __name__ == "__main__":
    main()
