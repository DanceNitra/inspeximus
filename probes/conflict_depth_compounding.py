"""COMPOUNDING-LAW probe: does the penalty for keeping superseded (stale) facts grow GEOMETRICALLY with
reasoning hop-depth?

Setup (controlled, synthetic — made-up entity tokens so the LLM CANNOT answer from parametric knowledge and
MUST read the store): a chain E0 --r1--> E1 --r2--> ... --rH--> EH. Every edge was UPDATED once, so the
CORRECT traversal follows the LATEST value at each hop. Question nests the relations:
"the rH of the r(H-1) of ... of E0?"  ->  answer = EH (latest chain end).

Three context conditions given to the SAME answerer LLM (instructed to prefer the most recent fact — a
STEELMAN for accumulate):
  1. supersession  : only the H latest edge-facts (what inspeximus's keyed store yields).
  2. accumulate    : the H latest + H stale ("Earlier, ... was E_old") facts on the SAME chain edges
                     (what a store that keeps superseded facts yields). Stale targets are wrong branches.
  3. distractor    : the H latest + H IRRELEVANT latest-facts about unrelated entities. SAME context size
                     as accumulate (2H facts) but NO stale conflict on the chain -> isolates conflict from length.

THE LAW UNDER TEST: accuracy(accumulate) ~ q^H (each hop independently risks following the stale branch, so
the whole chain is correct only if every hop picks latest), while supersession & distractor stay near-flat.
=> the accumulate-vs-supersession GAP WIDENS with H. FALSIFIER: if accumulate does NOT decay faster than
distractor (i.e. the gap is flat in H), the effect is context-length, not conflict-compounding -> KILL.
"""
import json, os, random, re, sys, time, urllib.request
from pathlib import Path

random.seed(20260716)
if not os.environ.get("LLM_BASE_URL"):
    # Relative to this file, not to the author's home directory. See #1: a public probe that
    # reads C:/Users/<someone>/ is a probe only that someone can run.
    _here = os.path.dirname(os.path.abspath(__file__))
    _envf = os.environ.get("AGORA_ENV_FILE") or next(
        (c for c in (os.path.join(_here, "..", ".env"),
                     os.path.join(_here, "..", "server", ".env"),
                     os.path.join(os.getcwd(), "server", ".env")) if os.path.exists(c)), "")
    if os.path.exists(_envf):
        for line in Path(_envf).read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
BASE = (os.environ.get("LLM_BASE_URL") or os.environ["AGORA_API_BASE_URL"]).rstrip("/")
KEY = os.environ.get("LLM_API_KEY") or os.environ["AGORA_API_KEY"]
MODEL = os.environ.get("LLM_MODEL") or os.environ.get("AGORA_LLM_MODEL_CHEAP", "deepseek-v4-flash")
OUT = Path(__file__).with_name("conflict_depth_compounding_result.json")
N = int(sys.argv[1]) if len(sys.argv) > 1 else 30
HOPS = [int(x) for x in (sys.argv[2].split(",") if len(sys.argv) > 2 else ["1", "2", "3", "4"])]

RELS = ["mentor", "successor", "patron", "hometown", "guild", "sponsor", "rival", "heir", "envoy", "warden"]
_CV = [c + v for c in "kzvtrbmngpsdl" for v in ("a", "e", "i", "o", "u", "or", "an", "el", "ix", "un")]


def token(used):
    while True:
        t = random.choice(_CV).capitalize() + random.choice(_CV) + random.choice(("th", "n", "x", "r", "l", "s"))
        if t not in used:
            used.add(t); return t


SYS_LABELED = ("You answer strictly from the given facts. When two facts conflict, the MOST RECENT one (the one "
               "marked as the latest / current update) is correct; ignore the earlier one. Follow the chain of "
               "relations in the question step by step. Reply with ONLY the single name that answers it, nothing else.")
SYS_ORDERED = ("You answer strictly from the given facts. The facts are listed in chronological order. If two facts "
               "state a value for the SAME entity and relation, the one appearing LATER in the list is the current "
               "value and overrides the earlier one. Follow the chain of relations in the question step by step. "
               "Reply with ONLY the single name that answers it, nothing else.")


