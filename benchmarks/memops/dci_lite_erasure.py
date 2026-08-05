"""The winning baseline of the agentic-memory literature, measured on the axis that literature omits.

WHY THIS ARM EXISTS. "Exploring Cross-Scenario Generality of Agentic Memory Systems: Diagnostics and
a Strong Baseline" (arXiv 2606.04315) evaluates eight memory systems across five scenarios and reports
that the best cross-task ranking belongs to DCI-Lite: "an agent harness that keeps evidence in raw text
and lets the LLM compose a per-question retrieval plan via grep and read". The paper evaluates task
accuracy only. It does not ask whether that harness can remove anything.

That question is not rhetorical for us, and the honest half has to be said first: our own pilot in this
directory measured our correction layer at 0.593 against a keep-everything store's 0.592 and published
the null. We agree with the paper about accuracy. What neither run has measured is what a raw-text
agentic store does when a user asks it to forget, and whether it does the same thing twice.

WHAT IS MEASURED. Per scenario, both arms ingest the same units and are then asked to erase the same
value. Four numbers, none of them LLM-judged -- every one is a string or byte check:

    retrieval_leakage   the deleted value still surfaces in the arm's own retrieved context
    paraphrase_residue  a surface form of it does
    raw_residue_files   files on disk that still contain it after deletion
    deterministic       the SAME ingest and the SAME deletion request, run 3x, lands byte-identical

FAIRNESS, binding, inherited from ERASURE_REVERT_SPEC.md. The claim is never "it cannot do this".
DCI-Lite is scored on the best path its own design exposes: its agent composes a grep plan, reads what
matches, and decides what to remove, which is exactly the loop the paper describes, extended to the
write side in the only way a self-managed raw-text store can be. It gets the strongest local model
available, not the cheapest, because a weak model makes a competitor look bad for a reason that has
nothing to do with its design -- a confound this lab has already paid for once. The model is recorded
with every row and the run is repeated on a second model, so model sensitivity is visible rather than
assumed.

CONTROLS, because this measurement is one we would like to win and that is exactly when a rigged
harness slips through:
  * POSITIVE, per scenario per arm: the value must be retrievable BEFORE deletion. A scenario where
    the arm never had the fact cannot demonstrate erasure, and counting it would score ignorance as
    compliance. Skipped and reported, not silently dropped.
  * DETERMINISM, on the deterministic arm: inspeximus must land 3/3 identical. If it does not, the
    determinism check is broken and no verdict about the other arm survives.
  * NEGATIVE: a value never asked to be forgotten must STILL be present afterwards in both arms. An
    arm that deletes everything scores a perfect zero on leakage and is useless.

Run:  python dci_lite_erasure.py --n 12 --repeats 3 --models qwen3:30b-a3b,gemma4:12b
"""
import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import statistics
import sys
import tempfile
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ.setdefault("MEMOPS_TOPK", "150")
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pilot  # noqa: E402
from erasure_revert_probe import aliases, norm  # noqa: E402
from inspeximus.core import Inspeximus, regex_extractor  # noqa: E402

OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
CTX_BUDGET = 12000          # identical for both arms; the pilot's own matched-budget rule


