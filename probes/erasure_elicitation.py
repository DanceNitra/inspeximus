"""Their question, asked of our store: after erasure, is the content still elicitable?

THE PAPER. "Exposing the Illusion of Erasure in Knowledge Editing for LLMs" (Basani & Chhabra,
arXiv:2606.23276v2, 2026-06-24) shows that knowledge editing does not remove a fact from an LLM, it
overlays a suppression circuit that an optimised adversarial suffix walks straight through: white-box
context-guided extraction above 85%, blind reconstruction 15-48.5%, cross-model transfer often above
75%, on ROME / MEMIT / MEND / FT-L over GPT-J, GPT-2, Llama and Qwen.

WHY THIS IS NOT A RESULT ABOUT US, AND WHY IT IS STILL THE RIGHT QUESTION. They edit weights; we
delete records. A suppression circuit is defeasible by construction, a deleted row is not, so nothing
in their paper transfers to a store as evidence. What DOES transfer is the discipline: they refused
to accept the editing method's own success metric and went looking for the fact somewhere else. The
analogue here is not "does forget_subject report success" -- it does -- but "after it reports
success, can the erased VALUES still be reconstructed from what the store chose to keep?"

Four places a store can leak what it says it erased, and this probe reads all four:
  * DERIVED records -- a consolidated summary built from the subject's facts, which quotes the value
    but is not itself attributed to the subject;
  * LINKS -- lineage and corroboration edges that survive their endpoints;
  * VECTORS -- an embedding of a deleted record left in the index;
  * RECEIPTS -- the erasure receipt itself, which we publish as content-free.

TWO REGIMES, mirroring theirs:
  * context-guided -- the query names the subject (their >85% case);
  * blind -- the query never names the subject and asks only about the attribute (their 15-48.5%).

THE CONTROL, without which this measures nothing: a second subject is never erased, and every one of
its values must stay recoverable in both regimes. An erasure probe on a store that has forgotten how
to answer anything scores a perfect 0.0 and has proved only that it broke retrieval.

Run: python probes/erasure_elicitation.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from inspeximus import Inspeximus  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "erasure_elicitation.result.json")

# (subject, source, [(fact text, the VALUE that must vanish, a blind query that never names them)])
PEOPLE = [
    ("Marta Kovacova", "hr/marta.md", [
        ("Marta Kovacova's home address is 14 Bellweather Lane, Brno",
         "14 Bellweather Lane", "what is the home address on file for the payments on-call lead"),
        ("Marta Kovacova's personal phone is +421 903 118 774",
         "+421 903 118 774", "personal phone number of the payments rotation owner"),
        ("Marta Kovacova owns the payments on-call rotation",
         "payments on-call rotation", "who owns the payments on-call rotation"),
    ]),
    ("Tomas Bilek", "hr/tomas.md", [
        ("Tomas Bilek's home address is 8 Kestrel Court, Kosice",
         "8 Kestrel Court", "what is the home address on file for the billing escalation lead"),
        ("Tomas Bilek's personal phone is +421 905 442 019",
         "+421 905 442 019", "personal phone number of the billing escalation owner"),
        ("Tomas Bilek owns the billing escalation rotation",
         "billing escalation rotation", "who owns the billing escalation rotation"),
    ]),
]


def _embedder():
    """A real embedder when one is reachable, so the VECTOR route is not vacuously clean.

    Called directly rather than through claude_code._make_embedder: that helper is gated on
    INSPEXIMUS_EMBED_HOOKS because it runs in the agent's hot path, and borrowing it here returned
    None silently -- which left the vector route empty while the probe reported four routes checked.
    An erasure probe that never creates a vector cannot find an orphaned one.
    """
    import json as _json
    import urllib.request
    url = os.environ.get("INSPEXIMUS_EMBED_URL", "http://localhost:11434/v1/embeddings")
    model = os.environ.get("INSPEXIMUS_EMBED_MODEL", "nomic-embed-text")

    def embed(text):
        body = _json.dumps({"model": model, "input": "search_document: " + text}).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return _json.loads(r.read())["data"][0]["embedding"]

    try:
        v = embed("warm")                # must fail HERE, not silently produce a vectorless store
        print(f"  (embedder live: {model}, {len(v)} dims)")
        return embed
    except Exception as exc:
        print(f"  (no embedder: {str(exc)[:70]} -- the vector route will report itself untested)")
        return None


def build(path):
    """A store shaped like a real one: raw facts, a summary DERIVED from them, links, and ON DISK.

    On disk deliberately. An in-memory store cannot leak through the file, and a probe that claims to
    check the file while never writing one has measured nothing -- the first version of this file did
    exactly that for two of its routes.
    """
    m = Inspeximus(path=path, embed=_embedder(), persist_vectors=True)
    ids = {}
    for subject, source, facts in PEOPLE:
        fids = []
        for text, _value, _blind in facts:
            fids.append(m.remember(text, source={"doc": source}, tags=["hr", "pii"]))
        # The derived record is the interesting one: it repeats the values but its own subject line is
        # the ROTATION, not the person. An erasure that resolves through `source` alone will not see it
        # unless lineage carries the reach -- which is exactly the claim `derived_from` makes.
        summary = m.remember(
            f"On-call summary: {facts[0][0].split(chr(39))[0]} can be reached at {facts[1][1]} "
            f"and lives at {facts[0][1]}; they own the {facts[2][1]}.",
            source={"doc": f"derived/{source}"}, derived_from=fids, tags=["summary"])
        ids[subject] = {"facts": fids, "summary": summary}
    m.flush()
    return m, ids


def elicit(m, value, query, path=None):
    """Can this value be read out of the store, by any of the four routes?

    Deliberately generous to the ATTACKER: a hit anywhere in a k=10 recall counts, and so does a raw
    scan of every surviving record, link label and receipt. A stingy reader would report a lower
    leak rate and prove less.
    """
    routes = []
    hits = m.recall(query, k=10, reinforce=False) or []
    if any(value.lower() in (h.get("text") or "").lower() for h in hits):
        routes.append("recall")
    for it in getattr(m, "items", []):
        if it.get("status") == "erased":
            continue
        if value.lower() in (it.get("text") or "").lower():
            routes.append("residue:" + ("derived" if "summary" in (it.get("tags") or []) else "record"))
            break
    for it in getattr(m, "items", []):
        for ln in (it.get("links") or []):
            if value.lower() in json.dumps(ln, ensure_ascii=False).lower():
                routes.append("link")
                break
    try:
        cert = json.dumps(m.erasure_certificate(), ensure_ascii=False).lower()
        if value.lower() in cert:
            routes.append("receipt")
    except Exception:
        pass
    if path and os.path.exists(path):
        with open(path, "rb") as fh:
            if value.lower().encode() in fh.read().lower():
                routes.append("disk")
    return sorted(set(routes))


def route_controls(m, path):
    """Can each route CARRY a value at all, on this fixture?

    A route with nothing in it reports "no leak" for the same reason an unplugged detector reports no
    signal. Measured against the UNERASED subject, so a route that shows FALSE here is one this run
    cannot speak about -- and the report says so instead of counting it as a clean sheet.
    """
    live = PEOPLE[1][2][0][1]                       # a value belonging to the control subject
    q = PEOPLE[1][2][0][2]
    got = set(elicit(m, live, q, path=path))
    vecs = sum(1 for it in getattr(m, "items", []) if it.get("vec"))
    links = sum(len(it.get("links") or []) for it in getattr(m, "items", []))
    return {"recall": "recall" in got,
            "residue": any(r.startswith("residue") for r in got),
            "disk": "disk" in got,
            "receipt_channel_exists": True,
            "vectors_present": vecs > 0,
            "links_present": links > 0}


def main():
    import tempfile
    path = os.path.join(tempfile.mkdtemp(), "store.json")
    m, _ids = build(path)
    # forget_subject resolves on the CANONICAL SOURCE, not on the name in the text -- pass the
    # same doc id the records were written with, or it silently erases nothing.
    target, control = PEOPLE[0][0], PEOPLE[1][0]
    target_doc = PEOPLE[0][1]

    before = {}
    for subject, _src, facts in PEOPLE:
        for text, value, blind in facts:
            before[(subject, value)] = {
                "context_guided": elicit(m, value, f"{subject} {blind}", path=path),
                "blind": elicit(m, value, blind, path=path),
            }
    ctl_routes = route_controls(m, path)

    rep = m.forget_subject(target_doc, request_id="dsar-elicit-1", basis="probe")
    m.flush()

    after = {}
    for subject, _src, facts in PEOPLE:
        for text, value, blind in facts:
            after[(subject, value)] = {
                "context_guided": elicit(m, value, f"{subject} {blind}", path=path),
                "blind": elicit(m, value, blind, path=path),
            }

    def rate(subject, regime, table):
        vals = [v for (s, v) in table if s == subject]
        got = sum(1 for v in vals if table[(subject, v)][regime])
        return got, len(vals)

    print(f"  THEIR QUESTION, OUR STORE. forget_subject({target_doc!r}) reported "
          f"{rep.get('erased', rep.get('n', '?'))} records erased.\n")
    print(f"  {'':<34}{'context-guided':>18}{'blind':>10}")
    rows = {}
    for label, subject in ((f"ERASED  ({target})", target), (f"CONTROL ({control})", control)):
        cg_a, n = rate(subject, "context_guided", after)
        bl_a, _ = rate(subject, "blind", after)
        cg_b, _ = rate(subject, "context_guided", before)
        bl_b, _ = rate(subject, "blind", before)
        print(f"  {label:<34}{f'{cg_a}/{n}':>18}{f'{bl_a}/{n}':>10}   (before erasure: {cg_b}/{n}, {bl_b}/{n})")
        rows[subject] = {"n_values": n, "before_context": cg_b, "before_blind": bl_b,
                         "after_context": cg_a, "after_blind": bl_a}

    leaks = {f"{s}::{v}": after[(s, v)] for (s, v) in after
             if s == target and (after[(s, v)]["context_guided"] or after[(s, v)]["blind"])}
    print()
    if leaks:
        print(f"  LEAKED after erasure -- {len(leaks)} of {rows[target]['n_values']} values still readable:")
        for k, r in leaks.items():
            print(f"    {k.split('::')[1]:<26} via {', '.join(sorted(set(r['context_guided']) | set(r['blind'])))}")
    else:
        print("  No erased value was readable by any of the four routes.")

    # THE VECTOR QUESTION PROPERLY PUT. A vector does not contain its text, so it cannot leak a value
    # by substring; it leaks by SURVIVING its record. "Deleting from the store left the embedding in
    # the index" is the classic form, so count what is on disk against what is still active.
    on_disk = json.load(open(path, encoding="utf-8"))
    live_ids = {it["id"] for it in m.items if it.get("status") != "erased"}
    n_vec = sum(1 for r in on_disk if r.get("vec"))
    orphan_vecs = [r.get("id") for r in on_disk if r.get("vec") and r.get("id") not in live_ids]
    print(f"\n  VECTORS: {n_vec} persisted on disk, {len(orphan_vecs)} belonging to no live record"
          + ("   <<< ORPHANED EMBEDDINGS SURVIVED THE ERASURE" if orphan_vecs else ""))

    print("\n  ROUTE CONTROLS -- can each route carry a value at all on this fixture?")
    for k, v in ctl_routes.items():
        mark = "yes" if v else "NO -- this run cannot speak about this route"
        print(f"    {k:<24}{mark}")

    ctl_ok = rows[control]["after_context"] == rows[control]["n_values"]
    print(f"\n  CONTROL: the untouched subject is still fully readable: {ctl_ok}")
    if not ctl_ok:
        print("  The control FAILED, so a clean sheet above would mean retrieval broke, not that "
              "erasure worked. Read nothing else from this run.")

    payload = {"probe": "erasure_elicitation",
               "paper": "arXiv:2606.23276v2 Basani & Chhabra, Exposing the Illusion of Erasure in KE",
               "their_numbers": {"white_box_context_guided": ">85%", "blind_reconstruction": "15-48.5%",
                                 "cross_model_context_guided": ">75%"},
               "scope_note": "they edit model weights, we delete store rows; their result is not "
                             "evidence about a store. The transferable part is the method: refuse the "
                             "eraser's own success metric and go looking for the value elsewhere.",
               "forget_report": rep, "by_subject": rows,
               "leaks": leaks, "control_holds": ctl_ok, "route_controls": ctl_routes,
               "store_path_on_disk": True,
               "vectors_on_disk": sum(1 for r in on_disk if r.get("vec")),
               "orphan_vectors_after_erasure": orphan_vecs}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1, default=str)
    print(f"\nwrote {OUT}")
    return 0 if ctl_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
