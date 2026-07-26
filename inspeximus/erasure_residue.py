"""Did the bytes actually go? A vendor-neutral residue check for any memory stack.

You called `delete()`. The API returned success and the value stopped being served. That is not the same
as the value being gone from disk, and for anyone with an erasure obligation it is the only part that
matters. Nothing about this is specific to inspeximus: point it at any directory -- a vector store, a
sqlite history, a JSONL trace, someone else's library -- and it answers for THAT deployment.

It reports three outcomes, and keeping them apart is the whole point:

  LIVE        a SQLite table still holds the value in a row. The system retained it.
  UNRECLAIMED the value is in the file's bytes but in no live row. SQLite (and most embedded stores) do
              not zero a page on delete, so the record is gone logically and the bytes linger until a
              VACUUM or compaction. This is a property of the storage engine, not a vendor's choice --
              reporting it as retention is the kind of over-claim that gets a report dismissed.
  PLAIN       a non-SQLite file (JSON, JSONL, log, backup) contains the value.

Measured on mem0 2.0.11 with a local qdrant while building this: after `delete()` and `reset()` the value
was in NO live row anywhere, and remained only as unreclaimed bytes in the vector store's sqlite. That is
the honest reading, and it is why the distinction exists.

The values you are searching for are secrets by construction, so this never echoes one. Findings carry a
short SHA-256 fingerprint instead, which is enough to correlate and useless to leak.

    from inspeximus.erasure_residue import scan_residue
    report = scan_residue(root="./data", values=["alice@example.com"])
    report["ok"]        # False if anything was found
    report["findings"]  # [{path, kind, table, column, fingerprint}, ...]

    python -m inspeximus.erasure_residue --root ./data --value alice@example.com
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3

_SQLITE_MAGIC = b"SQLite format 3\x00"
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache", ".pytest_cache"}


def _fingerprint(value: str) -> str:
    """A correlatable, non-reversible stand-in. The caller already knows the value; a report should not."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _is_sqlite(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(16) == _SQLITE_MAGIC
    except OSError:
        return False


def _live_rows(path: str, value: str) -> list[dict]:
    """Where a SQLite file still holds the value in an addressable row."""
    hits: list[dict] = []
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return hits
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        for t in tables:
            try:
                cols = [c[1] for c in con.execute(f'PRAGMA table_info("{t}")').fetchall()]
            except sqlite3.Error:
                continue
            for c in cols:
                try:
                    n = con.execute(
                        f'SELECT COUNT(*) FROM "{t}" WHERE CAST("{c}" AS TEXT) LIKE ?',
                        (f"%{value}%",)).fetchone()[0]
                except sqlite3.Error:
                    continue          # a column type that cannot be cast is not a hit
                if n:
                    hits.append({"table": t, "column": c, "rows": int(n)})
    finally:
        con.close()
    return hits


