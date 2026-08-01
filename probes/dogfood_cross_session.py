#!/usr/bin/env python3
"""Does our own memory layer survive a session boundary? A standing, repeatable self-check.

WHY THIS EXISTS
---------------
We dogfood inspeximus as the agent memory for our own work, and twice the dogfood FAILED: recall against
a ~2,550-record working store missed 3 of the 5 facts an agent needed to RESUME work after a session
boundary, and on one run a record written that same day did not appear at k=5 at all. The conclusion we
had to write down was "a handoff must not rely on inspeximus recall alone" -- which, for a library sold as
agent memory, is the sharpest possible indictment. That conclusion was reached twice by hand, on a store
nobody else can look at, with no way to tell whether a later release had moved the number.

This probe turns that anecdote into an INSTRUMENT: a synthetic corpus of the same SHAPE (decision records
with rationales, mixed memory types, spread over months, contested by thousands of same-vocabulary
distractors), a session boundary that is a real OS process boundary with a cold read from disk, and a
number that can come out bad and SAY so.

WHAT A "SESSION BOUNDARY" IS IN THIS LIBRARY, EXACTLY -- read this before reading the number
--------------------------------------------------------------------------------------------
There is no session object, no resume path, no handoff type. The entire notion of a session in the data
model is one optional stamp and one filter: `remember(session_id=...)` writes `meta.sid` (core.py:1361)
and `recall(session_id=...)` hard-filters the candidate pool on the user/agent/session hierarchy
(core.py:5336). All three levels unset -- which is how our own MCP server writes and reads -- means the
filter is a wildcard that removes nothing. So crossing a session boundary is, mechanically, *reopening the
file in a new process*, which is what this probe does.

That matters for how the number is read. A repo audit concluded the original 3-of-5 handoff failure was a
SCOPING problem (a resuming agent has no supported way to say "the facts from the work I was doing"), not a
ranking one -- and every ranking lever for it is on our kill list. This probe therefore MEASURES the
current state and proposes nothing. If the hit rate is disappointing, the answer is not to tune the
ranking; it is that the boundary this probe crosses is the only one the library has.

WHAT IT MEASURES
----------------
hit@1 / hit@5 / hit@25 over N facts a resuming agent would need, where a hit means the exact target record
id appears in the top-k for the question a resuming agent would actually ask. Plus, explicitly:

  * the RECENCY case -- a decision written "today" must be findable at k=5. That is the case we observed
    failing, so it is reported on its own line and not averaged away.
  * the CORRECTION case -- a decision that was later superseded on the same topic: the CURRENT decision
    must be found and the retired one must not be served.
  * per-fact ranks, including for the misses, so a reader can tell a RANKING problem (the record was at
    rank 9) from an ABSENCE problem (the record was nowhere in the top 200).

CONTROLS (the probe reports a broken instrument as broken, never as a low score)
-------------------------------------------------------------------------------
  POSITIVE       a record whose text is a VERBATIM match for the query must come back at rank 1. It carries
                 the default value and sits in the same contested corpus, so it exercises the whole ranking
                 path, not just "does the string exist". If it fails, the harness is broken -- the probe
                 says HARNESS_BROKEN and refuses to publish a hit rate, because a harness that cannot
                 retrieve an exact copy of its own query cannot be trusted to have measured anything.
  DISCRIMINATION every target id is re-scored against a DIFFERENT target's question. If the questions were
                 so generic that any decision record answers any of them, this mismatched pairing would
                 score about as well as the real one, and the headline number would be measuring nothing.
                 It must come in below the real hit@5. This is also the control that catches the opposite
                 harness failure from the positive one -- a scorer crediting hits it did not earn, which
                 would otherwise be indistinguishable from good retrieval.

TWO ARMS, because the live default is not the repeatable one
------------------------------------------------------------
`recall(reinforce=True)` -- the library default, and what our MCP server actually runs -- bumps each
returned record's value and resets its decay clock, so the ranking depends on which queries ran BEFORE it.
So we measure both: `cold_reinforce_on` (the live path, headline, fixed query order) and
`cold_reinforce_off` (order-independent, repeatable). If the two disagree, the disagreement is the finding.

HONESTY RULES BAKED IN
----------------------
The fixture is fixed BEFORE the score is looked at. The queries are the questions a resuming agent asks,
not paraphrases of the record text -- shrinking the store or softening the queries until the number looks
good is the exact failure mode this probe exists to prevent. A NULL RESULT IS A VALID DELIVERABLE.

Zero required dependencies, zero LLM, single store file. The default operating point is the zero-dependency
one (no embedder -> lexical recall), because that is what `inspeximus-mcp` runs with nothing configured.

A FIXTURE NOTE, because the first version of this probe was too easy
--------------------------------------------------------------------
Built with random-subject distractors only (reproduce with `--siblings 0 --distractors 2500`) it measured
hit@5 = 0.846 on 2,515 records -- which was not good news, it was a defect: each target was the only record
in the store that mentioned its own subject, so every question was a keyword lookup. The real store is the
opposite; weeks of work on one subject leave a dozen notes, measurements and half-decisions that use the
same words as the decision you want. The fixture now gives every target twelve such SIBLINGS drawn from its
own vocabulary, and a fourth control reports how far they actually climb, so nobody has to take "this
corpus is hard" on trust.

AND THE HONEST CAVEAT: even so, this reproduction currently scores BETTER than the hand observation it was
built for (2 of 5 facts found there). Either the synthetic corpus is easier than the real store, or the
real failure has a cause this fixture does not yet model. The result document says so in its own `caveats`
field. Read the number as a REGRESSION LINE across releases, not as a verdict that the dogfood is fine.

USAGE
-----
    python probes/dogfood_cross_session.py                    # measure + gate, writes the result JSON
    python probes/dogfood_cross_session.py --distractors 8000 # a harder operating point
    python probes/dogfood_cross_session.py --siblings 0       # the easy corpus, for comparison
    python probes/dogfood_cross_session.py --no-gate          # measure only; exit 0 unless the harness broke
    python probes/dogfood_cross_session.py --in-process       # skip the subprocess (fresh handle, cold read)

EXIT CODES
----------
    0  the instrument is sound and (unless --no-gate) the measurement met the declared bar
    1  the instrument is sound and the measurement MISSED the bar -- this is a real result, not an error
    2  HARNESS_BROKEN: a control failed. No hit rate is published; fix the harness, not the library.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from inspeximus import Inspeximus, __version__ as INSPEXIMUS_VERSION  # noqa: E402

DAY = 86400.0
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "dogfood_cross_session.result.json")

# The k values reported. `deep` is the diagnostic depth used only to locate a MISS, never to score one.
KS = (1, 5, 25)
DEEP_K = 200

# The declared bar. Stated up front so the verdict is not chosen after seeing the number.
# hit@5 is the operative one: five records is roughly what a resuming agent can afford to read.
BAR = {
    "hit@5": 0.80,          # 4 of every 5 facts a handoff needs must be in the top 5
    "recency_hit@5": True,  # the case we watched fail: a decision written today, at k=5
    "positive_control_rank": 1,
}

# ── the fixture: what a resuming agent needs to know, and how it would ask ────────────────────────────
#
# Each target is a real-shaped decision record (a decision plus its rationale) and the QUESTION a resuming
# agent asks when it needs that decision back. The question deliberately does NOT reuse the decision's
# phrasing -- an agent resuming work asks about its SITUATION ("where are the model tiers running?"), not
# about the sentence it is trying to find. `age_days` spreads the corpus over four months of simulated work.
TARGETS = [
    {
        "id": "llm_backend",
        "need": "which model backend to use before issuing any generation call",
        "decision": "run every model tier on the local GPU through the Ollama endpoint",
        "because": "the hosted provider returned an account-wide weekly rate limit on all three tiers at "
                   "once, so hosted throughput was zero and slow beat absent",
        "topic": "llm::tier-backend",
        "age_days": 4,
        "query": "where are the model tiers running right now, and why that choice?",
    },
    {
        "id": "reasoning_budget",
        "need": "the token budget to pass to the reasoning tier",
        "decision": "floor the reasoning tier token budget at 16000 and never cap it",
        "because": "at a 600-token cap the model spent the whole budget on internal thinking and emitted "
                   "no content at all, which reads downstream as an empty answer rather than an error",
        "topic": "llm::reasoning-tokens",
        "age_days": 11,
        "query": "how many tokens do I have to allow the reasoning model before it answers?",
    },
    {
        "id": "watchdog",
        "need": "how the long-running worker is supposed to be kept alive",
        "decision": "keep exactly one worker process alive from the main service watchdog and run zero "
                    "external supervisors",
        "because": "the supervisor kills any worker process it does not own, so alongside the watchdog the "
                   "two fight and the result is constant restart churn",
        "topic": "ops::worker-liveness",
        "age_days": 23,
        "query": "what keeps the background worker alive, and is a supervisor supposed to be running?",
    },
    {
        "id": "safe_push",
        "need": "the safe way to commit changes to the notes repository",
        "decision": "commit the notes repository only through the safe push tool, never a bulk add",
        "because": "roughly 380 files carry characters the filesystem cannot represent, and a bulk add "
                   "stages every one of them as a deletion",
        "topic": "repo::notes-commit",
        "age_days": 37,
        "query": "how do I commit changes in the notes repository without losing files?",
    },
    {
        "id": "killed_centering",
        "need": "which experiments are dead and must not be re-run",
        "decision": "stop re-running the embedding post-processing comparison",
        "because": "the centering step was already shipped as the default, so the comparison is measured "
                   "against an unreachable null and can only report zero",
        "topic": "research::embedding-postprocessing",
        "age_days": 52,
        # Rewritten: the first version ("is the embedding post-processing experiment still worth running?")
        # reused 67% of the decision's own content words, which the fixture guard in
        # tests/test_dogfood_cross_session.py rejected. A question may NAME its subject -- that is half
        # this decision's vocabulary on its own -- but it may not hand over the verb it is hunting for.
        "query": "was the embedding post-processing question already settled or is it still open?",
    },
    {
        "id": "reinforce_order",
        "need": "why two identical measurement runs disagree",
        "decision": "disable reinforcement when measuring retrieval",
        "because": "reinforcement rewrites the last-access clock of everything it returns, so the answer "
                   "to a later question depends on which questions were asked first",
        "topic": "measurement::reinforcement",
        "age_days": 19,
        "query": "two runs of the same retrieval measurement gave different answers, what causes that?",
    },
    {
        "id": "publish_gate",
        "need": "what has to happen before anything is published",
        "decision": "run validate, then storm, then audit, then verify before anything goes outward",
        "because": "every full run of that sequence has caught a real defect, and twice an unverified draft "
                   "had to be stopped by hand at the last moment",
        "topic": "process::publish-gate",
        "age_days": 66,
        "query": "what is the checklist before we send anything public?",
    },
    {
        "id": "track_record_blocked",
        "need": "whether the forecast track record can be published yet",
        "decision": "block the plan to publish the forecast track record",
        "because": "the main forecaster scores reliably worse than its own marginals, so publishing it "
                   "would advertise an anti-skill",
        "topic": "publish::forecast-record",
        "age_days": 8,
        "query": "can the forecasting track record go public yet or is it blocked?",
    },
    {
        "id": "who_posts",
        "need": "who is allowed to post which reply where",
        "decision": "the owner posts to the discussion forum and the agent posts on the issue tracker",
        "because": "the forum account is tied to the owner's own identity and a reply from anyone else "
                   "there misrepresents who is speaking",
        "topic": "outreach::posting-channel",
        "age_days": 44,
        "query": "who sends the reply on the discussion forum, me or the owner?",
    },
    {
        "id": "stale_reader",
        "need": "what to do after a script writes to the store from outside the running server",
        "decision": "restart the memory server connection after any write made outside its process",
        "because": "a long-lived server keeps a cached copy of the store, so after an external write it is "
                   "a stale reader and its own report will disagree with the file",
        "topic": "ops::external-write",
        "age_days": 15,
        "query": "I backfilled the store with a script, what do I need to do to the running server?",
    },
    {
        "id": "receipt_rotation",
        "need": "whether a cited evidence id can still be resolved",
        "decision": "check that a cited evidence id still resolves before gating on it",
        "because": "the evidence ledger is capped at a thousand entries, so a draft that sat in the queue "
                   "can outlive the very receipt it cites",
        "topic": "evidence::ledger-rotation",
        "age_days": 29,
        "query": "how long do evidence receipts stay resolvable before they rotate out?",
    },
    # THE RECENCY CASE. Written "today" (age 0) -- the case we watched fail. Reported on its own line.
    {
        "id": "today_release_freeze",
        "need": "today's decision, the one a session resuming tomorrow morning most needs",
        "decision": "freeze the release branch until the retrieval regression is closed",
        "because": "the regression changes what the store returns for a question it answered correctly "
                   "yesterday, and shipping that silently would be worse than shipping late",
        "topic": "release::freeze",
        "age_days": 0,
        "query": "what did we decide today about shipping the next release?",
        "recency": True,
    },
    # THE CORRECTION CASE. A second decision on `release::channel` retires the first (see STALE below);
    # the CURRENT one must be found and the retired one must not be served.
    {
        "id": "release_channel_current",
        "need": "how a release actually gets published today, not how it used to",
        "decision": "publish releases from the trusted publisher workflow",
        "because": "the old tag-triggered pipeline cannot mint the attestation the package index now "
                   "requires, so it fails at the last step every time",
        "topic": "release::channel",
        "age_days": 2,
        "query": "how does a release get published these days?",
        "supersedes_note": "retires release_channel_stale",
    },
]

# The retired half of the correction case. Written earlier on the SAME topic key, so the store's keyed
# supersession retires it when the current decision lands. It must never be served for the query above.
STALE = {
    "id": "release_channel_stale",
    "decision": "publish releases from the tag-triggered pipeline",
    "because": "it is the workflow we already have and it needs no new credentials",
    "topic": "release::channel",
    "age_days": 40,
}

# POSITIVE CONTROL. The query is a byte-for-byte copy of the record's text. It carries the library's
# DEFAULT value (no boost) and uses ordinary corpus vocabulary, so it is ranked by the same machinery
# against the same distractors -- it proves the instrument retrieves, it does not prove retrieval is easy.
CONTROL_TEXT = ("CONTROL RECORD: the nightly consolidation pass merges near-duplicate summary records "
                "and writes one consolidated note per cluster into the retrieval index every morning")

# AN OBSERVATION, NOT A CONTROL. Nothing about this subject was ever written. The store sets no relevance
# floor, so recall FILLS a deep k with weak matches rather than abstaining -- documented behaviour, and the
# reason `min_relevance` exists. This query measures how far that fill goes: how many target decisions come
# back for a question about a subject the store has never heard of, and whether one of them lands at rank 1.
#
# It was a HARNESS control in the first version and that was a mistake: at 174 records the store genuinely
# has nothing better to offer, so a target reached rank 1 and the probe declared its own instrument broken
# at small corpus sizes. The property is real but it belongs to the store, not to the harness -- so it is
# reported under `store_observations` and the unearned-credit duty sits with the discrimination control,
# which is size-robust. A control that fires on the operating point rather than on the defect is noise.
ABSENCE_QUERY = ("which vendor did we pick for the on-premise fax gateway and what did the annual "
                 "licence end up costing")

# ── the distractor corpus ─────────────────────────────────────────────────────────────────────────────
#
# Distractors are NOT noise. They are drawn from the same vocabulary as the targets (subsystems, verbs,
# rationales an engineering team actually writes down), because a store where the answer is the only record
# mentioning "release" measures nothing. They are what makes retrieval contested.
_SUBSYSTEMS = [
    "the ingest worker", "the retrieval index", "the audit bundle", "the release pipeline",
    "the token accounting", "the consolidation pass", "the receipt chain", "the tenant filter",
    "the embedding cache", "the decay clock", "the supersession ledger", "the erasure sweep",
    "the outreach queue", "the forecast ledger", "the notes repository", "the model router",
    "the worker watchdog", "the scheduler loop", "the metrics exporter", "the provenance graph",
    "the conflict detector", "the redaction filter", "the session adapter", "the vector matrix",
    "the reply drafter", "the claim verifier", "the paper reader", "the coverage report",
    "the schema migration", "the backup rotation", "the rate limiter", "the health probe",
    "the diff renderer", "the changelog builder", "the fixture loader", "the crash reporter",
]
_ACTIONS = [
    "cap", "retire", "batch", "pin", "gate", "split", "merge", "defer", "inline", "hoist",
    "quantise", "memoise", "shard", "throttle", "back off", "pre-warm", "flatten", "unroll",
    "vendor", "isolate", "checkpoint", "stream", "compact", "reorder", "de-duplicate",
]
_OBJECTS = [
    "the write path", "the read path", "the retry budget", "the flush interval", "the candidate pool",
    "the token window", "the worker count", "the queue depth", "the cache key", "the sort key",
    "the page size", "the timeout", "the backoff curve", "the fixture set", "the log level",
    "the index rebuild", "the warm-up pass", "the eviction policy", "the batch size", "the shard map",
]
_RATIONALES = [
    "the previous setting made the tail latency unpredictable under load",
    "it removes a dependency we were carrying for one function",
    "the old behaviour was correct but nobody could tell from the output",
    "two components disagreed about who owned the value",
    "it turns a silent wrong answer into a loud failure",
    "the measurement noise was the same size as the effect we were chasing",
    "a reader of the log could not tell success from a skipped step",
    "it costs one extra pass and saves a whole rebuild",
    "the guard never saw its target, so it always reported safe",
    "the default was chosen before we had any data and never revisited",
    "it makes the failure reproducible instead of intermittent",
    "the alternative needs a service we do not want to depend on",
]
_FACTS = [
    "{sub} handles about {n} records per pass at the current operating point",
    "{sub} was measured at {n} milliseconds median over {m} runs",
    "{sub} holds {n} entries before the rotation drops the oldest",
    "{sub} allocates roughly {n} megabytes while {obj} is warm",
    "{sub} retries {n} times before it gives up on {obj}",
    "{sub} covers {n} of {m} branches in the current suite",
]
_NOTES = [
    "reviewed {sub} and found nothing that needs changing this cycle",
    "{sub} and {sub2} were touched in the same change and should be reviewed together",
    "opened a question about whether {obj} belongs to {sub} or to {sub2}",
    "{sub} logged a warning about {obj} twice this week, not yet reproduced",
    "reading notes on {sub}: the current shape predates {obj} and was never revisited",
]

# TOPICAL SIBLINGS -- the part of the fixture that makes it resemble the real store.
#
# The first version of this probe used only the templated distractors above and measured hit@5 = 0.846 on
# 2,515 records. That looked like good news and was actually a fixture defect: a random-topic corpus leaves
# every target the ONLY record that mentions its own subject, so a query about the reasoning tier's token
# budget is a keyword lookup, not a retrieval problem. The real store is nothing like that -- weeks of work
# on a subject leave a dozen notes, measurements, half-decisions and superseded thinking that all use the
# same words as the decision you are looking for. Those are what a real recall has to rank the answer above.
#
# So each target gets `siblings` records built from ITS OWN vocabulary: same words, not the answer. One of
# them is itself decision-shaped, because in a real store the thing crowding out a decision is usually
# another decision. This makes retrieval contested at the topic level, which is the condition under which
# the dogfood was observed to fail. It is a change that makes the fixture HARDER, made because the easy
# version could not have reproduced the failure it exists to watch for.
_SIBLING_TEMPLATES = [
    "note: {a} and {b} came up again while reviewing {c}, nothing decided yet",
    "measured {a} {b} at {n} milliseconds across {m} runs, unchanged from last week",
    "open question: does {b} still matter now that {a} has changed?",
    "DECISION: leave {a} and {b} as they are for this cycle - because: {why}",
    "reading notes on {a}: the current {b} shape predates {c} and was never revisited",
    "{a} was touched in the same change as {b}, review them together",
    "logged a warning about {a} while {b} was warm, not yet reproduced",
    "the {b} report for {a} covers {n} of {m} branches",
    "asked whether {c} belongs with {a} or with {b}; parked",
    "{a} retried {n} times before {b} settled, which is more than expected",
    "draft: a short summary of how {a}, {b} and {c} interact today",
    "backlog: revisit {a} once {b} is stable, currently blocked on {c}",
]
_STOP = {
    "the", "and", "for", "with", "that", "this", "from", "into", "onto", "over", "than", "then", "them",
    "they", "have", "has", "had", "was", "were", "are", "not", "but", "any", "all", "one", "two", "its",
    "it", "a", "an", "of", "to", "in", "on", "at", "by", "as", "is", "be", "so", "if", "or", "no", "we",
    "our", "us", "you", "your", "every", "each", "only", "very", "more", "most", "when", "what", "which",
    "who", "how", "why", "does", "did", "do", "can", "will", "would", "could", "should", "must", "run",
    "runs", "way", "out", "up", "off", "own", "now", "new", "old", "same", "other", "some", "there",
}


def _content_tokens(text: str) -> list[str]:
    """The distinctive words of a target, used to build siblings that genuinely contest it."""
    seen, out = set(), []
    for w in "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text.lower()).split():
        if len(w) >= 4 and w not in _STOP and not w.isdigit() and w not in seen:
            seen.add(w)
            out.append(w)
    return out


def build_siblings(per_target: int, seed: int) -> list[dict]:
    """For every target, `per_target` records drawn from that target's own vocabulary. Deterministic."""
    rng = random.Random(seed ^ 0x51B)
    out, seen = [], set()
    for t in TARGETS:
        vocab = _content_tokens(t["decision"] + " " + t["because"])
        if len(vocab) < 3:
            continue
        for i in range(per_target):
            a, b, c = (rng.choice(vocab) for _ in range(3))
            text = _SIBLING_TEMPLATES[i % len(_SIBLING_TEMPLATES)].format(
                a=a, b=b, c=c, n=rng.randint(3, 900), m=rng.randint(5, 400),
                why=rng.choice(_RATIONALES))
            if text in seen:
                continue
            seen.add(text)
            out.append({"text": text,
                        "mtype": "procedural" if text.startswith("DECISION:") else
                                 rng.choice(("episodic", "semantic")),
                        "value": round(rng.uniform(0.6, 3.0), 2),
                        "age_days": round(rng.uniform(0.0, 120.0), 3),
                        "sibling_of": t["id"]})
    return out


