#!/usr/bin/env python3
"""How inspeximus behaves as a store grows: a reproducible scale benchmark.

    python tools/bench_scale.py                                  # full matrix, ~hours, prints progress
    python tools/bench_scale.py --sizes 1000,10000 --runs 3      # a quick subset
    python tools/bench_scale.py --check-fixture                  # prove the fixture shortcut is faithful
    python tools/bench_scale.py --profile sqlite:on:10000:verify_writes   # where one step spends its time
    python tools/bench_scale.py --render audits/2026-09-24/scale.json     # re-print the tables

THE MATRIX. Sizes 1k / 10k / 50k / 100k records, the JSON store and the SQLite row store, receipts off
and on, each cell measured `--runs` times (default 3) in a FRESH PROCESS. Per cell it records:

  * remember() per call   -- `--remembers` writes (default 50) on a store already holding N records;
                             every 5th write is KEYED onto an existing key, so it supersedes (retires)
                             the current value, which is the write an agent correcting a fact makes;
  * recall() latency      -- `--recalls` lexical queries (default 100), median and p95 per run;
  * file size on disk     -- the store file and every sidecar, as the fixture left them;
  * open time             -- `Inspeximus(path, receipts=...)` in a process that has not seen the store;
  * verify_writes()       -- on the untouched N-record store, straight after open;
  * erasure_certificate() -- after a forget_subject() of one subject (~15 records at every N, so the
                             erasure itself is the same size and only the store around it grows);
  * peak memory           -- peak RSS per phase (Linux: VmHWM reset through /proc/self/clear_refs) and
                             for the whole measuring process (ru_maxrss).

Also recorded because they explain the numbers: forget_subject() time, and how many whole-file
replaces / row-store saves each phase performed, and how many bytes those replaces wrote.

THE FIXTURE, AND WHY IT IS NOT N CALLS TO remember(). Every remember() on a JSON store rewrites the
whole file (`_save(force=True)`, core.py), and with receipts on it also rewrites the whole receipt
sidecar. Building a 100k store that way writes on the order of a terabyte and takes days, which is
itself the first finding, not a way to build a fixture. So the fixture is built by the REAL remember()
-- same records, ids, supersession, receipts and hash chain -- with the two whole-file writes deferred
for the first N-1 calls: the handle's `_save` is shadowed by a no-op and its receipts path is unset.
The N-th call runs unpatched, so the library's own save path writes the whole store and the whole
chain, and the chain head, exactly as it would after any write. The library source is not modified;
the patch lives on one handle, in the build process only, and every MEASUREMENT runs in a separate
process against an unpatched library (instrumented only by pass-through counters on the two write
primitives, the same technique perf/gate.py uses). `--check-fixture` builds 1k records both ways and
compares them: record and receipt counts, statuses, verify_writes() verdicts and file sizes.

Each fixture is built once per cell, snapshotted, and restored byte-for-byte to the SAME absolute path
before each run, so the chain head that inspeximus keeps outside the store (keyed by that path) is
found exactly as a real store would find it. Every run is its own process, so peak memory is per run.

Corpus: deterministic (seeded) records of 8-20 words drawn without replacement from a Zipf-weighted
vocabulary of ~4,000 words, sourced to one of five systems and ~N/75 entities per system (about 15
records per entity), 20% of them keyed (entity::attribute with an object value, so re-used keys
supersede). No embedder: recall is the default lexical path. No signing key: receipts=True is the
unsigned hash chain; pass `--receipts off,on,signed` to add an Ed25519-signed arm.
"""
from __future__ import annotations

import argparse
import gc
import json
import math
import os
import platform
import random
import resource
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_SIZES = (1000, 10000, 50000, 100000)
DEFAULT_STORES = ("json", "sqlite")
DEFAULT_RECEIPTS = ("off", "on")
SUBJECT = "crm/customer-3"          # the erased subject; exists at every size (see Corpus)
REQUEST_ID = "bench-scale-erasure"


# ── deterministic corpus ──────────────────────────────────────────────────────────────────────────

_DOMAIN = ("deploy budget customer invoice prefers meeting release database migration outage contract "
           "renewal pricing region latency backup password rotation oncall incident roadmap quarter "
           "hiring vendor security audit policy retention dashboard alert timezone language allergy "
           "birthday manager team project ticket feature regression staging cluster").split()
_SYLL = ("ka lo mi ra ten vos dor pel qui zan ber tis nor gal fen sho ul mar dex ri po nu wal cy "
         "ab ek ist on ur").split()
_SYSTEMS = ("crm/customer", "hr/employee", "support/ticket", "ops/service", "wiki/page")
_ATTRS = ("plan", "region", "owner", "tier", "contact", "status")


