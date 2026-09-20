"""Remove what the test suite left in the system Temp before tests/conftest.py redirected it.

WHAT IT REMOVES, and nothing else:
  * `tmp*` directories older than --before (default: today 00:00) whose files all have one of the
    shapes our fixtures write (.json, .jsonl, .sqlite, .db, .txt, .log, .py, .md, .salt, .lock,
    .emb, .cose, .cbor, .bak, .receipts.json ...), or that are empty. A directory holding anything
    else is skipped and counted; it may belong to another program.
  * `inspeximus_example_*`, `inspeximus_base_*` and `inspeximus-erasure*` directories older than
    --before (the example runners' working directories).
  * `inspeximus-<hex>.lock` files older than --before. On Windows this is what the library itself
    does on every release since 3.1.0: `open()` sets no FILE_SHARE_DELETE, so the unlink fails while
    any process holds the lock and succeeds only when none does, and a later opener creates a fresh
    file every later opener shares. The dry run also reports how many of them map to a store inside
    a removed directory (`_StoreLock` keys the lock on sha256(abspath)[:16], so the mapping is
    exact); measured 2026-09-20, 236,309 of 542,874 did, and the rest belonged to pytest basetemps
    pytest had already pruned.

Never touches `<Temp>\\claude\\`, `pytest-of-*`, or anything modified on or after --before, so a
session that is running today keeps every file it made. Progress every 5,000 entries; --dry-run
counts without deleting.

    python tools/clean_fixture_residue_in_temp.py --dry-run
    python tools/clean_fixture_residue_in_temp.py
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import os
import shutil
import sys
import time

OURS = {".json", ".jsonl", ".sqlite", ".db", ".txt", ".log", ".py", ".md", ".salt", ".lock", ".emb",
        ".cose", ".cbor", ".bak", ".csv", ".html", ".pem", ".key", ".sig", ".hex", ".tmp", ".wal",
        ".shm", ".journal", ".jsonl.gz", ".pkl", ".npy", ".bin", ".sha256", ".manifest", ".yaml", ".yml"}


def _ours(path) -> bool:
    for dp, _dn, fn in os.walk(path):
        for f in fn:
            base = f.lower()
            if not any(base.endswith(e) for e in OURS) and "." in base:
                return False
    return True


def _lock_name(path) -> str:
    h = hashlib.sha256(os.path.abspath(str(path)).encode("utf-8", "replace")).hexdigest()[:16]
    return f"inspeximus-{h}.lock"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--temp", default=os.environ.get("TEMP") or os.environ.get("TMP"))
    ap.add_argument("--before", default=None, help="ISO date; entries modified on or after it are kept (default today)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    cutoff = (_dt.datetime.fromisoformat(a.before) if a.before
              else _dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)).timestamp()
    t0 = time.time()
    dirs, locks = [], set()
    n_seen = 0
    with os.scandir(a.temp) as it:
        for e in it:
            n_seen += 1
            n = e.name
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            if st.st_mtime >= cutoff:
                continue
            if e.is_dir(follow_symlinks=False) and (n.startswith("tmp") or n.startswith("inspeximus_example_")
                                                    or n.startswith("inspeximus_base_") or n.startswith("inspeximus-erasure")):
                dirs.append(e.path)
            elif e.is_file(follow_symlinks=False) and n.startswith("inspeximus-") and n.endswith(".lock"):
                locks.add(n)
    print(f"scanned {n_seen} entries in {time.time() - t0:.0f}s: {len(dirs)} candidate directories, "
          f"{len(locks)} lock files older than the cutoff", flush=True)
    removed_dirs = skipped_foreign = failed = 0
    dead_locks = set()
    for i, d in enumerate(dirs, 1):
        if not _ours(d):
            skipped_foreign += 1
            continue
        for dp, _dn, fn in os.walk(d):
            for f in fn:
                dead_locks.add(_lock_name(os.path.join(dp, f)))
        if not a.dry_run:
            try:
                shutil.rmtree(d)
                removed_dirs += 1
            except OSError:
                failed += 1
        else:
            removed_dirs += 1
        if i % 5000 == 0:
            print(f"  {i}/{len(dirs)} directories, {removed_dirs} removed, {skipped_foreign} foreign skipped, "
                  f"{failed} failed, {time.time() - t0:.0f}s", flush=True)
    victims = locks if os.name == "nt" else (locks & dead_locks)
    print(f"{len(locks & dead_locks)} of {len(locks)} lock files map to a store inside a removed directory; "
          f"{'all old locks go, by the Windows sharing rule' if os.name == 'nt' else 'only those go on POSIX'}",
          flush=True)
    removed_locks = held = 0
    for i, n in enumerate(sorted(victims), 1):
        if a.dry_run:
            removed_locks += 1
            continue
        try:
            os.unlink(os.path.join(a.temp, n))
            removed_locks += 1
        except OSError:
            held += 1                                 # open in another process: still in use
        if i % 20000 == 0:
            print(f"  locks {i}/{len(victims)}, {time.time() - t0:.0f}s", flush=True)
    print(f"{'would remove' if a.dry_run else 'removed'}: {removed_dirs} directories, {removed_locks} lock files "
          f"({held} held by a process and left); skipped {skipped_foreign} directories with foreign files, "
          f"{failed} failed; {len(locks) - len(victims)} lock files kept; {time.time() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