def build_distractors(n: int, seed: int) -> list[dict]:
    """Deterministic same-vocabulary corpus. Same seed -> byte-identical texts, so a re-run measures the
    library, not a new random corpus."""
    rng = random.Random(seed)
    out, seen = [], set()
    guard = 0
    while len(out) < n and guard < n * 40:
        guard += 1
        roll = rng.random()
        sub, sub2 = rng.choice(_SUBSYSTEMS), rng.choice(_SUBSYSTEMS)
        obj = rng.choice(_OBJECTS)
        if roll < 0.45:                                     # decision-shaped, like the targets
            text = ("DECISION: {a} {obj} in {sub} - because: {why}"
                    .format(a=rng.choice(_ACTIONS), obj=obj, sub=sub, why=rng.choice(_RATIONALES)))
            mtype = "procedural"
        elif roll < 0.75:                                   # a measured fact
            text = rng.choice(_FACTS).format(sub=sub, obj=obj, n=rng.randint(3, 9000),
                                             m=rng.randint(5, 500))
            mtype = "semantic"
        else:                                               # a working note
            text = rng.choice(_NOTES).format(sub=sub, sub2=sub2, obj=obj)
            mtype = "episodic"
        if text in seen:
            continue
        seen.add(text)
        out.append({"text": text, "mtype": mtype,
                    "value": round(rng.uniform(0.6, 3.0), 2),
                    "age_days": round(rng.uniform(0.0, 120.0), 3)})
    return out


