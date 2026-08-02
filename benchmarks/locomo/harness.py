"""LOCOMO end-to-end QA + retrieval recall for inspeximus — the reproducible version.

WHY THIS EXISTS
---------------
README.md ("And it doesn't cost you recall") and CHANGELOG.md 1.11.0 cite
`retrieval-recall@25 = 0.783 (any) / 0.648 (all)` on LOCOMO (n=1536); CHANGELOG 1.54.0 concedes the pair
is "reported, not independently reproducible from this repo". Three concrete things made it so, all
verified before this file was written:

  1. `probes/retrieval_recall_locomo.py` (and `probes/locomo_qa.py`) resolve the dataset as
     `<HERE>/../../agora_output/lab/data/locomo10.json`. Both probes were written under `research/probes/`
     in a different repository; copied into `probes/`, that path points OUTSIDE this repo
     (`<repo-parent>/agora_output/...`) at a file that does not exist. The probes cannot load their data.
  2. Nothing was pinned. `recall()` defaults to `reinforce=True`, which mutates value/last_access and
     makes recall ORDER-DEPENDENT, so even with the data in place two runs need not agree.
  3. No result was ever committed, so there was nothing for a re-run to disagree WITH.

This module fixes all three and adds the half that was missing: end-to-end QA (question -> answer ->
judged), measured at the same operating point as the recall number so the two can finally be compared.

PROVENANCE — which probe produced which published number
--------------------------------------------------------
  probes/retrieval_recall_locomo.py   -> the published pair 0.783 / 0.648 @k=25, n=1536. LLM-free.
                                         This harness re-runs THAT metric definition (recall_any =
                                         >=1 gold evidence turn in top-k; recall_all = every gold
                                         evidence turn in top-k) over the same store builder.
  probes/locomo_qa.py                 -> the end-to-end QA scaffold: `conv_turns` (session date
                                         prepended, without which LOCOMO category 2 is unanswerable),
                                         `nomic_embed`, `build_inspeximus_store`, `recall_context`, and
                                         the inspeximus / fullcontext / naive arm triple. No QA number
                                         was ever published from it. This harness IMPORTS those
                                         functions rather than restating them, so the store and the
                                         retrieval are literally the published code path.
  probes/locomo_retrieval_map.py      -> the arm comparison behind "hybrid RRF beats a single vector
                                         index" (0.609 vs 0.552 recall@20). Not re-run here; it is the
                                         justification for `mode="hybrid"` in the pinned config.
  probes/locomo_metadata_prefilter.py -> the speaker pre-filter measurement (a hard filter wins overall
                                         but ZEROES the harm subset; soft keeps the gain and the
                                         fallback). Justifies `prefer={"speaker": ...}` over `where=`.
  probes/locomo_soft_prefer_filter.py -> validation of the SHIPPED soft `prefer=` through recall().
  probes/locomo_composed_soft_filters.py,
  probes/locomo_correlated_cue_composition.py
                                      -> composition of soft cues (capped sum vs product). Not part of
                                         the operating point; listed so the family is accounted for.

DEPENDENCIES: standard library + inspeximus. No benchmark dependency reaches the library.
NO LLM ON THE WRITE PATH: ingest is inspeximus's ordinary `remember()`. Models appear only as the
answerer and the judge, downstream of memory.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import statistics
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
for _p in (REPO, os.path.join(REPO, "probes")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import locomo_qa as LQ  # noqa: E402  the probe that produced the published scaffold

CONFIG_PATH = os.path.join(HERE, "config.json")
RESULTS_DIR = os.path.join(HERE, "results")
CACHE_DIR = os.path.join(HERE, ".cache")
DATA_DIR = os.path.join(HERE, "data")

# Six arms, one judge. The first three form the BAND (floor < inspeximus < ceiling); the last three are
# the CONTROLS that decide whether any number may be published at all.
BAND_ARMS = ("naive_recency", "inspeximus", "fullcontext")
CONTROL_ARMS = ("floor_empty", "floor_shuffled", "ceiling_verbatim")
QA_ARMS = BAND_ARMS + CONTROL_ARMS
CEILING_KEY = "__control_ceiling__"

ANSWER_PROMPT = ("Answer the question using ONLY this conversation memory. Be brief.\n\n"
                 "Memory:\n{context}\n\nQuestion: {question}\nAnswer:")
JUDGE_PROMPT = ("Question: {question}\nGold answer: {gold}\nModel answer: {pred}\n\n"
                "Does the model answer match the gold answer in meaning (allow paraphrase, date/format "
                "differences)? Reply with exactly YES or NO.")


def prompt_fingerprints() -> dict:
    """sha256 of the two prompt templates.

    A judged benchmark's number moves when its prompt moves, and a prompt is the one part of the
    operating point that a diff shows as prose rather than as a setting. Hashing it means an edited
    judge prompt shows up in the result file as a changed fingerprint instead of as unexplained drift.
    """
    return {"answer_prompt_sha256": hashlib.sha256(ANSWER_PROMPT.encode()).hexdigest(),
            "judge_prompt_sha256": hashlib.sha256(JUDGE_PROMPT.encode()).hexdigest()}


class DatasetMissing(Exception):
    """Raised, never swallowed: a missing dataset must skip with a reason, never score on a substitute."""


class GpuBusy(Exception):
    """Raised by the hard pre-flight. The harness refuses to start; it never kills anything."""


# --------------------------------------------------------------------------- config / dataset

def load_config(path: str | None = None) -> dict:
    with open(path or CONFIG_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


DATASET_ENV = "INSPEXIMUS_LOCOMO_PATH"
DATASET_ENV_LEGACY = "LOCOMO_PATH"          # what probes/locomo_*.py already use


def dataset_candidates(cfg: dict, override: str | None = None) -> list:
    return [("--data", override),
            (f"${DATASET_ENV}", os.environ.get(DATASET_ENV)),
            (f"${DATASET_ENV_LEGACY}", os.environ.get(DATASET_ENV_LEGACY)),
            ("benchmarks/locomo/data/", os.path.join(DATA_DIR, cfg["dataset"]["file"]))]


def resolve_dataset(cfg: dict, override: str | None = None) -> str:
    """--data > $INSPEXIMUS_LOCOMO_PATH > $LOCOMO_PATH > benchmarks/locomo/data/<file>.

    Raises DatasetMissing with every place it looked, so "it skipped" is never mistaken for "it passed".
    """
    tried = []
    for label, cand in dataset_candidates(cfg, override):
        if not cand:
            tried.append(f"{label}: not set")
            continue
        if os.path.exists(cand):
            return os.path.abspath(cand)
        tried.append(f"{label}: {cand} (absent)")
    raise DatasetMissing(
        "LOCOMO dataset not found. It is not redistributed here (it is not ours to ship).\n"
        f"  Get {cfg['dataset']['file']} from {cfg['dataset']['source']}\n"
        f"  then put it in benchmarks/locomo/data/ or set ${DATASET_ENV}.\n"
        f"  Expected sha256: {cfg['dataset']['sha256']}\n  Looked in:\n    " + "\n    ".join(tried))


def check_dataset_sha(cfg: dict, path: str, allow_drift: bool) -> dict:
    got = sha256_of(path)
    want = cfg["dataset"]["sha256"]
    ok = (got == want)
    if not ok and not allow_drift:
        raise DatasetMissing(
            f"Dataset sha256 mismatch — this is NOT the file the committed result was measured on.\n"
            f"  file:     {path}\n  expected: {want}\n  got:      {got}\n"
            f"  Re-download it, or pass --allow-dataset-drift to run anyway (the result is then stamped "
            f"dataset_drift=true and must not be committed as the baseline).")
    return {"path": path, "sha256": got, "expected_sha256": want, "matches_pin": ok,
            "drift_allowed": bool(allow_drift)}


def answerable_questions(sample: dict, cfg: dict) -> list:
    """The question set, in dataset order. Order is load-bearing: the reinforce=True arm is
    order-dependent by construction, so a stable order is what makes it re-runnable at all."""
    f = cfg["question_filter"]
    cats = set(f["categories"])
    out = []
    for q in sample.get("qa", []):
        if q.get("category") not in cats:
            continue
        if f["require_evidence"] and not q.get("evidence"):
            continue
        if f["require_answer"] and not str(q.get("answer", "")).strip():
            continue
        out.append(q)
    return out


def subset_samples(data: list, cfg: dict, subset: str) -> list:
    spec = cfg["subsets"][subset]
    idxs = spec["conversations"]
    if idxs is None:
        idxs = list(range(len(data)))
    return [(i, data[i]) for i in idxs if i < len(data)]


# --------------------------------------------------------------------------- GPU pre-flight (hard)

def _nvidia_free_mb():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free,memory.total",
                              "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=30)
        if out.returncode != 0 or not out.stdout.strip():
            return None, None, f"nvidia-smi exit {out.returncode}"
        first = out.stdout.strip().splitlines()[0]
        free, total = (int(x.strip()) for x in first.split(",")[:2])
        return free, total, None
    except Exception as e:                                          # noqa: BLE001
        return None, None, f"{type(e).__name__}: {str(e)[:80]}"


def _gpu_runners(cfg: dict):
    """Model-runner processes on this box, split into OURS and FOREIGN. Read-only: never signals one.

    The distinction is the whole point, and getting it wrong cost a run. The first version of this
    check simply forbade `llama-server.exe` — which on this machine is exactly how Ollama runs a model,
    so the gate forbade the benchmark's own inference backend and could never pass once a model
    loaded. A runner launched from inside the Ollama installation is ours; one launched from anywhere
    else is somebody else's job competing for the same card, which is what the gate exists to catch.
    """
    g = cfg["gpu"]
    hints = [h.lower() for h in g.get("own_runner_path_hints", [])]
    names = [n.lower() for n in g.get("runner_process_names", [])]
    rows = []
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine } | "
             "ForEach-Object { $_.Name + '|' + $_.CommandLine }"],
            capture_output=True, text=True, timeout=90)
        rows = [ln for ln in (out.stdout or "").splitlines() if "|" in ln]
    except Exception:                                               # noqa: BLE001
        try:
            out = subprocess.run(["ps", "-Ao", "comm,args"], capture_output=True, text=True, timeout=60)
            rows = [ln.strip() + "|" + ln.strip() for ln in (out.stdout or "").splitlines()[1:]]
        except Exception:                                           # noqa: BLE001
            return [], [], "could not enumerate processes"
    ours, foreign = [], []
    for row in rows:
        name, _sep, cmd = row.partition("|")
        low_name, low_cmd = name.strip().lower(), cmd.strip().lower()
        if not any(n in low_name for n in names):
            continue
        (ours if any(h in low_cmd for h in hints) else foreign).append(name.strip())
    return ours, foreign, None


def _ollama_resident(cfg: dict):
    """VRAM currently held by OUR OWN pinned models, from Ollama's /api/ps.

    Without this the gate cannot see its own footprint: the first run loads the answerer and the judge,
    and the second run is then refused for the memory the first run is legitimately holding. Foreign
    contention is what the gate exists to catch, so our own models are credited back.
    """
    ours = {cfg["qa"]["answerer_model"], cfg["qa"]["judge_model"], cfg["retrieval"]["embedder"]}
    base = cfg["qa"]["endpoint"].rstrip("/").rsplit("/v1", 1)[0]
    try:
        with urllib.request.urlopen(base + "/api/ps", timeout=15) as r:
            models = json.load(r).get("models") or []
    except Exception as e:                                          # noqa: BLE001
        return 0, [], f"{type(e).__name__}: {str(e)[:60]}"
    mb, names = 0, []
    for m in models:
        name = (m.get("name") or m.get("model") or "")
        if name in ours or name.split(":")[0] in {o.split(":")[0] for o in ours}:
            mb += int(m.get("size_vram") or 0) // (1024 * 1024)
            names.append(name)
    return mb, names, None


def gpu_sample(cfg: dict) -> dict:
    """A point-in-time reading of the card. Cheap enough to take at the start AND the end of a run.

    Sampling only at the start is how a run gets certified clean and then measured dirty: on
    2026-08-02 a scheduled task restarted two ~20 GB services in the middle of a 90-minute run, the
    pre-flight had already stamped `contended: false`, and the only visible symptom was that per-call
    latency quietly went from 2.6 s to 75 s. A result that records one endpoint cannot tell a reader
    which regime it was measured in.
    """
    free, total, err = _nvidia_free_mb()
    ours_mb, ours_names, _ps_err = _ollama_resident(cfg)
    own, foreign, _proc_err = _gpu_runners(cfg)
    return {"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "free_vram_mb": free, "total_vram_mb": total, "read_error": err,
            "own_models_vram_mb": ours_mb, "own_models_resident": sorted(ours_names),
            "own_runners": len(own), "foreign_runners": sorted(set(foreign))}


def gpu_window(cfg: dict, start: dict, end: dict) -> dict:
    """Did the card stay the way the pre-flight found it? Reported on every result."""
    drift = []
    if end.get("foreign_runners") and not start.get("foreign_runners"):
        drift.append(f"foreign runner(s) appeared during the run: {end['foreign_runners']}")
    f0, f1 = start.get("free_vram_mb"), end.get("free_vram_mb")
    if f0 is not None and f1 is not None:
        # Our own models loading is an expected drop; a drop far beyond what they hold is somebody else.
        unexplained = (f0 - f1) - (end.get("own_models_vram_mb", 0) - start.get("own_models_vram_mb", 0))
        if unexplained > cfg["gpu"].get("max_unexplained_vram_drop_mb", 2048):
            drift.append(f"{unexplained} MiB of VRAM went to something other than our own models")
    return {"start": start, "end": end, "drift": drift, "stable": not drift,
            "_note": ("A benchmark cannot police the machine it runs on; it can refuse to hide that the "
                      "machine changed underneath it. If `stable` is false, treat every wall-clock "
                      "number here as measured across two regimes and re-run before quoting it.")}


def gpu_preflight(cfg: dict, allow_shared: bool) -> dict:
    """HARD gate. Refuses to start when the GPU is contended; never kills anything.

    Process lifecycle belongs to the coordinator, not to a benchmark: this function reads, and raises.
    """
    g = cfg["gpu"]
    free, total, err = _nvidia_free_mb()
    ours_mb, ours_names, ps_err = _ollama_resident(cfg)
    effective = (free + ours_mb) if free is not None else None
    own_runners, foreign_runners, proc_err = _gpu_runners(cfg)
    blockers = []
    if free is None:
        blockers.append(f"could not read GPU memory ({err})")
    elif effective < g["min_free_vram_mb"]:
        blockers.append(f"free VRAM {free} MiB (+{ours_mb} MiB held by our own models = {effective}) "
                        f"< required {g['min_free_vram_mb']} MiB")
    if proc_err:
        blockers.append(f"could not enumerate model runners ({proc_err})")
    if foreign_runners:
        blockers.append("foreign model runner(s) on this GPU: " + ", ".join(sorted(set(foreign_runners))))
    state = {"free_vram_mb": free, "total_vram_mb": total, "min_free_vram_mb": g["min_free_vram_mb"],
             "own_models_vram_mb": ours_mb, "own_models_resident": ours_names,
             "effective_free_vram_mb": effective, "ollama_ps_error": ps_err,
             "own_runners": sorted(set(own_runners)), "foreign_runners": sorted(set(foreign_runners)),
             "blockers": blockers,
             "contended": bool(blockers), "override_used": bool(allow_shared and blockers)}
    if blockers and not allow_shared:
        raise GpuBusy("GPU pre-flight REFUSED to start:\n  - " + "\n  - ".join(blockers) +
                      "\n  Ask the coordinator to quiesce the GPU. Do not kill these processes yourself."
                      "\n  To measure anyway on a shared GPU, pass --allow-shared-gpu; the result is then"
                      " stamped gpu.contended=true and is NOT a quiesced measurement.")
    return state


# --------------------------------------------------------------------------- embeddings (cached)

class EmbedCache:
    """Disk-backed embedding cache.

    It does not replace the probe's embedder; it PRE-POPULATES `locomo_qa._EMB_CACHE` so that
    `LQ.build_inspeximus_store` runs its own unmodified code and makes zero HTTP calls for anything
    already seen. Provenance (same code path as the published number) plus a fast, byte-identical
    re-run on the retrieval side.
    """

    def __init__(self, model: str, url: str, batch: int, path: str | None = None):
        self.model, self.url, self.batch = model, url, batch
        self.path = path or os.path.join(
            CACHE_DIR, f"embeddings_{re.sub(r'[^A-Za-z0-9]+', '_', model)}.json")
        self.store: dict = {}
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as fh:
                    self.store = json.load(fh)
            except Exception:                                       # noqa: BLE001  a corrupt cache is not data
                self.store = {}
        self.latencies: list = []
        self.http_calls = 0
        self.misses = 0

    @staticmethod
    def _k(text: str) -> str:
        return hashlib.sha1(text[:4000].encode("utf-8")).hexdigest()

    def _post(self, chunk: list) -> list:
        body = json.dumps({"model": self.model, "input": chunk}).encode()
        req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=600) as r:
            vecs = json.load(r)["embeddings"]
        self.latencies.append(time.time() - t0)
        self.http_calls += 1
        return vecs

    def warm(self, texts, progress=None) -> None:
        todo, seen = [], set()
        for t in texts:
            if not t:
                continue
            k = self._k(t)
            if k not in self.store and k not in seen:
                seen.add(k)
                todo.append(t)
        self.misses += len(todo)
        for i in range(0, len(todo), self.batch):
            chunk = todo[i:i + self.batch]
            for t, v in zip(chunk, self._post([c[:4000] for c in chunk])):
                self.store[self._k(t)] = v
            if progress:
                progress(min(i + self.batch, len(todo)), len(todo))
        if todo:
            self.flush()

    def flush(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.store, fh)
        os.replace(tmp, self.path)

    def get(self, text: str):
        v = self.store.get(self._k(text))
        if v is None and text:
            self.warm([text])
            v = self.store.get(self._k(text))
        return v

    def install(self, texts) -> None:
        self.warm(texts)
        for t in texts:
            if t:
                LQ._EMB_CACHE[t] = self.store.get(self._k(t))

    def stats(self) -> dict:
        lat = sorted(self.latencies)
        return {"embed_http_calls": self.http_calls, "embed_misses": self.misses,
                "embed_cached_records": len(self.store),
                "embed_p50_s": round(statistics.median(lat), 3) if lat else None,
                "embed_max_s": round(lat[-1], 3) if lat else None}


# --------------------------------------------------------------------------- LLM

class LLM:
    """OpenAI-compatible chat client with per-call latency accounting.

    A 0.0 s reply is a CACHE HIT, not a call. Replies faster than `cache_hit_threshold_s` are counted
    and reported, never silently averaged away.
    """

    def __init__(self, cfg: dict):
        qa = cfg["qa"]
        self.url = qa["endpoint"].rstrip("/") + "/chat/completions"
        self.temperature = qa["temperature"]
        self.timeout = qa["request_timeout_s"]
        self.retries = qa["retries"]
        self.cache_hit_threshold_s = cfg.get("gpu", {}).get("cache_hit_threshold_s", 0.05)
        self.calls = 0
        self.errors = 0
        self.latencies: list = []
        self.suspected_cache_hits = 0
        self.by_model: dict = {}

    def __call__(self, model: str, prompt: str, max_tokens: int) -> str:
        body = json.dumps({"model": model, "temperature": self.temperature, "max_tokens": max_tokens,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        last = ""
        for attempt in range(self.retries):
            t0 = time.time()
            try:
                req = urllib.request.Request(
                    self.url, data=body,
                    headers={"Content-Type": "application/json", "Authorization": "Bearer local"})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    payload = json.load(r)
                dt = time.time() - t0
                content = (((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
                self.calls += 1
                self.latencies.append(dt)
                m = self.by_model.setdefault(model, {"calls": 0, "total_s": 0.0})
                m["calls"] += 1
                m["total_s"] += dt
                if dt < self.cache_hit_threshold_s:
                    self.suspected_cache_hits += 1
                return content.strip()
            except Exception as e:                                  # noqa: BLE001  transport/timeout
                last = f"{type(e).__name__}: {str(e)[:100]}"
                time.sleep(1 + 2 * attempt)
        self.errors += 1
        return f"__ERR__{last}"

    def probe(self, model: str) -> dict:
        """Liveness with a UNIQUE prompt, checking the ANSWER — a cached string proves nothing.

        The nonce is carried as an ignorable tag on a trivial question rather than as the thing to
        echo. Two earlier versions of this probe reported a perfectly healthy server as wrong: an
        arithmetic task measured whether an 8B model can add, and an echo-the-token task drew
        "I cannot fulfill your request" from llama3.1:8b. The nonce is what makes the call
        uncacheable; the question is what makes the answer checkable. They should not be the same
        string.
        """
        nonce = random.Random(time.time_ns()).randint(10 ** 9, 10 ** 10)
        raw = self(model, f"[request {nonce}] What colour is a clear daytime sky? Answer in one word.", 24)
        t = self.by_model.get(model, {})
        return {"model": model, "prompt_nonce": nonce, "expected_substring": "blue",
                "answer": raw[:60], "answer_correct": "blue" in raw.lower(),
                "alive": bool(raw) and not raw.startswith("__ERR__"),
                "mean_latency_s": round(t.get("total_s", 0.0) / max(t.get("calls", 1), 1), 3)}

    def stats(self) -> dict:
        lat = sorted(self.latencies)
        return {"llm_calls": self.calls, "llm_errors": self.errors,
                "llm_p50_s": round(statistics.median(lat), 3) if lat else None,
                "llm_p95_s": round(lat[int(0.95 * (len(lat) - 1))], 3) if lat else None,
                "llm_max_s": round(lat[-1], 3) if lat else None,
                "suspected_cache_hits": self.suspected_cache_hits,
                "cache_hit_threshold_s": self.cache_hit_threshold_s,
                "by_model": {k: {"calls": v["calls"], "mean_s": round(v["total_s"] / v["calls"], 3)}
                             for k, v in sorted(self.by_model.items())}}


_VERDICT_RE = re.compile(r"\b(YES|NO)\b")


def parse_verdict(raw: str):
    """True / False / None. None means the judge did not answer the question it was asked; those are
    counted as parse failures AND scored incorrect, never quietly dropped."""
    if not raw or raw.startswith("__ERR__"):
        return None
    m = _VERDICT_RE.search(raw.strip().upper())
    return (m.group(1) == "YES") if m else None


def judge_answer(llm, cfg: dict, question: str, gold: str, pred: str):
    raw = llm(cfg["qa"]["judge_model"],
              JUDGE_PROMPT.format(question=question, gold=gold, pred=pred),
              cfg["qa"]["judge_max_tokens"])
    return parse_verdict(raw), raw


def answer_question(llm, cfg: dict, context: str, question: str) -> str:
    return llm(cfg["qa"]["answerer_model"],
               ANSWER_PROMPT.format(context=context or "", question=question),
               cfg["qa"]["answer_max_tokens"])


# --------------------------------------------------------------------------- retrieval

def named_speakers(question: str, speakers) -> list:
    return [s for s in speakers if s and s.lower() in question.lower()]


def recall_hits(store, turns, question: str, k: int, mode: str, use_prefer: bool, reinforce: bool):
    """The published retrieval recipe: hybrid RRF + the SOFT speaker prefilter (`prefer=`).

    Mirrors `probes/locomo_qa.recall_context` / `probes/retrieval_recall_locomo.main`, with the two
    things those probes left implicit made explicit and pinnable: `reinforce`, and whether `prefer`
    fires at all.
    """
    prefer = None
    if use_prefer:
        named = named_speakers(question, {sp for _i, sp, _tx in turns if sp})
        if named:
            prefer = {"speaker": named}
    return store.recall(question, k=k, mode=mode, prefer=prefer, reinforce=reinforce) or []


def retrieval_recall(store, turns, questions, k: int, mode: str, reinforce: bool) -> dict:
    """recall_any@k / recall_all@k — the exact metric definition behind the published pair.

    TWO DENOMINATORS, from one pass, because the choice of denominator is worth 0.005 of recall and
    the published pair used the looser one:

      published  (n=1536 over the full set) — every question with non-empty evidence and category != 5
                 counts, exactly as `probes/retrieval_recall_locomo.py` counted. FIVE of those questions
                 carry evidence ids that match no turn in their own conversation, so they can never be
                 hit by any retriever; the probe scored them as misses. Reporting a number on a smaller
                 denominator and calling it the same metric would be quietly flattering.
      resolvable (n=1531) — those five dropped. The cleaner metric, reported alongside so the gap is
                 visible rather than argued about.
    """
    text2id = {f"{sp}: {tx}": tid for tid, sp, tx in turns if tx.strip()}
    known_ids = set(text2id.values())
    n = any_hit = all_hit = 0
    n_res = any_res = all_res = 0
    cat_tot: dict = {}
    cat_any: dict = {}
    cat_all: dict = {}
    for q in questions:
        raw_gold = {str(e) for e in (q.get("evidence") or [])}
        if not raw_gold:
            continue
        gold = raw_gold & known_ids
        cat = str(q.get("category"))
        n += 1
        cat_tot[cat] = cat_tot.get(cat, 0) + 1
        if not gold:
            continue                       # counted in n, can never hit — the published probe's behaviour
        n_res += 1
        hits = recall_hits(store, turns, q["question"], k, mode, True, reinforce)
        got = {text2id.get(h.get("text", "")) for h in hits} - {None}
        if gold & got:
            any_hit += 1
            any_res += 1
            cat_any[cat] = cat_any.get(cat, 0) + 1
        if gold <= got:
            all_hit += 1
            all_res += 1
            cat_all[cat] = cat_all.get(cat, 0) + 1
    return {"k": k, "mode": mode, "reinforce": reinforce, "prefer": "speaker",
            "denominator": "published", "n": n,
            "recall_any": round(any_hit / n, 4) if n else None,
            "recall_all": round(all_hit / n, 4) if n else None,
            "n_resolvable": n_res,
            "recall_any_resolvable": round(any_res / n_res, 4) if n_res else None,
            "recall_all_resolvable": round(all_res / n_res, 4) if n_res else None,
            "n_unresolvable_evidence": n - n_res,
            "by_category_any": {c: round(cat_any.get(c, 0) / cat_tot[c], 4) for c in sorted(cat_tot)},
            "by_category_all": {c: round(cat_all.get(c, 0) / cat_tot[c], 4) for c in sorted(cat_tot)},
            "by_category_n": {c: cat_tot[c] for c in sorted(cat_tot)}}


def merge_retrieval(parts: list) -> dict:
    """Pool per-conversation retrieval counts into one number over the whole subset."""
    parts = [p for p in parts if p and p.get("n")]
    if not parts:
        return {"n": 0, "recall_any": None, "recall_all": None}
    n = sum(p["n"] for p in parts)
    n_res = sum(p["n_resolvable"] for p in parts)
    any_hits = sum(round(p["recall_any"] * p["n"]) for p in parts)
    all_hits = sum(round(p["recall_all"] * p["n"]) for p in parts)
    cat_n: dict = {}
    cat_any: dict = {}
    cat_all: dict = {}
    for p in parts:
        for c, cn in p["by_category_n"].items():
            cat_n[c] = cat_n.get(c, 0) + cn
            cat_any[c] = cat_any.get(c, 0) + round(p["by_category_any"].get(c, 0.0) * cn)
            cat_all[c] = cat_all.get(c, 0) + round(p["by_category_all"].get(c, 0.0) * cn)
    head = parts[0]
    return {"k": head["k"], "mode": head["mode"], "reinforce": head["reinforce"],
            "prefer": head["prefer"], "denominator": "published",
            "n": n, "n_conversations": len(parts),
            "recall_any": round(any_hits / n, 4), "recall_all": round(all_hits / n, 4),
            "n_resolvable": n_res,
            "recall_any_resolvable": round(any_hits / n_res, 4) if n_res else None,
            "recall_all_resolvable": round(all_hits / n_res, 4) if n_res else None,
            "n_unresolvable_evidence": n - n_res,
            "by_category_any": {c: round(cat_any[c] / cat_n[c], 4) for c in sorted(cat_n)},
            "by_category_all": {c: round(cat_all[c] / cat_n[c], 4) for c in sorted(cat_n)},
            "by_category_n": dict(sorted(cat_n.items()))}


# --------------------------------------------------------------------------- QA arms + controls

def derangement(n: int, seed: int) -> list:
    """A permutation with no fixed point (n >= 2). Every question gets ANOTHER question's context."""
    if n < 2:
        return list(range(n))
    rnd = random.Random(seed)
    idx = list(range(n))
    for _ in range(1000):
        rnd.shuffle(idx)
        if all(idx[i] != i for i in range(n)):
            return idx
    # Deterministic fallback. It must be the rotation of the IDENTITY, not of the shuffled list:
    # rotating a shuffled permutation can leave a fixed point (idx=[1,0,2] -> [0,2,1], where 0 maps
    # to itself), and one fixed point means one question is quietly answered from its own context —
    # the floor control scoring itself.
    return [(i + 1) % n for i in range(n)]