class Corpus:
    """Records, queries and extra writes for a store of `size` records, reproducible from `seed`."""

    def __init__(self, size: int, seed: int = 20260924):
        self.size = size
        rng = random.Random(seed)
        synth = set()
        while len(synth) < 4000:
            synth.add("".join(rng.choice(_SYLL) for _ in range(rng.randint(2, 4))))
        self.vocab = list(_DOMAIN) + sorted(synth - set(_DOMAIN))
        # Zipf(1.05) over the vocabulary, the rank-frequency shape of natural text.
        acc, cum = 0.0, []
        for r in range(len(self.vocab)):
            acc += 1.0 / (r + 1) ** 1.05
            cum.append(acc)
        self._cum = cum
        # ~15 records per entity at every size, so one subject's erasure is the same size throughout.
        self.entities = max(1, size // (len(_SYSTEMS) * 15))
        self.seed = seed

    def _words(self, rng, k):
        out = []
        while len(out) < k:                     # without replacement: no record looks keyword-stuffed
            w = rng.choices(self.vocab, cum_weights=self._cum, k=1)[0]
            if w not in out:
                out.append(w)
        return out

    def record(self, i: int) -> dict:
        rng = random.Random(self.seed * 1_000_003 + i)
        system = _SYSTEMS[i % len(_SYSTEMS)]
        ent = rng.randrange(self.entities)
        doc = f"{system}-{ent}"
        words = self._words(rng, rng.randint(8, 20))
        rec = {"text": f"{doc.split('/')[1]} " + " ".join(words),
               "tags": [system.split("/")[0], words[0]],
               "source": {"doc": doc}}
        if rng.random() < 0.20:                 # keyed: a (subject, attribute) fact with an object value
            attr = rng.choice(_ATTRS)
            obj = rng.choice(self.vocab[:400])
            rec["key"] = f"{doc}::{attr}"
            rec["object"] = obj
            rec["text"] = f"{doc.split('/')[1]} {attr} is {obj}; " + " ".join(words[:8])
        return rec

    def queries(self, n: int) -> list:
        rng = random.Random(self.seed + 7)
        out = []
        for j in range(n):
            ws = self._words(rng, rng.randint(2, 3))
            if j % 3 == 0:                      # a third name an entity, the way an agent asks
                ws.append(f"{_SYSTEMS[j % 5].split('/')[1]}-{rng.randrange(self.entities)}")
            out.append(" ".join(ws))
        return out

    def extra_writes(self, m: int) -> list:
        """`m` writes made on top of the N-record store. Every 5th is keyed onto an EXISTING key."""
        rng = random.Random(self.seed + 11)
        out = []
        for j in range(m):
            rec = self.record(self.size + j)
            rec.pop("key", None)
            rec.pop("object", None)
            if j % 5 == 4:
                src = self.record(rng.randrange(self.size))
                while "key" not in src:
                    src = self.record(rng.randrange(self.size))
                obj = rng.choice(self.vocab[400:800])      # a new value -> supersedes the current one
                rec = {"text": f"{src['source']['doc'].split('/')[1]} "
                               f"{src['key'].split('::')[1]} is now {obj}",
                       "tags": src["tags"], "source": src["source"], "key": src["key"], "object": obj}
            out.append(rec)
        return out


# ── process memory ────────────────────────────────────────────────────────────────────────────────

def _status_kb(field: str):
    try:
        with open("/proc/self/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith(field + ":"):
                    return int(line.split()[1])
    except OSError:
        pass
    return None


def _reset_peak() -> bool:
    """Reset VmHWM (Linux >= 4.0) so the next reading is the peak of ONE phase, not of the process."""
    try:
        with open("/proc/self/clear_refs", "w", encoding="ascii") as fh:
            fh.write("5")
        return True
    except OSError:
        return False


def _maxrss_mb() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(r / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)


class WriteCounter:
    """Pass-through counters on the two write primitives, as perf/gate.py does: whole-file replaces
    (store, receipts, tombstones, other) with the bytes they wrote, and row-store saves."""

    def __init__(self, core, rows):
        self.core, self.rows = core, rows
        self.c = {}
        real_replace, real_rows_save = core._durable_replace, rows.save if rows else None

        def replace(path, payload, *a, **kw):
            name = str(path)
            kind = ("receipts" if name.endswith(".receipts.json") else
                    "tombstones" if name.endswith(".tombstones.json") else
                    "store" if name.endswith(".json") else "other")
            self.c[f"replace_{kind}"] = self.c.get(f"replace_{kind}", 0) + 1
            self.c[f"replace_{kind}_bytes"] = self.c.get(f"replace_{kind}_bytes", 0) + len(payload)
            return real_replace(path, payload, *a, **kw)

        def rows_save(*a, **kw):
            self.c["rows_save"] = self.c.get("rows_save", 0) + 1
            return real_rows_save(*a, **kw)

        core._durable_replace = replace
        if rows:
            rows.save = rows_save

    def take(self) -> dict:
        out, self.c = self.c, {}
        return out


class Phase:
    """Time one step and record its peak RSS and the writes it made."""

    def __init__(self, res: dict, name: str, wc: WriteCounter | None = None):
        self.res, self.name, self.wc = res, name, wc

    def __enter__(self):
        gc.collect()
        if self.wc:
            self.wc.take()
        self.peak_ok = _reset_peak()
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        dt = time.perf_counter() - self.t0
        self.res[self.name] = {
            "seconds": round(dt, 6),
            "peak_rss_mb": round(_status_kb("VmHWM") / 1024, 1) if self.peak_ok else None,
            "rss_after_mb": round((_status_kb("VmRSS") or 0) / 1024, 1),
            "writes": self.wc.take() if self.wc else {},
        }
        return False


def _log(msg: str) -> None:
    sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    sys.stderr.flush()


# ── workers (each runs in its own process) ───────────────────────────────────────────────────────

def _open(path: str, receipts: str):
    from inspeximus.core import Inspeximus, receipt_key_for
    kw = {"receipts": receipts != "off"}
    if receipts == "signed":
        kw["receipt_key"] = receipt_key_for(path)
    return Inspeximus(path, **kw)


def worker_build(a: dict) -> dict:
    """Build one fixture with the real remember(), deferring the whole-file writes (see module doc)."""
    sys.path.insert(0, str(ROOT))
    size, path, organic = a["size"], a["path"], a.get("organic", False)
    corpus = Corpus(size, a["seed"])
    m = _open(path, a["receipts"])
    receipts_path = m._receipts_path
    if not organic:
        m._save = lambda force=False: None       # this handle only; the N-th write runs unpatched
        m._receipts_path = None                  # _emit_write_receipt then skips sidecar + head
    t0 = time.perf_counter()
    step = max(1, size // 10)
    for i in range(size - 1):
        m.remember(**corpus.record(i))
        if (i + 1) % step == 0:
            _log(f"    build {a['label']}: {i + 1:,}/{size:,} records ({time.perf_counter() - t0:.1f}s)")
    if not organic:
        del m._save
        m._receipts_path = receipts_path
    t1 = time.perf_counter()
    m.remember(**corpus.record(size - 1))        # unpatched: writes the whole store, chain and head
    m.flush()
    t2 = time.perf_counter()
    statuses = {}
    for r in m._items:
        statuses[r.get("status")] = statuses.get(r.get("status"), 0) + 1
    return {"records": len(m._items), "receipts": len(m._receipts), "statuses": statuses,
            "build_seconds": round(t2 - t0, 3), "final_persist_seconds": round(t2 - t1, 3),
            "organic": organic}


def _files(dirpath: str) -> dict:
    out = {}
    for name in sorted(os.listdir(dirpath)):
        fp = os.path.join(dirpath, name)
        if os.path.isfile(fp):
            out[name] = os.path.getsize(fp)
    return out


def worker_measure(a: dict) -> dict:
    sys.path.insert(0, str(ROOT))
    import inspeximus
    from inspeximus import core
    rows = getattr(core, "_rows", None)
    wc = WriteCounter(core, rows)
    path, size, label = a["path"], a["size"], a["label"]
    corpus = Corpus(size, a["seed"])
    queries = corpus.queries(a["recalls"] + a["interleaved"])
    queries, inter_q = queries[:a["recalls"]], queries[a["recalls"]:]
    writes = corpus.extra_writes(a["remembers"] + a["interleaved"])
    writes, inter_w = writes[:a["remembers"]], writes[a["remembers"]:]
    res = {"baseline_rss_mb": round((_status_kb("VmRSS") or 0) / 1024, 1),
           "files_bytes": _files(os.path.dirname(path)),
           "inspeximus_version": getattr(inspeximus, "__version__", None)}
    res["file_bytes_total"] = sum(res["files_bytes"].values())
    ph = {}

    with Phase(ph, "open", wc):
        m = _open(path, a["receipts"])
    res["records_loaded"] = len(m._items)
    res["receipts_loaded"] = len(m._receipts)
    res["store_format"] = "sqlite" if m._rows_available() else "json"
    _log(f"    {label}: open {ph['open']['seconds']:.3f}s ({res['records_loaded']:,} records, "
         f"{res['file_bytes_total'] / 1e6:.1f} MB on disk)")

    with Phase(ph, "verify_writes", wc):
        ok, problems = m.verify_writes()
    res["verify_writes_ok"], res["verify_writes_problems"] = ok, problems[:3]
    res["verify_writes_problem_count"] = len(problems)
    _log(f"    {label}: verify_writes {ph['verify_writes']['seconds']:.3f}s -> ok={ok}")

    lat = []
    with Phase(ph, "recall", wc):
        for q in queries:
            t = time.perf_counter()
            m.recall(q, k=6)
            lat.append(time.perf_counter() - t)
    res["recall_ms"] = [round(x * 1000, 4) for x in lat]
    _log(f"    {label}: recall x{len(lat)} median {statistics.median(lat) * 1000:.2f} ms, "
         f"p95 {_pct(lat, 95) * 1000:.2f} ms")

    # How many records the recalls above left marked as changed. On a row store every write
    # re-serialises those before it can know nothing changed (see scale.md, "recall marks rows").
    res["touched_after_recall"] = len(getattr(m, "_touched", ()) or ())

    lat, keyed = [], []
    with Phase(ph, "remember", wc):
        for w in writes:
            t = time.perf_counter()
            m.remember(**w)
            dt = time.perf_counter() - t
            lat.append(dt)
            if "key" in w:
                keyed.append(dt)
    res["remember_ms"] = [round(x * 1000, 4) for x in lat]
    res["remember_keyed_ms"] = [round(x * 1000, 4) for x in keyed]
    _log(f"    {label}: remember x{len(lat)} median {statistics.median(lat) * 1000:.2f} ms, "
         f"p95 {_pct(lat, 95) * 1000:.2f} ms (first, right after the recalls: {lat[0] * 1000:.2f} ms)")

    # The agent loop: recall, then write. A burst of writes hides any cost a read leaves behind
    # for the next write; alternating them shows it on every call.
    lat_w, lat_r = [], []
    with Phase(ph, "interleaved", wc):
        for q, w in zip(inter_q, inter_w):
            t = time.perf_counter()
            m.recall(q, k=6)
            t1 = time.perf_counter()
            m.remember(**w)
            t2 = time.perf_counter()
            lat_r.append(t1 - t)
            lat_w.append(t2 - t1)
    res["interleaved_recall_ms"] = [round(x * 1000, 4) for x in lat_r]
    res["interleaved_remember_ms"] = [round(x * 1000, 4) for x in lat_w]
    _log(f"    {label}: interleaved x{len(lat_w)} remember-after-recall median "
         f"{statistics.median(lat_w) * 1000:.2f} ms")

    with Phase(ph, "forget_subject", wc):
        er = m.forget_subject(SUBJECT, request_id=REQUEST_ID)
    res["erased"] = er.get("erased")

    with Phase(ph, "erasure_certificate", wc):
        cert = m.erasure_certificate(request_id=REQUEST_ID)
    res["certificate_count"] = cert.get("count")
    res["certificate_self_check"] = cert.get("self_check", {}).get("verified")
    _log(f"    {label}: forget_subject {ph['forget_subject']['seconds']:.3f}s (erased {res['erased']}), "
         f"erasure_certificate {ph['erasure_certificate']['seconds']:.3f}s")

    with Phase(ph, "flush", wc):
        m.flush()
    res["files_bytes_after"] = _files(os.path.dirname(path))
    res["phases"] = ph
    res["peak_rss_mb"] = _maxrss_mb()
    return res


def _pct(xs, p):
    """Nearest-rank percentile; with 100 samples p95 is the 95th smallest."""
    s = sorted(xs)
    return s[max(0, min(len(s) - 1, math.ceil(p / 100 * len(s)) - 1))]


def worker_profile(a: dict) -> dict:
    """cProfile one step on a restored fixture; the top functions by cumulative and by own time."""
    import cProfile
    import io
    import pstats
    sys.path.insert(0, str(ROOT))
    import inspeximus.core  # noqa: F401  -- the import is not part of any step
    path, size, step = a["path"], a["size"], a["step"]
    corpus = Corpus(size, a["seed"])
    pr = cProfile.Profile()
    if step == "open":
        pr.enable()
        m = _open(path, a["receipts"])
        pr.disable()
    else:
        m = _open(path, a["receipts"])
        if step == "remember":
            ws = corpus.extra_writes(a["remembers"])
            pr.enable()
            for w in ws:
                m.remember(**w)
            pr.disable()
        elif step == "recall":
            qs = corpus.queries(a["recalls"])
            pr.enable()
            for q in qs:
                m.recall(q, k=6)
            pr.disable()
        elif step == "verify_writes":
            pr.enable()
            m.verify_writes()
            pr.disable()
        elif step == "erasure_certificate":
            m.forget_subject(SUBJECT, request_id=REQUEST_ID)
            pr.enable()
            m.erasure_certificate(request_id=REQUEST_ID)
            pr.disable()
        else:
            raise SystemExit(f"unknown step {step!r}")
    out = {}
    for key in ("cumulative", "tottime"):
        buf = io.StringIO()
        pstats.Stats(pr, stream=buf).strip_dirs().sort_stats(key).print_stats(25)
        out[key] = buf.getvalue()
    return out


# ── orchestration ─────────────────────────────────────────────────────────────────────────────────

def machine_spec(workdir: str) -> dict:
    def _read(p):
        try:
            return Path(p).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
    cpu = next((ln.split(":", 1)[1].strip() for ln in _read("/proc/cpuinfo").splitlines()
                if ln.startswith("model name")), platform.processor())
    mem = next((int(ln.split()[1]) for ln in _read("/proc/meminfo").splitlines()
                if ln.startswith("MemTotal")), None)
    fs, best = None, ""
    for ln in _read("/proc/mounts").splitlines():
        parts = ln.split()
        if len(parts) > 2 and os.path.abspath(workdir).startswith(parts[1]) and len(parts[1]) >= len(best):
            best, fs = parts[1], f"{parts[2]} on {parts[0]} ({parts[1]})"
    hyper = "yes" if " hypervisor" in _read("/proc/cpuinfo") else "no/unknown"
    import sqlite3
    try:
        import cryptography
        crypto = cryptography.__version__
    except Exception as e:                                          # noqa: BLE001
        crypto = f"unavailable ({type(e).__name__})"
    try:
        import numpy
        np_v = numpy.__version__
    except Exception:                                               # noqa: BLE001
        np_v = None
    try:
        commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True,
                                text=True, timeout=10).stdout.strip() or None
    except Exception:                                               # noqa: BLE001
        commit = None
    sys.path.insert(0, str(ROOT))
    try:
        import inspeximus
        version = inspeximus.__version__
    except BaseException as e:                                      # noqa: BLE001
        version = f"import failed: {type(e).__name__}"
    return {"cpu": cpu, "logical_cpus": os.cpu_count(), "virtualised": hyper,
            "ram_gb": round(mem / 1024 / 1024, 1) if mem else None,
            "workdir_filesystem": fs, "kernel": platform.release(), "os": platform.platform(),
            "python": sys.version.split()[0], "python_impl": platform.python_implementation(),
            "sqlite": sqlite3.sqlite_version, "cryptography": crypto, "numpy": np_v,
            "inspeximus_version": version, "git_commit": commit}


def _run_worker(kind: str, args: dict, env: dict) -> dict:
    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as fh:
        out = fh.name
    try:
        proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--_worker", kind,
                               "--_args", json.dumps(args), "--_out", out], env=env)
        if proc.returncode != 0:
            raise RuntimeError(f"{kind} worker failed for {args.get('label')} (exit {proc.returncode})")
        return json.loads(Path(out).read_text(encoding="utf-8"))
    finally:
        try:
            os.unlink(out)
        except OSError:
            pass