def scan_residue(root: str, values, max_file_mb: float = 512.0,
                 skip_dirs=None, follow_symlinks: bool = False) -> dict:
    """Search `root` for values that should no longer exist anywhere.

    Returns {ok, checked_files, skipped, findings, problems}. `ok` is True only when nothing was found --
    including unreclaimed bytes, because "logically deleted but still on the disk you are handing to
    someone" is exactly the state an erasure obligation is about. The `kind` on each finding is what tells
    you whether to file a bug or run a VACUUM.
    """
    vals = [v for v in (values or []) if isinstance(v, str) and v]
    if not vals:
        return {"ok": False, "checked_files": 0, "skipped": [], "findings": [],
                "problems": ["no values were given, so nothing was searched for -- an empty search is "
                             "not a clean result"]}

    skip = set(skip_dirs or ()) | _SKIP_DIRS
    findings: list[dict] = []
    problems: list[str] = []
    skipped: list[dict] = []
    checked = 0
    limit = int(max_file_mb * 1024 * 1024)

    for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_symlinks):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for name in filenames:
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            try:
                size = os.path.getsize(path)
            except OSError as e:
                skipped.append({"path": rel, "why": f"{type(e).__name__}"})
                continue
            if size > limit:
                # Silently skipping a big file would make a store look clean because it was too large to
                # look at, so this is reported rather than dropped.
                skipped.append({"path": rel, "why": f"larger than max_file_mb ({size / 1e6:.0f} MB)"})
                continue
            try:
                with open(path, "rb") as fh:
                    blob = fh.read()
            except OSError as e:
                skipped.append({"path": rel, "why": f"{type(e).__name__}"})
                continue
            checked += 1

            present = [v for v in vals if v.encode("utf-8") in blob]
            if not present:
                continue
            sqlite_file = _is_sqlite(path)
            for v in present:
                live = _live_rows(path, v) if sqlite_file else []
                if live:
                    for hit in live:
                        findings.append({"path": rel, "kind": "LIVE", "fingerprint": _fingerprint(v),
                                         "table": hit["table"], "column": hit["column"],
                                         "rows": hit["rows"]})
                elif sqlite_file:
                    findings.append({"path": rel, "kind": "UNRECLAIMED", "fingerprint": _fingerprint(v),
                                     "note": "no live row holds it; the page has not been reclaimed. "
                                             "VACUUM (sqlite) or compact the store, or use encryption "
                                             "plus key destruction if the disk leaves your control."})
                else:
                    findings.append({"path": rel, "kind": "PLAIN", "fingerprint": _fingerprint(v),
                                     "note": "a non-SQLite file still contains it (JSON, log, trace or "
                                             "backup). Nothing will reclaim this on its own."})

    if any(f["kind"] == "LIVE" for f in findings):
        problems.append("the value is still held in a LIVE row: the system retained it, and this is a "
                        "retention question for whoever wrote that store")
    if any(f["kind"] == "UNRECLAIMED" for f in findings):
        problems.append("the value survives only as UNRECLAIMED bytes: logically deleted, physically "
                        "present. Common to embedded stores and NOT a vendor defect -- but still on the "
                        "disk you would be handing over")
    if any(f["kind"] == "PLAIN" for f in findings):
        problems.append("a plain file still contains the value; nothing reclaims this automatically")
    if skipped:
        problems.append(f"{len(skipped)} file(s) could not be read or were skipped; a store is not clean "
                        f"because part of it was not looked at")

    return {"ok": not findings and not skipped, "checked_files": checked,
            "skipped": skipped, "findings": findings, "problems": problems}


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="python -m inspeximus.erasure_residue",
        description="Check whether values you erased are still on disk. Works on any store, not just "
                    "inspeximus. Values are never echoed; findings carry a fingerprint.")
    ap.add_argument("--root", required=True, help="directory to search")
    ap.add_argument("--value", action="append", default=[],
                    help="a value that should be gone (repeatable)")
    ap.add_argument("--value-file", help="file with one value per line")
    ap.add_argument("--max-file-mb", type=float, default=512.0)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args(argv)

    values = list(a.value)
    if a.value_file:
        with open(a.value_file, encoding="utf-8") as fh:
            values += [ln.strip() for ln in fh if ln.strip()]

    rep = scan_residue(a.root, values, max_file_mb=a.max_file_mb)
    if a.json:
        print(json.dumps(rep, indent=2))
    else:
        print(f"checked {rep['checked_files']} file(s) under {a.root}")
        for f in rep["findings"]:
            where = f" [{f.get('table')}.{f.get('column')} x{f.get('rows')}]" if f["kind"] == "LIVE" else ""
            print(f"  {f['kind']:12s} {f['path']}{where}   fp={f['fingerprint']}")
        for p in rep["problems"]:
            print(f"  ! {p}")
        print("RESULT:", "clean — no residue found" if rep["ok"] else "residue found (see above)")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
