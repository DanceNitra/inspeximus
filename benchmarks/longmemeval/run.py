"""LongMemEval_S end-to-end QA harness for inspeximus — question in, answer out, judged.

WHY THIS EXISTS
---------------
Every published agent-memory number this repository owned was a RETRIEVAL metric
(retrieval-recall@25 = 0.783 / 0.648 on LoCoMo). Buyers, comparison tables and competitor READMEs
open with an END-TO-END QA accuracy. This harness produces that number: ingest a LongMemEval
haystack into inspeximus with the normal zero-LLM write path, recall top-k for the question, let a
fixed answerer model answer from ONLY the recalled context, and let a fixed judge model score the
answer against the dataset's gold answer.

A single-arm score is not interpretable, so every run measures FIVE arms under ONE judge, ONE
answerer and ONE matched context budget:

  oracle       CEILING  — the gold evidence session(s) only (perfect retrieval; the setting the
                          dataset authors ship as `longmemeval_oracle`).
  inspeximus            — `recall(question, k=K, mode=MODE)` over a store built from every haystack
                          turn.
  recency      FLOOR    — the most recent turns of the same haystack, filled to the same character
                          budget. No memory system, just "keep the tail".
  shuffled     CONTROL  — inspeximus's own retrieval machinery pointed at a DIFFERENT question's
                          store. Same code path, same budget, wrong haystack.
  empty        CONTROL  — no context at all. Measures what the answerer knows without memory.
  full_context          — the whole haystack. On LongMemEval_S that is ~490k characters, so at any
                          context window this machine can serve it is reported as NOT COMPUTABLE
                          with the measured character count, which is the honest result and is the
                          reason the benchmark exists.

BAND CHECK (the harness's own falsification test). `recency < inspeximus < oracle` must hold
strictly. Outside that band the harness is measuring something other than memory quality and the
number is worthless — the run reports `band_check.passed = false` and exits non-zero. `shuffled`
and `empty` must also sit at or below `recency`.

WHAT IS PINNED
--------------
Dataset file + its sha256, subset membership (deterministic, no seed), k, recall mode, the context
character budget, the answerer, the judge, the embedder, the judge prompt (recorded by sha256), and
`temperature=0` everywhere. All of it lands in the result JSON.

NO LLM ON THE WRITE PATH. Ingestion is `remember()` plus a local embedding model. There is no
extraction, summarisation or rewriting step. The judge and the answerer are read/eval-side only.

RUN
---
    python benchmarks/longmemeval/run.py --download          # fetch the dataset (278 MB) once
    python benchmarks/longmemeval/judge_calibration.py       # required: run.py exits 6 without it
    python benchmarks/longmemeval/run.py --subset small      # the pinned 20-question subset

Exit codes: 0 ok · 2 dataset missing/unusable · 3 GPU pre-flight refused · 4 model pre-flight failed
· 5 band check failed · 6 judge not calibrated for these prompts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))

from inspeximus import Inspeximus  # noqa: E402  (after sys.path so a fresh clone works uninstalled)

# ── pinned operating point ──────────────────────────────────────────────────────────────────────
DATASET_NAME = "longmemeval_s"
DATASET_URL = "https://huggingface.co/datasets/xiaowu0162/longmemeval/resolve/main/longmemeval_s"
#: sha256 of the file this harness was built and measured against. A different hash is reported, not
#: fatal — the upstream dataset may be revised — but a number from a different file is a different
#: number and must say so.
DATASET_SHA256 = "08d8dad4be43ee2049a22ff5674eb86725d0ce5ff434cde2627e5e8e7e117894"
DATASET_N = 500

K = 25                      # same k as this repo's published LoCoMo retrieval operating point
MODE = "hybrid"             # lexical + semantic RRF; explicit, not left to the `auto` threshold
CONTEXT_CHAR_BUDGET = 24000  # ~6k tokens. IDENTICAL for every arm — a budget gap flips rankings.
NUM_CTX = 8192              # explicit; Ollama's default (2048) silently truncates and would measure that
# EVERY BUDGET IS A FLOOR, NEVER A CAP, AND 192 WAS STILL A CAP.
#
# The rule is a year old in our own store and the owner has now stated it again: any script calling
# any model sets max_tokens HIGH, at least 8000, because these are thinking models that burn
# thousands of tokens before the first visible character. When an LLM loop returns empty, the FIRST
# suspect is the budget, and the fix is never to shrink the experiment around it.
#
# This file shipped a 24-token pre-flight. That is not a check, it is a filter that passes only
# models old enough to answer immediately, which is exactly how a 7-month-old local judge sailed
# through the same gate that failed the model under test. Measured here against
# deepseek-v4-flash:0731-cloud on the pre-flight prompt: cap 24 empty, cap 64 empty, cap 128 answers
# using 51 tokens. Then I set 192, which is the same mistake with a bigger number.
PREFLIGHT_MAX_TOKENS = int(os.environ.get("PREFLIGHT_MAX_TOKENS", "8000"))
ANSWER_MAX_TOKENS = int(os.environ.get("ANSWER_MAX_TOKENS", "8000"))
# NO WEAK LOCAL MODEL JUDGES THIS. The judge was qwen2.5:7b at a 96-token budget: a 7B model given
# less room than it needs to think, marking a reasoner's work. Both roles now run the cloud reasoner
# with a real budget. The cost of a large ceiling on a short answer is nothing, because the model
# stops when it is done; the cost of a small one is an empty completion scored as a wrong answer.
JUDGE_MAX_TOKENS = int(os.environ.get("JUDGE_MAX_TOKENS", "8000"))
# THE ANSWERER IS A PARAMETER, AND THAT IS THE POINT OF THIS RUN.
#
# On 2026-08-27 this benchmark reported oracle 0.500 and inspeximus 0.500 and its own band check
# FAILED: "inspeximus (0.5) is not strictly below the oracle ceiling (0.5)". When flat retrieval
# equals the oracle, retrieval is not the bottleneck and no retrieval arm can show anything. The
# tell was temporal-reasoning, where the retrieval arms scored 0.50 against an ORACLE of 0.17: a
# perfect context did worse than a partial one, which is not a memory result, it is an 8B answerer.
#
# The README already said the numbers are "not comparable to published LongMemEval numbers" because
# "Zep, mem0 and the paper's own baselines use GPT-4-class answerers and judges; these are 7-8 B
# local models". That is the roadmap's gap #1 -- no citable LongMemEval score -- restated as a
# property of our own harness.
#
# THE ANSWERER AND THE JUDGE ARE THE SAME MODEL, AND THAT IS A KNOWN WEAKNESS, NOT A DESIGN.
#
# This block used to say the judge stayed local and was "deliberately a different model family from
# the answerer". That stopped being true on 2026-08-27, when the local 7B judge was replaced because
# a weak model must not mark a reasoner's work, and the comment was left describing the arrangement
# it had just lost. A file that documents a property it no longer has is worse than one that
# documents nothing, so it is written down plainly instead: both roles now run the same cloud model,
# self-preference bias is therefore live and unmeasured, and any comparison against a published
# LongMemEval figure has to carry that caveat.
#
# The gate that replaced the family separation is judge_calibration_gate(), below: whatever judge is
# configured must have a calibration receipt measured on THESE prompts. It does not care which model
# you choose, only that there is evidence for it.
ANSWERER = os.environ.get("ANSWERER", "deepseek-v4-flash:0731-cloud")
JUDGE = os.environ.get("JUDGE", "deepseek-v4-flash:0731-cloud")
EMBEDDER = "nomic-embed-text"
OLLAMA = os.environ.get("OLLAMA_HOST_URL", "http://localhost:11434")

#: Subset sizes. `small` is the default and the one the README quotes.
SUBSETS = {"smoke": 3, "small": 20, "medium": 50, "full": 470}
ARMS = ["oracle", "inspeximus", "iterative", "recency", "shuffled", "empty", "full_context"]
ROUNDS = int(os.environ.get("ROUNDS", "1"))
FOLLOWUP_PROMPT = (
    "A question needs evidence from several past conversations. You have some of it.\n\n"
    "QUESTION: {question}\n\nWHAT YOU HAVE ALREADY FOUND:\n{seen}\n\n"
    "Name up to three SHORT search queries for the parts that are still missing. "
    "One per line, no numbering, no explanation. If nothing is missing, reply with nothing.")
CEILING_ARM, SYSTEM_ARM, FLOOR_ARM = "oracle", "inspeximus", "recency"

#: GPU pre-flight thresholds. This box shares one RTX 3090 with a long-running research process; a
#: score measured while another model owns the card is a score of the contention, not of the memory
#: system. The gate REFUSES rather than warns.
# The floor covers the answerer AND the judge. With the answerer on a cloud tag only the judge and
# the embedder are resident, so the requirement is set from what actually loads rather than from the
# worst case. Overridable, and the value used is stamped into the result.
# With the answerer AND the judge on cloud tags, the only resident model is nomic-embed-text at
# ~0.3 GB. The 20 GB floor described a configuration that no longer exists.
MIN_FREE_VRAM_MB = int(os.environ.get("MIN_FREE_VRAM_MB", str(4 * 1024)))
# THE MODELS THIS RUN IS ENTITLED TO HAVE RESIDENT. Anything else on the card is somebody's job.
#
# This gate used to look for a PROCESS NAME, "llama-server", and refuse if one existed. That was
# right while the answerer and judge were local: a resident llama-server meant a big model on the
# card. With both on cloud tags the only thing this benchmark loads locally is nomic-embed-text,
# which Ollama also serves through llama-server, so the gate reported our own embedder as a foreign
# job and could never pass. A check that cannot be satisfied by the configuration it is guarding is
# not a safety gate.
#
# It now asks Ollama what is actually RESIDENT and flags any model that is not ours. That is the
# question it was always trying to ask, and it still catches the case it exists for: someone else's
# 20 GB model sitting on the card.
OWN_MODELS_PREFIXES = ("nomic-embed-text",)

# ── prompts (hashed into the result) ────────────────────────────────────────────────────────────
ANSWER_PROMPT = """You are answering a question about a user's past conversations with an assistant.