def _backdate(store: Inspeximus, rid: str, age_days: float, now: float) -> None:
    """Move a record's clocks back so the corpus spans real time. `ts`, `valid_from` and `last_access` all
    matter: `last_access` drives the per-type decay that weights the ranking."""
    t = now - age_days * DAY
    for r in store._items:
        if r["id"] == rid:
            r["ts"] = t
            r["valid_from"] = t
            r["last_access"] = t
            r["iso"] = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            return
    raise KeyError("record %r vanished before it could be back-dated" % rid)


def build_store(path: str, n_distractors: int, seed: int, siblings: int = 12) -> dict:
    """Write the corpus and return the manifest a reader needs: which record id answers which question."""
    now = time.time()
    m = Inspeximus(path=path)
    manifest = {"targets": [], "control": {}, "stale": {}, "written_utc": now}

    # The retired half of the correction case goes in FIRST, so the current decision genuinely supersedes it.
    stale_id = m.remember_decision(STALE["decision"], because=STALE["because"], topic=STALE["topic"])
    _backdate(m, stale_id, STALE["age_days"], now)
    manifest["stale"] = {"id": stale_id, "fact": STALE["id"], "topic": STALE["topic"]}

    # Distractors and topical siblings next, so the targets are not simply the newest thing in the store.
    sibs = build_siblings(siblings, seed) if siblings else []
    sib_ids: dict[str, list[str]] = {}
    for d in build_distractors(n_distractors, seed) + sibs:
        did = m.remember(d["text"], mtype=d["mtype"], value=d["value"])
        _backdate(m, did, d["age_days"], now)
        if d.get("sibling_of"):
            sib_ids.setdefault(d["sibling_of"], []).append(did)

    # The positive control carries the library DEFAULT value -- no boost, no special type.
    cid = m.remember(CONTROL_TEXT)
    _backdate(m, cid, 6.0, now)
    manifest["control"] = {"id": cid, "query": CONTROL_TEXT}

    for t in TARGETS:
        rid = m.remember_decision(t["decision"], because=t["because"], topic=t["topic"])
        _backdate(m, rid, t["age_days"], now)
        manifest["targets"].append({
            "id": rid, "fact": t["id"], "need": t["need"], "query": t["query"],
            "age_days": t["age_days"], "recency": bool(t.get("recency")),
            "topic": t["topic"], "sibling_ids": sib_ids.get(t["id"], []),
            "query_overlap_with_decision": round(
                len(set(_content_tokens(t["decision"])) & set(_content_tokens(t["query"])))
                / (len(set(_content_tokens(t["decision"]))) or 1), 3),
        })

    m.flush()
    manifest["store_records"] = len(m._items)
    manifest["distractors"] = n_distractors
    manifest["siblings"] = len(sibs)
    manifest["siblings_per_target"] = siblings
    manifest["active_records"] = sum(1 for r in m._items if r.get("status") == "active")
    return manifest


