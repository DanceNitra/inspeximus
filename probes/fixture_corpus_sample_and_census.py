"""Sample the 20-day fixture corpus the suite left in the user's Temp, archive it, and measure two things.

WHAT THE CORPUS IS. Measured 2026-09-20: 422,798 `tmp*` directories in %TEMP%, every one a store
the test suite created and never removed (see tests/conftest.py for the fix), plus 596,291 empty
`inspeximus-<hex>.lock` files. The directories hold stores in every state the suite can produce:
tampered, truncated, erased, receipts turned on part-way, written by twelve processes at once. That
is a corpus no fixture generator would give us, so a sample is archived before the cleanup deletes it.

TWO MEASUREMENTS.
  1. RESIDUE CENSUS. The suite erases known secrets (the SECRET constants in tests/). Any byte of one
     found in a sample store beside a tombstones sidecar is an erasure a test called complete while
     the value survived somewhere the test did not read. 0 hits is a result; a hit is a finding.
  2. COMPATIBILITY. Open every store in the sample with the library in this tree (3.0.0), count the
     stores that open, the exceptions by type, and the verify_writes() verdicts. The assignment
     that produced 3.0.0 required 100% backward compatibility; this is the corpus it can be measured
     on rather than asserted.

    python probes/fixture_corpus_sample_and_census.py --n 20000 --out C:/Users/Danculus/inspeximus-corpus
"""
from __future__ import annotations

import argparse
import collections
import datetime as _dt
import json
import os
import random
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SECRETS = ["sk-alice-777-DO-NOT-LEAK", "sk-globex-999-DO-NOT-LEAK", "alice-probe-9f3c@example.com",
           "Ludwig Wittgenstein", "type-1 diabetes", "555-123-4567", "GLOBEX_SECRET"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20000)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=20260920)
    ap.add_argument("--temp", default=os.environ.get("TEMP") or os.environ.get("TMP"))
    a = ap.parse_args()
    today = _dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    t0 = time.time()
    cands = []
    with os.scandir(a.temp) as it:
        for e in it:
            if e.name.startswith("tmp") and e.is_dir(follow_symlinks=False):
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                if st.st_mtime < today:
                    cands.append((e.name, st.st_mtime))
    print(f"enumerated {len(cands)} tmp* directories older than today in {time.time() - t0:.0f}s", flush=True)
    random.Random(a.seed).shuffle(cands)
    sample = cands[:a.n]
    os.makedirs(a.out, exist_ok=True)
    manifest = []
    copied = skipped = 0
    for i, (name, mt) in enumerate(sample, 1):
        src = os.path.join(a.temp, name)
        dst = os.path.join(a.out, name)
        try:
            files = []
            for dp, _dn, fn in os.walk(src):
                for f in fn:
                    p = os.path.join(dp, f)
                    files.append({"path": os.path.relpath(p, src), "bytes": os.path.getsize(p)})
            shutil.copytree(src, dst, dirs_exist_ok=True)
            manifest.append({"name": name, "mtime": mt, "files": files})
            copied += 1
        except Exception as ex:                       # noqa: BLE001 -- a dir another process holds is skipped, counted
            skipped += 1
        if i % 1000 == 0:
            print(f"  archived {i}/{len(sample)} ({skipped} skipped) {time.time() - t0:.0f}s", flush=True)
    with open(os.path.join(a.out, "MANIFEST.json"), "w", encoding="utf-8") as fh:
        json.dump({"sampled_from": len(cands), "seed": a.seed, "n": len(manifest), "skipped": skipped,
                   "taken": _dt.datetime.now().isoformat(timespec="seconds"), "dirs": manifest}, fh)
    print(f"archived {copied} directories, {skipped} skipped, manifest written", flush=True)

    # ---- 1. residue census
    hits = []
    by_file = collections.Counter()
    tomb_dirs = 0
    for d in manifest:
        base = os.path.join(a.out, d["name"])
        has_tomb = any(f["path"].endswith(".tombstones.json") for f in d["files"])
        tomb_dirs += has_tomb
        for f in d["files"]:
            p = os.path.join(base, f["path"])
            try:
                with open(p, "rb") as fh:
                    blob = fh.read()
            except OSError:
                continue
            for s in SECRETS:
                if s.encode("utf-8") in blob:
                    by_file[(os.path.splitext(f["path"])[1] or f["path"], s)] += 1
                    if has_tomb:
                        hits.append({"dir": d["name"], "file": f["path"], "secret": s})
    print(f"residue: {tomb_dirs} sample stores carry a tombstones sidecar (an erasure ran); "
          f"{len(hits)} secret occurrences in those stores", flush=True)
    for (ext, s), n in by_file.most_common(12):
        print(f"  {n:6d}  {s!r} in {ext}")

    # ---- 2. compatibility
    from inspeximus import Inspeximus, __version__
    from inspeximus import sqlite_store as rows
    opened = collections.Counter()
    verdicts = collections.Counter()
    errors = collections.Counter()
    for d in manifest:
        base = os.path.join(a.out, d["name"])
        for f in d["files"]:
            p = os.path.join(base, f["path"])
            if f["path"].count(os.sep) or "." in os.path.basename(f["path"]).replace(".json", "").replace(".sqlite", ""):
                continue                              # sidecars and nested files are not stores
            if not (rows.looks_like_sqlite(p) or f["path"].endswith(".json")):
                continue
            try:
                m = Inspeximus(path=p, receipts=os.path.exists(p + ".receipts.json"))
                opened["ok"] += 1
                ok, probs = m.verify_writes(coverage_strict=False)
                verdicts["verify:" + ("PASS" if ok else "FAIL")] += 1
                for pr in probs[:3]:
                    verdicts["problem:" + pr.split(":")[0][:40]] += 1
            except Exception as ex:                   # noqa: BLE001 -- the count IS the measurement
                opened["error"] += 1
                errors[type(ex).__name__ + ": " + str(ex)[:70]] += 1
    print(f"compatibility ({__version__}): opened {opened['ok']} stores, {opened['error']} raised", flush=True)
    for k, n in verdicts.most_common(15):
        print(f"  {n:6d}  {k}")
    for k, n in errors.most_common(10):
        print(f"  {n:6d}  {k}")
    out = {"corpus": a.out, "sampled_from": len(cands), "archived": copied, "skipped": skipped,
           "residue": {"stores_with_tombstones": tomb_dirs, "secret_hits_in_erased_stores": hits[:200],
                       "by_file": {f"{s} in {ext}": n for (ext, s), n in by_file.items()}},
           "compat": {"library": __version__, "opened": dict(opened), "verdicts": dict(verdicts),
                      "errors": dict(errors)}, "elapsed_s": round(time.time() - t0, 1)}
    with open(os.path.join(ROOT, "probes", "fixture_corpus_sample_and_census.result.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    print(f"done in {time.time() - t0:.0f}s; result beside this probe", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