Today's date: {question_date}

Retrieved memory (this is everything you know; it may be empty or irrelevant):
---
{context}
---

Question: {question}

Answer from the retrieved memory only. Do not use outside knowledge and do not guess. If the memory
does not contain the answer, reply exactly: I don't know.
Answer concisely."""

JUDGE_PROMPT = """You grade a memory-augmented assistant's answer against a gold answer. Be strict and literal.

QUESTION: {question}

GOLD ANSWER: {gold}

THE ANSWER TO GRADE: {answer}

Reply with ONLY one JSON object and no prose: {{"correct": 0 or 1}}

correct = 1 when the answer conveys the same essential information as the gold answer. Paraphrase,
extra detail, and different formatting of the same value are fine.
correct = 0 when the answer contradicts the gold answer, omits the value the question asked for,
answers a different question, or says it does not know / has no information."""

JUDGE_PROMPT_PREFERENCE = """You grade a memory-augmented assistant's answer against a gold PREFERENCE rubric.
The gold answer describes what the user would and would not prefer, not a single fact.

QUESTION: {question}

GOLD PREFERENCE RUBRIC: {gold}

THE ANSWER TO GRADE: {answer}

Reply with ONLY one JSON object and no prose: {{"correct": 0 or 1}}

correct = 1 when the answer satisfies what the rubric says the user would prefer and avoids what the
rubric says they would not prefer.
correct = 0 otherwise, including when the answer says it does not know."""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


PROMPT_HASHES = {
    "answer_prompt_sha256": _sha(ANSWER_PROMPT),
    "judge_prompt_sha256": _sha(JUDGE_PROMPT),
    "judge_prompt_preference_sha256": _sha(JUDGE_PROMPT_PREFERENCE),
}


# ── dataset resolution: find it, or FAIL LOUDLY with the command that fixes it ───────────────────
class DatasetMissing(SystemExit):
    def __init__(self, msg: str):
        super().__init__(2)
        self.msg = msg


def candidate_paths(name: str = DATASET_NAME) -> list[Path]:
    """Every place the dataset may live, in priority order. Environment first so a shared cache on a
    build machine does not force a copy into the repository."""
    out = []
    explicit = os.environ.get("LONGMEMEVAL_DATA")
    if explicit:
        out.append(Path(explicit))
    dirn = os.environ.get("LONGMEMEVAL_DATA_DIR")
    if dirn:
        out.append(Path(dirn) / f"{name}.json")
    out.append(HERE / "data" / f"{name}.json")
    out.append(Path.home() / ".cache" / "longmemeval" / f"{name}.json")
    return out


MISSING_MESSAGE = """
LongMemEval was not found and this harness will NOT score on a substitute dataset.

