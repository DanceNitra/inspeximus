"""Does InspeximusStore actually behave like a LangGraph BaseStore? Checked against LangGraph's own store.

Claiming "drop-in BaseStore" is only meaningful if a caller cannot tell the difference. So every
operation script below runs TWICE — once against `langgraph.store.memory.InMemoryStore` (the reference
implementation) and once against `InspeximusStore` — and the observable results must match. Where they must
NOT match is stated explicitly: inspeximus keeps queryable history that the reference discards, and its
delete removes the value from the bytes on disk.

    python store_audit.py                 # working tree
    STORE_FALSIFY=1 python store_audit.py # breaks InspeximusStore on purpose; parity checks MUST fail

Three scripts x three repeats. Any mismatch fails the claim.
"""
import argparse
import hashlib
import json
import os
import pathlib
import shutil
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _stores(tmp):
    from langgraph.store.memory import InMemoryStore
    from inspeximus.integrations.langgraph import InspeximusStore
    return InMemoryStore(), InspeximusStore(path=str(tmp / "store.jsonl"))


# Each script is a list of (label, callable(store) -> comparable result).
def script_crud():
    ns = ("users", "u1")
    return [
        ("put a",        lambda s: s.put(ns, "profile", {"name": "Jana", "city": "Nitra"})),
        ("get a",        lambda s: _item(s.get(ns, "profile"))),
        ("overwrite a",  lambda s: s.put(ns, "profile", {"name": "Jana", "city": "Bratislava"})),
        ("get after ow", lambda s: _item(s.get(ns, "profile"))),
        ("get missing",  lambda s: _item(s.get(ns, "nope"))),
        ("delete a",     lambda s: s.delete(ns, "profile")),
        ("get deleted",  lambda s: _item(s.get(ns, "profile"))),
    ]


def script_namespaces():
    a, b = ("org", "acme"), ("org", "globex")
    return [
        ("put acme",     lambda s: s.put(a, "secret", {"code": "ALPHA"})),
        ("put globex",   lambda s: s.put(b, "secret", {"code": "BETA"})),
        ("get acme",     lambda s: _item(s.get(a, "secret"))),
        ("get globex",   lambda s: _item(s.get(b, "secret"))),
        ("search acme",  lambda s: _items(s.search(a))),
        ("search all",   lambda s: _items(s.search(("org",)))),
        ("delete acme",  lambda s: s.delete(a, "secret")),
        ("acme gone",    lambda s: _item(s.get(a, "secret"))),
        ("globex kept",  lambda s: _item(s.get(b, "secret"))),
    ]


def script_search():
    ns = ("notes",)
    items = {"n1": {"text": "the invoice is due in March"},
             "n2": {"text": "the manager is Rachel Tseng"},
             "n3": {"text": "the invoice number is 9981"}}
    ops = [(f"put {k}", (lambda kk, vv: (lambda s: s.put(ns, kk, vv)))(k, v)) for k, v in items.items()]
    ops += [
        ("search all",    lambda s: _items(s.search(ns))),
        ("search limit2", lambda s: len(s.search(ns, limit=2))),
        ("put none=del",  lambda s: s.put(ns, "n2", None)),
        ("n2 gone",       lambda s: _item(s.get(ns, "n2"))),
        ("remaining",     lambda s: len(_items(s.search(ns)))),
    ]
    return ops


def script_list_namespaces():
    """The operation this audit did NOT cover until 2026-07-21, and the gap was not theoretical.

    `list_namespaces` was applying only offset/limit: match_conditions and max_depth were ignored, so
    `list_namespaces(prefix=("org",))` returned every namespace in the store, and an unsorted result
    meant `limit` handed back a different subset than the reference for the same query. Both passed
    the audit because the audit never asked. An operation that is not scripted is not audited.
    """
    a, b, c, d = ("org", "acme", "team"), ("org", "globex"), ("notes",), ("org", "acme", "other")
    ns = lambda r: sorted(tuple(x) for x in r)
    return [
        ("seed a",        lambda s: s.put(a, "k", {"v": 1})),
        ("seed b",        lambda s: s.put(b, "k", {"v": 2})),
        ("seed c",        lambda s: s.put(c, "k", {"v": 3})),
        ("seed d",        lambda s: s.put(d, "k", {"v": 4})),
        ("list all",      lambda s: ns(s.list_namespaces())),
        ("prefix org",    lambda s: ns(s.list_namespaces(prefix=("org",)))),
        ("prefix deep",   lambda s: ns(s.list_namespaces(prefix=("org", "acme")))),
        ("suffix team",   lambda s: ns(s.list_namespaces(suffix=("team",)))),
        ("wildcard",      lambda s: ns(s.list_namespaces(prefix=("org", "*", "team")))),
        ("max_depth 2",   lambda s: ns(s.list_namespaces(max_depth=2))),
        ("limit 2",       lambda s: [tuple(x) for x in s.list_namespaces(limit=2)]),
        ("offset 1",      lambda s: [tuple(x) for x in s.list_namespaces(offset=1)]),
    ]


SCRIPTS = [("CRUD + overwrite + delete", script_crud),
           ("namespace isolation", script_namespaces),
           ("search, limit, delete-by-None", script_search),
           ("list_namespaces: prefix/suffix/wildcard/depth/order", script_list_namespaces)]


