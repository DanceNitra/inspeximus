"""distill_and_remember_probe.py — the OPTIONAL LLM capture half (deterministic orchestration).

mnemo core stays zero-dep/zero-LLM: the caller injects a `distiller(prompt, text)` (any LLM / subagent) that runs
Mnemo.DISTILL_PROMPT and returns JSON; mnemo parses it and stores each item DETERMINISTICALLY — decisions via
remember_decision (topic-keyed supersession + revert), facts via remember. This probe uses a MOCK distiller (no
LLM) to lock the orchestration + fail-open behavior; a live subagent distiller is tested separately.
Asserts:
  1. mnemo passes its DISTILL_PROMPT to the distiller.
  2. decisions + facts from the returned JSON are stored (decision -> remember_decision, fact -> semantic).
  3. malformed items (empty text, non-dict) are skipped, not crashed.
  4. a distilled decision's topic gives keyed supersession (new decision retires old; one active per topic).
  5. fail-open: a distiller that raises, or returns unparseable output, returns a clean error dict (no crash).
"""
import sys, json
sys.path.insert(0, ".")
from mnemo import Mnemo

FAILS = []
def check(n, c):
    print(f"  [{'OK ' if c else 'XXX'}] {n}")
    if not c: FAILS.append(n)

m = Mnemo(path=None)
TRANSCRIPT = ("We debated the next release. Decision: ship 1.16 with distill_and_remember. "
              "Also noted: nomic-embed-text needs search_document/search_query prefixes. "
              "Then some off-topic chit-chat about lunch.")
seen_prompt = {}
def mock(prompt, text):
    seen_prompt["p"] = prompt
    return json.dumps({"items": [
        {"kind": "decision", "text": "ship 1.16 with distill_and_remember", "topic": "release::1.16", "because": "closes capture gap", "support": "ship 1.16 with distill_and_remember"},
        {"kind": "fact", "text": "nomic-embed-text needs search_document/search_query prefixes", "topic": "embed::nomic", "support": "nomic-embed-text needs search_document/search_query prefixes"},
        {"kind": "decision", "text": "", "because": "empty -> skip", "support": "We debated the next release"},
        "not-a-dict -> skip",
    ]})
r = m.distill_and_remember(TRANSCRIPT, mock)
check("1 mnemo passed its DISTILL_PROMPT", "distill" in seen_prompt.get("p", "").lower())
check("2 decision + fact stored (2 captured)", r["captured"] == 2 and r["decisions"] == 1 and r["facts"] == 1)
check("2b decision recallable", "1.16" in (m.recall("what to ship", k=1) or [{}])[0].get("text", ""))
check("2c fact recallable", "nomic" in (m.recall("nomic prefixes", k=1) or [{}])[0].get("text", ""))
check("3 malformed items skipped (not 4)", r["captured"] == 2)

# --- correctness gate: an item whose `support` quote is NOT verbatim in the transcript is a hallucination -> DROP
def hallu(prompt, text):
    return json.dumps({"items": [
        {"kind": "decision", "text": "we chose Postgres over SQLite", "topic": "vendor::db", "because": "invented", "support": "we all agreed Postgres is the winner"},  # support NOT in transcript
        {"kind": "decision", "text": "ship 1.16 with distill_and_remember", "topic": "release::1.16b", "support": "ship 1.16 with distill_and_remember"},                     # support IS in transcript
    ]})
mg = Mnemo(path=None)
rg = mg.distill_and_remember(TRANSCRIPT, hallu)
check("G1 hallucinated item (support not in source) DROPPED", rg["dropped"] == 1 and rg["captured"] == 1)
check("G2 supported item survives the gate", rg["decisions"] == 1)
check("G3 hallucinated decision never entered the store", not any("Postgres" in (x.get("text") or "") for x in mg.items))
check("G4 require_support=False keeps both (gate opt-out)", mg.distill_and_remember(TRANSCRIPT, hallu, require_support=False)["captured"] == 2)
check("G5 too-short support (<12 non-space chars) is not accepted as grounding",
      mg._support_ok("ship 1.16", TRANSCRIPT) is False and mg._support_ok("ship 1.16 with distill_and_remember", TRANSCRIPT) is True)

m.distill_and_remember("we now defer 1.16 for more levers", lambda p, t: json.dumps({"items": [{"kind": "decision", "text": "defer 1.16", "topic": "release::1.16", "because": "more levers", "support": "we now defer 1.16 for more levers"}]}))
active = [x for x in m.items if x.get("key") == "decision::release::1.16" and x.get("status") == "active"]
check("4 distilled decision topic supersedes (one active)", len(active) == 1 and "defer" in active[0].get("text", ""))

check("5a fail-open on distiller exception", m.distill_and_remember("x", lambda p, t: 1 / 0).get("error") == "distiller_failed")
check("5b fail-open on unparseable output", m.distill_and_remember("x", lambda p, t: "not json").get("error") == "unparseable_distiller_output")
check("5c accepts a dict/list directly (not only str)", m.distill_and_remember("a direct dict works fine here", lambda p, t: {"items": [{"kind": "fact", "text": "direct dict works", "support": "a direct dict works fine here"}]})["captured"] == 1)

print(f"\n{'ALL PASS' if not FAILS else 'FAILED: ' + ', '.join(FAILS)}")
sys.exit(1 if FAILS else 0)