# ── the read side: a COLD reader, on the far side of a session boundary ───────────────────────────────

def read_phase(store_path: str, manifest: dict, reinforce: bool) -> dict:
    """Open the store cold and answer the questions a resuming agent would ask.

    This function is the whole point of the probe: it is called from a process that never wrote anything,
    against a store it loads from disk. When run as the probe's default it is a separate OS process; the
    `--in-process` variant is a fresh handle over the same file, which the tests use.
    """
    m = Inspeximus(path=store_path)
    loaded = len(m._items)

    # POSITIVE CONTROL FIRST. If the instrument cannot retrieve a verbatim copy of its own query at rank 1,
    # nothing measured afterwards means anything, so it runs before the measurement and is reported apart.
    ctl = m.recall(manifest["control"]["query"], k=KS[-1], reinforce=False)
    ctl_ids = [r["id"] for r in ctl]
    ctl_rank = ctl_ids.index(manifest["control"]["id"]) + 1 if manifest["control"]["id"] in ctl_ids else None

    # NO-FLOOR FILL observation (see ABSENCE_QUERY). Reported, not gated.
    target_ids = {t["id"] for t in manifest["targets"]}
    absent = [r["id"] for r in m.recall(ABSENCE_QUERY, k=KS[-1], reinforce=False)]
    absence_top1_is_target = bool(absent) and absent[0] in target_ids
    absence_fill = [i for i in absent if i in target_ids]

    # The measurement. One recall per question at the deepest SCORED k, in a fixed order; hit@1 and hit@5
    # are prefixes of the same result, so the reinforce-on arm pays exactly the reinforcement a real
    # k=25 agent call would pay -- not the reinforcement of a deeper diagnostic sweep.
    per_fact, mode_used = [], None
    for t in manifest["targets"]:
        res = m.recall(t["query"], k=KS[-1], reinforce=reinforce)
        mode_used = mode_used or getattr(m, "_last_mode", None)
        ids = [r["id"] for r in res]
        rank = ids.index(t["id"]) + 1 if t["id"] in ids else None
        # How hard was this question actually? A fixture whose siblings never reach the top of the list is
        # not contesting anything, and a hit rate measured on it says nothing about a real store. Counted
        # from the SAME result as the score, so it costs no extra recall and cannot perturb the arm.
        sib = set(t.get("sibling_ids") or ())
        per_fact.append({
            "fact": t["fact"], "need": t["need"], "query": t["query"],
            "age_days": t["age_days"], "recency": t["recency"],
            "rank": rank,
            **{"hit@%d" % k: bool(rank is not None and rank <= k) for k in KS},
            "own_siblings_in_top5": sum(1 for i in ids[:5] if i in sib),
            "own_siblings_in_top25": sum(1 for i in ids[:25] if i in sib),
            # How much of the answer the question already gave away. Published per fact so nobody has to
            # take "the queries are not paraphrases of the records" on trust; the suite enforces a ceiling.
            "query_overlap_with_decision": t.get("query_overlap_with_decision"),
            "top1_text": (res[0]["text"][:110] if res else None),
        })

    # DIAGNOSTIC sweep, after every scored query, with reinforcement off so it perturbs nothing. It answers
    # the question a bare hit rate cannot: was the miss a RANKING problem or an ABSENCE problem?
    for row, t in zip(per_fact, manifest["targets"]):
        if row["rank"] is None:
            deep = [r["id"] for r in m.recall(t["query"], k=DEEP_K, reinforce=False)]
            row["deep_rank"] = deep.index(t["id"]) + 1 if t["id"] in deep else None
            row["diagnosis"] = ("ranked below k=%d but present at %d" % (KS[-1], row["deep_rank"])
                                if row["deep_rank"] else
                                "not retrieved at all within the top %d" % DEEP_K)

    # DISCRIMINATION CONTROL. Re-score every target id against the NEXT target's question. Reinforcement is
    # off here so it cannot disturb the measured arm above. A mismatched hit rate close to the real one
    # would mean the questions do not pick out their own fact, and the headline number measures nothing.
    tgts = manifest["targets"]
    mismatched = 0
    for i, t in enumerate(tgts):
        other = tgts[(i + 1) % len(tgts)]
        ids = [r["id"] for r in m.recall(other["query"], k=5, reinforce=False)]
        mismatched += int(t["id"] in ids)

    # The correction case: the retired decision must not be served for the current question.
    cur = next((t for t in tgts if t["fact"] == "release_channel_current"), None)
    stale_served = None
    if cur is not None:
        got = [r["id"] for r in m.recall(cur["query"], k=KS[-1], reinforce=False)]
        stale_served = manifest["stale"]["id"] in got

    n = len(per_fact) or 1
    return {
        "loaded_records": loaded,
        "recall_mode": mode_used,
        "reinforce": reinforce,
        "per_fact": per_fact,
        **{"hit@%d" % k: round(sum(1 for r in per_fact if r["hit@%d" % k]) / n, 4) for k in KS},
        "missed_at_5": [r["fact"] for r in per_fact if not r["hit@5"]],
        "missed_at_25": [r["fact"] for r in per_fact if not r["hit@25"]],
        "control_rank": ctl_rank,
        "control_top1_text": (ctl[0]["text"][:110] if ctl else None),
        "mean_own_siblings_in_top5": round(sum(r["own_siblings_in_top5"] for r in per_fact) / n, 3),
        "mean_own_siblings_in_top25": round(sum(r["own_siblings_in_top25"] for r in per_fact) / n, 3),
        "targets_with_no_sibling_in_top25": sum(1 for r in per_fact if not r["own_siblings_in_top25"]),
        "absence_top1_is_target": absence_top1_is_target,
        "absence_weak_fill_in_25": len(absence_fill),
        "mismatched_hit@5": round(mismatched / n, 4),
        "stale_decision_served": stale_served,
    }


