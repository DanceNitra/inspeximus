"""Does a keyed write on a large row store fail with "database is locked" while other processes read it?

Crew OS, 2026-09-22, on the live 23.7 MB store with 14 processes open on it: remember() returned an
id, `last_write` read landed, `_persist_error` held "OperationalError: database is locked", and the
record was not on disk; five attempts in a row failed, the sixth landed. The writer holds the
inter-process store lock, so the contention is with READERS: a load of a 23.7 MB store holds a
SHARED lock for its duration, and a writer's COMMIT needs EXCLUSIVE.

Arms, on a copy of a store of the given size:
  A) writer alone: N keyed writes, count persist errors.
  B) writer beside R reader processes that open a fresh handle in a loop (the Crew OS pattern).
Result (3.5.1, the live store copied, 12 readers, 40 writes, `.result.json` beside this file):
readers do NOT cause it. 0 persist errors in both arms, 40 of 40 landed, 354 reader loads in 90 s.
The writer holds the inter-process store lock, so a `database is locked` at the writer comes from
a client outside that lock: a raw sqlite3 connection with an open transaction (the reporter's own
tooling opens such connections, with `PRAGMA busy_timeout=5000`). 3.5.2 retries the row write a
bounded number of times, names that cause in the error, and stamps `persisted` on `last_write`.
Usage: python probes/database_is_locked_under_many_readers.py [store_copy_path] [readers] [writes]
With no arguments it runs a short form (4 readers, 8 writes, 15 s) so the probe registry can run it.
"""
import json, os, sys, time, shutil, tempfile, multiprocessing as mp
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _reader(args):
    path, seconds = args
    sys.path.insert(0, ROOT)
    from inspeximus import Inspeximus
    t0 = time.time(); n = 0
    while time.time() - t0 < seconds:
        m = Inspeximus(path)
        m.recall("budget review", k=3)
        n += 1
    return n


def _writer(args):
    path, writes = args
    sys.path.insert(0, ROOT)
    from inspeximus import Inspeximus
    errs = 0; landed = 0; t0 = time.time(); attempts = []
    for i in range(writes):
        m = Inspeximus(path)
        m.remember("layer %d" % i, key="probe::locked::L%d" % i, object="v1")
        lw = dict(m.last_write)
        if m._persist_error:
            errs += 1
        attempts.append({"persisted": lw.get("persisted"), "error": (m._persist_error or {}).get("error")})
    fresh = Inspeximus(path)
    landed = sum(1 for r in fresh.items if r.get("key", "").startswith("probe::locked::") and r["status"] == "active")
    return {"writes": writes, "persist_errors": errs, "landed": landed, "elapsed_s": round(time.time() - t0, 1),
            "persisted_field_seen": any(a["persisted"] is not None for a in attempts),
            "persisted_false": sum(1 for a in attempts if a["persisted"] is False)}


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/.inspeximus/mcp_memory_chain.json")
    full = len(sys.argv) > 1
    readers = int(sys.argv[2]) if len(sys.argv) > 2 else (12 if full else 4)
    writes = int(sys.argv[3]) if len(sys.argv) > 3 else (40 if full else 8)
    seconds = 90 if full else 15
    d = tempfile.mkdtemp()
    path = os.path.join(d, "copy.json")
    from inspeximus import __version__, Inspeximus
    if os.path.exists(src):
        shutil.copyfile(src, path)
    else:   # no live store here (CI): a synthetic one of a few thousand rows stands in
        seed = Inspeximus(path)
        for i in range(2000):
            seed.remember("synthetic record %d about budgets, venues and reviews" % i, key="seed::%d" % i, object="v")
        seed.flush()
    out = {"probe": os.path.basename(__file__), "inspeximus": __version__, "store_bytes": os.path.getsize(path),
           "readers": readers, "writes": writes, "save_retries_env": os.environ.get("INSPEXIMUS_SAVE_RETRIES")}
    print("store copy %.1f MB, %d readers, %d writes, inspeximus %s" % (out["store_bytes"] / 1e6, readers, writes, __version__), flush=True)
    out["A_writer_alone"] = _writer((path, writes))
    print("A", json.dumps(out["A_writer_alone"]), flush=True)
    with mp.Pool(readers + 1) as pool:
        rs = [pool.apply_async(_reader, ((path, seconds),)) for _ in range(readers)]
        time.sleep(3)
        w = pool.apply_async(_writer, ((path, writes),))
        out["B_writer_beside_readers"] = w.get()
        out["B_reader_loads"] = sum(r.get() for r in rs)
    print("B", json.dumps(out["B_writer_beside_readers"]), "reader loads", out["B_reader_loads"], flush=True)
    if full:   # the short form is a smoke run; the recorded result is the full one
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from _receipt import write_receipt
        write_receipt(__file__, out)
    assert out["A_writer_alone"]["landed"] == writes and out["B_writer_beside_readers"]["landed"] == writes


if __name__ == "__main__":
    mp.freeze_support()
    main()
