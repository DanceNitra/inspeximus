"""retrieval_exposure_coverage_probe.py — does retrieval-exposure predict earned-outcome coverage?

Tests icophy's cross-framework hypothesis (DeepSeek-V3 #1462): "the fastest way to raise the earned-outcome
coverage of memory is to close retrieval loops — a memory that is retrieved and acted on generates a natural
outcome signal; unused memory stays dark." Measured on Agora's OWN live store (8 dungeon-agent mnemo files).

FINDING (verified 2nd-way + per-store; the AUDIT is the point):
 1. STRUCTURAL, not correlational: 0 of the earned-outcome (good>0) records were NEVER retrieved — earned
    outcome is 100% downstream of retrieval, across all 8 stores. So the naive "retrieval predicts coverage"
    correlation is TAUTOLOGICAL here (credit() only ever fires on a recalled memory). Disclosed, not hidden.
 2. NON-TAUTOLOGICAL lever = the CONVERSION rate: retrieval is necessary but converts to an earned outcome
    only ~28% of the time (16-62% across agents). Exposure raises the ceiling; conversion is the bottleneck.
 3. Broader corroboration signal (has non-retrieval paths: >=2 links / graduation, so NOT tautological):
    exposed ~1.2x more corroborated (80-92% vs 71-73%), robust across all 8 stores. Real but modest.

Exposure proxy: last_access > ts + 1s (last_access is set ONLY at creation and in recall() -> a value above
creation-time means recalled at least once). Age confound noted: exposure concentrates in the oldest tertile.

RUN: python mnemo/probes/retrieval_exposure_coverage_probe.py
"""
import json, glob, sys
sys.stdout.reconfigure(errors="replace")

STORES = "agora-game-server/.agent_memory/*.json"

def load(f):
    d = json.load(open(f, encoding="utf-8"))
    return d.get("items") if isinstance(d, dict) else d

def exposed(r):
    la, ts = r.get("last_access"), r.get("ts")
    return la is not None and ts is not None and la > ts + 1.0

def earned(r):
    return float(r.get("good", 0) or 0) > 0

def corroborated(r):
    g, b = float(r.get("good", 0) or 0), float(r.get("bad", 0) or 0)
    return (g > 0 and g >= b) or r.get("mtype") == "semantic" or len(set(r.get("links") or [])) >= 2

def main():
    g_active = g_exposed = g_earned = g_earned_unexp = 0
    per = {}
    for f in sorted(glob.glob(STORES)):
        ag = f.replace("\\", "/").split("/")[-1].replace(".json", "")
        act = [r for r in load(f) if r.get("status") == "active"]
        ex = [r for r in act if exposed(r)]
        ue = [r for r in act if not exposed(r)]
        ea = [r for r in act if earned(r)]
        g_active += len(act); g_exposed += len(ex); g_earned += len(ea)
        g_earned_unexp += sum(1 for r in ea if not exposed(r))
        conv = sum(earned(r) for r in ex) / max(1, len(ex))
        ce = sum(corroborated(r) for r in ex) / max(1, len(ex))
        cu = sum(corroborated(r) for r in ue) / max(1, len(ue))
        per[ag] = (len(act), len(ex), conv, ce, cu)
    print(f"GLOBAL active={g_active} exposed={g_exposed} earned(good>0)={g_earned} earned_UNexposed={g_earned_unexp}")
    print(f"  TAUTOLOGY CHECK: earned_UNexposed={g_earned_unexp} (0 => earned outcome is 100% downstream of retrieval)")
    print(f"  retrieval->earned conversion = {g_earned/max(1,g_exposed)*100:.1f}%  (necessary, NOT sufficient)")
    print("\nPER-STORE (active, exposed, conv%, corr|exposed%, corr|unexposed%)")
    for ag, (a, e, cv, ce, cu) in sorted(per.items()):
        print(f"  {ag:12s} act={a:5d} exp={e:4d} conv={cv*100:5.1f}%  corrExp={ce*100:5.1f}% corrUnexp={cu*100:5.1f}%")
    out = {"global": {"active": g_active, "exposed": g_exposed, "earned": g_earned,
                      "earned_unexposed": g_earned_unexp,
                      "conversion_pct": round(g_earned/max(1,g_exposed)*100, 2)},
           "per_store": {k: {"active": v[0], "exposed": v[1], "conversion_pct": round(v[2]*100, 2),
                             "corr_exposed_pct": round(v[3]*100, 2), "corr_unexposed_pct": round(v[4]*100, 2)}
                         for k, v in per.items()}}
    json.dump(out, open("mnemo/probes/retrieval_exposure_coverage_probe_result.json", "w"), indent=2)
    print("\n-> mnemo/probes/retrieval_exposure_coverage_probe_result.json")

if __name__ == "__main__":
    main()
