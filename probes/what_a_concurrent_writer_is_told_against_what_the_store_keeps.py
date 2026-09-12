"""Four stores, the same concurrent writers: how much of what a writer was TOLD landed actually did.

WHY THIS REPLACES THE EARLIER COMPARISON. `twelve_writers_and_the_one_that_stopped_writing.py`
measured our row store against our own JSON store and reported the row store losing nothing while
JSON lost 44 percent. A pre-draft skeptic took that apart on 2026-09-12 and it was right on the part
that mattered: THE JSON ARM WAS NEVER GIVEN ITS OWN DOCUMENTED REMEDY. `StoreChangedOnDisk` says
"Call reload() to merge the two and retry", the row path performs that union automatically, and the
JSON workers caught the exception with `pass`. So 44 percent measured a caller who ignores the
recovery path, not a format that cannot survive concurrency. Adding the retry took JSON to 192 of
192 at 24 writers.

It was also the wrong opponent. Beating our own older backend is a comparison we control on both
sides. This file adds mem0, which is the most adopted product in this category, running entirely
locally so the arm costs nothing and needs no key.

THE FOUR ARMS, and each exists to remove one objection:

  rows          our row store, the shipped default
  json-naive    our JSON store with the exception swallowed, as the old probe ran it
  json-retry    our JSON store given the recovery its own error message prescribes
  mem0          mem0 OSS, local qdrant + local sqlite history, infer=False

`json-naive` against `json-retry` is the honest control: if the gap between them is most of the
loss, then the story is about a caller's discipline rather than about a format, and this file has to
say so in its own receipt.

WHAT IS AND IS NOT MEASURED. This measures the WRITE PATH: does a record a writer was told was
stored come back out. `infer=False` skips mem0's LLM extraction on purpose, because extraction
quality is a different axis and one we have no business grading here. It also keeps the arm free and
deterministic. Nothing here says anything about retrieval accuracy, which the owner's board calls a
measured dead end.

The mem0 arm needs a local Ollama with `nomic-embed-text`. Without it the arm is SKIPPED and the
receipt records that it was skipped, because an arm that quietly did not run is worse than a missing
one.

RUN IT: python probes/what_a_concurrent_writer_is_told_against_what_the_store_keeps.py
Separate OS processes throughout, so the interpreter lock cannot hide the race.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, REPO)

from _receipt import write_receipt                              # noqa: E402

OLLAMA = "http://127.0.0.1:11434"

#: Every worker prints one WROTE line per record it was TOLD was stored, then its own count. The
#: comparison is against what the writers CLAIMED, never against what they attempted: a writer that
#: exhausted its retries and said so is a load limit, while a writer that reported success and lost
#: the record is the silent loss this file exists to catch.
OURS = '''
import sys, time, random
sys.path.insert(0, %(repo)r)
import os
os.environ["INSPEXIMUS_STORE_FORMAT"] = %(fmt)r
from inspeximus import Inspeximus
from inspeximus.core import StoreChangedOnDisk
if os.environ.get("INSPEXIMUS_PROBE_FORCE_UNLOCKED"):
    # THE CONTROL. Take the platform lock away so the run reproduces the one loss mechanism this
    # project has already explained. If the DEGRADED line stays quiet here, the diagnostic below is
    # blind and its silence on an ordinary run means nothing.
    import inspeximus.core as _core
    _core._LOCK_PRIMITIVE = (None, None)
RETRY = %(retry)s
path, wid, n = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
m = Inspeximus(path=path)
m._save_min_s = 0
wrote = []
for i in range(n):
    text = "w%%d r%%d uniq-%%d-%%d" %% (wid, i, wid, i)
    for attempt in range(12 if RETRY else 1):
        try:
            m.remember(text, key="w%%d::%%d" %% (wid, i), mtype="fact")
            m.flush()
            wrote.append(text)
            break
        except StoreChangedOnDisk:
            if not RETRY:
                break
            # THE REMEDY THE PRODUCT'S OWN ERROR PRESCRIBES. Leaving this out is what made the
            # earlier comparison unfair to the JSON arm.
            time.sleep(random.uniform(0.005, 0.03) * (attempt + 1))
            m = Inspeximus(path=path)
            m._save_min_s = 0
        except Exception:
            break
for t in wrote:
    print("WROTE\\t" + t)
# WAS THE LOCK HELD WHEN A RECORD WENT MISSING? That one field separates the two candidate
# explanations, and without it every loss here looked alike. Measured on this harness: with the
# lock held, 0 of 448 records went missing; with it degraded, writers report success and records
# vanish. A MISSING count above zero while this reports 0 is a race this project cannot yet explain.
try:
    from inspeximus.core import _StoreLock
    print("DEGRADED\\t%%d\\t%%s" %% (sum(_StoreLock.DEGRADED.values()),
          "; ".join(_StoreLock.DEGRADED_WHY.values()) or "-"))
except Exception as _e:
    print("DEGRADED\\t-1\\t%%r" %% (_e,))
'''

MEM0 = '''
import sys, os, warnings
warnings.filterwarnings("ignore")
os.environ.pop("OPENAI_API_KEY", None)
d, wid, n = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
from mem0 import Memory
cfg = {
  "vector_store": {"provider": "qdrant", "config": {"path": os.path.join(d, "qd"),
                                                    "on_disk": True, "embedding_model_dims": 768}},
  "history_db_path": os.path.join(d, "history.db"),
  "embedder": {"provider": "ollama", "config": {"model": "nomic-embed-text",
                                                "ollama_base_url": %(ollama)r,
                                                "embedding_dims": 768}},
  "llm": {"provider": "ollama", "config": {"model": "qwen2.5:7b", "ollama_base_url": %(ollama)r}},
}
wrote = []
try:
    m = Memory.from_config(cfg)
except Exception as e:
    print("INIT_FAILED\\t%%r" %% (e,))
    raise SystemExit(0)
for i in range(n):
    text = "w%%d r%%d uniq-%%d-%%d" %% (wid, i, wid, i)
    try:
        # infer=False stores the text verbatim: this measures the write path, not extraction.
        m.add(text, user_id="shared", infer=False)
        wrote.append(text)
    except Exception:
        pass
for t in wrote:
    print("WROTE\\t" + t)
'''


def ollama_ready() -> bool:
    """True only if the daemon answers AND the embedder we ask for is present."""
    try:
        with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=5) as r:
            names = [m.get("name", "") for m in json.loads(r.read()).get("models", [])]
        return any(n.startswith("nomic-embed-text") for n in names)
    except Exception:                                            # noqa: BLE001
        return False


def _count_ours(path: str) -> set:
    from inspeximus import sqlite_store as ss
    if ss.looks_like_sqlite(path):
        rows = ss.load(path)
    else:
        try:
            with open(path, encoding="utf-8") as fh:
                rows = json.load(fh)
        except Exception:                                        # noqa: BLE001
            return set()
    return {r.get("text") or r.get("value") or "" for r in rows}


def _count_mem0(d: str) -> set:
    import warnings
    warnings.filterwarnings("ignore")
    os.environ.pop("OPENAI_API_KEY", None)
    from mem0 import Memory
    cfg = {
        "vector_store": {"provider": "qdrant", "config": {"path": os.path.join(d, "qd"),
                                                          "on_disk": True,
                                                          "embedding_model_dims": 768}},
        "history_db_path": os.path.join(d, "history.db"),
        "embedder": {"provider": "ollama", "config": {"model": "nomic-embed-text",
                                                      "ollama_base_url": OLLAMA,
                                                      "embedding_dims": 768}},
        "llm": {"provider": "ollama", "config": {"model": "qwen2.5:7b", "ollama_base_url": OLLAMA}},
    }
    m = Memory.from_config(cfg)
    got = m.get_all(filters={"user_id": "shared"})
    items = got.get("results", got) if isinstance(got, dict) else got
    return {i.get("memory") or "" for i in items}


def trial(arm: str, writers: int, per: int, force_unlocked: bool = False) -> dict:
    work = tempfile.mkdtemp(prefix="cw_")
    src = os.path.join(work, "w.py")
    if arm == "mem0":
        body, target = MEM0 % {"ollama": OLLAMA}, work
    else:
        fmt = "rows" if arm == "rows" else "json"
        body = OURS % {"repo": REPO, "fmt": fmt, "retry": arm == "json-retry"}
        target = os.path.join(work, "memory.json")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write(body)

    t0 = time.time()
    env = dict(os.environ)
    env.pop("INSPEXIMUS_PROBE_FORCE_UNLOCKED", None)
    if force_unlocked:
        env["INSPEXIMUS_PROBE_FORCE_UNLOCKED"] = "1"
    procs = [subprocess.Popen([sys.executable, src, target, str(w), str(per)],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              text=True, encoding="utf-8", errors="replace", env=env)
             for w in range(writers)]
    claimed, init_failed, degraded, why = [], 0, 0, ""
    for p in procs:
        out, _ = p.communicate()
        for line in (out or "").splitlines():
            if line.startswith("WROTE\t"):
                claimed.append(line.split("\t", 1)[1])
            elif line.startswith("INIT_FAILED"):
                init_failed += 1
            elif line.startswith("DEGRADED\t"):
                # ASK EVERY WORKER WHETHER THE LOCK HELD. Without this a loss is unattributable:
                # a degraded lock and an unexplained race produce the same MISSING count.
                parts = line.split("\t")
                degraded += max(0, int(parts[1]))
                if len(parts) > 2 and parts[2] not in ("-", ""):
                    why = parts[2]

    on_disk = _count_mem0(target) if arm == "mem0" else _count_ours(target)
    missing = [t for t in claimed if t not in on_disk]
    return {"arm": arm, "writers": writers, "attempted": writers * per,
            "claimed": len(claimed), "on_disk": len(on_disk), "missing": len(missing),
            "init_failed": init_failed, "degraded_writes": degraded, "degraded_why": why,
            "seconds": round(time.time() - t0, 1)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per", type=int, default=8)
    ap.add_argument("--trials", type=int, default=1 if os.environ.get("PYTEST_CURRENT_TEST") else 3)
    ap.add_argument("--widths", default=None, help="comma separated, default 2,12")
    a = ap.parse_args()
    widths = [int(x) for x in a.widths.split(",")] if a.widths else [2, 12]

    arms = ["rows", "json-naive", "json-retry"]
    mem0_ok = ollama_ready()
    if mem0_ok:
        arms.append("mem0")
    else:
        print("  mem0 arm SKIPPED: no local Ollama with nomic-embed-text at %s" % OLLAMA)

    out = {"probe": os.path.basename(__file__),
           "question": "of the records a concurrent writer was TOLD were stored, how many are there",
           "per_writer": a.per, "trials": a.trials, "widths": widths,
           "mem0_arm_ran": mem0_ok, "results": []}

    for writers in widths:
        for arm in arms:
            for t in range(a.trials):
                r = trial(arm, writers, a.per)
                out["results"].append(r)
                print("  %-11s %2d writers, trial %d/%d: claimed %d, on disk %d, MISSING %d  (%.0fs)"
                      % (arm, writers, t + 1, a.trials, r["claimed"], r["on_disk"], r["missing"],
                         r["seconds"]), flush=True)

    print()
    # THREE NUMBERS, NOT ONE, because "missing" alone scored mem0 as perfect while one of its two
    # writers never started. Measured 2026-09-12: mem0's local qdrant refuses the second process
    # with "Storage folder ... is already accessed by another instance of Qdrant client. If you
    # require concurrent access, use Qdrant server instead." Nothing is lost and nothing is written,
    # and a summary that reports only silent loss calls that a clean run.
    #
    #   attempted  what the writers set out to write
    #   claimed    what they were TOLD was stored
    #   missing    claimed records that are not in the store  -- the silent loss
    #   blocked    writers that could not open the store at all -- refused, not lost
    summary = {}
    for arm in arms:
        rs = [r for r in out["results"] if r["arm"] == arm]
        summary[arm] = {"attempted": sum(r["attempted"] for r in rs),
                        "claimed": sum(r["claimed"] for r in rs),
                        "missing": sum(r["missing"] for r in rs),
                        "writers_blocked": sum(r["init_failed"] for r in rs),
                        "degraded_writes": sum(r.get("degraded_writes", 0) for r in rs),
                        "degraded_why": next((r["degraded_why"] for r in rs
                                              if r.get("degraded_why")), ""),
                        "clean_trials": sum(1 for r in rs if r["missing"] == 0),
                        "trials": len(rs)}
        s = summary[arm]
        print("  %-11s attempted %4d | told stored %4d | MISSING %3d | writers refused %2d | "
              "%d of %d trials without silent loss"
              % (arm, s["attempted"], s["claimed"], s["missing"], s["writers_blocked"],
                 s["clean_trials"], s["trials"]))
    out["summary"] = summary

    print()
    print("  MISSING is a broken promise. REFUSED is an honest no. They are different failures and")
    print("  an arm with zero of the first and many of the second is not concurrent, only safe.")

    # WHAT THE LOCK WAS DOING WHILE THE RECORDS WENT MISSING. Report it in both directions: a loss
    # under a degraded lock is a known mechanism with a known remedy, and a loss under a held lock
    # is the race this project has looked for since the first red CI run and has not reproduced.
    print()
    for arm in ("rows", "json-naive", "json-retry"):
        s = summary.get(arm)
        if not s:
            continue
        if s["degraded_writes"]:
            print("  %-11s ran with a DEGRADED lock on %d writes: %s"
                  % (arm, s["degraded_writes"], s["degraded_why"] or "reason not reported"))
        elif s["missing"]:
            print("  %-11s lost %d records with the lock HELD on every write. That is the "
                  "unexplained race, not the known degraded-lock path." % (arm, s["missing"]))
        else:
            print("  %-11s lock held on every write, nothing missing." % arm)

    # THE CONTROL THE SKEPTIC ASKED FOR. If json-retry loses about as little as rows, then the
    # earlier 44 percent was the caller and not the format, and this receipt has to say it out loud
    # rather than let the headline stand.
    naive, retry = summary.get("json-naive"), summary.get("json-retry")
    if naive and retry:
        closed = naive["missing"] - retry["missing"]
        out["retry_closed_records"] = closed
        print()
        # THE FIRST VERSION OF THIS LINE PRINTED "closed -1 of the 0 records". It assumed the naive
        # arm always loses at least as much as the retry arm. Measured 2026-09-12 across two runs of
        # this file: naive lost 1 and retry 0, then naive lost 0 and retry 1. The silent loss lands
        # in whichever arm is unlucky, so it is not a property of the caller's discipline. What the
        # retry demonstrably fixes is REFUSALS -- naive was told 216 of 336 were stored, retry 336 --
        # and that is a different failure from losing a record you were promised.
        if naive["missing"] == retry["missing"] == 0:
            print("  neither json arm lost a record this run, so this run says nothing about the "
                  "retry; it is a rare event and needs more trials than %d." % naive["trials"])
        elif closed > 0:
            print("  the documented retry closed %d of the %d records json lost without it"
                  % (closed, naive["missing"]))
        else:
            print("  the retry did NOT help this run: naive lost %d, retry lost %d. The silent loss "
                  "is rare and lands in either arm, which is evidence it is one race rather than a "
                  "consequence of skipping the recovery path."
                  % (naive["missing"], retry["missing"]))
        print("  what the retry DOES fix is refusals: naive was told %d of %d were stored, retry %d."
              % (naive["claimed"], naive["attempted"], retry["claimed"]))
    # THE CONTROL FOR THE DIAGNOSTIC ITSELF, and it runs on every invocation rather than on a flag,
    # because a control you have to remember to pass is a control that is not run. The rows arm is
    # repeated with the platform lock removed in the workers. If that run does not report a degraded
    # write, the DEGRADED field above cannot see the one mechanism we already understand, and every
    # "lock held on every write" line in this receipt is an artifact of a blind instrument.
    print()
    c = trial("rows", max(2, min(widths)), a.per, force_unlocked=True)
    out["control_lock_removed"] = c
    out["control_fired"] = c["degraded_writes"] > 0
    if c["degraded_writes"] > 0:
        # WHAT THIS CONTROL DOES AND DOES NOT SHOW. It proves the DEGRADED field reaches this
        # summary, which is all it is for. It does NOT predict a loss: measured 2026-09-12, the rows
        # arm lost 0 of 16 records with our lock removed entirely, because SQLite serialises the
        # writers by itself. So on the rows arm our lock is a second line, not the only one, and a
        # missing record there is not explained by pointing at the lock.
        print("  CONTROL: with the platform lock removed, %d writes reported themselves unprotected, "
              "so the diagnostic can fire. Records missing under that lock: %d of %d (SQLite "
              "serialises the writers on its own, so this number is not expected to rise)."
              % (c["degraded_writes"], c["missing"], c["claimed"]))
    else:
        print("  CONTROL DID NOT FIRE: the lock was removed and no writer noticed. Every lock line "
              "above is void, not reassuring.")
    write_receipt(__file__, out)
    return 0 if out["control_fired"] else 2


if __name__ == "__main__":
    sys.exit(main())
