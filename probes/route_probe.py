"""route_probe.py — receipt for the SHIPPED Mnemo.route() (0.7.9): the write-path intent router.

Re-runs the full 148-row fixture from intent_tagger_router_probe (assert / correct / value-obscuring +
named + original reverts / byte-identical echo-vs-reaffirm twins incl. the forged-context adversarial
class / innocent temporal chatter) — but every decision is made by the SHIPPED store.route() on the
mnemo_pypi package layout, exactly as an installed user would call it. Expected to reproduce the probe's
measured picture: marked classes 1.00 end-to-end under every policy; the unmarked twins land on the
documented policy frontier (safe 1.00/0.00, context 1.00/1.00 honest twins but forged-context 0.00,
trusting 0.00/1.00).

RUN: python mnemo/probes/route_probe.py
"""
import sys, os, json, pathlib, importlib.util

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "mnemo_pypi"))
from mnemo import Mnemo  # the SHIPPED layout

_spec = importlib.util.spec_from_file_location(
    "fixture_mod", os.path.join(os.path.dirname(__file__), "intent_tagger_router_probe.py"))
_fx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fx)


def replay(row, policy):
    store = Mnemo(path=None)
    store.echo_guard = True
    for i, v in enumerate(row["history"]):
        t = (_fx.T_ASSERT[0] if i == 0 else _fx.T_CORRECT[0]).format(ent=row["entity"], v=v)
        store.remember(t, key=row["entity"], object=v)
    val = _fx.extract_value(row["utterance"].lower(), row["entity"], row["history"] + ["zenith"])
    rep = store.route(row["utterance"], key=row["entity"] if val else None, object=val,
                      context=row["context"], policy=policy)
    act = [r for r in store.items if r.get("key") == row["entity"] and r.get("status") == "active"
           and r.get("object") is not None]
    return rep, (act[-1]["object"] if act else None)


def main():
    rows = _fx.build_fixture()
    out = {"n": len(rows), "policies": {}}
    for policy in ("safe", "context", "trusting"):
        per = {}
        for r in rows:
            rep, cur = replay(r, policy)
            d = per.setdefault(r["cls"], {"n": 0, "state_ok": 0})
            d["n"] += 1
            d["state_ok"] += (cur == r["expect_current"])
        for c, d in per.items():
            d["state_acc"] = round(d["state_ok"] / d["n"], 3)
        out["policies"][policy] = per
        print(f"== route() policy: {policy} ==")
        for c, d in per.items():
            print(f"  {c:26s} n={d['n']:2d} end2end_state_acc={d['state_acc']:.2f}")
    hl = {p: {"echo_blocked": out["policies"][p]["echo"]["state_acc"],
              "reaffirm_honored": out["policies"][p]["reaffirm_unmarked"]["state_acc"],
              "forged_ctx_echo_blocked": out["policies"][p]["adversarial_context_echo"]["state_acc"]}
          for p in out["policies"]}
    out["headline"] = hl
    print("\nHEADLINE:", json.dumps(hl))
    marked = ["correction", "revert_obscuring", "revert_named", "revert_original", "innocent_temporal"]
    all_marked_pass = all(out["policies"][p][c]["state_acc"] == 1.0 for p in out["policies"] for c in marked)
    print("ALL MARKED CLASSES 1.00 under every policy:", all_marked_pass)
    json.dump(out, open(os.path.join(os.path.dirname(__file__), "route_probe_result.json"), "w"), indent=2)


if __name__ == "__main__":
    main()