def _cell_env(store: str, keyhome: str) -> dict:
    env = dict(os.environ)
    env["INSPEXIMUS_KEY_HOME"] = keyhome            # chain heads (and any key) kept per cell
    if store == "json":
        env["INSPEXIMUS_STORE_FORMAT"] = "json"     # otherwise a JSON store is converted to rows on open
    else:
        env.pop("INSPEXIMUS_STORE_FORMAT", None)    # the default: new stores are rows
    env["PYTHONHASHSEED"] = "0"
    return env


def _label(store, receipts, size):
    return f"{store}/receipts={receipts}/n={size}"


def _cell_paths(workdir, store, receipts, size):
    name = f"{store}-{receipts}-{size}"
    live = os.path.join(workdir, "live", name)
    return {"name": name, "live": live, "store_dir": os.path.join(live, "store"),
            "keyhome": os.path.join(live, "keyhome"), "pristine": os.path.join(workdir, "pristine", name),
            "path": os.path.join(live, "store", "memory.json" if store == "json" else "memory.db")}


def _restore(cp):
    shutil.rmtree(cp["live"], ignore_errors=True)
    shutil.copytree(cp["pristine"], cp["live"])


def build_fixture(workdir, store, receipts, size, seed, organic=False) -> dict:
    cp = _cell_paths(workdir, store, receipts, size)
    marker = os.path.join(cp["pristine"], "fixture.json")
    if not organic and os.path.exists(marker):
        return json.loads(Path(marker).read_text(encoding="utf-8"))
    shutil.rmtree(cp["live"], ignore_errors=True)
    os.makedirs(cp["store_dir"])
    os.makedirs(cp["keyhome"])
    info = _run_worker("build", {"size": size, "path": cp["path"], "receipts": receipts, "seed": seed,
                                 "label": _label(store, receipts, size), "organic": organic},
                       _cell_env(store, cp["keyhome"]))
    if organic:
        return info
    shutil.rmtree(cp["pristine"], ignore_errors=True)
    shutil.copytree(cp["live"], cp["pristine"])
    Path(marker).write_text(json.dumps(info, indent=1), encoding="utf-8")
    return info


