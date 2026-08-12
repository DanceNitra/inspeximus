"""If we added a lineage-aware independence rail, would it ever fire on real data?

The companion probe (corroboration_counts_an_echo_as_two_witnesses.py) shows the DEFECT: two links
derived from one parent, carrying different document names, pass our >=2-distinct-source bar as two
independent witnesses. The obvious fix is to collapse links that share an ancestor.

Before shipping that, ask the question this repository keeps having to learn: would the fix ever SEE
its case? A guard is not shipped when it is written, it is shipped when its INPUT arrives, and we have
now measured four separate mechanisms that were correct and unreached (`attested_key` at 0 of 111,264;
`slash(scope='source')` resolving on a field no writer set; source coverage 98.3% populated and 0.01%
re-fetchable).

Scans the local stores this deployment actually writes. It cannot be run by an outsider -- the stores
are private -- which is why the numbers are reported as OURS, the same way the 98.3%/0.01% source
coverage is. The mechanism probe beside it is the part anyone can reproduce.

Run:  python probes/would_a_lineage_rail_reach_anything_here.py
"""
import json
import os

ROOTS = [os.path.expanduser(r"~\agora"), os.path.expanduser(r"~\inspeximus-repo"), os.path.expanduser("~")]
SKIP_DIRS = {".git", "node_modules", "__pycache__", "Temp", "AppData"}


def _stores():
    """Every real store, found by RECORD SHAPE rather than by filename.

    os.walk, not glob: glob does not match dot-directories, and the agents' stores live in
    `.agent_memory/`. A first pass that filtered on the filename found 30 files and 0 records with two
    links, against a codebase comment documenting 96,716 of them -- the scan was wrong, not the data,
    and a zero that disagrees with a written measurement is an instrument to check, not a finding.
    """
    seen, out = set(), []
    for root in ROOTS:
        if not os.path.isdir(root):
            continue
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if d not in SKIP_DIRS]
            for f in fn:
                if not f.endswith(".json"):
                    continue
                p = os.path.join(dp, f)
                if p in seen:
                    continue
                seen.add(p)
                try:
                    if os.path.getsize(p) < 5000:
                        continue
                    with open(p, encoding="utf-8") as fh:
                        d = json.load(fh)
                except (OSError, ValueError):
                    continue
                items = d.get("items") if isinstance(d, dict) else (d if isinstance(d, list) else None)
                if (isinstance(items, list) and items and isinstance(items[0], dict)
                        and "id" in items[0] and ({"text", "ts", "mtype"} & set(items[0]))):
                    out.append((p, items))
    return out


def main():
    stores = _stores()
    recs = multi = with_lineage = echo = 0
    for _p, items in stores:
        by = {r["id"]: r for r in items if isinstance(r, dict) and "id" in r}
        for r in by.values():
            recs += 1
            links = r.get("links") or []
            if len(links) < 2:
                continue
            multi += 1
            parents, any_lineage = [], False
            for lid in links:
                lr = by.get(lid)
                if lr is None:
                    continue
                df = lr.get("derived_from") or []
                if df:
                    any_lineage = True
                    parents.append(tuple(sorted(df)))
            if any_lineage:
                with_lineage += 1
            if len(parents) >= 2 and any(parents[i] == parents[j]
                                         for i in range(len(parents))
                                         for j in range(i + 1, len(parents))):
                echo += 1

    assert stores, "no stores found -- the scan is broken; a zero here would mean nothing"
    assert recs > 1000, f"only {recs} records scanned; the finder is not reaching the real stores"

    print(f"  stores scanned                        : {len(stores)}")
    print(f"  records                               : {recs:,}")
    print(f"  with >=2 corroborating links          : {multi:,} ({100.0 * multi / recs:.1f}%)")
    print(f"  ...where ANY link carries lineage     : {with_lineage:,} "
          f"({100.0 * with_lineage / multi:.3f}% of those)")
    print(f"  ...where two links SHARE a parent     : {echo:,}")
    print()
    if echo == 0 and with_lineage * 200 < multi:
        print("  VERDICT: a lineage rail would cost NOTHING here -- and reach nothing either.")
        print("           derived_from is essentially absent on corroborating links, so the rail would")
        print("           be correct and unreached: the fix must be lineage COVERAGE first, then the")
        print("           rail. Shipping the rail alone would be a guard that reports safe because its")
        print("           case never arrives.")
        verdict = "RAIL-WOULD-BE-UNREACHED"
    elif echo == 0:
        print("  VERDICT: no echoes present today; the rail would be free to add and would bind.")
        verdict = "RAIL-FREE-AND-BINDING"
    else:
        print(f"  VERDICT: {echo:,} records would lose corroboration. Not free -- measure before flipping.")
        verdict = "RAIL-HAS-A-COST"

    out = {"stores": len(stores), "records": recs, "with_two_links": multi,
           "any_link_has_lineage": with_lineage, "links_share_a_parent": echo, "verdict": verdict}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "would_a_lineage_rail_reach_anything_here.result.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2)
    print(f"\n  wrote {os.path.basename(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