Looked in:
{tried}

Get it one of these ways:

  1. python benchmarks/longmemeval/run.py --download
       downloads {url}
       (278 MB) to ~/.cache/longmemeval/{name}.json

  2. manually, then point the harness at it:
       curl -L -o {name}.json "{url}"
       export LONGMEMEVAL_DATA=/path/to/{name}.json

The dataset is LongMemEval (Wu et al., ICLR 2025, arXiv:2410.10813), CC-BY-NC 4.0, published at
huggingface.co/datasets/xiaowu0162/longmemeval. This repository does not redistribute it.
""".strip()


def resolve_dataset(explicit: str | None = None, name: str = DATASET_NAME) -> Path:
    paths = [Path(explicit)] if explicit else candidate_paths(name)
    for p in paths:
        if p.is_file():
            return p
    raise DatasetMissing(MISSING_MESSAGE.format(
        tried="\n".join(f"  - {p}" for p in paths), url=DATASET_URL, name=name))


def download(name: str = DATASET_NAME) -> Path:
    dest = Path.home() / ".cache" / "longmemeval" / f"{name}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(".json.part")
    print(f"downloading {DATASET_URL}\n        -> {dest}", flush=True)
    with urllib.request.urlopen(DATASET_URL, timeout=120) as r, open(part, "wb") as fh:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
            got += len(chunk)
            if total:
                print(f"\r  {got / 1e6:7.1f} / {total / 1e6:.1f} MB", end="", flush=True)
    print()
    part.replace(dest)
    return dest


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


REQUIRED_KEYS = {"question_id", "question", "question_type", "question_date", "answer",
                 "haystack_sessions", "haystack_dates", "haystack_session_ids", "answer_session_ids"}


def load_dataset(path: Path) -> list[dict]:
    """Load and ASSERT it is LongMemEval. A harness that happily scores whatever JSON it was pointed
    at is the failure mode this whole file is written against."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise DatasetMissing(f"{path} is not a LongMemEval instance list")
    missing = REQUIRED_KEYS - set(data[0])
    if missing:
        raise DatasetMissing(
            f"{path} does not look like LongMemEval: instance 0 is missing {sorted(missing)}.\n"
            f"This harness scores LongMemEval only. It will not fall back to another dataset.")
    if len(data) != DATASET_N:
        print(f"  NOTE: expected {DATASET_N} instances, found {len(data)} — recorded in the result",
              flush=True)
    return data


# ── deterministic subset selection (no seed: the membership is a function of the data) ───────────
def select_subset(data: list[dict], n: int) -> list[dict]:
    """Stratified proportional sample across `question_type`, deterministic and re-derivable.

    Abstention instances (`question_id` ending in `_abs`, 30 of 500) are EXCLUDED: their gold answer
    is "you did not mention this", which needs an abstention rubric rather than a semantic match, and
    scoring them under the factual rubric would measure the judge instead of the memory. Declared
    here and in PREREGISTRATION.md, before any run.

    Allocation is largest-remainder over the type frequencies; within a type, instances are ordered
    by sha256 of their `question_id`. No RNG is involved, so the subset cannot drift between runs or
    be re-rolled until it flatters us — and unlike a plain lexicographic sort it is not correlated
    with the dataset's id conventions. That correlation is not hypothetical: 89 of the 133
    temporal-reasoning questions carry a `gpt4_` prefix, and lexicographic order would have put every
    one of them behind the hex-named ones, so a 6-question temporal stratum would have contained
    exactly zero of them."""
    pool = [x for x in data if not str(x["question_id"]).endswith("_abs")]
    types: dict[str, list[dict]] = {}
    for x in pool:
        types.setdefault(x["question_type"], []).append(x)
    for v in types.values():
        v.sort(key=lambda x: _sha(str(x["question_id"])))
    n = min(n, len(pool))
    total = len(pool)
    exact = {t: len(v) * n / total for t, v in types.items()}
    alloc = {t: int(e) for t, e in exact.items()}
    # largest remainder, ties broken by type name so the result is stable across dict orderings
    for t, _ in sorted(((t, (-(exact[t] - alloc[t]), t)) for t in types), key=lambda kv: kv[1]):
        if sum(alloc.values()) >= n:
            break
        alloc[t] += 1
    while sum(alloc.values()) > n:                      # can only trigger on tiny n
        t = max(sorted(alloc), key=lambda t: alloc[t])
        alloc[t] -= 1
    out = []
    for t in sorted(types):
        out.extend(types[t][:alloc[t]])
    out.sort(key=lambda x: _sha(str(x["question_id"])))
    return out


