"""Auto-stamped lineage: the store carries the recall->write edge so a laundered summary can't escape a retraction.

Context. Our retraction probe (retraction_propagation.py) left one boundary open: an app-side LLM summarize step
that DROPS the derived_from link produces an "orphan" the retraction can't reach. jacksonxly's fix was to fail
closed on missing lineage; our own storm+verify pass (storm-reports/provenance-default-deny-agent-memory-briefing)
found the source-string form of that is textbook Biba (1977) and RELOCATES trust (a caller who must attach a
source can forge one; MINJA/AgentPoison carry valid provenance anyway). The ONE form with measured defense value
is AUTO-STAMPED LINEAGE PROPAGATION -- the store inherits the retrieved parents into the derived write, so the
lineage EDGE is carried by the store from the recall->write flow, not supplied by the untrusted LLM (MemLineage,
arXiv:2605.14421: signature-only 6/6 attacks -> 0/6 once ancestor lineage propagates, sub-ms overhead).

This probe MEASURES that mechanism on shipped mnemo: recall() records what it surfaced; a subsequent
remember(..., derived=True) with no explicit parent auto-stamps derived_from from that recall, so the summary
carries its ancestors' taint and a slash on the root REACHES it -- the untrusted summarize step never had to
preserve the link. A derived write with NO preceding recall stays an orphan (fail-closed).

HONEST SCOPE (from the storm/verify pass -- do NOT overclaim): this closes the LAUNDERED/UNDECLARED-SUMMARY path
(lineage carried by the store, not the caller). It is Biba-style integrity, an APPLICATION of taint-tracking /
information-flow control, NOT a new idea, and it does NOT stop poison that carries valid provenance through the
legitimate channel (MINJA, arXiv:2503.03704) or that attacks retrieval geometry (AgentPoison, NeurIPS 2024) --
those need content-moderation + trust-decay retrieval, where provenance appears in no winning defense.

FALSIFIER: if the auto-stamped summary did NOT carry the root's taint (so a slash on the root missed it), or if a
derived write with no preceding recall were NOT an orphan, the mechanism would be doing nothing. Neither holds.

Deterministic, zero-dependency (lexical recall, no embedder). MIT. Part of Agora / mnemo.
Run:  python mnemo/probes/autostamp_lineage.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from mnemo import Mnemo


def _is_lb(m, rid):
    by = {x["id"]: x for x in m.items}
    r = by.get(rid)
    return bool(r) and r.get("status") == "active" and Mnemo._is_corroborated(r, by)


def main():
    m = Mnemo(os.path.join(tempfile.mkdtemp(), "autostamp.jsonl"))
    print("=== Auto-stamped lineage: store carries the recall->write edge (MemLineage mechanism) ===\n")

    # a poison admitted with real-looking provenance, made load-bearing by banked credit
    P = m.remember("The billing API disables retries in production.", source={"doc": "vendor-brief-42"})
    m.credit([P], "good", weight=4.0)

    # THE FLOW: the app recalls context, an (untrusted) LLM summarizes it, the app writes the summary back.
    # It writes with derived=True but does NOT thread derived_from (the link the LLM step dropped).
    hits = m.recall("billing API retries production", k=5)
    S = m.remember("Ops summary: retries stay off in prod.", derived=True)   # no derived_from, no source
    by = {x["id"]: x for x in m.items}
    s_taint = by[S].get("taint") or []
    s_parents = by[S].get("derived_from") or []
    m.credit([S], "good", weight=3.0)   # the summary also earns its own standing

    print("recall surfaced:            %s" % [h["id"] for h in hits])
    print("summary auto-stamped parents: %s   (caller passed NONE -- the store inferred them)" % s_parents)
    print("summary inherited taint:      %s   (the root's canonical source rides through)" % s_taint)
    print("summary is an orphan?         %s   (False -> lineage was carried, not lost)\n" % bool(by[S].get("orphan")))

    lb_before = _is_lb(m, S)
    res = m.slash([P], scope="source")            # a correctness signal lands on the poison root
    lb_after = _is_lb(m, S)
    print("t0  summary load-bearing:     %s" % lb_before)
    print("t1  slash([P]) on the root -> revoked %d record(s); summary load-bearing: %s  %s"
          % (res["slashed"], lb_after, "<-- retraction REACHED the auto-stamped summary" if not lb_after else ""))
    m.restore([P], scope="source")
    print("t2  restore([P]) -> summary load-bearing: %s  (reversible)\n" % _is_lb(m, S))

    # contrast: a declared-derived write with NO preceding recall -> nothing to inherit -> orphan (fail-closed)
    m2 = Mnemo(os.path.join(tempfile.mkdtemp(), "b.jsonl"))
    O = m2.remember("Summary with no recall before it.", derived=True)
    m2.credit([O], "good", weight=3.0)
    o_orphan = bool({x["id"]: x for x in m2.items}[O].get("orphan"))
    print("CONTRAST: a derived write with NO preceding recall -> orphan=%s, load-bearing=%s (fail-closed)."
          % (o_orphan, _is_lb(m2, O)))

    # --- self-check (the falsifier) ---
    assert P in s_parents, "auto-stamp failed: the summary did not inherit the recalled parent"
    assert any("vendorbrief" in t for t in s_taint), "taint did not ride through the auto-stamped lineage"
    assert not by[S].get("orphan"), "auto-stamped summary must NOT be an orphan"
    assert lb_before is True and lb_after is False, "retraction must reach the auto-stamped summary"
    assert o_orphan is True, "fail-closed broke: a derived write with no recall must be an orphan"

    print("\nVERDICT: the store carries the recall->write lineage, so a summary the untrusted LLM step 'laundered'")
    print("(dropped the link) still inherits its ancestors' taint and FALLS with a retraction on the root -- the")
    print("caller never had to preserve the link. HONEST SCOPE: this closes the laundered-summary path (Biba-style")
    print("integrity, an application of taint-tracking); it does NOT stop provenance-carrying poison (MINJA) or")
    print("retrieval-geometry attacks (AgentPoison) -- those need content moderation + trust-decay retrieval.")


if __name__ == "__main__":
    main()