def build_contexts(store, turns, questions, cfg: dict) -> dict:
    """Retrieved context per arm, for every question. No LLM here — this half is memory only.

    BAND (comparison, one judge, matched 6000-char budget except where noted):
      naive_recency    : the last k turns, query-blind — what many agent frameworks ship as memory.
      inspeximus       : the pinned recipe (hybrid RRF + soft speaker prefer).
      fullcontext      : the whole conversation, no retrieval at all. The ceiling arm is allowed the
                         whole conversation by definition; the realised size is recorded per item.
    CONTROLS (must-pass, independent of the band):
      floor_empty      : no context. Chance for open-ended QA is ~0. Anything above the bound means the
                         answerer is answering from world knowledge, and NOTHING here measures memory.
      floor_shuffled   : each question gets ANOTHER question's retrieved context (a derangement). Same
                         length, style and conversation — a strictly harder floor than a foreign store,
                         and a conservative one: a deranged context can hold the answer by luck.
      ceiling_verbatim : the gold answer written INTO THE STORE as a record, then retrieved normally.
                         Exercises the whole chain (write -> recall -> answer -> judge). The record is
                         keyed, so each question's control record SUPERSEDES the previous one and only
                         the live one can be recalled.
    """
    r, qa = cfg["retrieval"], cfg["qa"]
    k, mode, reinforce = r["k"], r["mode"], r["reinforce"]
    budget, full_budget = qa["context_char_budget"], qa["fullcontext_char_budget"]
    out = {a: [] for a in QA_ARMS}

    full_text = "\n".join(f"{sp}: {tx}" for _i, sp, tx in turns)
    naive_text = "\n".join(f"{sp}: {tx}" for _i, sp, tx in turns[-k:])

    base = []
    for q in questions:
        hits = recall_hits(store, turns, q["question"], k, mode, True, reinforce)
        base.append("\n".join(h.get("text", "") for h in hits)[:budget])
    out["inspeximus"] = base
    out["naive_recency"] = [naive_text[:budget] for _ in questions]
    out["fullcontext"] = [full_text[:full_budget] for _ in questions]
    out["floor_empty"] = ["" for _ in questions]
    perm = derangement(len(questions), cfg["seed"])
    out["floor_shuffled"] = [base[perm[i]] for i in range(len(questions))]

    speakers = {sp for _i, sp, _tx in turns if sp}
    default_speaker = sorted(speakers)[0] if speakers else "Speaker"
    ceiling_hits = 0
    ceiling_ids, ceiling_texts = [], []
    for q in questions:
        named = named_speakers(q["question"], speakers)
        spk = named[0] if named else default_speaker
        gold = str(q.get("answer", "")).strip()
        text = f"{spk}: [{q['question']}] {gold}"
        rec_id = store.remember(text, key=CEILING_KEY, meta={"speaker": spk})
        ceiling_ids.append(rec_id)
        ceiling_texts.append(text)
        hits = recall_hits(store, turns, q["question"], k, mode, True, reinforce)
        texts = [h.get("text", "") for h in hits]
        if text in texts:
            ceiling_hits += 1
        out["ceiling_verbatim"].append("\n".join(texts)[:budget])

    # Erase the control records BY ID. `forget()` takes ids, not keys -- the first version of this
    # passed CEILING_KEY, which erased nothing, raised nothing, and returned a normal-looking
    # {"forgotten": 0} that a bare try/except then hid. The count is asserted and reported so a
    # cleanup that stops finding its target cannot pass as a cleanup that had nothing to do.
    ids = [i for i in ceiling_ids if i]
    forgotten = 0
    forget_error = None
    if ids:
        try:
            forgotten = int((store.forget(ids) or {}).get("forgotten", 0))
        except Exception as e:                                      # noqa: BLE001
            forget_error = f"{type(e).__name__}: {str(e)[:80]}"

    return {"contexts": out,
            "ceiling_retrieved_rate": round(ceiling_hits / len(questions), 4) if questions else None,
            "ceiling_records_written": len(ids),
            "ceiling_records_forgotten": forgotten,
            "ceiling_cleanup_complete": bool(ids) and forgotten == len(ids) and not forget_error,
            "ceiling_forget_error": forget_error,
            "ceiling_texts": ceiling_texts,
            "fullcontext_truncated": len(full_text) > full_budget,
            "fullcontext_chars": min(len(full_text), full_budget),
            "conversation_chars": len(full_text)}