# ── local model calls (stdlib only; Ollama native API so num_ctx is explicit) ────────────────────
class Latency:
    """Per-call timings. A 0.0 s reply is a cache hit, not a call — recorded so it can be seen."""

    def __init__(self):
        self.calls: dict[str, list[float]] = {}

    def add(self, kind: str, dt: float):
        self.calls.setdefault(kind, []).append(dt)

    def report(self) -> dict:
        out = {}
        for kind, xs in self.calls.items():
            xs = sorted(xs)
            out[kind] = {"n": len(xs), "median_s": round(statistics.median(xs), 3),
                         "p95_s": round(xs[int(0.95 * (len(xs) - 1))], 3),
                         "max_s": round(xs[-1], 3), "zero_s_calls": sum(1 for x in xs if x <= 0.0)}
        return out


LAT = Latency()


def _post(url: str, payload: dict, timeout: int = 600) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def chat(model: str, prompt: str, max_tokens: int, kind: str, retries: int = 3) -> str:
    body = {"model": model, "stream": False,
            "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": 0.0, "num_ctx": NUM_CTX, "num_predict": max_tokens,
                        "seed": 0}}
    last = ""
    for attempt in range(retries):
        t0 = time.time()
        try:
            r = _post(f"{OLLAMA}/api/chat", body)
            LAT.add(kind, time.time() - t0)
            content = ((r.get("message") or {}).get("content") or "").strip()
            if content:
                return content
            last = "empty completion"
        except Exception as e:                                   # noqa: BLE001 - reported, not swallowed
            last = f"{type(e).__name__}: {str(e)[:120]}"
        time.sleep(2 + 2 * attempt)
    return f"__ERR__{last}"


_EMB_CACHE: dict[str, list[float] | None] = {}


def embed_batch(texts: list[str], batch: int = 96) -> None:
    todo = [t for t in dict.fromkeys(texts) if t and t not in _EMB_CACHE]
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        t0 = time.time()
        try:
            r = _post(f"{OLLAMA}/api/embed", {"model": EMBEDDER, "input": chunk})
            LAT.add("embed_batch", time.time() - t0)
            for t, e in zip(chunk, r["embeddings"]):
                _EMB_CACHE[t] = e
        except Exception as e:                                   # noqa: BLE001
            LAT.add("embed_batch", time.time() - t0)
            print(f"  embed batch failed ({type(e).__name__}); those turns lose their vector", flush=True)
            for t in chunk:
                _EMB_CACHE.setdefault(t, None)


def embed_one(text: str):
    if text not in _EMB_CACHE:
        embed_batch([text])
    return _EMB_CACHE.get(text)


# ── pre-flight: the GPU gate and the "is this model actually answering" gate ─────────────────────
def gpu_state() -> dict:
    """Read the card. Never touches a process — quiescing the GPU is the coordinator's decision."""
    state: dict = {"nvidia_smi": False, "free_mb": None, "total_mb": None, "util_pct": None,
                   "foreign_processes": [], "error": None}
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=30)
        if out.returncode == 0 and out.stdout.strip():
            free, total, util = (int(x.strip()) for x in out.stdout.strip().splitlines()[0].split(","))
            state.update(nvidia_smi=True, free_mb=free, total_mb=total, util_pct=util)
        else:
            state["error"] = (out.stderr or out.stdout or "nvidia-smi returned no rows").strip()[:200]
    except Exception as e:                                       # noqa: BLE001
        state["error"] = f"{type(e).__name__}: {str(e)[:160]}"
    try:
        import urllib.request as _u
        with _u.urlopen(OLLAMA + "/api/ps", timeout=20) as r:
            resident = [m.get("name") or "" for m in (json.loads(r.read().decode()).get("models") or [])]
        state["resident_models"] = resident
        state["foreign_processes"] = sorted(
            m for m in resident if not any(m.startswith(p) for p in OWN_MODELS_PREFIXES))
    except Exception as e:                                       # noqa: BLE001
        # Cannot tell who is on the card. Say so rather than reporting an empty list as "clear".
        state["resident_models"] = None
        state["foreign_processes"] = []
        state["residency_unknown"] = f"{type(e).__name__}: {str(e)[:120]}"
    return state


def judge_calibration_gate() -> tuple[bool, str]:
    """Refuse to score unless THIS judge, with THESE prompts, has a calibration receipt.

    The rule existed as a line in the module docstring, "run before trusting a score", and it did
    not hold: on 2026-08-27 a run scored 20 questions with a 7B local judge that had been calibrated
    a month earlier on a contended card, while the model under test failed its own pre-flight. A
    sentence telling the operator to run something is not a gate; the operator is the person who
    forgets.

    Nothing here is a list of acceptable models. A list would go stale the first time a better judge
    appeared, which is the same defect as a hand-written allow-list anywhere else. The gate asks for
    EVIDENCE about whichever judge is configured, and the evidence expires by itself: the receipt
    carries the sha256 of every prompt it was measured with, so editing a prompt invalidates the
    calibration in the same commit that changes it.
    """
    p = HERE / "results" / ("judge_calibration_%s.json" % JUDGE.replace(":", "-"))
    if not p.exists():
        return False, "no calibration receipt for judge %r at %s" % (JUDGE, p.name)
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:                                            # noqa: BLE001
        return False, "calibration receipt %s is unreadable: %s" % (p.name, e)
    if rec.get("judge") != JUDGE:
        return False, ("receipt %s was measured on judge %r, not %r"
                       % (p.name, rec.get("judge"), JUDGE))
    for k, want in PROMPT_HASHES.items():
        got = rec.get(k)
        if got != want:
            return False, ("%s changed since calibration (%s: receipt %s, now %s). The receipt is "
                           "void; re-run the calibration." % (k, k, str(got)[:12], want[:12]))
    return True, "calibrated %s" % rec.get("generated_utc", "?")