def _post(messages, mt=512):   # deepseek-v4-flash is a reasoning model: a tight cap truncates it mid-think
    body = json.dumps({"model": MODEL, "max_tokens": mt, "temperature": 0.0, "messages": messages}).encode()
    req = urllib.request.Request(BASE + "/chat/completions", data=body,
                                 headers={"Authorization": "Bearer " + KEY, "content-type": "application/json"})
    for a in range(4):
        try:
            r = json.load(urllib.request.urlopen(req, timeout=120))
            return (r["choices"][0]["message"]["content"] or "").strip()
        except Exception as e:
            if a == 3:
                return f"__ERR__{e}"
            time.sleep(2 * (a + 1))


def build(h):
    """Return (question, answer_token, list of (cond, sys, facts, shuffle?))."""
    used = set()
    ents = [token(used) for _ in range(h + 1)]           # E0..Eh (latest chain)
    rels = random.sample(RELS, h)
    latest_lab = [f"As of the latest update, the {rels[i]} of {ents[i]} is {ents[i + 1]}." for i in range(h)]
    stale_lab = [f"Earlier, the {rels[i]} of {ents[i]} was {token(used)}." for i in range(h)]   # wrong branch, tagged
    distr = [f"As of the latest update, the {random.choice(RELS)} of {token(used)} is {token(used)}."
             for _ in range(h)]                          # irrelevant, same count as stale
    # untagged variant: recency recoverable ONLY from list order (old_i then new_i per edge, in hop order)
    ordered = []
    for i in range(h):
        ordered.append(f"The {rels[i]} of {ents[i]} is {token(used)}.")     # old (wrong) value, written first
        ordered.append(f"The {rels[i]} of {ents[i]} is {ents[i + 1]}.")     # new (correct) value, written later
    latest_plain = [f"The {rels[i]} of {ents[i]} is {ents[i + 1]}." for i in range(h)]
    # nested question: the rH of the r(H-1) of ... of E0
    q = ents[0]
    for i in range(h):
        q = f"the {rels[i]} of {q}"
    q = f"What is {q}? Give only the name."
    conds = [
        ("supersession", SYS_LABELED, latest_plain[:], True),          # inspeximus: only current values
        ("accumulate_labeled", SYS_LABELED, latest_lab + stale_lab, True),   # steelman: recency tagged
        ("accumulate_ordered", SYS_ORDERED, ordered, False),           # realistic: recency = list order only
        ("distractor", SYS_LABELED, latest_lab + distr, True),         # length control (2H, no conflict)
    ]
    return q, ents[-1], conds


def run():
    rows = []
    CONDS = ["supersession", "accumulate_labeled", "accumulate_ordered", "distractor"]
    for h in HOPS:
        acc = {c: 0 for c in CONDS}
        n_ok = 0
        for _ in range(N):
            q, ans, conds = build(h)
            for cond, sys_p, facts, do_shuffle in conds:
                fl = facts[:]
                if do_shuffle:
                    random.shuffle(fl)
                p = _post([{"role": "system", "content": sys_p},
                           {"role": "user", "content": "Facts:\n" + "\n".join(fl) + f"\n\n{q}"}])
                if not p.startswith("__ERR__") and ans.lower() in p.lower():
                    acc[cond] += 1
            n_ok += 1
        row = {"hops": h, "n": n_ok, **{c: round(acc[c] / n_ok, 3) for c in CONDS},
               "gap_super_minus_ordered": round((acc["supersession"] - acc["accumulate_ordered"]) / n_ok, 3)}
        rows.append(row)
        print(f"H={h}: super={row['supersession']:.0%} distractor={row['distractor']:.0%} "
              f"accum_labeled={row['accumulate_labeled']:.0%} accum_ordered={row['accumulate_ordered']:.0%}  "
              f"GAP(super-ordered)={row['gap_super_minus_ordered']:+.0%}")
        OUT.write_text(json.dumps({"ok": True, "model": MODEL, "N": N, "rows": rows}, indent=1), encoding="utf-8")
    return rows


try:
    run()
except Exception as e:
    import traceback
    OUT.write_text(json.dumps({"ok": False, "error": repr(e), "tb": traceback.format_exc()}, indent=1), encoding="utf-8")
    print("ERR", e)
