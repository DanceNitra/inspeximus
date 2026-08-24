# inspeximus product plan — why anyone downloads this

Written 2026-08-25. Every number below was measured the night it was written; the commands are in
the text so the plan can be re-run rather than believed. Replaces nothing: it is the first plan in
this repo. The strategy memory it supersedes had been stale for 35 days across 426 commits, and
during that time it still named "get a LOCOMO number" as gap #1, which is how the same retired
experiment got started three times.

## 1. Where we actually are

**The product works.** The README's headline demo, run verbatim in a clean directory against a fresh
store:

```
import time             : 181 ms
after correction        : 'The staging database is db-7.internal'   (expected, matches)
after revert            : 'The staging database is db-3.internal'   (expected, matches)
total wall time         : 192 ms
store on disk           : 1403 B
```

Zero core dependencies, Python 3.8+, no LLM, no network, no service. The one capability we lead on
does what the front page says it does, in under a fifth of a second.

**Distribution is the whole problem.** PyPI, last 30 days:

| package | downloads / month | vs us |
|---|---:|---:|
| mem0ai | 4,050,473 | 287× |
| langmem | 707,978 | 50× |
| zep-cloud | 353,166 | 25× |
| cognee | 210,802 | 15× |
| letta | 204,800 | 15× |
| **inspeximus** | **14,117** | — |

**And we are shrinking, not growing.** Weekly, most recent last: 6,495 / 2,595 / 2,355 / 2,847 /
2,469. The first two weeks average 4,545; the last two average 2,658. That is **0.58×**. Week 30 was
a spike that decayed to baseline in about two weeks, and the baseline has been flat since.

The lesson in that table is the plan's foundation: **a post buys a spike that decays in two weeks.**
Whatever we do next has to compound instead.

## 2. What we are not going to do, and why

**Not another benchmark number.** Two of our own adversarial passes settled this: clean-LOCOMO recall
is a tie and the wrong battle, and the defensible edge is determinism, write-path reliability, cost
and auditability rather than capability-accuracy. mem0 does not have 4M downloads because of its
LOCOMO score; langmem has 708K because LangChain ships it. Distribution follows integration and
discovery, not leaderboards. A number we publish changes nothing about either.

**Not a rewrite, a rebrand, or a new repo.** Everything below lands on the package that already
exists.

## 3. The one sentence the plan has to make true

Someone with a corrected fact that keeps coming back should find inspeximus within one search, and
understand in thirty seconds that it is the only thing that fixes it deterministically.

Today they will not, for three measurable reasons.

## 4. The four things to fix, in order

### #1 — We sell auditability and ship a 963 KB core

`inspeximus/core.py` is **963,295 bytes**; `cli.py` 89 KB, `audit_bundle.py` 72 KB, 22,205 lines
across the package. The pitch is *auditable, reproducible, zero-dependency*, and the first thing a
careful buyer does is open the file. Nobody audits a megabyte.

This is the highest-leverage fix because it is the only one that attacks the claim itself. Split the
core along the seams the API already implies (store/recall, supersession, erasure, compliance,
crypto) with the public surface unchanged, so `from inspeximus import Inspeximus` keeps working
byte-for-byte. Success is measured, not asserted: no public name moves, the full suite passes, and
the largest single file drops below 100 KB.

### #2 — Nothing compounds after the spike

We have no mechanism that keeps producing discovery once a post falls off the front page. The
adapters exist (11 framework extras) but an adapter is not a default; being installed is not being
reached for. The compounding surfaces, in the order they pay:

- **Being what an assistant answers with.** People now ask a model "python agent memory that handles
  corrections" more often than they search. That answer is shaped by what is written where models
  read: the README's first screen, the PyPI description, and the docs pages that name the problem in
  the user's words rather than ours.
- **Being findable by the problem, not the product name.** We have no page that says "a corrected
  fact keeps coming back" in the words someone types when it happens to them.
- **The MCP surface.** `pip install` is a Python decision; MCP reaches every Claude Code and Cursor
  user without one. We already ship the server. It is under-exploited relative to its cost.

### #3 — The second minute is undefined

The first thirty seconds are excellent and end at `revert()`. What the visitor should do in the next
five minutes is not laid out anywhere: how to put this under a real agent, what the MCP install is,
what happens at ten thousand records. A visitor who is convinced and then has nowhere to go leaves
convinced and empty-handed.

### #4 — We do not know why the ones who came, came

14,117 downloads a month is small but it is not zero, and we have never asked what those people
wanted. The W30 spike had a cause we can name; the 2,500/week baseline does not. Until we know which
half of the pitch pulled them, every other decision is a guess dressed as a plan.

## 5. What is still missing from this plan, stated rather than hidden

Two inputs are guesses right now, and both need evidence before section 4's ordering is trustworthy:

1. **What competitors shipped in the last 35 days.** The competitive scan behind our positioning is
   from 2026-07-20. Five weeks is a long time in this category.
2. **What actually makes a developer install a memory library.** Section 4 asserts an ordering from
   first principles. The honest version of it reads issue threads, first-run docs and the places
   people ask, and lets the evidence order the list.

Neither is expensive, but both cost model calls, so they are named here rather than assumed. The
plan above is the version buildable from what could be measured locally tonight, and the section-4
ordering is the part most likely to move once those two land.
