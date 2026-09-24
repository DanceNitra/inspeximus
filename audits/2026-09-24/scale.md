# How inspeximus behaves as a store grows

2026-09-24 · inspeximus 3.9.1 (main at cff4e29) · harness `tools/bench_scale.py` · raw data `scale.json`

## Status: partial

The run was stopped on request to save budget. **Measured: 1k, 10k and 50k records, all four
configurations, ONE run each.** Runs 2 and 3 (the spread) and every 100k measurement were not done;
see [Not run yet](#not-run-yet) for the exact commands. With one run per cell the tables below show
no min–max spread. On this machine the repo's own perf gate records 15–40% run-to-run spread for
wall-clock timings (`perf/gate.py`), so read single timings as ±~40%. The growth conclusions rest on
ratios of 5–10× and more, well outside that band.

## What grows faster than it should

| Step | Growth measured (1k → 10k → 50k) | Where | Why |
|---|---|---|---|
| `verify_writes()` / `erasure_certificate()` with receipts | 0.12 s → 8.9 s → 175 s (JSON), 0.13 s → 10.5 s → 195 s (SQLite); exponent **1.8–2.0** | `core.py:6411–6413` | For every receipt, a set comprehension scans **every** receipt to collect `amends`: O(R²). A profile at 10k puts 8.03 of 9.85 s on that one line. `erasure_certificate()` calls `verify_writes()` (`core.py:10479`) and inherits the cost. |
| `remember()` with receipts on (both stores) | +16–17 → +165–184 → +870–900 ms per call over receipts off | `core.py:3808–3809` | Each write re-encodes the **whole** receipt chain with `indent=2` and fsyncs it: 9.2 MB per write at 10k, 46 MB at 50k. `indent` forces CPython's pure-Python encoder, which is the reason `_dump_store`'s docstring (`core.py:397`) gives for not using it on the store. |
| `remember()` on the JSON store | 10 → 100 → 590 ms per call; exponent 1.0–1.1 **per call**, so O(n²) to fill a store | `core.py:3498` → `16304–16305` → `397` → `2137` | Each write copies every record, serializes the whole store and fsyncs it: 5.1 MB per write at 10k, 25.5 MB at 50k. |
| `remember()` on the SQLite store | 4.1 → 27 → 268 ms per call; exponent 0.8, then **1.4** | `core.py:16305`, `core.py:16381–16382`, `sqlite_store.py:430–443` | The row store writes one row, but the Python around it touches every record. `_save` builds a `vec`-stripped copy of every record that the row path never uses (its own comment at `core.py:16385` says so). It then builds a set of every id plus `set(self._row_snapshot)`. `_save_known` walks every item twice and copies the whole snapshot (`now = dict(before)`). The step above linear from 10k to 50k is one run and not diagnosed. |
| `remember()` right after a `recall()` on SQLite | 12 → 110 → 561 ms, 2–4× the SQLite burst median; same as JSON at 50k (564 ms) | `core.py:12848`, `core.py:1454`, `sqlite_store.py:441–447` | `recall()` writes `r["_stale_derived"]` into every record it **scores**. On the row store that is a `_TrackedDict`, and the write marks the record changed. After one burst of recalls, 953 / 9,574 / 47,958 records were marked (all active records). The next save re-serializes every one of them, only to find no row changed, because `_doc` drops `_`-keys (`sqlite_store.py:218`). In the recall→remember agent loop this is paid on every write. |
| `recall()` warm latency | 1.3 → 23 → 208 ms (JSON), 3.2 → 42 → 321 ms (SQLite) median; exponent 1.0–1.4 | `core.py:12721`, `12737`, `12833`, `12873`, `13010–13011` | No index: each query filters the whole store, runs the read-guard pass, rebuilds an id→record dict and a position dict over the whole store, scores every eligible record, and sorts every scored candidate (O(c log c), which is why the exponent sits above 1). SQLite is about 1.5–2× slower because every field read goes through `_TrackedDict.get` / `__getitem__` in Python (`core.py:1473`, `1480`): 8.2 M calls for 100 queries at 10k. |
| First `recall()` after open | 30–41 ms → 340–510 ms → **2.1–2.6 s** | `core.py:12299`, `core.py:8084` | The first query tokenizes every record and runs the read-guard regexes on every record, once per process. A short-lived process (a hook) pays this on every start, on top of open. |

Linear, as it should be: file size (exponent 1.00), open (0.9–1.1), `forget_subject()` (1.0–1.1),
and `verify_writes()` with receipts off. Memory grows linearly above the ~38 MB interpreter baseline.
It is dominated by the SQLite diff snapshot and the receipt chain; see the memory notes below.

## Machine and software

| | |
|---|---|
| CPU | Intel Xeon @ 2.10 GHz (family 6, model 207; AVX-512), 4 vCPU, KVM guest |
| RAM | 15.7 GB, no swap |
| Disk | ext4 on /dev/vda (virtual block device), page cache warm (every store was just restored) |
| OS | Linux 6.18.44, glibc 2.39 |
| Python | CPython 3.11.15, no numpy (recall is lexical; numpy only accelerates semantic recall) |
| SQLite | 3.45.1 (journal_mode=DELETE, synchronous=NORMAL, secure_delete=ON, as the library sets them) |
| cryptography | 50.0.1 in a venv. The system package here is broken (`_cffi_backend` missing) and makes `import inspeximus` panic, so it could not be used. |
| inspeximus | 3.9.1; harness commit c5dc121 |

## Method

- **Matrix.** Sizes 1k / 10k / 50k (100k not measured). JSON store (`INSPEXIMUS_STORE_FORMAT=json`)
  and SQLite row store (the default). `receipts=False` and `receipts=True` (the unsigned hash chain;
  `--receipts off,on,signed` adds a signed arm, not run).
- **One process per measurement.** The store is restored byte-for-byte to the same absolute path, so
  the chain head kept outside the store is found. Then the store is opened, and `verify_writes()` runs
  on the untouched N-record store. After that: 100 `recall(k=6)` queries, 50 `remember()` writes
  (every 5th keyed onto an existing key, so it supersedes), 20 recall→remember pairs,
  `forget_subject()` of one subject (13–15 records at every N), `erasure_certificate()`, and `flush()`.
- **Latency.** Warm recall = queries 2–100; the first query is reported on its own.
  p95 is nearest-rank.
- **Memory.** Peak RSS per step (VmHWM reset through `/proc/self/clear_refs`). The process peak is
  the max over steps. The `peak_rss_mb` field stored per run in this `scale.json` is NOT that: it
  was read from `ru_maxrss`, which the resets also reset, so it only covers the final `flush` step.
  The harness now records the max of the steps, and the tables use the per-step peaks.
- **Corpus.** Deterministic, seed 20260924. Each record is 8–20 words drawn without replacement from a
  Zipf(1.05) vocabulary of ~4,000 words, sourced to one of five systems (`crm/customer-N`, ...),
  about 15 records per entity; 20% are keyed with an object value.
- **Fixture.** Filling a JSON store through N `remember()` calls is itself O(n²): every call rewrites
  the file. So each fixture is built by the real `remember()` with the store and receipt-sidecar
  writes held back for the first N−1 calls. The patch lives on the build handle only. The N-th call
  runs unpatched and persists everything through the library's own save path. Built both ways at 1k,
  the two stores are identical in every property checked:

  | cell | records | receipts | active / superseded | `verify_writes()` | bytes on disk (organic / shortcut) | build s (organic / shortcut) |
  |---|---|---|---|---|---|---|
  | json, receipts off | 1000 / 1000 | 0 / 0 | 953/47 both | False / False (no chain) | 483,643 / 483,599 | 5.75 / 0.12 |
  | json, receipts on | 1000 / 1000 | 1000 / 1000 | 953/47 both | True / True | 1,404,300 / 1,404,185 | 14.41 / 0.19 |
  | sqlite, receipts off | 1000 / 1000 | 0 / 0 | 953/47 both | False / False (no chain) | 753,664 / 749,568 | 2.73 / 0.20 |
  | sqlite, receipts on | 1000 / 1000 | 1000 / 1000 | 953/47 both | True / True | 1,678,416 / 1,670,196 | 11.61 / 0.24 |

  Reproduce with `python tools/bench_scale.py --check-fixture`. Even the shortcut build grows about
  quadratically (3.2 s → 65 s → 238 s for 10k / 50k / 100k JSON, receipts off, in `scale.json`
  under `fixtures`) with no disk writes at all. That cost is the keyed writes' whole-store scans in
  `remember()` and `_supersede_by_key` (`core.py:3477`, `6973–6977`, `7078`).

## Results

One run per cell, so there are no min–max ranges yet. Latency cells are the run's median and p95.
Tables regenerate with `python tools/bench_scale.py --render audits/2026-09-24/scale.json`.


#### remember() per call, median (ms)

| N | json / receipts off | json / receipts on | sqlite / receipts off | sqlite / receipts on |
|---:|---:|---:|---:|---:|
| 1,000 | 10.21 | 27.37 | 4.14 | 20.34 |
| 10,000 | 99.93 | 265 | 26.73 | 211 |
| 50,000 | 590 | 1,463 | 268 | 1,168 |

#### remember() per call, p95 (ms)

| N | json / receipts off | json / receipts on | sqlite / receipts off | sqlite / receipts on |
|---:|---:|---:|---:|---:|
| 1,000 | 12.74 | 35.55 | 4.83 | 26.43 |
| 10,000 | 136 | 325 | 61.27 | 289 |
| 50,000 | 854 | 1,644 | 349 | 1,423 |

#### remember() keyed (superseding) write, median (ms)

| N | json / receipts off | json / receipts on | sqlite / receipts off | sqlite / receipts on |
|---:|---:|---:|---:|---:|
| 1,000 | 10.27 | 27.09 | 4.74 | 20.92 |
| 10,000 | 104 | 270 | 32.10 | 214 |
| 50,000 | 613 | 1,483 | 261 | 1,139 |

#### remember() right after a recall, median of the interleaved recall→remember loop (ms)

| N | json / receipts off | json / receipts on | sqlite / receipts off | sqlite / receipts on |
|---:|---:|---:|---:|---:|
| 1,000 | 10.65 | 27.69 | 12.24 | 25.55 |
| 10,000 | 98.68 | 260 | 110 | 300 |
| 50,000 | 564 | 1,519 | 561 | 1,618 |

#### recall() latency, warm median (ms)

| N | json / receipts off | json / receipts on | sqlite / receipts off | sqlite / receipts on |
|---:|---:|---:|---:|---:|
| 1,000 | 1.25 | 1.84 | 3.23 | 3.76 |
| 10,000 | 22.85 | 22.54 | 42.03 | 39.53 |
| 50,000 | 208 | 186 | 321 | 304 |

#### recall() latency, warm p95 (ms)

| N | json / receipts off | json / receipts on | sqlite / receipts off | sqlite / receipts on |
|---:|---:|---:|---:|---:|
| 1,000 | 2.04 | 2.68 | 5.25 | 7.02 |
| 10,000 | 50.39 | 59.93 | 90.05 | 94.20 |
| 50,000 | 397 | 371 | 608 | 615 |

#### First recall() after open, cold (ms)

| N | json / receipts off | json / receipts on | sqlite / receipts off | sqlite / receipts on |
|---:|---:|---:|---:|---:|
| 1,000 | 29.63 | 39.27 | 40.98 | 39.83 |
| 10,000 | 373 | 341 | 514 | 448 |
| 50,000 | 2,635 | 2,075 | 2,396 | 2,392 |

#### File size on disk, store + all sidecars (MB)

| N | json / receipts off | json / receipts on | sqlite / receipts off | sqlite / receipts on |
|---:|---:|---:|---:|---:|
| 1,000 | 0.5 | 1.4 | 0.7 | 1.7 |
| 10,000 | 4.9 | 14.1 | 7.1 | 16.4 |
| 50,000 | 24.3 | 70.5 | 35.7 | 81.8 |

#### Open time (s)

| N | json / receipts off | json / receipts on | sqlite / receipts off | sqlite / receipts on |
|---:|---:|---:|---:|---:|
| 1,000 | 0.010 | 0.025 | 0.018 | 0.025 |
| 10,000 | 0.130 | 0.184 | 0.146 | 0.268 |
| 50,000 | 0.793 | 1.161 | 0.912 | 1.485 |

#### verify_writes() (s)

| N | json / receipts off | json / receipts on | sqlite / receipts off | sqlite / receipts on |
|---:|---:|---:|---:|---:|
| 1,000 | 0.000 | 0.123 | 0.034 | 0.125 |
| 10,000 | 0.009 | 8.878 | 0.313 | 10.5 |
| 50,000 | 0.015 | 175.1 | 1.923 | 194.9 |

#### erasure_certificate() (s)

| N | json / receipts off | json / receipts on | sqlite / receipts off | sqlite / receipts on |
|---:|---:|---:|---:|---:|
| 1,000 | 0.001 | 0.101 | 0.033 | 0.134 |
| 10,000 | 0.003 | 9.356 | 0.336 | 10.7 |
| 50,000 | 0.017 | 178.6 | 2.201 | 196.5 |

#### forget_subject() of one ~15-record subject (s)

| N | json / receipts off | json / receipts on | sqlite / receipts off | sqlite / receipts on |
|---:|---:|---:|---:|---:|
| 1,000 | 0.017 | 0.020 | 0.021 | 0.018 |
| 10,000 | 0.199 | 0.202 | 0.199 | 0.227 |
| 50,000 | 1.137 | 1.112 | 1.190 | 1.071 |

#### Peak RSS of the measuring process (MB)

| N | json / receipts off | json / receipts on | sqlite / receipts off | sqlite / receipts on |
|---:|---:|---:|---:|---:|
| 1,000 | 45.2 | 49.4 | 48.4 | 53.3 |
| 10,000 | 101.4 | 142.0 | 127.6 | 172.0 |
| 50,000 | 347.4 | 571.2 | 480.9 | 686.0 |

#### RSS after open (MB)

| N | json / receipts off | json / receipts on | sqlite / receipts off | sqlite / receipts on |
|---:|---:|---:|---:|---:|
| 1,000 | 40.8 | 43.5 | 42.9 | 46.1 |
| 10,000 | 55.5 | 74.4 | 78.0 | 92.5 |
| 50,000 | 124.9 | 219.3 | 227.6 | 301.9 |

### Growth exponents

Slope of log(metric) against log(N) between adjacent sizes: 0 is flat, 1 is linear, 2 is quadratic. For a per-call metric, 1 already means every call touches the whole store.

#### remember() median

| step | json / receipts off | json / receipts on | sqlite / receipts off | sqlite / receipts on |
|---|---:|---:|---:|---:|
| 1,000 → 10,000 | 0.99 | 0.99 | 0.81 | 1.02 |
| 10,000 → 50,000 | 1.10 | 1.06 | 1.43 | 1.06 |

#### remember() after a recall

| step | json / receipts off | json / receipts on | sqlite / receipts off | sqlite / receipts on |
|---|---:|---:|---:|---:|
| 1,000 → 10,000 | 0.97 | 0.97 | 0.95 | 1.07 |
| 10,000 → 50,000 | 1.08 | 1.10 | 1.01 | 1.05 |

#### recall() median

| step | json / receipts off | json / receipts on | sqlite / receipts off | sqlite / receipts on |
|---|---:|---:|---:|---:|
| 1,000 → 10,000 | 1.26 | 1.09 | 1.11 | 1.02 |
| 10,000 → 50,000 | 1.37 | 1.31 | 1.26 | 1.27 |

#### first recall()

| step | json / receipts off | json / receipts on | sqlite / receipts off | sqlite / receipts on |
|---|---:|---:|---:|---:|
| 1,000 → 10,000 | 1.10 | 0.94 | 1.10 | 1.05 |
| 10,000 → 50,000 | 1.21 | 1.12 | 0.96 | 1.04 |

#### recall() p95

| step | json / receipts off | json / receipts on | sqlite / receipts off | sqlite / receipts on |
|---|---:|---:|---:|---:|
| 1,000 → 10,000 | 1.39 | 1.35 | 1.23 | 1.13 |
| 10,000 → 50,000 | 1.28 | 1.13 | 1.19 | 1.17 |

#### file size

| step | json / receipts off | json / receipts on | sqlite / receipts off | sqlite / receipts on |
|---|---:|---:|---:|---:|
| 1,000 → 10,000 | 1.00 | 1.00 | 0.98 | 0.99 |
| 10,000 → 50,000 | 1.00 | 1.00 | 1.00 | 1.00 |

#### open

| step | json / receipts off | json / receipts on | sqlite / receipts off | sqlite / receipts on |
|---|---:|---:|---:|---:|
| 1,000 → 10,000 | 1.11 | 0.87 | 0.91 | 1.03 |
| 10,000 → 50,000 | 1.12 | 1.14 | 1.14 | 1.06 |

#### verify_writes()

| step | json / receipts off | json / receipts on | sqlite / receipts off | sqlite / receipts on |
|---|---:|---:|---:|---:|
| 1,000 → 10,000 | 1.51 | 1.86 | 0.97 | 1.92 |
| 10,000 → 50,000 | 0.35 | 1.85 | 1.13 | 1.82 |

#### erasure_certificate()

| step | json / receipts off | json / receipts on | sqlite / receipts off | sqlite / receipts on |
|---|---:|---:|---:|---:|
| 1,000 → 10,000 | 0.57 | 1.97 | 1.01 | 1.90 |
| 10,000 → 50,000 | 1.07 | 1.83 | 1.17 | 1.81 |

#### peak RSS

| step | json / receipts off | json / receipts on | sqlite / receipts off | sqlite / receipts on |
|---|---:|---:|---:|---:|
| 1,000 → 10,000 | 0.35 | 0.46 | 0.42 | 0.51 |
| 10,000 → 50,000 | 0.77 | 0.86 | 0.82 | 0.86 |

## Notes on the slowdowns

**`verify_writes()` is quadratic, and it is the audit path.** With receipts on, 50k records take about
3 minutes per call on this machine, and `erasure_certificate()` pays it again. The measured exponent of
1.8–2.0 projects about 10–12 minutes per call at 100k (a projection, not a measurement). The
comprehension at `core.py:6411` only needs "which fields did a LATER receipt for this memory declare
it amends". One backward pass that collects `amends` per `memory_id` would answer that for every
receipt in O(R). Not changed here: the task was to measure, not to modify the library. A second
quadratic is latent at `core.py:6382`: for each receipt whose record is gone, every tombstone is
scanned. The benchmark erases a constant 15 records, so it does not show, but it is
O(erased × tombstones) on a store with many erasures.

**The receipt sidecar makes the SQLite store about as slow as JSON.** At 50k, SQLite `remember()`
goes from 268 ms to 1,168 ms with receipts on, against 1,463 ms for JSON with receipts. The row store
writes one row, but the receipt chain is still one JSON document rewritten in full, pretty-printed,
on every write (`core.py:3808`).

**Reads leave work for the next write.** `recall()` annotates every scored record with
`_stale_derived` (`core.py:12848`). The comment at `core.py:6301` says it is set "on the records it
returns"; it is set on every scored candidate.
- SQLite: the annotation marks the record changed, so the next write re-serializes every record the
  recall scored.
- JSON: the annotation is saved into the file. The store grew 4.9 → 5.1 MB at 10k and 24.3 → 25.5 MB
  at 50k after the first recall and write. In a separate 200-record check, `_stale_derived` was in 200
  of 201 records.

**Opening.** Open is linear, but both formats do avoidable per-record work.
- JSON: `r.setdefault("mtype", _infer_type(...))` runs the type-inference regexes for every record
  even when `mtype` is present, because a `setdefault` argument is evaluated eagerly (`core.py:8903`).
  That is about 40% of JSON open time in a 10k profile.
- SQLite: open parses every row, wraps it in `_TrackedDict` (`core.py:8778`), then re-serializes
  every record into the diff snapshot (`core.py:8779`, `sqlite_store.py:254–261`).
- With receipts on, the whole chain is parsed as well.

**Memory.** RSS after open at 50k:

| config | RSS after open |
|---|---|
| JSON, receipts off | 125 MB |
| SQLite, receipts off | 228 MB |
| JSON, receipts on | 219 MB |
| SQLite, receipts on | 302 MB |

The SQLite store keeps a serialized copy of every row in `_row_snapshot` beside the parsed records.
The receipt chain adds 75–95 MB at 50k. Peaks come from different steps per configuration:

| config | peak comes from | why |
|---|---|---|
| JSON | `remember()` | the whole store as one string |
| JSON, receipts on | `remember()` | the pretty-printed chain |
| SQLite | `erasure_certificate()` / `verify_writes()` | re-reads every row from disk to compare (`core.py:6288–6330`) and re-parses the receipt sidecar (`core.py:6648–6650`) |

Peak at 50k: 347 / 571 / 481 / 686 MB (JSON off / JSON on / SQLite off / SQLite on).

## Not run yet

Not measured: **runs 2 and 3 for 1k, 10k and 50k** (so there is no spread yet), and **all three runs
at 100k** for every configuration. The 100k fixtures were built; their build metadata is in
`scale.json` under `fixtures`. The first 100k measurement was interrupted and saved nothing.

Run from the repository root with Python ≥ 3.9. `cryptography` is needed only for
`--receipts ...,signed`, but a broken install of it makes `import inspeximus` fail. If that happens,
use a venv with `pip install "cryptography>=41"`.

Finish everything on one machine, into a new results file:

```sh
python tools/bench_scale.py --out audits/2026-09-24/scale-local.json
```

That is the full default matrix: sizes 1k,10k,50k,100k; json,sqlite; receipts off,on; 3 runs each.
Only the remaining sizes:

```sh
python tools/bench_scale.py --sizes 100000 --runs 3 --out audits/2026-09-24/scale-100k.json
python tools/bench_scale.py --sizes 1000,10000,50000 --runs 3 --out audits/2026-09-24/scale-1k-50k.json
```

The runs print progress to stderr and save the results file after every run. After an interruption,
add `--resume` with the same `--out` and `--workdir` to skip finished runs and built fixtures.
`--render <file>` prints these tables.

Do not `--resume` into this `scale.json` from a different machine. Its `machine` block describes the
machine above, and mixing machines in one file makes the spread meaningless. Budget, estimated from
the 50k numbers: on this machine one pass over the four 100k configurations takes about an hour,
and all three runs about 3.5 hours.
Each receipts-on cell spends about 10–12 minutes (projected) in each of `verify_writes()` and
`erasure_certificate()`.

## Caveats

- One run per cell (see Status). No spread is reported yet.
- One machine, a KVM guest, warm page cache. fsync cost on other storage will move the JSON
  `remember()` numbers.
- Lexical recall only (no embedder); receipts unsigned; synthetic corpus without `derived_from`
  lineage.