def gpu_preflight(allow_contended: bool) -> dict:
    st = gpu_state()
    reasons = []
    if st["free_mb"] is None:
        reasons.append(f"nvidia-smi could not be read ({st['error']}); free VRAM is unknown")
    elif st["free_mb"] < MIN_FREE_VRAM_MB:
        reasons.append(f"only {st['free_mb']} MiB VRAM free, need >= {MIN_FREE_VRAM_MB} MiB")
    if st["foreign_processes"]:
        reasons.append("another model is resident on the card: " + ", ".join(st["foreign_processes"]))
    if st.get("residency_unknown"):
        reasons.append("could not read what is resident (%s); refusing rather than assuming a clear card"
                       % st["residency_unknown"])
    st["passed"] = not reasons
    st["reasons"] = reasons
    st["override"] = bool(reasons) and allow_contended
    if reasons and not allow_contended:
        print("\nGPU PRE-FLIGHT REFUSED — not starting.\n", flush=True)
        for r in reasons:
            print(f"  - {r}", flush=True)
        print("\nAnother process owns this GPU. Do NOT kill it; ask the coordinator to quiesce the\n"
              "card, then re-run. To take a deliberately CONTENDED measurement (stamped as such in\n"
              "the result JSON and not quotable as a clean number), pass --allow-contended-gpu.\n",
              flush=True)
        raise SystemExit(3)
    if reasons:
        print("\nGPU PRE-FLIGHT FAILED but --allow-contended-gpu was passed. Every number from this\n"
              "run is stamped contended=true and must be reported that way.\n", flush=True)
        for r in reasons:
            print(f"  - {r}", flush=True)
        print(flush=True)
    return st


def model_preflight(models: list[str]) -> dict:
    """A live-model check that cannot be satisfied by a cache: a nonce that has never been sent
    before, echoed back. A string coming back is not evidence the model ran, and a 0.0 s reply is a
    cache hit rather than a call — so the probe is unique per run and the ANSWER is checked.

    Deliberately an ECHO, not arithmetic. The first version asked for a five-digit subtraction and
    `llama3.1:8b` got it wrong, which failed the gate for the one reason it is not there to detect:
    the model was alive, answering, and simply bad at mental arithmetic. This gate answers "is a live
    model reading MY prompt", not "is it clever"."""
    nonce = f"LME-{int(time.time() * 1000) % 1000000:06d}-{os.getpid() % 1000:03d}"
    out = {}
    ok = True
    for m in models:
        t0 = time.time()
        # A CAP IS A MODEL FILTER, AND THIS ONE WAS SET TO 24.
        #
        # Every current model spends tokens on hidden reasoning before the first visible character,
        # so a tight cap returns a truncated completion with EMPTY content and the check reads it as
        # "the model did not answer". Measured here on 2026-08-27 against
        # deepseek-v4-flash:0731-cloud, same prompt, temperature 0, one nonce:
        #
        #     cap  24 -> empty, eval_count 24 (hit the cap)
        #     cap  64 -> empty, eval_count 64 (hit the cap)
        #     cap 128 -> the nonce, eval_count 51
        #
        # At 24 the only model that passes is one old enough to emit its first token immediately,
        # which is why qwen2.5:7b sailed through the same pre-flight that failed the answerer. The
        # pre-flight was selecting for age, not for correctness.
        reply = chat(m, f"Repeat this code back exactly and write nothing else: {nonce}",
                     PREFLIGHT_MAX_TOKENS,
                     "preflight")
        dt = round(time.time() - t0, 2)
        good = nonce in reply and dt > 0.0
        out[m] = {"latency_s": dt, "reply": reply[:80], "expected": nonce, "answered": good}
        print(f"  preflight {m:16} {dt:6.2f}s answered={good} {reply[:40]!r}", flush=True)
        ok = ok and good
    probe = f"unique embedding probe {nonce}"
    t0 = time.time()
    vec = embed_one(probe)
    out[EMBEDDER] = {"latency_s": round(time.time() - t0, 2),
                     "dim": len(vec) if vec else None, "answered": bool(vec)}
    print(f"  preflight {EMBEDDER:16} {out[EMBEDDER]['latency_s']:6.2f}s dim={out[EMBEDDER]['dim']}",
          flush=True)
    out["all_answered"] = ok and bool(vec)
    return out


# ── ingestion (ZERO LLM on the write path) ──────────────────────────────────────────────────────
def instance_turns(inst: dict) -> list[dict]:
    """Flatten the haystack into ordered turns. The session DATE is prepended to the text because
    LongMemEval's temporal-reasoning questions are unanswerable without it, and dropping it would
    handicap every arm equally but silently."""
    turns = []
    for sid, date, session in zip(inst["haystack_session_ids"], inst["haystack_dates"],
                                  inst["haystack_sessions"]):
        for i, msg in enumerate(session):
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            turns.append({"session_id": sid, "date": date, "role": msg.get("role", ""),
                          "idx": i, "text": f"[{date}] {msg.get('role', '')}: {content}",
                          "is_evidence": bool(msg.get("has_answer"))})
    return turns


def build_store(turns: list[dict]) -> Inspeximus:
    """One store per instance. `remember()` + a local embedder; no extraction, no summarisation, no
    model call on the write path."""
    embed_batch([t["text"] for t in turns])
    store = Inspeximus(path=None, embed=embed_one)
    for t in turns:
        store.remember(t["text"], mtype="episodic",
                       meta={"session_id": t["session_id"], "role": t["role"], "date": t["date"]})
    return store


