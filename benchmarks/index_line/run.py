"""Does the index line carry retrieval on a PUBLIC corpus, and how much of it survives a budget?

WHY THIS EXISTS. `Inspeximus.memory_index()` ships with a table in its docstring: on a 316-note store
with 120 questions, a written line ("what the record CONCLUDED") reaches recall@3 0.683/0.833 against
0.333/0.508 for a hand-written title and hook, and at the SAME token budget the hand-written index
already spends it still reaches 0.733. Every one of those numbers was measured on a PRIVATE memory
directory. The docstring says so honestly, and that is the problem: a number nobody outside can
recompute is not evidence, it is a claim about code we happen to run. This file is the public half.

THE CORPUS. LongMemEval-S, 500 questions over 19,829 distinct chat sessions, pinned by sha256 so a
revision upstream cannot silently change a published figure. Each question names its evidence session
in `answer_session_ids`, which is exactly the ground truth an index-line experiment needs: a store too
big to hold, read through one line per record, and a question that only the right record answers.
Licence is CC-BY-NC 4.0, so figures from it are publishable as research and MUST NOT be quoted as a
product benchmark.

THE MEASUREMENT. Each question ranks its OWN haystack (median 50 sessions) rather than all 19,829.
That is the realistic setting, it is what the dataset was built for, and it keeps the null reachable:
with ~50 candidates and one gold, chance recall@3 is about 0.06, so an arm that scores 0.5 is doing
something. Ranking is cosine over nomic-embed-text, one scorer for every arm, so the arms differ ONLY
in what text represents the record.

ARMS, and three of the four cost nothing:

  ceiling        the full session text. The upper bound: what retrieval looks like when the whole
                 record is in front of the ranker. If an arm approaches this, the line is not the
                 bottleneck; if the ceiling itself is low, the corpus cannot exercise the mechanism
                 and every other number here is void.
  default_line   what `memory_index()` actually emits with no summariser: the record's opening
                 sentence. This is the SHIPPED default and the row a user gets for free.
  first_user     the first user turn alone, the nearest thing a chat session has to a title.
  idf_terms      the session's highest-idf terms, the strongest deterministic line we know how to
                 build. In the private experiment this was a null (+0.017 / +0.025, both intervals
                 containing zero) and the reason `summarise` is a PARAMETER and not a dependency.
                 If it is a null here too, that replicates on a corpus anyone can fetch.

The written-line arm needs an LLM for every record and is NOT run here: 50 questions is 2,493 distinct
sessions and about 6.1M input tokens. It runs only after the free arms show the ceiling is high and
the deterministic arms fall short of it, because if either fails there is nothing for a written line
to recover and the spend would buy a number with no question behind it.

CONTROLS, each able to fail:
  * the gold session must be IN the haystack it is ranked against. If it is not, recall is measuring
    a fixture bug and every arm is void.
  * a SHUFFLED-line arm, where each record is given another record's line, must collapse to chance.
    Without it a high score could come from the ranker seeing the question rather than the line.
  * the ceiling must beat the weakest arm. A corpus where full text and one line score the same
    cannot show anything about lines -- measured once before, where 3 of 4 arms got byte-identical
    context on 54 of 54 questions and the run was worthless.
  * every arm ranks the SAME candidate set for the same question, asserted per question.
  * sessions longer than the embedder's window are truncated; the count is reported, never hidden.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import random
import sys
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
DATASET_SHA256 = "08d8dad4be43ee2049a22ff5674eb86725d0ce5ff434cde2627e5e8e7e117894"
EMBED_MODEL = "nomic-embed-text"
EMBED_URL = "http://localhost:11434/api/embed"     # /api/embeddings 500s above ~8k chars
CHUNK_CHARS = 6000              # measured safe: at 6k the tail still moves the vector
EMBED_CHAR_CAP = CHUNK_CHARS
N_QUESTIONS = int(os.environ.get("N_QUESTIONS", "50"))
SEED = 20260826
WORKERS = int(os.environ.get("WORKERS", "12"))   # localhost HTTP: the work is wait, not compute


def dataset_path() -> str:
    for p in (os.environ.get("LONGMEMEVAL_DATA") or "",
              os.path.expanduser("~/.cache/longmemeval/longmemeval_s.json"),
              os.path.join(HERE, "..", "longmemeval", "data", "longmemeval_s.json")):
        if p and os.path.exists(p):
            return p
    raise SystemExit(
        "REFUSED: LongMemEval-S not found. Fetch it once with\n"
        "  python benchmarks/longmemeval/run.py --download\n"
        "This benchmark will not invent a corpus to keep running.")


def load() -> list:
    p = dataset_path()
    raw = io.open(p, "rb").read()
    got = hashlib.sha256(raw).hexdigest()
    if got != DATASET_SHA256:
        raise SystemExit(
            "REFUSED: the dataset on disk is not the one these figures were measured against.\n"
            "  expected %s\n  found    %s\n"
            "A number from a different file is a different number." % (DATASET_SHA256, got))
    return json.loads(raw.decode("utf-8"))


def session_text(sess: list) -> str:
    return "\n".join((t.get("role", "") + ": " + t.get("content", "")).strip() for t in sess)


# ── the four line variants, none of which calls a model ───────────────────────────────────────────
def first_sentence(t: str) -> str:
    """What `memory_index()` falls back to when no summariser is given: the record's opening."""
    t = t.strip().replace("\n", " ")
    for stop in (". ", "? ", "! "):
        i = t.find(stop)
        if 0 < i < 300:
            return t[:i + 1]
    return t[:300]