def score_arm(llm, cfg: dict, questions, contexts, arm: str, on_item=None) -> dict:
    """answer -> judge, for one arm.

    Answering and judging run as two PHASES rather than interleaved per question. Alternating two
    models per item makes Ollama evict and reload them on a contended GPU; phasing keeps one model
    resident at a time. It changes the wall clock, never the measurement — each answer is still graded
    by the same judge from the same prompt.
    """
    preds = []
    for i, q in enumerate(questions):
        preds.append(answer_question(llm, cfg, contexts[i], q["question"]))
        if on_item:
            on_item(f"{arm}/answer", i + 1, len(questions), None)

    correct = parse_fail = 0
    by_cat: dict = {}
    rows = []
    ctx_chars = []
    for i, q in enumerate(questions):
        gold = str(q.get("answer", "")).strip()
        pred = preds[i]
        verdict, raw = judge_answer(llm, cfg, q["question"], gold, pred)
        ok = verdict is True
        if verdict is None:
            parse_fail += 1
        correct += ok
        cat = str(q.get("category"))
        b = by_cat.setdefault(cat, [0, 0])
        b[0] += ok
        b[1] += 1
        ctx_chars.append(len(contexts[i]))
        rows.append({"arm": arm, "category": q.get("category"), "question": q["question"],
                     "gold": gold, "pred": pred[:400], "judge_raw": raw[:80], "correct": bool(ok),
                     "context_chars": len(contexts[i])})
        if on_item:
            on_item(f"{arm}/judge", i + 1, len(questions), ok)
    n = len(questions)
    return {"arm": arm, "n": n, "correct": correct, "parse_fail": parse_fail,
            "accuracy": round(correct / n, 4) if n else None,
            "mean_context_chars": round(sum(ctx_chars) / n) if n else 0,
            "by_category": {c: round(v[0] / v[1], 4) for c, v in sorted(by_cat.items()) if v[1]},
            "by_category_n": {c: v[1] for c, v in sorted(by_cat.items())},
            "rows": rows}


