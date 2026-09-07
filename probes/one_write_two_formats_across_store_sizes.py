"""One persisted write, two storage formats, several store sizes -- repeated, with the spread shown.

WHY THIS EXISTS. The row store shipped with a headline of "0.5431 s becomes 0.0341 s", a 16x
speedup. That number was measured on `sqlite_store.save(dirty=[id])`, a helper no application calls,
on a 32,643-record store, once. Measured through the library instead, the answer changes sign twice:
a whole-file rewrite is cheap while the file is small, and `flush()` deliberately takes the complete
diff, which the row format pays for and the JSON format does not.

WHAT IT DOES. For each (size, flush-policy, format) it builds a fresh store, times 30 persisted
appends, and takes the median. Then it does the whole thing again, three independent times. It
reports every trial, not an average of them: one run of ten appends is a number, not a result, and
the spread is what says whether the ordering is real or noise.

SEEDING IS BULK, ON PURPOSE. `remember()` persists immediately, so seeding a 30,000-record store
through it means 30,000 whole-file writes. The first version of this probe did that and ran for five
hours without printing a line. The records are written once, directly, and the library then opens
the result.

CONTROLS. Each arm asserts the store is in the format it claims and that the fixture actually
loaded, so a silently-empty store cannot produce a fast, meaningless number. The verdict per cell is
reported only when all three trials agree on the direction.

RUN IT: python probes/one_write_two_formats_across_store_sizes.py
"""
import json
import os
import shutil
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus                                   # noqa: E402
from inspeximus import sqlite_store as ss                           # noqa: E402

SIZES = (1000, 10000, 30000)
TRIALS = 3
SAMPLES = 30


#: Built once per (format, size) and copied for each trial. Building it every time is most of the
#: run: a 30,000-record store takes seconds to write and a tenth of a second to copy, and the thing
#: under test is one append, not the seeding.
_TEMPLATES: dict = {}


def _template(fmt, n):
    key = (fmt, n)
    if key in _TEMPLATES:
        return _TEMPLATES[key]
    path = os.path.join(tempfile.mkdtemp(), "m.json")
    recs = [{"id": "s%06d" % i, "key": "s%d" % i, "mtype": "fact", "status": "active",
             "text": "seed record %d with a realistic amount of text in it" % i,
             "ts": 1000.0 + i, "last_access": 1000.0 + i, "valid_from": 1000.0 + i,
             "value": 1.0, "tags": [], "links": [], "meta": {},
             "iso": "2026-09-07T00:00:00Z"} for i in range(n)]
    if fmt == "rows":
        ss.save(path, recs, {})
    else:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(recs, fh, ensure_ascii=False)
    _TEMPLATES[key] = path
    return path


def _seed(fmt, n):
    """A FRESH store for this trial, copied from the template so each trial starts identical."""
    src = _template(fmt, n)
    path = os.path.join(tempfile.mkdtemp(), "m.json")
    shutil.copy2(src, path)
    return path


def _median_write(fmt, n, do_flush):
    os.environ.pop("INSPEXIMUS_STORE_FORMAT", None)
    if fmt == "json":
        os.environ["INSPEXIMUS_STORE_FORMAT"] = "json"
    path = _seed(fmt, n)
    m = Inspeximus(path=path)
    m._save_min_s = 0
    assert ss.looks_like_sqlite(path) is (fmt == "rows"), "arm is not in the format it claims"
    assert len(m._items) == n, "fixture did not load: %d of %d" % (len(m._items), n)
    times = []
    for i in range(SAMPLES):
        t0 = time.time()
        m.remember("the appended record %d" % i, key="a%d" % i)
        if do_flush:
            m.flush()
        times.append(time.time() - t0)
    os.environ.pop("INSPEXIMUS_STORE_FORMAT", None)
    return statistics.median(times)


def main():
    out = {"trials": TRIALS, "samples_per_trial": SAMPLES, "cells": []}
    started = time.time()
    for n in SIZES:
        for do_flush in (True, False):
            js, rs = [], []
            for t in range(TRIALS):
                js.append(_median_write("json", n, do_flush))
                rs.append(_median_write("rows", n, do_flush))
                print("    %6d  flush=%-5s trial %d/%d: JSON %.4f s  rows %.4f s  (%.0fs elapsed)"
                      % (n, do_flush, t + 1, TRIALS, js[-1], rs[-1], time.time() - started),
                      flush=True)
            ratios = [j / r for j, r in zip(js, rs)]
            agree = all(x > 1 for x in ratios) or all(x < 1 for x in ratios)
            out["cells"].append({
                "records": n, "flush_every_write": do_flush,
                "json_s": [round(x, 5) for x in js], "rows_s": [round(x, 5) for x in rs],
                "json_median": round(statistics.median(js), 5),
                "rows_median": round(statistics.median(rs), 5),
                "ratio_json_over_rows": [round(x, 2) for x in ratios],
                "all_trials_agree_on_direction": agree})

    print("\n  records  flush   JSON median   rows median   ratio (per trial)      direction")
    for c in out["cells"]:
        print("  %7d  %-5s  %10.4f s  %10.4f s   %-22s %s"
              % (c["records"], c["flush_every_write"], c["json_median"], c["rows_median"],
                 ", ".join("%.2f" % x for x in c["ratio_json_over_rows"]),
                 ("rows faster" if c["rows_median"] < c["json_median"] else "JSON faster")
                 + ("" if c["all_trials_agree_on_direction"] else "  <<< TRIALS DISAGREE")))
    disputed = [c for c in out["cells"] if not c["all_trials_agree_on_direction"]]
    print("\n  cells where the three trials disagree on the direction: %d" % len(disputed))
    print("  total %.0f s" % (time.time() - started))
    if os.environ.get("PYTEST_CURRENT_TEST"):
        # UNDER THE SUITE THIS IS A SMOKE TEST, NOT A MEASUREMENT. Every uncited probe is executed
        # in parallel with several thousand tests, and those numbers are 20% slower with the
        # smallest cell reversed. One such run was committed because it looked like an ordinary
        # change to the working tree. The assertions above still ran; only the receipt is spared.
        print("  running under pytest, so the receipt is NOT rewritten: these numbers describe a "
              "saturated machine.")
        return 0
    path = os.path.splitext(os.path.abspath(__file__))[0] + ".result.json"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(out, indent=1))
    print("  receipt: %s" % os.path.basename(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
