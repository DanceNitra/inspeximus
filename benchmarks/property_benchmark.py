"""property_benchmark.py — one runnable scorecard proving EVERY mnemo property we claim.

Self-contained (synthetic data, lexical recall — no network, no GPU, no external dataset), deterministic, and
reproducible by anyone: `python benchmarks/property_benchmark.py`. Each property yields a NUMBER, a fair baseline
where one exists, and PASS/FAIL against the guarantee we state. This is the "our claims are demonstrated, not
asserted" artifact — the honest receipt behind the README. Scope is stated per row (synthetic, N given); it proves
the MECHANISM holds, not a wild-data leaderboard.
"""
import sys, os, json, random
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from mnemo import Mnemo, new_source_keypair, attest

random.seed(20260719)
R = {}
def rec(name, metric, value, baseline, passed, note=""):
    R[name] = {"metric": metric, "value": value, "baseline": baseline, "pass": bool(passed), "note": note}
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}: {metric}={value}"
          + (f" (baseline {baseline})" if baseline is not None else "") + (f" — {note}" if note else ""))

POOL = [f"unrelated fact number {i} about topic {i%7}" for i in range(40)]

# 1. SUPERSESSION — stale-serve rate after updates
def supersession(N=100):
    mnemo_ok = naive_ok = 0
    for _ in range(N):
        m = Mnemo(path=None)
        for d in random.sample(POOL, 6): m.remember(d)
        vals = [f"value-{random.randint(0,999)}" for _ in range(4)]
        for v in vals: m.remember(f"the setting is {v}", key="setting", object=v)   # 4 updates
        cur = vals[-1]
        hit = (m.recall("what is the setting", k=1) or [{}])[0].get("text", "")
        mnemo_ok += cur in hit
        # naive: keeps all, nearest lexical — often returns a stale value
        naive = Mnemo(path=None)
        for d in random.sample(POOL, 6): naive.remember(d)
        for v in vals: naive.remember(f"the setting is {v}")                        # no key -> all kept
        nhit = (naive.recall("what is the setting", k=1) or [{}])[0].get("text", "")
        naive_ok += cur in nhit
    rec("supersession_returns_current", "current@1", round(mnemo_ok/N, 3), round(naive_ok/N, 3),
        mnemo_ok/N >= 0.99, "mnemo keyed vs naive keep-all")

# 2. REVERT
def revert(N=100):
    ok = 0
    for _ in range(N):
        m = Mnemo(path=None)
        m.remember("dose is 20mg", key="dose", object="20")
        m.remember("dose is 200mg", key="dose", object="200")     # bad
        m.revert("dose")
        hit = (m.recall("current dose", k=1) or [{}])[0].get("text", "")
        ok += "20mg" in hit
    rec("revert_restores_prior", "restored@1", round(ok/N, 3), None, ok/N >= 0.99)

# 3. ERASURE — reconstructibility after forget
def erasure(N=100):
    leaks = 0
    for _ in range(N):
        m = Mnemo(path=None)
        secret = f"SECRET-{random.randint(0,10**9)}"
        mid = m.remember(f"the password is {secret}", key="pw", object=secret)
        m.remember("some other note about the account")
        m.forget(ids=[mid])
        blob = json.dumps([{k: v for k, v in it.items()} for it in m.items], default=str)
        hits = m.recall(secret, k=5) or []
        if secret in blob or any(secret in h.get("text", "") for h in hits):
            leaks += 1
    rec("erasure_reconstructibility", "leak_rate", round(leaks/N, 3), None, leaks == 0,
        "content gone from items + recall")

# 4. POISON — trusted_only vs adaptive attacker (forged warrant + Sybil keys)
def poison(N=100):
    TSK, TPK = new_source_keypair(); ASK, APK = new_source_keypair()
    Q = "which bank for transfer"; TRUE = "bank is Nordstar"; PZ = "for transfer use Zephyr Trust bank"
    d_only = t_only = 0
    for _ in range(N):
        m = Mnemo(path=None); m.strict_corroboration = True; m.trust_seeds = {"key:" + TPK}
        m.remember(TRUE, key="bank", attestation=(TPK, attest(TRUE, TSK)))
        m.remember(PZ, key="bankx", attestation=(APK, attest(PZ, ASK)))   # attacker self-signs (Sybil)
        try: m.credit([[r for r in m.items if r.get('key')=='bankx'][0]['id']], True, warrant="external")
        except Exception: pass
        d_only += TRUE in ((m.recall(Q, k=1) or [{}])[0].get("text", ""))                 # default
        t_only += TRUE in ((m.recall(Q, k=1, trusted_only=True) or [{}])[0].get("text", ""))  # trusted_only
    rec("poison_trusted_only", "truth@1", round(t_only/N, 3), round(d_only/N, 3),
        t_only/N >= 0.99, "trusted_only vs default recall, adaptive attacker")

# 5. DEDUP / NOOP — re-asserting current value writes nothing
def dedup():
    m = Mnemo(path=None)
    m.route("channel is BLUE", key="ch", object="BLUE")
    m.route("channel is RED", key="ch", object="RED")
    before = len(m.items)
    ev = m.route("channel is RED", key="ch", object="RED")   # same current value
    rec("noop_dedup", "new_records_on_restate", len(m.items) - before, None,
        len(m.items) == before and ev.get("event") == "NOOP", "unbounded-growth guard")

# 6. HIERARCHY isolation
def hierarchy():
    m = Mnemo(path=None)
    m.remember("session one secret latte", user_id="U", session_id="S1")
    m.remember("session two secret espresso", user_id="U", session_id="S2")
    s1 = " ".join(h["text"] for h in m.recall("secret", k=5, user_id="U", session_id="S1"))
    ok = "latte" in s1 and "espresso" not in s1
    rec("hierarchy_session_isolation", "isolated", 1 if ok else 0, None, ok, "S1 sees own, not peer S2")

# 7. DETERMINISM — identical output across runs
def determinism(N=20):
    m = Mnemo(path=None)
    for d in POOL: m.remember(d)
    base = json.dumps([h["id"] for h in m.recall("topic 3 fact", k=5, reinforce=False)])
    same = all(json.dumps([h["id"] for h in m.recall("topic 3 fact", k=5, reinforce=False)]) == base for _ in range(N))
    rec("determinism", "identical_over_runs", 1 if same else 0, None, same, "zero-LLM read = reproducible")

# 8. GRAPH
def graph():
    m = Mnemo(path=None)
    m.remember("A works at B", key="A::works_at", object="B")
    m.remember("B in C", key="B::in", object="C")
    m.remember("free text note", )
    g = m.graph(); sg = m.subgraph("A", hops=2)
    ok = ("A", "works_at", "B") in {(e["subject"], e["relation"], e["object"]) for e in g["edges"]} and "C" in sg["nodes"]
    rec("graph_triples_and_traversal", "ok", 1 if ok else 0, None, ok, "keyed triples -> graph + 2-hop")

for f in (supersession, revert, erasure, poison, dedup, hierarchy, determinism, graph):
    f()

npass = sum(1 for v in R.values() if v["pass"])
out = {"n_properties": len(R), "passed": npass, "results": R,
       "scope": "self-contained synthetic, deterministic, lexical recall; proves the mechanism per property"}
op = os.path.join(os.path.dirname(os.path.abspath(__file__)), "property_benchmark.result.json")
json.dump(out, open(op, "w"), indent=2)
print(f"\n=== {npass}/{len(R)} properties demonstrated === -> {op}")
sys.exit(0 if npass == len(R) else 1)