def _run_arm_subprocess(store_path: str, manifest_path: str, reinforce: bool) -> dict:
    """Cross a REAL session boundary: a new interpreter, no inherited state, cold read from disk."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run(
        [sys.executable, os.path.abspath(__file__), "--_read-phase", store_path,
         "--_manifest", manifest_path, "--_reinforce", "1" if reinforce else "0"],
        capture_output=True, text=True, timeout=1800, env=env,
        cwd=os.path.dirname(HERE),
    )
    if r.returncode != 0:
        raise RuntimeError("the cold reader process failed (exit %d):\n%s\n%s"
                           % (r.returncode, r.stdout[-2000:], r.stderr[-2000:]))
    return json.loads(r.stdout[r.stdout.index("{RESULT}") + len("{RESULT}"):])


# ── orchestration + verdict ───────────────────────────────────────────────────────────────────────────

def run_harness(work_dir: str, n_distractors: int, seed: int, in_process: bool = False,
                siblings: int = 12) -> dict:
    """Build the store, cross the boundary twice (once per arm), and assemble the result document."""
    os.makedirs(work_dir, exist_ok=True)
    store_path = os.path.join(work_dir, "dogfood_store.json")
    manifest = build_store(store_path, n_distractors, seed, siblings)
    manifest_path = os.path.join(work_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)

    arms = {}
    for name, reinforce in (("cold_reinforce_on", True), ("cold_reinforce_off", False)):
        # Each arm gets its OWN copy of the file. The reinforce-on arm mutates decay clocks as it reads,
        # and one arm's reads must not be the other arm's starting conditions.
        arm_store = os.path.join(work_dir, "%s.json" % name)
        shutil.copyfile(store_path, arm_store)
        arms[name] = (read_phase(arm_store, manifest, reinforce) if in_process
                      else _run_arm_subprocess(arm_store, manifest_path, reinforce))

    return assemble(manifest, arms, seed, in_process)


def assemble(manifest: dict, arms: dict, seed: int, in_process: bool) -> dict:
    """Turn the arms into the published document, controls first. The verdict is decided against BAR,
    which was declared at the top of this file before any number existed."""
    headline = "cold_reinforce_on"          # the LIVE path: what our MCP server runs by default
    h = arms[headline]

    ctl_ok_all = all(a["control_rank"] == BAR["positive_control_rank"] for a in arms.values())
    discrim_ok = all(a["mismatched_hit@5"] < a["hit@5"] for a in arms.values())
    # A fixture whose same-subject siblings never surface is not contesting anything -- the number would
    # then describe a keyword lookup, which is exactly how a probe comes to report SAFE forever. Skipped
    # when the caller asked for no siblings (`--siblings 0`), because then there is nothing to contest with.
    has_sibs = any(t.get("sibling_ids") for t in manifest["targets"])
    contest_ok = (not has_sibs) or all(a["mean_own_siblings_in_top25"] > 0 for a in arms.values())

    recency_rows = {name: next((r for r in a["per_fact"] if r["recency"]), None)
                    for name, a in arms.items()}
    rec_h = recency_rows[headline]

    controls = {
        "positive": {
            "passed": bool(ctl_ok_all),
            "rank_headline": h["control_rank"],
            "rank_by_arm": {n: a["control_rank"] for n, a in arms.items()},
            "query_is_verbatim_record_text": True,
            "record_value": "library default (no boost)",
            "note": ("a verbatim copy of the query was retrieved at rank 1, so the instrument retrieves "
                     "and the hit rates below are about the library"
                     if ctl_ok_all else
                     "the instrument could NOT retrieve a verbatim copy of its own query at rank 1. The "
                     "harness is broken, not the library. No hit rate is published."),
        },
        "discrimination": {
            "passed": bool(discrim_ok),
            "mismatched_hit@5_by_arm": {n: a["mismatched_hit@5"] for n, a in arms.items()},
            "real_hit@5_by_arm": {n: a["hit@5"] for n, a in arms.items()},
            "note": ("each target scored against another target's question comes in below the real rate, "
                     "so the questions pick out their own fact rather than any decision record"
                     if discrim_ok else
                     "a target scored against SOMEONE ELSE'S question does as well as against its own: "
                     "the questions do not discriminate, so the headline number measures nothing"),
        },
        "contest": {
            "passed": bool(contest_ok),
            "mean_own_siblings_in_top5": h["mean_own_siblings_in_top5"],
            "mean_own_siblings_in_top25": h["mean_own_siblings_in_top25"],
            "targets_with_no_sibling_in_top25": h["targets_with_no_sibling_in_top25"],
            "note": ("same-subject records reach the top of the list, so the hit rate was measured under "
                     "contest rather than on a corpus where the answer is the only record about its subject"
                     if contest_ok else
                     "no same-subject sibling reached any top 25: the corpus is not contesting the targets, "
                     "so this hit rate describes a keyword lookup and must not be read as agent recall"),
        },
    }

    doc = {
        "probe": "dogfood_cross_session",
        "question": "does inspeximus return the facts an agent needs to resume work after a session boundary?",
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "inspeximus_version": INSPEXIMUS_VERSION,
        "operating_point": {
            "store_records": manifest["store_records"],
            "active_records": manifest["active_records"],
            "distractors": manifest["distractors"],
            "topical_siblings": manifest.get("siblings", 0),
            "siblings_per_target": manifest.get("siblings_per_target", 0),
            "targets": len(manifest["targets"]),
            "seed": seed,
            "embedder": None,
            "recall_mode": h.get("recall_mode"),
            "recall_mode_note": "no embedder configured -> lexical, the zero-dependency default the MCP "
                                "server runs with nothing set",
            "session_boundary": ("fresh Inspeximus handle, cold read from disk (in-process variant)"
                                 if in_process else
                                 "separate OS process, cold read from disk"),
            "session_scoping": "unused: user_id/agent_id/session_id are left unset on every write and "
                               "every read, so recall's hierarchy filter (core.py:5336) is a wildcard "
                               "and removes nothing. That is also how our MCP server writes and reads, "
                               "and it is the library's ONLY notion of a session in the data model.",
            "records_loaded_by_cold_reader": h["loaded_records"],
        },
        "controls": controls,
        "store_observations": {
            "no_relevance_floor_fill": {
                "query": ABSENCE_QUERY,
                "subject_absent_from_store": True,
                "target_decisions_returned_in_top25_by_arm":
                    {n: a["absence_weak_fill_in_25"] for n, a in arms.items()},
                "a_target_decision_was_the_top_answer_by_arm":
                    {n: a["absence_top1_is_target"] for n, a in arms.items()},
                "note": "with min_relevance unset, recall FILLS k rather than abstaining, so a question "
                        "about a subject the store has never seen still returns decisions. Documented "
                        "behaviour (pass min_relevance to abstain instead) and reported here because it "
                        "is what a resuming agent would actually be handed. Not a harness control: at "
                        "small store sizes there is nothing better to return, so gating on it would fire "
                        "on the operating point rather than on a defect.",
            },
        },
        "bar": BAR,
    }

    if not (ctl_ok_all and discrim_ok and contest_ok):
        # A broken instrument does not get to publish a score. This is the whole reason the controls run
        # first: a low number from a broken harness is worse than no number, because it looks like evidence.
        doc["verdict"] = "HARNESS_BROKEN"
        doc["arms"] = {n: {kk: a[kk] for kk in ("control_rank", "absence_top1_is_target",
                                                "mismatched_hit@5", "mean_own_siblings_in_top25",
                                                "loaded_records")}
                       for n, a in arms.items()}
        doc["hit_rates_withheld"] = ("a control failed, so the measurement is not reported. Fix the "
                                     "harness and re-run; do not read a score out of a broken instrument.")
        doc["exit_code"] = 2
        return doc

    doc["arms"] = arms
    doc["headline_arm"] = headline
    doc["headline"] = {
        "hit@1": h["hit@1"], "hit@5": h["hit@5"], "hit@25": h["hit@25"],
        "missed_at_5": h["missed_at_5"], "missed_at_25": h["missed_at_25"],
    }
    doc["recency_case"] = {
        "fact": rec_h["fact"] if rec_h else None,
        "written": "today (age 0 days)",
        "query": rec_h["query"] if rec_h else None,
        "rank_by_arm": {n: (row["rank"] if row else None) for n, row in recency_rows.items()},
        "hit@5": bool(rec_h and rec_h["hit@5"]),
        "note": "the case observed failing in the real dogfood: a record written the same day, at k=5",
    }
    doc["correction_case"] = {
        "current_decision_found_at": next((r["rank"] for r in h["per_fact"]
                                           if r["fact"] == "release_channel_current"), None),
        "retired_decision_served": h["stale_decision_served"],
        "note": "keyed supersession must serve the current decision and withhold the retired one",
    }
    # What the default costs, stated as facts rather than as a rate. reinforce=True bumps the value and
    # resets the decay clock of everything it returns, so a query changes the ranking seen by the queries
    # after it. A fact whose rank differs between the arms is that effect, measured.
    off = {r["fact"]: r["rank"] for r in arms["cold_reinforce_off"]["per_fact"]}
    moved = [{"fact": r["fact"], "rank_reinforce_on": r["rank"], "rank_reinforce_off": off.get(r["fact"])}
             for r in arms["cold_reinforce_on"]["per_fact"] if r["rank"] != off.get(r["fact"])]
    doc["arm_agreement"] = {
        "hit@1_reinforce_on": arms["cold_reinforce_on"]["hit@1"],
        "hit@1_reinforce_off": arms["cold_reinforce_off"]["hit@1"],
        "hit@5_reinforce_on": arms["cold_reinforce_on"]["hit@5"],
        "hit@5_reinforce_off": arms["cold_reinforce_off"]["hit@5"],
        "hit@25_reinforce_on": arms["cold_reinforce_on"]["hit@25"],
        "hit@25_reinforce_off": arms["cold_reinforce_off"]["hit@25"],
        "facts_whose_rank_moved": moved,
        "note": "reinforce=True is the library default and rewrites the value and decay clock of every "
                "record it returns, so each query changes the ranking the NEXT query sees. Any fact listed "
                "in facts_whose_rank_moved is that effect measured on this corpus; both arms read the same "
                "bytes from disk, and the only difference is the default. The off arm is the "
                "order-independent number and is the one to compare across releases.",
    }

    doc["caveats"] = [
        "This is a SYNTHETIC reproduction of the shape of the failure, not the store it was seen on. It "
        "currently measures BETTER than the hand observation it was built for (2 of 5 facts found there, "
        "%d of %d here at k=5). Either this corpus is easier than the real one, or the real failure has a "
        "cause this fixture does not yet model. Treat the number as a REGRESSION LINE for releases, not as "
        "a claim that the dogfood is fine."
        % (sum(1 for r in h["per_fact"] if r["hit@5"]), len(h["per_fact"])),
        "No embedder is configured, so recall is lexical -- the zero-dependency default. A store with an "
        "embedder is a different operating point and this probe does not measure it.",
        "The boundary crossed here is a PROCESS boundary. The library's only session concept is the "
        "optional meta.sid stamp and recall's hierarchy filter, both left unset (wildcard) throughout, so "
        "nothing is scoped out. A repo audit put the original handoff failure down to that missing scoping "
        "rather than to ranking; this probe measures the current state and proposes no ranking change.",
        "The store sets no relevance floor, so a deep k is always FILLED; see the absence control.",
        "The headline arm uses the library default reinforce=True, whose answers depend on query order. "
        "The query order here is the fixture order, fixed; the reinforce=off arm is the order-free number.",
    ]

    failures = []
    if h["hit@5"] < BAR["hit@5"]:
        failures.append("hit@5 %.4f < bar %.2f" % (h["hit@5"], BAR["hit@5"]))
    if BAR["recency_hit@5"] and not doc["recency_case"]["hit@5"]:
        failures.append("the record written today was not in the top 5")
    if h["stale_decision_served"]:
        failures.append("a retired decision was served for the current question")
    doc["bar_failures"] = failures
    doc["verdict"] = "PASS" if not failures else "FAIL"
    doc["exit_code"] = 0 if not failures else 1
    return doc


def render(doc: dict) -> str:
    op = doc["operating_point"]
    L = ["", "=" * 92,
         "DOGFOOD CROSS-SESSION SELF-CHECK   inspeximus %s" % doc["inspeximus_version"],
         "=" * 92,
         "operating point : %d records (%d distractors + %d topical siblings, %d target facts), %s"
         % (op["store_records"], op["distractors"], op["topical_siblings"], op["targets"],
            op["recall_mode"] or "?"),
         "                  seed=%s, embedder=%s" % (op["seed"], op["embedder"]),
         "session boundary: %s -> cold reader loaded %d records"
         % (op["session_boundary"], op["records_loaded_by_cold_reader"]),
         "",
         "CONTROLS",
         "  positive       (verbatim query -> rank 1)      : %s  rank=%s"
         % ("PASS" if doc["controls"]["positive"]["passed"] else "FAIL",
            doc["controls"]["positive"]["rank_headline"]),
         "  discrimination (wrong question -> worse)       : %s  mismatched hit@5=%s vs real %s"
         % ("PASS" if doc["controls"]["discrimination"]["passed"] else "FAIL",
            doc["controls"]["discrimination"]["mismatched_hit@5_by_arm"],
            doc["controls"]["discrimination"]["real_hit@5_by_arm"]),
         "  contest        (fixture is actually hard)      : %s  same-subject siblings per query: "
         "%.2f in top5, %.2f in top25"
         % ("PASS" if doc["controls"]["contest"]["passed"] else "FAIL",
            doc["controls"]["contest"]["mean_own_siblings_in_top5"],
            doc["controls"]["contest"]["mean_own_siblings_in_top25"]),
         "",
         "STORE OBSERVATION (reported, not gated)",
         "  no relevance floor: a question about a subject absent from the store still returned %s target"
         % doc["store_observations"]["no_relevance_floor_fill"]
              ["target_decisions_returned_in_top25_by_arm"],
         "                      decisions in its top 25; top-1 was a target: %s"
         % doc["store_observations"]["no_relevance_floor_fill"]
              ["a_target_decision_was_the_top_answer_by_arm"],
         ]
    if doc["verdict"] == "HARNESS_BROKEN":
        L += ["", "VERDICT: HARNESS_BROKEN -- " + doc["hit_rates_withheld"],
              "  " + doc["controls"]["positive"]["note"], "=" * 92, ""]
        return "\n".join(L)

    h = doc["headline"]
    L += ["", "HIT RATES (headline arm: %s -- the library default, what our MCP server runs)"
          % doc["headline_arm"],
          "  hit@1  %.3f      hit@5  %.3f      hit@25 %.3f" % (h["hit@1"], h["hit@5"], h["hit@25"]),
          "  reinforce=off arm: hit@1 %.3f      hit@5  %.3f      hit@25 %.3f"
          % (doc["arm_agreement"]["hit@1_reinforce_off"], doc["arm_agreement"]["hit@5_reinforce_off"],
             doc["arm_agreement"]["hit@25_reinforce_off"]),
          ("  the default (reinforce=True) moved: "
           + ", ".join("%s %s->%s" % (m["fact"], m["rank_reinforce_off"], m["rank_reinforce_on"])
                       for m in doc["arm_agreement"]["facts_whose_rank_moved"])
           if doc["arm_agreement"]["facts_whose_rank_moved"] else
           "  the default moved no fact's rank on this corpus"),
          "",
          "RECENCY CASE (written today, must be in top 5): %s   rank=%s"
          % ("HIT" if doc["recency_case"]["hit@5"] else "MISS",
             doc["recency_case"]["rank_by_arm"].get(doc["headline_arm"])),
          "CORRECTION CASE: current decision at rank %s, retired decision served=%s"
          % (doc["correction_case"]["current_decision_found_at"],
             doc["correction_case"]["retired_decision_served"]),
          "",
          "PER FACT (headline arm)"]
    for r in doc["arms"][doc["headline_arm"]]["per_fact"]:
        mark = "hit " if r["hit@5"] else ("k25 " if r["hit@25"] else "MISS")
        extra = ""
        if r["rank"] is None:
            extra = "   <- %s" % r.get("diagnosis", "")
        L.append("  [%s] rank=%-5s %-26s %s%s"
                 % (mark, r["rank"], r["fact"], r["query"][:44], extra))
    if h["missed_at_5"]:
        L += ["", "MISSED AT k=5: " + ", ".join(h["missed_at_5"])]
    L += ["", "VERDICT: %s" % doc["verdict"]]
    for f in doc.get("bar_failures", []):
        L.append("  - " + f)
    L += ["=" * 92, ""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--distractors", type=int, default=2400,
                    help="same-vocabulary records across many subjects (default 2400; with the siblings "
                         "this lands the store near the ~2,550 records of the real one that failed)")
    ap.add_argument("--siblings", type=int, default=12,
                    help="per target, records built from THAT target's own vocabulary, so retrieval is "
                         "contested on the subject and not just on the corpus (default 12; 0 disables and "
                         "reproduces the easier first version of this fixture)")
    ap.add_argument("--seed", type=int, default=20260801)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--no-gate", action="store_true",
                    help="report the number but exit 0 unless a control failed")
    ap.add_argument("--in-process", action="store_true",
                    help="cross the boundary with a fresh handle instead of a new process")
    ap.add_argument("--keep-store", action="store_true", help="do not delete the synthetic store")
    # internal: the cold-reader entry point
    ap.add_argument("--_read-phase", dest="read_store", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--_manifest", dest="read_manifest", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--_reinforce", dest="read_reinforce", default="0", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)

    if a.read_store:                                        # we ARE the cold reader
        with open(a.read_manifest, encoding="utf-8") as fh:
            manifest = json.load(fh)
        out = read_phase(a.read_store, manifest, a.read_reinforce == "1")
        sys.stdout.write("{RESULT}" + json.dumps(out))
        return 0

    work = tempfile.mkdtemp(prefix="dogfood_xsession_")
    try:
        doc = run_harness(work, a.distractors, a.seed, in_process=a.in_process, siblings=a.siblings)
    finally:
        if a.keep_store:
            print("synthetic store kept at %s" % work)
        else:
            shutil.rmtree(work, ignore_errors=True)

    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
    sys.stdout.write(render(doc))
    print("written: %s" % a.out)

    code = doc["exit_code"]
    if a.no_gate and code == 1:
        print("(--no-gate: the measurement missed the bar; reporting it and exiting 0)")
        return 0
    return code


if __name__ == "__main__":
    sys.exit(main())