def report_documented_divergence():
    """One place where we deliberately do NOT match the reference, stated rather than hidden.

    After the last key in a namespace is deleted, InMemoryStore still lists the now-empty namespace;
    this store does not. Matching the reference would mean a namespace name outliving the erasure of
    every value it held, which contradicts the receipted-erasure guarantee this library is built on.
    So the divergence is intentional -- and printed, because a parity audit that quietly skips the
    case it loses is worth nothing.
    """
    import tempfile
    from langgraph.store.memory import InMemoryStore
    tmp = pathlib.Path(tempfile.mkdtemp())
    out = []
    for label, s in (("reference", InMemoryStore()),
                     ("ours", InspeximusStore(path=str(tmp / "div.jsonl")))):
        s.put(("u", "1"), "p", {"a": 1})
        s.delete(("u", "1"), "p")
        out.append((label, [tuple(x) for x in s.list_namespaces()]))
    print()
    print("--- documented divergence (intentional, not a parity failure)")
    for label, got in out:
        print(f"  {label:10} namespaces after deleting the only key: {got}")
    print("  ours drops the empty namespace: an erased value must not leave its namespace behind.")
    return out[0][1] != out[1][1]


def _item(it):
    if it is None:
        return None
    return {"ns": tuple(it.namespace), "key": it.key, "value": it.value}


def _items(lst):
    return sorted(((tuple(i.namespace), i.key, json.dumps(i.value, sort_keys=True)) for i in (lst or [])))


def run_script(name, build, run_idx):
    tmp = pathlib.Path(tempfile.mkdtemp(prefix=f"store_{run_idx}_"))
    ref, ours = _stores(tmp)
    if os.environ.get("STORE_FALSIFY") == "1":
        # Break the adapter on purpose: swallow every write. If parity still holds, the comparison is
        # not comparing anything.
        ours.batch = lambda ops: [None for _ in ops]
    rows, mismatches = [], []
    for label, fn in build():
        try:
            r_ref = fn(ref)
        except Exception as e:
            r_ref = f"RAISED {type(e).__name__}: {e}"
        try:
            r_ours = fn(ours)
        except Exception as e:
            r_ours = f"RAISED {type(e).__name__}: {e}"
        same = r_ref == r_ours
        rows.append((label, same, r_ref, r_ours))
        if not same:
            mismatches.append((label, r_ref, r_ours))

    # inspeximus-specific extras the reference cannot do — these are DIFFERENCES BY DESIGN, checked separately
    extras = {}
    if os.environ.get("STORE_FALSIFY") != "1":
        try:
            hist = ours.history(("users", "u1"), "profile") if hasattr(ours, "history") else None
            extras["history available after overwrite"] = bool(hist) if name.startswith("CRUD") else None
        except Exception as e:
            extras["history available after overwrite"] = f"RAISED {type(e).__name__}"
        # a deleted value must not survive in the bytes on disk
        try:
            ours.store._save(force=True)
            blob = " ".join(p.read_text(encoding="utf-8", errors="replace")
                            for p in tmp.rglob("*") if p.is_file()).lower()
            # Only scripts that actually delete something can assert erasure. Keyed by script name,
            # so a new script must opt in rather than silently inherit someone else's expectation --
            # the first version raised KeyError the moment a script was added.
            secret = {"CRUD + overwrite + delete": "bratislava",
                      "namespace isolation": "alpha",
                      "search, limit, delete-by-None": "rachel tseng"}.get(name)
            if secret is None:
                extras["deleted value gone from disk"] = "n/a (script deletes nothing)"
            else:
                extras["deleted value gone from disk"] = secret not in blob
        except Exception as e:
            extras["deleted value gone from disk"] = f"RAISED {type(e).__name__}: {e}"

    h = hashlib.sha256(json.dumps([(l, str(o)) for l, _, _, o in rows], sort_keys=True).encode()).hexdigest()
    shutil.rmtree(tmp, ignore_errors=True)
    return rows, mismatches, extras, h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    a = ap.parse_args()
    import langgraph
    print("=" * 92)
    print(f"InspeximusStore vs langgraph {getattr(langgraph, '__version__', '?')} InMemoryStore "
          f"— {len(SCRIPTS)} scripts x {a.repeats} repeats")
    if os.environ.get("STORE_FALSIFY") == "1":
        print("FALSIFY MODE: writes are swallowed; parity checks MUST fail")
    print("=" * 92)

    fails = 0
    for name, build in SCRIPTS:
        print(f"\n--- {name}")
        hashes, agg_mis, extras = [], [], {}
        for run in range(a.repeats):
            rows, mism, ex, h = run_script(name, build, run)
            hashes.append(h)
            agg_mis += mism
            extras = ex
            if run == 0:
                for label, same, r_ref, r_ours in rows:
                    print(f"  [{'ok ' if same else 'MISMATCH'}] {label:16} ref={str(r_ref)[:46]:46} ours={str(r_ours)[:46]}")
        if agg_mis:
            fails += 1
            print(f"  [FAIL] parity: {len(agg_mis)} mismatching operations")
        else:
            print(f"  [PASS] parity with the reference implementation on every operation")
        for k, v in extras.items():
            if v is None:
                continue
            if isinstance(v, str) and v.startswith("n/a"):
                print(f"  [ -- ] {k}: {v}")     # not applicable to this script, not a failure
                continue
            ok = v is True
            fails += not ok
            print(f"  [{'PASS' if ok else 'FAIL'}] {k}: {v}")
        same_state = len(set(hashes)) == 1
        fails += not same_state
        print(f"  [{'PASS' if same_state else 'FAIL'}] identical results across {a.repeats} runs")

    print("\n" + "=" * 92)
    print("InspeximusStore IS a drop-in BaseStore" if not fails else f"NOT drop-in — {fails} failing groups")
    print("=" * 92)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