# ── context construction: ONE character budget, every arm ───────────────────────────────────────
def _fill(texts: list[str], budget: int = CONTEXT_CHAR_BUDGET) -> tuple[str, bool]:
    kept, used, truncated = [], 0, False
    for t in texts:
        if used + len(t) + 1 > budget:
            truncated = True
            break
        kept.append(t)
        used += len(t) + 1
    return "\n".join(kept), truncated


def context_for(arm: str, inst: dict, turns: list[dict], store, alt_store) -> tuple[str, dict]:
    """Return (context, info). Every arm is filled to the SAME character budget — a budget gap between
    arms once flipped the ranking of a benchmark in this repository, so it is matched by construction
    and the realised size is recorded per arm."""
    info: dict = {"truncated": False}
    if arm == "empty":
        return "", info
    if arm == "oracle":
        gold = set(inst["answer_session_ids"])
        texts = [t["text"] for t in turns if t["session_id"] in gold]
        info["evidence_turns"] = len(texts)
        ctx, info["truncated"] = _fill(texts)
        return ctx, info
    if arm == "recency":
        # fill backwards from the newest turn, then present chronologically
        kept, used = [], 0
        for t in reversed(turns):
            if used + len(t["text"]) + 1 > CONTEXT_CHAR_BUDGET:
                info["truncated"] = True
                break
            kept.append(t["text"])
            used += len(t["text"]) + 1
        info["turns"] = len(kept)
        return "\n".join(reversed(kept)), info
    if arm == "full_context":
        total = sum(len(t["text"]) + 1 for t in turns)
        info["haystack_chars"] = total
        if total > CONTEXT_CHAR_BUDGET:
            info["not_computable"] = (
                f"haystack is {total} characters; the answerer's window at num_ctx={NUM_CTX} holds "
                f"about {CONTEXT_CHAR_BUDGET}. Not computable at this operating point.")
            return None, info
        ctx, info["truncated"] = _fill([t["text"] for t in turns])
        return ctx, info
    if arm == "iterative":
        # THE ARM THIS FILE'S OWN README DIAGNOSED AND NEVER RAN.
        #
        # The README says of the flat arm: "It scores 0.00 on multi-session against a 0.20 ceiling:
        # a single top-k pass returns turns about the question, and a question spanning several
        # sessions needs turns about each part of it." That second clause describes
        # `recall_iterative`, which this library has shipped the whole time: retrieve, let a model
        # read the hits and name what is missing, retrieve again on those follow-ups, merge.
        #
        # Its measured lever on the analogous task (LoCoMo multi-hop full-evidence recall@50, equal
        # budget B=50) is flat 0.145 -> iterative 0.297, n=276. Nobody had pointed it at THIS
        # benchmark, so the multi-session cell read 0.00 and got quoted as the axis being hard
        # rather than as the one arm we had never run.
        #
        # SAME BUDGET as every other arm: `_fill` truncates to CONTEXT_CHAR_BUDGET, so the extra
        # retrieval buys different turns, never more of them. Cost is one model call per round.
        def ask_followup(q, current):
            seen = "\n".join((h.get("text") or "")[:400] for h in (current or [])[:6])
            out = chat(ANSWERER, FOLLOWUP_PROMPT.format(question=q, seen=seen or "(nothing)"),
                       180, "followup") or ""
            qs = [ln.strip(" -*\t") for ln in out.splitlines() if len(ln.strip(" -*\t")) > 8]
            return qs[:3]

        hits = store.recall_iterative(inst["question"], ask_followup, k=K, rounds=ROUNDS,
                                      mode=MODE, reinforce=False) or []
        info["hits"] = len(hits)
        info["rounds"] = ROUNDS
        ctx, info["truncated"] = _fill([h.get("text", "") for h in hits])
        return ctx, info
    src = alt_store if arm == "shuffled" else store
    hits = src.recall(inst["question"], k=K, mode=MODE, reinforce=False) or []
    info["hits"] = len(hits)
    ctx, info["truncated"] = _fill([h.get("text", "") for h in hits])
    return ctx, info


# ── answering and judging ───────────────────────────────────────────────────────────────────────
def answer_one(inst: dict, context: str) -> str:
    return chat(ANSWERER, ANSWER_PROMPT.format(question_date=inst["question_date"],
                                               context=context or "(no memory available)",
                                               question=inst["question"]),
                ANSWER_MAX_TOKENS, "answer")


def parse_verdict(raw: str):
    m = re.search(r"\{[^{}]*\}", raw or "", re.S)
    if m:
        try:
            v = json.loads(m.group(0)).get("correct")
            if v in (0, 1, True, False):
                return int(bool(v))
        except Exception:                                        # noqa: BLE001
            pass
    m = re.search(r'"?correct"?\s*[:=]\s*(0|1|true|false)', raw or "", re.I)
    if m:
        return int(m.group(1).lower() in ("1", "true"))
    return None


def judge_one(inst: dict, answer: str) -> tuple[int | None, str]:
    tpl = JUDGE_PROMPT_PREFERENCE if inst["question_type"] == "single-session-preference" else JUDGE_PROMPT
    raw = chat(JUDGE, tpl.format(question=inst["question"], gold=inst["answer"], answer=answer),
               JUDGE_MAX_TOKENS, "judge")
    return parse_verdict(raw), raw