def merge_arm(parts: list) -> dict:
    parts = [p for p in parts if p and p.get("n")]
    if not parts:
        return {"n": 0, "accuracy": None}
    n = sum(p["n"] for p in parts)
    correct = sum(p["correct"] for p in parts)
    cat_n: dict = {}
    cat_c: dict = {}
    for p in parts:
        for c, cn in p["by_category_n"].items():
            cat_n[c] = cat_n.get(c, 0) + cn
            cat_c[c] = cat_c.get(c, 0) + round(p["by_category"].get(c, 0.0) * cn)
    return {"arm": parts[0]["arm"], "n": n, "correct": correct,
            "parse_fail": sum(p["parse_fail"] for p in parts),
            "accuracy": round(correct / n, 4),
            "mean_context_chars": round(sum(p["mean_context_chars"] * p["n"] for p in parts) / n),
            "by_category": {c: round(cat_c[c] / cat_n[c], 4) for c in sorted(cat_n)},
            "by_category_n": dict(sorted(cat_n.items()))}


def evaluate_controls(cfg: dict, arms: dict) -> dict:
    """The gate that decides whether a number may be published at all."""
    c = cfg["controls"]
    checks: dict = {}
    for arm, bound, kind in (("floor_empty", c["floor_empty_max"], "max"),
                             ("floor_shuffled", c["floor_shuffled_max"], "max"),
                             ("ceiling_verbatim", c["ceiling_verbatim_min"], "min")):
        acc = (arms.get(arm) or {}).get("accuracy")
        if acc is None:
            checks[arm] = {"accuracy": None, "bound": bound, "kind": kind, "passed": False,
                           "why": "arm did not run — a control that never ran has measured nothing"}
            continue
        passed = (acc <= bound) if kind == "max" else (acc >= bound)
        if passed:
            why = "ok"
        elif kind == "max":
            why = (f"floor scored {acc} > {bound}: the answerer is not answering from the store, so the "
                   f"inspeximus arm is not measuring memory")
        else:
            why = (f"ceiling scored {acc} < {bound}: the chain cannot score even a verbatim answer, so a "
                   f"low inspeximus arm would be the harness, not the store")
        checks[arm] = {"accuracy": acc, "bound": bound, "kind": kind, "passed": bool(passed), "why": why}
    checks["all_passed"] = all(v["passed"] for v in checks.values() if isinstance(v, dict))
    return checks


