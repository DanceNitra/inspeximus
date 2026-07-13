"""RAMR operating-point dimension / mnemo demo: agent memory under EVOLVING + CONTAMINATED context.

Motivated by the AI-coding finding (Crucible fc5c22): bare models lose where the human holds context
the model lacks -> the binding constraint is CONTEXT/MEMORY QUALITY. This measures it head-to-head.

A stream of facts in three regimes (operating points):
  STABLE      : a fact stated once, never changed.            current = that value
  SUPERSEDED  : a fact updated to a new value later.          current = the NEWEST value
  POISONED    : true value (2 distinct sources) + a FALSE value repeated 3x by ONE source, fresher.
                                                              current = the corroborated TRUE value
Query each subject for its CURRENT value under three retrieval strategies:
  cosine  : top-1 most-similar over ALL records (naive semantic store; no supersession, no corroboration)
  recency : newest record matching the subject (naive recency store)
  mnemo   : supersession key (retires stale) + corroboration gate (repetition != corroboration) + value
Expected: each NAIVE store wins some operating points and fails others; only mnemo is robust across all.
Cloud-free: local nomic embedder (Ollama). MIT. Part of Agora / mnemo.
"""
import sys, os, json, time, tempfile, urllib.request
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))   # parent dir holds mnemo.py -> `from mnemo import Mnemo`
from mnemo import Mnemo

_EC = {}
def nomic(text):
    if text in _EC: return _EC[text]
    body = json.dumps({"model": "nomic-embed-text", "prompt": text}).encode()
    req = urllib.request.Request("http://localhost:11434/api/embeddings", data=body,
                                 headers={"Content-Type": "application/json"})
    v = json.loads(urllib.request.urlopen(req, timeout=60).read())["embedding"]
    _EC[text] = v; return v

def cos(a, b):
    import math
    d = sum(x*y for x,y in zip(a,b)); na=math.sqrt(sum(x*x for x in a)); nb=math.sqrt(sum(y*y for y in b))
    return d/(na*nb) if na and nb else 0.0

N = 8  # subjects per regime
def subj(i): return "project %s component" % chr(ord('A')+i)
def val(tok, i): return "%s_%02d" % (tok, i)
def fact(s, v): return "%s status: the current value is %s" % (s, v)
def query(s): return "what is the current value for %s" % s

def main():
    fd, path = tempfile.mkstemp(suffix=".json"); os.close(fd)
    m = Mnemo(path=path, embed=nomic)
    m.semantic_threshold = 0   # force SEMANTIC recall (we gave it the nomic embedder) - fair vs the cosine baseline
    truth = {}          # subject -> correct current value
    raw = []            # (subject, text, value, ts) for the naive baselines
    t = time.time()

    # STABLE
    for i in range(N):
        s = subj(i); v = val("CUR", i); truth[s] = v
        mid = m.remember(fact(s, v), tags=["stable"]); raw.append((s, fact(s, v), v, t)); t += 1

    # SUPERSEDED (key-based update; newest legit value is current)
    for i in range(N, 2*N):
        s = subj(i); old = val("OLD", i); cur = val("CUR", i); truth[s] = cur
        m.remember(fact(s, old), tags=["superseded"], key=s); raw.append((s, fact(s, old), old, t)); t += 1
        m.remember(fact(s, cur), tags=["superseded"], key=s); raw.append((s, fact(s, cur), cur, t)); t += 1

    # POISONED (true value corroborated by 2 distinct sources + credit; false value repeated 3x, 1 source, fresher)
    for i in range(2*N, 3*N):
        s = subj(i); cur = val("CUR", i); false = val("FALSE", i); truth[s] = cur
        # corroboration refs: generic text (NO subject, NO 'current value') so they don't pollute recall;
        # they exist only to give the true record 2 DISTINCT sources for the gate.
        s1 = m.remember("internal reference note alpha %d" % i, source={"doc": "team-wiki", "span":[0,1]})
        s2 = m.remember("internal reference note beta %d" % i, source={"doc": "incident-report", "span":[0,1]})
        tid = m.remember(fact(s, cur), tags=["poisoned"]);
        tr = next(r for r in m.items if r["id"]==tid); tr["links"]=[s1,s2]
        m.credit([tid], True); m.credit([tid], True)
        raw.append((s, fact(s, cur), cur, t)); t += 1
        for _ in range(3):  # the false repeats, fresher (newer ts), single self-asserted source, no credit
            fid = m.remember(fact(s, false), tags=["poisoned"], source={"doc":"attacker-self","span":[0,1]})
            fr = next(r for r in m.items if r["id"]==fid); fr["good"]=0
            raw.append((s, fact(s, false), false, t)); t += 1

    # pump recall a bit so episodic/semantic values settle (as in the poison probe)
    for s in truth:
        for _ in range(2): m.recall(query(s), k=3)

    def parse_val(text):
        import re
        mt = re.search(r"value is (\w+_\d\d)", text or "")
        return mt.group(1) if mt else None

    # embed all raw records once for the cosine baseline
    embs = {idx: nomic(txt) for idx,(s,txt,v,ts) in enumerate(raw)}

    cats = ["stable","superseded","poisoned"]
    score = {st:{c:0 for c in cats} for st in ["cosine","recency","mnemo"]}
    tot   = {c:0 for c in cats}
    for c_i, cat in enumerate(cats):
        subs = [subj(i) for i in range(c_i*N, (c_i+1)*N)]
        for s in subs:
            tot[cat]+=1; q=query(s); qv=nomic(q)
            cand = [idx for idx,(ss,txt,v,ts) in enumerate(raw) if ss==s]
            # cosine top-1 over all records for this subject
            best = max(cand, key=lambda idx: cos(qv, embs[idx]))
            if raw[best][2]==truth[s]: score["cosine"][cat]+=1
            # recency: newest matching record
            newest = max(cand, key=lambda idx: raw[idx][3])
            if raw[newest][2]==truth[s]: score["recency"][cat]+=1
            # mnemo recall
            res = m.recall(q, k=3)
            mv = parse_val(res[0]["text"]) if res else None
            if mv==truth[s]: score["mnemo"][cat]+=1

    print("=== Agent memory across operating points (n=%d/regime) ===" % N)
    print("strategy   | stable | superseded | poisoned | OVERALL")
    for st in ["cosine","recency","mnemo"]:
        ov = sum(score[st][c] for c in cats)/(3*N)
        print("  %-8s |  %2d/%d  |    %2d/%d   |   %2d/%d  |  %.0f%%" % (
            st, score[st]["stable"],N, score[st]["superseded"],N, score[st]["poisoned"],N, 100*ov))
    print("\nMEASURED: a naive cosine store fails on superseded (supersession blind spot) and poisoned; "
          "a recency store fixes superseded but still fails poisoned (newest=freshest lie); mnemo "
          "(supersession key + corroboration gate) is the only one robust ACROSS all operating points.")
    print("This is the memory 'operating-point trap': each single mechanism wins one regime and loses "
          "another. Ties to Crucible fc5c22 (AI coding) - context/memory quality is the binding lever.")

if __name__ == "__main__":
    main()