def first_user_turn(sess: list) -> str:
    for t in sess:
        if (t.get("role") or "").lower() == "user":
            return first_sentence(t.get("content") or "")
    return first_sentence(session_text(sess))


def idf_line(text: str, idf: dict, k: int = 22) -> str:
    seen, out = set(), []
    for w in [w for w in _tok(text) if w not in seen and not seen.add(w)]:
        out.append((idf.get(w, 0.0), w))
    out.sort(reverse=True)
    return " ".join(w for _, w in out[:k])


def _tok(t: str) -> list:
    return [w for w in "".join(c.lower() if c.isalnum() else " " for c in t).split() if len(w) > 2]


# ── embedding, batched, local, and it refuses rather than degrading ───────────────────────────────
_CACHE: dict = {}


def _embed_one(t: str) -> list:
    body = json.dumps({"model": EMBED_MODEL, "input": t}).encode()
    req = urllib.request.Request(EMBED_URL, data=body,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))["embeddings"][0]


def embed(texts: list) -> list:
    """One vector per text, cached by sha1. Long texts are CHUNKED, never truncated.

    THE DEFECT THIS EXISTS FOR, measured 2026-08-26 before any figure was produced. The old
    `/api/embeddings` route returns HTTP 500 above about 8,000 characters. The newer `/api/embed`
    accepts 24,000 and SILENTLY DROPS the tail: two texts identical for their first 16,000
    characters and differing in the last five embed to cosine 1.000000, and at 12,000 to 0.999947.
    The median LongMemEval session is 10,012 characters and p90 is 16,792, so a `ceiling` arm built
    on a single call would have been "the first fourteen thousand characters" wearing the name of
    the whole record, with nothing raised and nothing logged.

    So a record longer than CHUNK_CHARS becomes several vectors and its score is the MAX over them.
    That is the honest ceiling: the question is whether the evidence is anywhere in the record.
    """
    out: list = [None] * len(texts)
    todo = []
    for i, t in enumerate(texts):
        h = hashlib.sha1(t.encode("utf-8", "replace")).hexdigest()
        if h in _CACHE:
            out[i] = _CACHE[h]
        else:
            todo.append((i, h, t))
    def one(job):
        i, h, t = job
        t = t if t.strip() else "(empty)"
        chunks = [t[j:j + CHUNK_CHARS] for j in range(0, len(t), CHUNK_CHARS)] or [t]
        return i, h, [_embed_one(c) for c in chunks]

    if todo:
        done = 0
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            try:
                for i, h, vecs in ex.map(one, todo):
                    _CACHE[h] = vecs
                    out[i] = vecs
                    done += 1
                    if done % 500 == 0:
                        print("      embedded %d/%d" % (done, len(todo)), flush=True)
            except Exception as e:
                raise SystemExit(
                    "REFUSED: the embedder failed after %d of %d (%s). The rule here is to fix the "
                    "embedder, never to substitute a lighter proxy to keep a run alive."
                    % (done, len(todo), e))
    return out