# ── the run ─────────────────────────────────────────────────────────────────────────────────────
def band_check(scores: dict) -> dict:
    """The harness's own falsification test. It must be able to fail, and it fails the RUN."""
    sys_s, floor_s, ceil_s = scores.get(SYSTEM_ARM), scores.get(FLOOR_ARM), scores.get(CEILING_ARM)
    checks, reasons = {}, []
    have = all(x is not None for x in (sys_s, floor_s, ceil_s))
    if not have:
        return {"passed": False, "checks": {},
                "reasons": [f"missing a score for one of {FLOOR_ARM}/{SYSTEM_ARM}/{CEILING_ARM}"]}
    checks["above_floor"] = sys_s > floor_s
    checks["below_ceiling"] = sys_s < ceil_s
    if not checks["above_floor"]:
        reasons.append(f"{SYSTEM_ARM} ({sys_s}) is not strictly above the {FLOOR_ARM} floor ({floor_s})")
    if not checks["below_ceiling"]:
        reasons.append(f"{SYSTEM_ARM} ({sys_s}) is not strictly below the {CEILING_ARM} ceiling ({ceil_s})")
    for control in ("shuffled", "empty"):
        if scores.get(control) is not None:
            checks[f"{control}_at_or_below_floor"] = scores[control] <= floor_s
            if scores[control] > floor_s:
                reasons.append(f"control arm {control} ({scores[control]}) scores above the floor "
                               f"({floor_s}) — the harness is rewarding something other than memory")
    return {"passed": not reasons, "checks": checks, "reasons": reasons}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--subset", default="small", choices=sorted(SUBSETS))
    ap.add_argument("--n", type=int, default=None, help="override the subset size (recorded)")
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--data", default=None, help="explicit path to the LongMemEval json")
    ap.add_argument("--download", action="store_true", help="fetch the dataset and exit")
    ap.add_argument("--allow-contended-gpu", action="store_true",
                    help="proceed even though the GPU pre-flight failed; stamps the result contended")
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)

    if a.download:
        p = download()
        print(f"saved {p}  sha256={file_sha256(p)}")
        return 0

    started = time.time()
    print(f"LongMemEval end-to-end QA · subset={a.subset} k={K} mode={MODE} "
          f"budget={CONTEXT_CHAR_BUDGET}c answerer={ANSWERER} judge={JUDGE}\n", flush=True)

    # Locate the dataset BEFORE the GPU gate: it is a pure existence check, and a user with no
    # dataset should get the actionable message whether or not somebody else is using the card.
    path = resolve_dataset(a.data)
    gpu = gpu_preflight(a.allow_contended_gpu)

    sha = file_sha256(path)
    data = load_dataset(path)
    print(f"  dataset {path}  n={len(data)}  sha256={sha[:16]}…"
          f"{'  (matches the pinned hash)' if sha == DATASET_SHA256 else '  DIFFERS from the pinned hash'}",
          flush=True)

    pre = model_preflight([ANSWERER, JUDGE])
    if not pre["all_answered"]:
        print("\nMODEL PRE-FLIGHT FAILED — a model did not return the correct answer to a unique "
              "prompt. Not scoring.", flush=True)
        return 4

    cal_ok, cal_why = judge_calibration_gate()
    if not cal_ok:
        print("\nJUDGE NOT CALIBRATED — not scoring.\n  %s\n"
              "  Run: python benchmarks/longmemeval/judge_calibration.py" % cal_why, flush=True)
        return 6

    n = a.n or SUBSETS[a.subset]
    sample = select_subset(data, n)
    arms = [x.strip() for x in a.arms.split(",") if x.strip()]
    print(f"  subset: {len(sample)} questions, stratified, deterministic (abstention excluded)")
    print(f"  arms:   {', '.join(arms)}\n", flush=True)

    # ── phase 1: build one store per instance (embeddings; no LLM) ──────────────────────────────
    stores, all_turns = [], []
    t0 = time.time()
    for i, inst in enumerate(sample):
        turns = instance_turns(inst)
        all_turns.append(turns)
        need_store = bool({"inspeximus", "shuffled", "iterative"} & set(arms))
        stores.append(build_store(turns) if need_store else None)
        print(f"  [{i + 1}/{len(sample)}] {inst['question_id']:16} {len(turns):4d} turns ingested "
              f"({time.time() - t0:.0f}s elapsed)", flush=True)
    ingest_s = time.time() - t0

    # ── phase 2: contexts (no LLM) ──────────────────────────────────────────────────────────────
    items = []
    for i, inst in enumerate(sample):
        alt = stores[(i + 1) % len(sample)] if len(sample) > 1 else stores[i]
        for arm in arms:
            ctx, info = context_for(arm, inst, all_turns[i], stores[i], alt)
            items.append({"qid": inst["question_id"], "arm": arm, "type": inst["question_type"],
                          "question": inst["question"], "gold": inst["answer"],
                          "context_chars": len(ctx) if ctx is not None else None,
                          "context": ctx, "info": info, "_inst": inst})
    if len(sample) > 1 and {"shuffled", "inspeximus"} <= set(arms):
        same = sum(1 for it in items if it["arm"] == "shuffled" and it["context"] and
                   it["context"] == next(x["context"] for x in items
                                         if x["qid"] == it["qid"] and x["arm"] == "inspeximus"))
        print(f"\n  control wiring: shuffled context identical to inspeximus for {same}/{len(sample)} "
              f"questions (must be 0)", flush=True)

    # ── phase 3: answer everything with ONE model resident ──────────────────────────────────────
    todo = [it for it in items if it["context"] is not None]
    print(f"\n  answering {len(todo)} items with {ANSWERER}", flush=True)
    t0 = time.time()
    for j, it in enumerate(todo):
        it["answer"] = answer_one(it["_inst"], it["context"])
        if (j + 1) % 10 == 0 or j + 1 == len(todo):
            print(f"    {j + 1}/{len(todo)} ({time.time() - t0:.0f}s)", flush=True)
    answer_s = time.time() - t0

    # ── phase 4: judge everything with ONE model resident ───────────────────────────────────────
    print(f"\n  judging {len(todo)} items with {JUDGE}", flush=True)
    t0 = time.time()
    for j, it in enumerate(todo):
        it["correct"], raw = judge_one(it["_inst"], it["answer"])
        it["judge_raw"] = raw[:200]
        if (j + 1) % 10 == 0 or j + 1 == len(todo):
            print(f"    {j + 1}/{len(todo)} ({time.time() - t0:.0f}s)", flush=True)
    judge_s = time.time() - t0

    # ── phase 5: aggregate ──────────────────────────────────────────────────────────────────────
    per_arm: dict = {}
    for arm in arms:
        rows = [it for it in items if it["arm"] == arm]
        scored = [it for it in rows if it.get("correct") in (0, 1)]
        nc = [it for it in rows if it["context"] is None]
        # A model call that failed after its retries returns "__ERR__…", which the judge then scores
        # as wrong. Counted per arm so a network or GPU failure cannot silently deflate an arm and be
        # read as the memory system missing.
        entry = {"n": len(rows), "n_scored": len(scored),
                 "llm_errors": sum(1 for it in rows if str(it.get("answer", "")).startswith("__ERR__")),
                 "unparsed_judgements": len(rows) - len(scored) - len(nc),
                 "accuracy": round(sum(it["correct"] for it in scored) / len(scored), 3) if scored else None,
                 "mean_context_chars": (round(statistics.mean([it["context_chars"] for it in rows
                                                               if it["context_chars"] is not None]))
                                        if any(it["context_chars"] is not None for it in rows) else None),
                 "truncated": sum(1 for it in rows if it["info"].get("truncated"))}
        if nc:
            entry["not_computable"] = nc[0]["info"].get("not_computable")
            entry["accuracy"] = None
        by_type: dict = {}
        for it in scored:
            b = by_type.setdefault(it["type"], [0, 0])
            b[0] += it["correct"]
            b[1] += 1
        entry["by_question_type"] = {t: {"accuracy": round(v[0] / v[1], 3), "n": v[1]}
                                     for t, v in sorted(by_type.items())}
        per_arm[arm] = entry

    scores = {arm: per_arm[arm]["accuracy"] for arm in per_arm}
    band = band_check(scores)

    result = {
        "benchmark": "LongMemEval_S end-to-end QA",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "headline": {arm: scores[arm] for arm in arms},
        "band_check": band,
        "operating_point": {
            "dataset": DATASET_NAME, "dataset_path": str(path), "dataset_sha256": sha,
            "dataset_sha256_pinned": DATASET_SHA256, "dataset_matches_pin": sha == DATASET_SHA256,
            "dataset_n": len(data), "dataset_source": DATASET_URL,
            "subset": a.subset, "subset_n": len(sample),
            "subset_rule": "stratified proportional by question_type (largest remainder), ordered by "
                           "sha256(question_id) within type, abstention (_abs) excluded, no RNG",
            "question_ids": [x["question_id"] for x in sample],
            "k": K, "recall_mode": MODE, "reinforce": False,
            "context_char_budget": CONTEXT_CHAR_BUDGET,
            "embedder": EMBEDDER, "answerer": ANSWERER, "judge": JUDGE,
            "num_ctx": NUM_CTX, "temperature": 0.0, "seed": 0,
            "answer_max_tokens": ANSWER_MAX_TOKENS, "judge_max_tokens": JUDGE_MAX_TOKENS,
        "preflight_max_tokens": PREFLIGHT_MAX_TOKENS,
            "llm_on_write_path": False,
            "inspeximus_version": getattr(__import__("inspeximus"), "__version__", "unknown"),
            **PROMPT_HASHES,
        },
        "gpu_preflight": gpu,
        "contended": bool(gpu.get("override")),
        "model_preflight": pre,
        "arms": per_arm,
        "timing_s": {"ingest": round(ingest_s, 1), "answer": round(answer_s, 1),
                     "judge": round(judge_s, 1), "total": round(time.time() - started, 1)},
        "latency": LAT.report(),
        # `.get`, not `[...]`: a NOT-COMPUTABLE arm (full_context) never reaches the answer or judge
        # phase, so it has neither key. Indexing crashed the whole run at the very last step and threw
        # away 5 minutes of GPU work that had already produced every number.
        "per_item": [{k: it.get(k) for k in ("qid", "arm", "type", "context_chars", "correct")}
                     | {"answer": (it.get("answer") or "")[:300]} for it in items],
    }

    out = Path(a.out) if a.out else (HERE / "results" /
                                     f"longmemeval_s_{a.subset}_{time.strftime('%Y%m%d-%H%M%S')}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 78)
    print(f"LongMemEval_S · {len(sample)} questions · k={K} {MODE} · {CONTEXT_CHAR_BUDGET}c budget · "
          f"answerer {ANSWERER} · judge {JUDGE}")
    for arm in arms:
        e = per_arm[arm]
        acc = e["accuracy"]
        label = f"{acc:.3f}" if acc is not None else "N/A"
        extra = f"  {e['not_computable']}" if e.get("not_computable") else \
                f"  (n={e['n_scored']}, ctx~{e['mean_context_chars']}c)"
        print(f"  {arm:14} {label}{extra}")
    print(f"\nBAND CHECK ({FLOOR_ARM} < {SYSTEM_ARM} < {CEILING_ARM}): "
          f"{'PASSED' if band['passed'] else 'FAILED'}")
    for r in band["reasons"]:
        print(f"  - {r}")
    if result["contended"]:
        print("\nCONTENDED: the GPU pre-flight failed and was overridden. Do not quote these numbers "
              "as a clean measurement.")
    print(f"\nsaved {out}")
    print("=" * 78)
    return 0 if band["passed"] else 5


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DatasetMissing as e:
        print(e.msg, file=sys.stderr)
        raise SystemExit(2) from None