def evaluate_band(cfg: dict, arms: dict) -> dict:
    """floor_arm < subject_arm < ceiling_arm, strictly. Reported either way — never tuned."""
    b = cfg["band"]
    got = {role: (arms.get(b[f"{role}_arm"]) or {}).get("accuracy")
           for role in ("floor", "subject", "ceiling")}
    missing = [r for r, v in got.items() if v is None]
    if missing:
        return {"arms": {f"{r}_arm": b[f"{r}_arm"] for r in ("floor", "subject", "ceiling")},
                "accuracy": got, "passed": False,
                "why": f"arm(s) did not run: {', '.join(missing)}"}
    above = got["subject"] > got["floor"]
    below = got["subject"] < got["ceiling"]
    why = "ok"
    if not above:
        why = (f"inspeximus {got['subject']} <= naive-recency floor {got['floor']}: retrieval bought "
               f"nothing over a last-k buffer on this subset")
    elif not below:
        why = (f"inspeximus {got['subject']} >= full-context ceiling {got['ceiling']}: focused retrieval "
               f"matched or beat stuffing the whole conversation in — a real result on this subset, but "
               f"it means the ceiling arm is not bounding the subject arm")
    return {"arms": {f"{r}_arm": b[f"{r}_arm"] for r in ("floor", "subject", "ceiling")},
            "accuracy": got, "above_floor": bool(above), "below_ceiling": bool(below),
            "passed": bool(above and below), "why": why}