def embedder_reads_the_whole_chunk() -> bool:
    """CONTROL: two texts of CHUNK_CHARS differing only in their last token must NOT embed identically.

    Without this the whole benchmark can run green on a scorer that never saw half of every record.
    """
    pad = "alpha bravo " * (CHUNK_CHARS // 12)
    a, b = _embed_one(pad + "ZZTOP"), _embed_one(pad + "QQBOT")
    return cos(a, b) < 0.9999


def score(qv: list, vecs: list) -> float:
    """Similarity of a query to a record: the best of its chunks."""
    return max(cos(qv, v) for v in vecs)


def cos(a: list, b: list) -> float:
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return s / (na * nb)


def main() -> int:
    t0 = time.time()
    data = load()
    rng = random.Random(SEED)
    qs = data[:N_QUESTIONS]
    print("  LongMemEval-S, %d of %d questions, sha256 pinned | %d embed workers"
          % (len(qs), len(data), WORKERS), flush=True)

    # idf over the sessions actually used, so the deterministic arm is as strong as it can be here
    df: Counter = Counter()
    docs = 0
    for q in qs:
        for s in q.get("haystack_sessions") or []:
            df.update(set(_tok(session_text(s))))
            docs += 1
    idf = {w: math.log(docs / (1 + c)) for w, c in df.items()}

    arms = ("ceiling", "default_line", "first_user", "idf_terms", "CONTROL_shuffled_line")
    hits = {a: {1: 0, 3: 0, 5: 0} for a in arms}
    n_scored = 0
    gold_in_haystack = 0
    chunked = 0
    chunks_total = 0
    empty_lines = 0
    same_candidates = True
    cand_counts = []

    for qi, q in enumerate(qs):
        sess_ids = q.get("haystack_session_ids") or []
        sessions = q.get("haystack_sessions") or []
        gold = set(q.get("answer_session_ids") or [])
        if not gold or not sessions or len(sess_ids) != len(sessions):
            continue
        # CONTROL: the answer must be inside the set we rank, or recall measures a fixture bug
        if not (gold & set(sess_ids)):
            continue
        gold_in_haystack += 1
        cand_counts.append(len(sessions))

        full = [session_text(s) for s in sessions]
        chunked += sum(1 for t in full if len(t) > CHUNK_CHARS)
        chunks_total += sum(max(1, -(-len(t) // CHUNK_CHARS)) for t in full)
        empty_lines += sum(1 for t in [first_user_turn(x) for x in sessions] if not t.strip())
        lines = {
            "ceiling": full,
            "default_line": [first_sentence(t) for t in full],
            "first_user": [first_user_turn(s) for s in sessions],
            "idf_terms": [idf_line(t, idf) for t in full],
        }
        shuf = list(lines["default_line"])
        rng.shuffle(shuf)
        lines["CONTROL_shuffled_line"] = shuf

        qv = embed([q["question"]])[0][0]
        for a in arms:
            if len(lines[a]) != len(sessions):
                same_candidates = False
            vecs = embed(lines[a])
            order = sorted(range(len(sessions)), key=lambda i: -score(qv, vecs[i]))
            for k in (1, 3, 5):
                if any(sess_ids[i] in gold for i in order[:k]):
                    hits[a][k] += 1
        n_scored += 1
        if (qi + 1) % 5 == 0:
            print("    %d/%d questions, %.0fs" % (qi + 1, len(qs), time.time() - t0), flush=True)

    rec = {a: {k: (hits[a][k] / n_scored if n_scored else 0.0) for k in (1, 3, 5)} for a in arms}
    chance3 = (3.0 / (sum(cand_counts) / len(cand_counts))) if cand_counts else 0.0

    v = {}
    # THE CONTROL THAT WOULD HAVE CAUGHT THE SILENT TRUNCATION, run against the live endpoint.
    v["CONTROL_the_embedder_reads_to_the_END_of_a_chunk"] = embedder_reads_the_whole_chunk()
    v["CONTROL_the_corpus_was_actually_ranked"] = n_scored >= 20
    v["CONTROL_the_gold_session_is_in_every_haystack_scored"] = gold_in_haystack == n_scored
    v["CONTROL_every_arm_ranked_the_same_candidates"] = same_candidates
    v["CONTROL_a_shuffled_line_collapses_to_chance"] = (
        rec["CONTROL_shuffled_line"][3] < max(0.15, chance3 * 3))
    v["CONTROL_the_ceiling_is_high_enough_to_show_anything"] = rec["ceiling"][3] >= 0.5
    v["THE_CEILING_BEATS_THE_SHIPPED_DEFAULT"] = rec["ceiling"][3] > rec["default_line"][3]
    v["A_DETERMINISTIC_LINE_DOES_NOT_CLOSE_THE_GAP"] = (
        max(rec["idf_terms"][3], rec["first_user"][3]) < rec["ceiling"][3] - 0.05)

    print("\n  === recall@k, %d questions, %d candidates median ===" % (
        n_scored, sorted(cand_counts)[len(cand_counts) // 2] if cand_counts else 0))
    for a in arms:
        print("    %-24s @1 %.3f   @3 %.3f   @5 %.3f" % (a, rec[a][1], rec[a][3], rec[a][5]))
    print("    %-24s @3 %.3f" % ("(chance)", chance3))
    print()
    for k, ok in v.items():
        print("  %s  %s" % ("YES" if ok else "no ", k))
    print("\n  sessions needing >1 chunk: %d | chunks embedded: %d | empty lines: %d"
          " | elapsed %.0fs" % (chunked, chunks_total, empty_lines, time.time() - t0))

    out = {
        "benchmark": os.path.basename(__file__),
        "corpus": {"name": "LongMemEval-S", "sha256": DATASET_SHA256,
                   "licence": "CC-BY-NC 4.0 -- research figures only, NOT a product benchmark",
                   "questions_used": n_scored, "questions_available": len(data),
                   "candidates_median": sorted(cand_counts)[len(cand_counts) // 2] if cand_counts else 0},
        "scorer": {"model": EMBED_MODEL, "local": True, "endpoint": EMBED_URL,
                   "chunk_chars": CHUNK_CHARS, "pooling": "max over chunks",
                   "sessions_needing_more_than_one_chunk": chunked,
                   "chunks_embedded": chunks_total, "empty_index_lines": empty_lines,
                   "why_chunked": ("/api/embed silently drops the tail past ~12-16k chars: two "
                                   "texts differing only after 16k embed to cosine 1.000000")},
        "recall": rec, "chance_at_3": chance3, "verdicts": v,
        "not_run": ("the written-line arm. 50 questions is 2,493 distinct sessions and ~6.1M input "
                    "tokens; it is only worth spending once the free arms show a gap for a written "
                    "line to close."),
        "seed": SEED, "elapsed_s": round(time.time() - t0, 1),
    }
    io.open(os.path.join(HERE, "result.json"), "w", encoding="utf-8").write(
        json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if all(v.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