def _save_json(path, data):
    tmp = f"{path}.tmp"
    Path(tmp).write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def run_matrix(ns) -> dict:
    sizes = [int(s) for s in ns.sizes.split(",")]
    stores = ns.stores.split(",")
    receipts = ns.receipts.split(",")
    os.makedirs(ns.workdir, exist_ok=True)
    out_path = ns.out
    if ns.resume and os.path.exists(out_path):
        data = json.loads(Path(out_path).read_text(encoding="utf-8"))
    else:
        data = {"schema": "inspeximus-bench-scale/1", "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "machine": machine_spec(ns.workdir),
                "config": {"sizes": sizes, "stores": stores, "receipts": receipts, "runs": ns.runs,
                           "recalls": ns.recalls, "remembers": ns.remembers,
                           "interleaved": ns.interleaved, "seed": ns.seed,
                           "subject": SUBJECT, "command": " ".join(sys.argv)},
                "fixtures": {}, "runs": []}
    cells = [(st, rc, n) for n in sizes for st in stores for rc in receipts]
    _log(f"machine: {data['machine']['cpu']} x{data['machine']['logical_cpus']}, "
         f"{data['machine']['ram_gb']} GB, {data['machine']['workdir_filesystem']}")
    _log(f"{len(cells)} cells x {ns.runs} runs; results -> {out_path}")

    for i, (st, rc, n) in enumerate(cells, 1):
        lab = _label(st, rc, n)
        _log(f"fixture {i}/{len(cells)} {lab}")
        t = time.perf_counter()
        info = build_fixture(ns.workdir, st, rc, n, ns.seed)
        data["fixtures"][lab] = info
        _log(f"  fixture ready: {info['records']:,} records, {info['receipts']:,} receipts, "
             f"built in {info['build_seconds']:.1f}s (final unpatched write {info['final_persist_seconds']:.2f}s; "
             f"this step {time.perf_counter() - t:.1f}s)")
        _save_json(out_path, data)

    done = {(r["label"], r["run"]) for r in data["runs"]}
    todo = [(run, c) for run in range(1, ns.runs + 1) for c in cells if (_label(*c), run) not in done]
    t_all, finished = time.perf_counter(), 0
    for run, (st, rc, n) in todo:
        lab = _label(st, rc, n)
        cp = _cell_paths(ns.workdir, st, rc, n)
        _restore(cp)
        _log(f"run {run}/{ns.runs} {lab} [{finished + 1}/{len(todo)}]")
        t = time.perf_counter()
        res = _run_worker("measure", {"size": n, "path": cp["path"], "receipts": rc, "seed": ns.seed,
                                      "label": lab, "recalls": ns.recalls, "remembers": ns.remembers,
                                      "interleaved": ns.interleaved},
                          _cell_env(st, cp["keyhome"]))
        res.update({"label": lab, "store": st, "receipts": rc, "size": n, "run": run,
                    "wall_seconds": round(time.perf_counter() - t, 2)})
        data["runs"].append(res)
        _save_json(out_path, data)
        finished += 1
        el = time.perf_counter() - t_all
        _log(f"  done in {res['wall_seconds']:.1f}s, peak RSS {res['peak_rss_mb']} MB; "
             f"{finished}/{len(todo)} runs, {el / 60:.1f} min elapsed")
    data["finished"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _save_json(out_path, data)
    if not ns.keep:
        shutil.rmtree(os.path.join(ns.workdir, "live"), ignore_errors=True)
    return data


def check_fixture(ns) -> int:
    """Build 1k records organically and through the fixture path, per store x receipts, and compare."""
    n, rows, ok_all = ns.check_size, [], True
    for st in ns.stores.split(","):
        for rc in ns.receipts.split(","):
            both = {}
            for organic in (True, False):
                wd = os.path.join(ns.workdir, "check-organic" if organic else "check-fast")
                cp = _cell_paths(wd, st, rc, n)
                shutil.rmtree(os.path.join(wd, "pristine", cp["name"]), ignore_errors=True)
                info = build_fixture(wd, st, rc, n, ns.seed, organic=organic)
                if organic:
                    os.makedirs(cp["pristine"], exist_ok=True)
                    shutil.rmtree(cp["pristine"])
                    shutil.copytree(cp["live"], cp["pristine"])
                _restore(cp)
                m = _run_worker("measure", {"size": n, "path": cp["path"], "receipts": rc, "seed": ns.seed,
                                            "label": _label(st, rc, n) + (" organic" if organic else " fast"),
                                            "recalls": 10, "remembers": 10, "interleaved": 5},
                                   _cell_env(st, cp["keyhome"]))
                both["organic" if organic else "fast"] = (info, m)
            (io_, mo), (if_, mf) = both["organic"], both["fast"]
            same = (io_["records"] == if_["records"] and io_["receipts"] == if_["receipts"]
                    and io_["statuses"] == if_["statuses"] and mo["verify_writes_ok"] == mf["verify_writes_ok"]
                    and mo["records_loaded"] == mf["records_loaded"])
            size_ratio = mf["file_bytes_total"] / max(1, mo["file_bytes_total"])
            ok_all &= same and abs(size_ratio - 1) < 0.02
            rows.append({"cell": _label(st, rc, n), "records": (io_["records"], if_["records"]),
                         "receipts": (io_["receipts"], if_["receipts"]),
                         "statuses": (io_["statuses"], if_["statuses"]),
                         "verify_writes_ok": (mo["verify_writes_ok"], mf["verify_writes_ok"]),
                         "bytes_on_disk": (mo["file_bytes_total"], mf["file_bytes_total"]),
                         "build_seconds": (io_["build_seconds"], if_["build_seconds"]),
                         "equivalent": same and abs(size_ratio - 1) < 0.02})
    for r in rows:
        print(json.dumps(r))
    if ns.out:
        _save_json(ns.out, {"check_fixture": rows, "machine": machine_spec(ns.workdir)})
    print("fixture shortcut is EQUIVALENT" if ok_all else "fixture shortcut DIFFERS -- see rows above")
    return 0 if ok_all else 1


def profile_step(ns) -> int:
    st, rc, n, step = ns.profile.split(":")
    n = int(n)
    build_fixture(ns.workdir, st, rc, n, ns.seed)
    cp = _cell_paths(ns.workdir, st, rc, n)
    _restore(cp)
    out = _run_worker("profile", {"size": n, "path": cp["path"], "receipts": rc, "seed": ns.seed,
                                  "step": step, "recalls": ns.recalls, "remembers": ns.remembers,
                                  "label": _label(st, rc, n)}, _cell_env(st, cp["keyhome"]))
    print(out["cumulative"])
    print(out["tottime"])
    return 0


# ── rendering ─────────────────────────────────────────────────────────────────────────────────────

def _fmt(x, unit):
    if x is None:
        return "–"
    if unit == "ms":
        return f"{x:.2f}" if x < 100 else f"{x:,.0f}"
    if unit == "s":
        return f"{x:.3f}" if x < 10 else f"{x:,.1f}"
    if unit == "MB":
        return f"{x:.1f}" if x < 1000 else f"{x:,.0f}"
    return str(x)


def summarise(data: dict) -> dict:
    """Per cell: each metric as {median, min, max} over runs (per-run medians/p95s for latencies)."""
    metrics = {
        "remember_median_ms": lambda r: statistics.median(r["remember_ms"]),
        "remember_first_ms": lambda r: r["remember_ms"][0],
        "remember_interleaved_median_ms": lambda r: (statistics.median(r["interleaved_remember_ms"])
                                                     if r.get("interleaved_remember_ms") else None),
        "remember_p95_ms": lambda r: _pct(r["remember_ms"], 95),
        "remember_keyed_median_ms": lambda r: statistics.median(r["remember_keyed_ms"]) if r["remember_keyed_ms"] else None,
        # Warm: every query after the first. The first query after open builds the per-process
        # token and read-guard caches over the whole store and is reported on its own.
        "recall_median_ms": lambda r: statistics.median(r["recall_ms"][1:]),
        "recall_p95_ms": lambda r: _pct(r["recall_ms"][1:], 95),
        "recall_first_ms": lambda r: r["recall_ms"][0],
        "file_mb": lambda r: r["file_bytes_total"] / 1e6,
        "open_s": lambda r: r["phases"]["open"]["seconds"],
        "verify_writes_s": lambda r: r["phases"]["verify_writes"]["seconds"],
        "forget_subject_s": lambda r: r["phases"]["forget_subject"]["seconds"],
        "erasure_certificate_s": lambda r: r["phases"]["erasure_certificate"]["seconds"],
        "peak_rss_mb": lambda r: r["peak_rss_mb"],
        "open_peak_rss_mb": lambda r: r["phases"]["open"]["peak_rss_mb"],
        "rss_after_open_mb": lambda r: r["phases"]["open"]["rss_after_mb"],
    }
    by = {}
    for r in data["runs"]:
        by.setdefault(r["label"], []).append(r)
    out = {}
    for lab, rs in by.items():
        cell = {"store": rs[0]["store"], "receipts": rs[0]["receipts"], "size": rs[0]["size"], "runs": len(rs)}
        for name, f in metrics.items():
            vals = [v for v in (f(r) for r in rs) if v is not None]
            if vals:
                cell[name] = {"median": statistics.median(vals), "min": min(vals), "max": max(vals)}
        out[lab] = cell
    return out


def render(data: dict) -> str:
    summ = summarise(data)
    sizes = sorted({c["size"] for c in summ.values()})
    configs = sorted({(c["store"], c["receipts"]) for c in summ.values()},
                     key=lambda sr: (sr[0], ["off", "on", "signed"].index(sr[1]) if sr[1] in ("off", "on", "signed") else 9))
    lines = []

    def table(title, metric, unit, note=""):
        lines.append(f"#### {title}\n")
        if note:
            lines.append(note + "\n")
        hdr = "| N | " + " | ".join(f"{s} / receipts {r}" for s, r in configs) + " |"
        lines.append(hdr)
        lines.append("|---:|" + "---:|" * len(configs))
        for n in sizes:
            cells = []
            for s, r in configs:
                c = summ.get(_label(s, r, n), {}).get(metric)
                if not c:
                    cells.append("–")
                    continue
                spread = (f" ({_fmt(c['min'], unit)}–{_fmt(c['max'], unit)})" if c["max"] != c["min"] else "")
                cells.append(f"{_fmt(c['median'], unit)}{spread}")
            lines.append(f"| {n:,} | " + " | ".join(cells) + " |")
        lines.append("")

    def exponent_table(title, metric):
        lines.append(f"#### {title}\n")
        lines.append("| step | " + " | ".join(f"{s} / receipts {r}" for s, r in configs) + " |")
        lines.append("|---|" + "---:|" * len(configs))
        for a, b in zip(sizes, sizes[1:]):
            cells = []
            for s, r in configs:
                ca = summ.get(_label(s, r, a), {}).get(metric)
                cb = summ.get(_label(s, r, b), {}).get(metric)
                if not ca or not cb or ca["median"] <= 0 or cb["median"] <= 0:
                    cells.append("–")
                else:
                    cells.append(f"{math.log(cb['median'] / ca['median']) / math.log(b / a):.2f}")
            lines.append(f"| {a:,} → {b:,} | " + " | ".join(cells) + " |")
        lines.append("")

    runs = max((c["runs"] for c in summ.values()), default=0)
    lines.append(f"Each cell: median over {runs} runs, (min–max) across runs. Latency cells are the median "
                 f"of the per-run medians / p95s.\n")
    table("remember() per call, median (ms)", "remember_median_ms", "ms")
    table("remember() per call, p95 (ms)", "remember_p95_ms", "ms")
    table("remember() keyed (superseding) write, median (ms)", "remember_keyed_median_ms", "ms")
    table("remember() right after a recall, median of the interleaved recall→remember loop (ms)",
          "remember_interleaved_median_ms", "ms")
    table("recall() latency, warm median (ms)", "recall_median_ms", "ms")
    table("recall() latency, warm p95 (ms)", "recall_p95_ms", "ms")
    table("First recall() after open, cold (ms)", "recall_first_ms", "ms")
    table("File size on disk, store + all sidecars (MB)", "file_mb", "MB")
    table("Open time (s)", "open_s", "s")
    table("verify_writes() (s)", "verify_writes_s", "s")
    table("erasure_certificate() (s)", "erasure_certificate_s", "s")
    table("forget_subject() of one ~15-record subject (s)", "forget_subject_s", "s")
    table("Peak RSS of the measuring process (MB)", "peak_rss_mb", "MB")
    table("RSS after open (MB)", "rss_after_open_mb", "MB")
    lines.append("### Growth exponents\n")
    lines.append("Slope of log(metric) against log(N) between adjacent sizes: 0 is flat, 1 is linear, "
                 "2 is quadratic. For a per-call metric, 1 already means every call touches the whole store.\n")
    for title, metric in (("remember() median", "remember_median_ms"),
                          ("remember() after a recall", "remember_interleaved_median_ms"),
                          ("recall() median", "recall_median_ms"), ("first recall()", "recall_first_ms"),
                          ("recall() p95", "recall_p95_ms"), ("file size", "file_mb"), ("open", "open_s"),
                          ("verify_writes()", "verify_writes_s"),
                          ("erasure_certificate()", "erasure_certificate_s"), ("peak RSS", "peak_rss_mb")):
        exponent_table(title, metric)
    return "\n".join(lines)


# ── entry point ───────────────────────────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sizes", default=",".join(str(s) for s in DEFAULT_SIZES))
    p.add_argument("--stores", default=",".join(DEFAULT_STORES), help="json,sqlite")
    p.add_argument("--receipts", default=",".join(DEFAULT_RECEIPTS), help="off,on[,signed]")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--recalls", type=int, default=100)
    p.add_argument("--remembers", type=int, default=50)
    p.add_argument("--interleaved", type=int, default=20, help="recall-then-remember pairs")
    p.add_argument("--seed", type=int, default=20260924)
    p.add_argument("--workdir", default=os.path.join(tempfile.gettempdir(), "inspeximus-bench-scale"),
                   help="fixtures and live stores (default: %(default)s)")
    p.add_argument("--out", default=str(ROOT / "audits" / "2026-09-24" / "scale.json"))
    p.add_argument("--resume", action="store_true", help="keep finished runs in --out, do the rest")
    p.add_argument("--keep", action="store_true", help="keep the live stores after the run")
    p.add_argument("--check-fixture", action="store_true")
    p.add_argument("--check-size", type=int, default=1000)
    p.add_argument("--profile", help="store:receipts:size:step, step in open|recall|remember|"
                                     "verify_writes|erasure_certificate")
    p.add_argument("--render", metavar="JSON", help="print the markdown tables for a results file")
    p.add_argument("--_worker", help=argparse.SUPPRESS)
    p.add_argument("--_args", help=argparse.SUPPRESS)
    p.add_argument("--_out", help=argparse.SUPPRESS)
    ns = p.parse_args(argv)

    if ns._worker:
        args = json.loads(ns._args)
        fn = {"build": worker_build, "measure": worker_measure, "profile": worker_profile}[ns._worker]
        Path(ns._out).write_text(json.dumps(fn(args)), encoding="utf-8")
        return 0
    if ns.render:
        print(render(json.loads(Path(ns.render).read_text(encoding="utf-8"))))
        return 0
    if ns.check_fixture:
        return check_fixture(ns)
    if ns.profile:
        return profile_step(ns)
    data = run_matrix(ns)
    print(render(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