# --------------------------------------------------------------------------- comparison / tolerance

def compare_to_baseline(baseline: dict, current: dict, tol: dict) -> dict:
    """Every published field, checked against the committed baseline within its stated tolerance.

    Fails LOUDLY on a field present in the baseline and missing from the run: a comparison that skips
    what it cannot find reports SAFE for a harness that stopped producing the number at all.
    """
    diffs = []

    def check(path, want, got, band):
        if want is None and got is None:
            return
        if want is None or got is None:
            diffs.append({"field": path, "baseline": want, "current": got, "tolerance": band,
                          "delta": None, "within": False, "why": "field missing on one side"})
            return
        d = abs(got - want)
        diffs.append({"field": path, "baseline": round(want, 4), "current": round(got, 4),
                      "tolerance": band, "delta": round(d, 4), "within": d <= band})

    b_ret = baseline.get("retrieval") or {}
    c_ret = current.get("retrieval") or {}
    for key in sorted(set(b_ret) | set(c_ret)):
        if key.startswith("_") or key == "published_comparison":
            continue
        for metric in ("recall_any", "recall_all", "recall_any_resolvable", "recall_all_resolvable"):
            b, c = (b_ret.get(key) or {}), (c_ret.get(key) or {})
            if metric in b or metric in c:
                check(f"retrieval.{key}.{metric}", b.get(metric), c.get(metric), tol["retrieval_recall"])

    b_arms = (baseline.get("qa") or {}).get("arms") or {}
    c_arms = (current.get("qa") or {}).get("arms") or {}
    for arm in sorted(set(b_arms) | set(c_arms)):
        band = tol["control_accuracy"] if arm in CONTROL_ARMS else tol["qa_accuracy"]
        check(f"qa.arms.{arm}.accuracy", (b_arms.get(arm) or {}).get("accuracy"),
              (c_arms.get(arm) or {}).get("accuracy"), band)

    return {"tolerance": tol, "fields": diffs, "n_checked": len(diffs),
            "n_outside": sum(1 for d in diffs if not d["within"]),
            "within_tolerance": bool(diffs) and all(d["within"] for d in diffs)}