# ------------------------------------------------------------------ the model
def ask(model, prompt, num_predict=16000):
    """One completion. num_predict is a FLOOR, never a cap: a reasoning model that spends its budget
    thinking and emits nothing is the recurring failure here, and a truncated answer would be scored
    as the competitor failing to act."""
    body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                       "options": {"temperature": 0, "num_predict": num_predict}}).encode()
    req = urllib.request.Request(OLLAMA + "/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return (json.load(r).get("response") or "")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            if attempt == 2:
                raise
            time.sleep(3)
    return ""


def strip_think(s):
    return re.sub(r"<think>.*?</think>", "", s or "", flags=re.S).strip()


# ------------------------------------------------------------------ the DCI-Lite store
class RawTextStore:
    """Evidence kept in raw text; the agent reaches it with grep and read. Nothing is indexed and
    nothing is rewritten on ingest -- that is the whole point of the design being replicated."""

    patterns_seen = 0
    patterns_bad = 0
    patterns_empty = 0

    def __init__(self, path):
        self.path = path
        self.lines = []

    def append(self, text):
        self.lines.append({"id": len(self.lines), "text": text})

    def flush(self):
        with open(self.path, "w", encoding="utf-8") as f:
            for r in self.lines:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def grep(self, pattern, limit=400):
        """Run one pattern. The model writes greps the way a person does -- `/dept/i`, `'dept'`,
        `grep -i dept` -- and Python's re treats those delimiters as literal characters, so the
        pattern matches nothing and the arm silently retrieves zero. Measured: the positive control
        failed on 18 of 20 dci_lite runs for exactly this reason, which is a defect in this harness,
        not a property of the design being replicated (ERASURE_REVERT_SPEC fairness rule 4: any zero
        from a competitor is our bug until a positive control says otherwise). Delimiters are
        stripped, and a pattern that still matches nothing falls back to a literal search of its own
        word characters before it is allowed to count as an empty result."""
        pat = (pattern or "").strip().strip("`").strip()
        pat = re.sub(r"^(grep\s+(-\w+\s+)*)", "", pat)
        m = re.match(r"^/(.*)/[a-z]*$", pat)           # /body/flags
        if m:
            pat = m.group(1)
        pat = pat.strip("/").strip("'\"")
        RawTextStore.patterns_seen += 1
        hits = []
        try:
            rx = re.compile(pat, re.I)
            hits = [r for r in self.lines if rx.search(r["text"])]
        except re.error:
            RawTextStore.patterns_bad += 1
        if not hits:
            literal = " ".join(re.findall(r"[\w.@$-]{3,}", pat))
            if literal:
                lo = literal.lower()
                hits = [r for r in self.lines if lo in r["text"].lower()]
                if not hits:
                    for tok in literal.split():
                        t = tok.lower()
                        hits += [r for r in self.lines if t in r["text"].lower()]
            if not hits:
                RawTextStore.patterns_empty += 1
        out, seen = [], set()
        for r in hits:
            if r["id"] not in seen:
                seen.add(r["id"])
                out.append(r)
        return out[:limit]

    def read(self, ids):
        want = set(ids)
        return [r for r in self.lines if r["id"] in want]

    def delete(self, ids):
        want = set(ids)
        before = len(self.lines)
        self.lines = [r for r in self.lines if r["id"] not in want]
        self.flush()
        return before - len(self.lines)

    def hash(self):
        return hashlib.sha256(json.dumps([r["text"] for r in self.lines],
                                         ensure_ascii=False).encode()).hexdigest()


#: A retrieval plan depends on the QUESTION, not on what the store currently holds, so the same
#: question asked before and after a deletion produces the same greps. Caching them in-process is
#: therefore free of semantic effect and cuts the run from hours to minutes. It is an in-process
#: dict on purpose -- a shared cache FILE has corrupted a probe here before.
_PLAN_CACHE = {}


def dci_patterns(model, question):
    """The agent composes its retrieval plan. Faithful to the paper: the LLM writes the grep."""
    ck = (model, question)
    if ck in _PLAN_CACHE:
        return _PLAN_CACHE[ck]
    out = strip_think(ask(model,
        "You search a plain-text memory with grep. Write up to 3 case-insensitive regular "
        "expressions that would find the records needed to answer the question.\n"
        f"QUESTION: {question}\n"
        "Reply with ONLY the patterns, one per line, no numbering, no explanation."))
    pats = [p.strip().strip("`") for p in out.splitlines() if p.strip() and len(p.strip()) < 200]
    plan = pats[:3] or [re.escape(question.split()[-1])]
    _PLAN_CACHE[ck] = plan
    return plan


def dci_context(model, store, question):
    seen, ctx = set(), []
    for p in dci_patterns(model, question):
        for r in store.grep(p):
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            ctx.append("- " + r["text"])
    return norm("\n".join(ctx)[:CTX_BUDGET])


def dci_forget(model, store, target):
    """The write side, in the only shape a self-managed raw-text store allows: the agent greps for
    what the request refers to, reads the matches, and names the records to remove."""
    calls = 0
    pats = dci_patterns(model, f"records mentioning {target}")
    calls += 1
    cands, seen = [], set()
    for p in pats:
        for r in store.grep(p):
            if r["id"] not in seen:
                seen.add(r["id"])
                cands.append(r)
    if not cands:
        return 0, calls, len(cands)
    listing = "\n".join(f'{r["id"]}: {r["text"][:200]}' for r in cands[:120])
    out = strip_think(ask(model,
        "The user has asked you to forget a fact. Below are records from their memory file.\n"
        f"FORGET: {target}\n\nRECORDS:\n{listing}\n\n"
        "Reply with ONLY the id numbers of the records that must be deleted so the fact is gone, "
        "comma-separated. Reply NONE if no record carries it. Do not delete records about other "
        "facts the user did not ask you to forget."))
    calls += 1
    ids = [int(x) for x in re.findall(r"\b\d+\b", out)][:200]
    return store.delete(ids), calls, len(cands)


# ------------------------------------------------------------------ the deterministic arm
def build_inspeximus(lc, path):
    m = Inspeximus(path=path)
    m.extractor = regex_extractor
    m.echo_guard = True
    for _si, role, sent in pilot.units_of(lc):
        try:
            m.remember(sent, tags=[role])
        except Exception:
            pass
    m._save(force=True)
    return m


def insp_context(m, q):
    return norm(pilot.recall_inspeximus(m, q, True)[:CTX_BUDGET])


def insp_hash(m):
    return hashlib.sha256(json.dumps(
        sorted((r.get("text", ""), r.get("status", "")) for r in m.items),
        ensure_ascii=False).encode()).hexdigest()


# ------------------------------------------------------------------ one scenario, one arm
def deletion_key(target, retained, units):
    """The string a competent engineer would delete on: the most specific surface form of the target
    that ACTUALLY OCCURS in what was ingested.

    The first version took the first comma-segment, which is what the sibling probe does. On these
    scenarios that is often a whole descriptive clause -- "Lucas (Argentine friend) got Italian dual
    citizenship, took about 14 m" -- and it appears verbatim in 0 of 3,294 ingested units. Deleting on
    it removes nothing, the value was never reachable, and the positive control correctly reported
    that nothing was being measured. Returns None when no form of the target is present at all, which
    makes the scenario unusable rather than a zero.
    """
    blob = [norm(u) for u in units]
    keep = {norm(r) for r in retained}

    def present(f):
        return len(f) > 3 and f not in keep and any(f in u for u in blob)

    forms = sorted(aliases(target, retained) | {norm(target), norm(target.split(",")[0])},
                   key=len, reverse=True)
    for f in forms:
        if present(f):
            return f
    # Fallback: a contiguous word n-gram of the target, chosen by how IDENTIFYING it is. Without a
    # fallback, a target phrased as a description -- "ibuprofen ~400mg as needed for knee pain" --
    # is excluded even though the word an engineer would delete on is sitting right there. But
    # picking the LONGEST present n-gram picked "as needed for" over "ibuprofen", a generic phrase
    # that would delete unrelated records: length is not specificity. Rarest-in-corpus is, so the
    # key is the n-gram matching the fewest records, ties broken by the longer string.
    w = norm(target).split()
    best = None
    for n in range(len(w), 0, -1):
        for i in range(len(w) - n + 1):
            g = " ".join(w[i:i + n])
            if not present(g):
                continue
            c = sum(1 for u in blob if g in u)
            if best is None or (c, -len(g)) < (best[1], -len(best[0])):
                best = (g, c)
    return best[0] if best else None


def run_arm(arm, model, lc, ev, repeats, primary=None):
    ops = [o for o in (ev.get("operations") or []) if o.get("validity") == "confirmed"]
    forgets = [o for o in ops if o.get("type") == "forget" and o.get("old_value")]
    target = forgets[0]["old_value"]
    retained = [o["new_value"] for o in ops
                if o.get("type") == "remember" and o.get("new_value")
                and (primary or "") not in norm(o["new_value"])]
    al = aliases(target, retained) - {primary}
    probes = [a["question"] for a in (lc.get("answer") or []) if a.get("question")][:8]
    if not probes:
        return None

    hashes, row = [], None
    for rep in range(repeats):
        d = tempfile.mkdtemp(prefix=f"dci_{arm}_")
        try:
            if arm == "inspeximus":
                store = build_inspeximus(lc, os.path.join(d, "store.jsonl"))
                ctx = lambda q: insp_context(store, q)          # noqa: E731
            elif arm == "dci_lite_det":
                store = RawTextStore(os.path.join(d, "memory.txt"))
                for _si, _role, sent in pilot.units_of(lc):
                    store.append(sent)
                store.flush()
                # The deterministic arm gets a deterministic reader too: a literal scan for the key,
                # not a model-written grep, so retrieval is not the variable under test here either.
                def ctx(q, _s=store):
                    matched = ["- " + r["text"] for r in _s.lines if primary in norm(r["text"])]
                    return norm(chr(10).join(matched)[:CTX_BUDGET])
            else:
                store = RawTextStore(os.path.join(d, "memory.txt"))
                for _si, _role, sent in pilot.units_of(lc):
                    store.append(sent)
                store.flush()
                ctx = lambda q: dci_context(model, store, q)    # noqa: E731

            if rep == 0:
                # POSITIVE CONTROL: the fact must be reachable before deletion, or this scenario
                # cannot demonstrate erasure and scoring it would credit ignorance as compliance.
                present_before = any(primary in ctx(q) for q in probes)
                keep_probe = retained[0] if retained else None
                keep_before = bool(keep_probe) and norm(keep_probe) in ctx(f"What is my {keep_probe}?")

            calls = 0
            if arm == "dci_lite_det":
                # THE CONTROL THIS HARNESS SHOULD HAVE HAD FIRST. Same raw-text store as the LLM arm,
                # same deterministic contract as ours: delete every record containing `primary`. If
                # this lands where inspeximus lands, then nothing in the comparison was about storage
                # design or about us -- the only variable that ever moved was the model in the loop.
                ids = [r["id"] for r in store.lines if primary in norm(r["text"])]
                deleted = store.delete(ids)
                steps = 1
                h = store.hash()
            elif arm == "inspeximus":
                deleted = store.forget(
                    where=lambda r: primary in norm(r.get("text", ""))).get("forgotten", 0)
                steps = 1
                h = insp_hash(store)
            else:
                deleted, calls, _cands = dci_forget(model, store, target)
                steps = 2
                h = store.hash()
            hashes.append(h)

            if rep == 0:
                leak = sum(1 for q in probes if primary in ctx(q)) / len(probes)
                para = sum(1 for q in probes if any(a in ctx(q) for a in al)) / len(probes)
                raw = 0
                for f in pathlib.Path(d).rglob("*"):
                    if f.is_file():
                        try:
                            if primary in norm(f.read_text(encoding="utf-8", errors="replace")):
                                raw += 1
                        except Exception:
                            pass
                # NEGATIVE CONTROL: a fact the user kept must survive. An arm that deletes the file
                # scores a perfect 0.0 leakage and is worthless.
                keep_after = bool(keep_probe) and norm(keep_probe) in ctx(f"What is my {keep_probe}?")
                row = {"arm": arm, "model": model if arm != "inspeximus" else None,
                       "target": target, "deleted": deleted, "steps": steps, "llm_calls": calls,
                       "retrieval_leakage": leak, "paraphrase_residue": para,
                       "raw_residue_files": raw, "probes": len(probes),
                       "present_before": present_before,
                       "kept_reachable_before": keep_before, "kept_reachable_after": keep_after}
        finally:
            shutil.rmtree(d, ignore_errors=True)

    # A single run compared against itself is always "deterministic", which is a check that cannot
    # fail. Below two repeats the answer is UNKNOWN and must be reported as such, not as a pass.
    row["deterministic"] = (len(set(hashes)) == 1) if repeats >= 2 else None
    row["distinct_states"] = len(set(hashes))
    row["repeats"] = repeats
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--models", default="qwen3:30b-a3b,gemma4:12b")
    ap.add_argument("--out", default="dci_lite_erasure_result.json")
    a = ap.parse_args()

    have_lc = {p.name for p in (HERE / "data_lc").glob("*.json")}
    usable = []
    for f in sorted((HERE / "data").glob("*.json")):
        if f.name not in have_lc:
            continue
        try:
            ev = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        ops = [o for o in (ev.get("operations") or []) if o.get("validity") == "confirmed"]
        if any(o.get("type") == "forget" and o.get("old_value") for o in ops):
            usable.append(f.name)
    # No silent caps: say what was left out, so a subset never reads as full coverage.
    print(f"  {len(usable)} scenarios carry a confirmed forget; running the first {a.n} "
          f"(sorted, so the subset is reproducible), {len(usable) - a.n} not run")

    models = [m for m in a.models.split(",") if m.strip()]
    rows, excluded = [], []

    # Decide every deletion key FIRST, from the data alone, before a model is loaded. An unusable
    # scenario then costs nothing instead of an hour of GPU.
    plan = []
    for name in usable[:a.n]:
        lc = json.loads((HERE / "data_lc" / name).read_text(encoding="utf-8"))
        ev = json.loads((HERE / "data" / name).read_text(encoding="utf-8"))
        units = [s for _i, _r, s in pilot.units_of(lc)]
        ops = [o for o in (ev.get("operations") or []) if o.get("validity") == "confirmed"]
        fg = [o for o in ops if o.get("type") == "forget" and o.get("old_value")]
        retained0 = [o["new_value"] for o in ops if o.get("type") == "remember" and o.get("new_value")]
        key = deletion_key(fg[0]["old_value"], retained0, units)   # SAME key for every arm (rule 3)
        if not key:
            excluded.append(name)
            print(f"  {name:26} EXCLUDED: no surface form of the forget target occurs in the "
                  f"ingested units, so no arm could erase it and none would be measured")
            continue
        plan.append((name, lc, ev, key))

    # MODEL-MAJOR ordering. Alternating models per scenario evicted one from VRAM every time --
    # qwen3:30b-a3b is 18.6 GB and gemma4:12b is 8.4 GB against a 24 GB card, so they cannot both
    # stay resident and each swap reloads a model from disk. Running one model across every
    # scenario before touching the next keeps it hot for the whole pass.
    jobs = [("inspeximus", None), ("dci_lite_det", None)] + [("dci_lite", m) for m in models]
    for arm, model in jobs:
        for name, lc, ev, key in plan:
            t0 = time.time()
            try:
                r = run_arm(arm, model, lc, ev, a.repeats, primary=key)
            except Exception as e:
                print(f"  {name:26} {arm:11} ERROR {type(e).__name__}: {e}")
                continue
            if r is None:
                print(f"  {name:26} skipped (no probe questions)")
                continue
            r["file"] = name
            r["seconds"] = round(time.time() - t0, 1)
            rows.append(r)
            print(f"  {name:26} {arm:11} {r['model'] or '-':<14} "
                  f"del={r['deleted']:>3} leak={r['retrieval_leakage']:.3f} "
                  f"para={r['paraphrase_residue']:.3f} raw={r['raw_residue_files']} "
                  f"det={str(r['deterministic']):5} states={r['distinct_states']} "
                  f"before={r['present_before']} {r['seconds']}s", flush=True)
            (HERE / a.out).write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")

    print("\n  ==== summary (only scenarios whose positive control held) ====")
    groups = {}
    for r in rows:
        groups.setdefault((r["arm"], r["model"]), []).append(r)
    for (arm, model), rs in sorted(groups.items(), key=lambda kv: str(kv[0])):
        ok = [r for r in rs if r["present_before"]]
        if not ok:
            print(f"  {arm} {model}: 0 of {len(rs)} passed the positive control -- NOTHING MEASURED")
            continue
        known = [r for r in ok if r["deterministic"] is not None]
        det = sum(1 for r in known if r["deterministic"])
        print(f"  {arm:11} {model or '-':<14} n={len(ok):>2}/{len(rs)}  "
              f"leak={statistics.mean(r['retrieval_leakage'] for r in ok):.3f}  "
              f"para={statistics.mean(r['paraphrase_residue'] for r in ok):.3f}  "
              f"raw={statistics.mean(r['raw_residue_files'] for r in ok):.2f}  "
              f"deterministic={det}/{len(known)}{'  (UNKNOWN: <2 repeats)' if not known else ''}  "
              f"kept_after={sum(1 for r in ok if r['kept_reachable_after'])}/"
              f"{sum(1 for r in ok if r['kept_reachable_before'])}")
    print("")
    print("  excluded for an absent target: %d -> %s" % (len(excluded), excluded))
    print("  grep patterns: %d run, %d uncompilable, %d empty after the literal fallback"
          % (RawTextStore.patterns_seen, RawTextStore.patterns_bad, RawTextStore.patterns_empty))
    print("  wrote %s" % a.out)


if __name__ == "__main__":
    main()