def compare_to_published(cfg: dict, retrieval: dict) -> dict:
    """Does the reproduced recall match the pair we published? Reported either way, untuned."""
    ref = cfg["published_reference"]
    tol = ref["match_tolerance"]
    out = {"reference": {k: v for k, v in ref.items() if not k.startswith("_")}, "arms": {}}
    for arm, got in sorted(retrieval.items()):
        if arm.startswith("_") or not isinstance(got, dict) or "recall_any" not in got:
            continue
        row = {"n": got.get("n"), "k": got.get("k"), "reinforce": got.get("reinforce"),
               "same_denominator_as_published": got.get("n") == ref.get("n")}
        matches = True
        for metric in ("recall_any", "recall_all"):
            g, w = got.get(metric), ref.get(metric)
            row[metric] = g
            d = round(g - w, 4) if (g is not None and w is not None) else None
            row[f"{metric}_delta"] = d
            row[f"{metric}_within_{tol}"] = (abs(d) <= tol) if d is not None else False
            matches = matches and ((abs(d) <= tol) if d is not None else False)
        row["matches_published"] = matches
        out["arms"][arm] = row
    out["any_arm_matches_published"] = any(v.get("matches_published") for v in out["arms"].values())
    return out


# --------------------------------------------------------------------------- store building

def build_store(sample: dict, cache: EmbedCache, questions=None, progress=None):
    """Build the inspeximus store for one conversation, through the probe's own builder.

    Everything the run will embed (turns + questions) is warmed in one batched pass first, so
    `LQ.build_inspeximus_store` makes zero HTTP calls and ingest is pure inspeximus `remember()` —
    zero LLM on the write path.
    """
    turns = LQ.conv_turns(sample)
    texts = [f"{sp}: {tx}" for _i, sp, tx in turns if tx.strip()]
    cache.install(texts + [q["question"] for q in (questions or [])])
    LQ.nomic_embed = lambda ts: [cache.get(t) for t in ts]          # route the probe at the disk cache
    LQ._embed_one = cache.get
    if progress:
        progress(len(texts))
    return LQ.build_inspeximus_store(turns), turns
