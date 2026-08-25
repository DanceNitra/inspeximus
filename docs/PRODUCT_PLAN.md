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

**CORRECTION, same night, and it kills my own reading of that table.** I first wrote that we are
shrinking, citing weekly downloads 6,495 / 2,595 / 2,355 / 2,847 / 2,469 and a 0.58x ratio. That was
an invention laid on a real number. **The package's first upload to PyPI was 2026-07-21**, so week 30
is week one and there is no earlier baseline to decline from. The "decay" is our own release cadence:
113 versions in five weeks, 40 uploads on 2026-07-25 alone, and downloads track upload days at
r = 0.96.

**Worse, the downloads are mostly not people.** 75.0% of them report no Python version and no OS.
The peer rate is 2.7% for mem0ai and 0.6% for letta, so we are ~28x above it:

| package | downloads with no Python version reported |
|---|---:|
| letta | 0.6% |
| mem0ai | 2.7% |
| **inspeximus** | **75.0%** |

Pip-shaped traffic is about 4,195 over 35 days, ~120/day, and 69% of that is a single Python minor
version, which is a CI-image signature rather than a user base. **Zero packages depend on us**: no
`inspeximus` in any requirements.txt, uv.lock or poetry.lock on GitHub outside our own repos. Repo
traffic over 14 days was 37 views from 28 unique visitors against roughly 10,000 "downloads".

**The honest state is pre-adoption, not early traction.** Three people outside this project have ever
engaged with it: one filed a bug, one posted it to HN (2 points, 0 comments), and one curator looked
and declined.

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

## 4. The five things to fix, reordered by evidence

The first ordering in this document was reasoned from first principles and two of its five items were
wrong. What follows is ordered by what outsiders actually did.

### #1 — A curator evaluated us and said no, in writing

`Snseam/awesome-agent-memory` issue #19, 2026-08-03, lists inspeximus among entries deliberately not
promoted, with the reason attached: *"GitHub-only or vendor/self-claimed benchmark signals"*.

That is the single most valuable sentence anyone outside this project has ever written about us, and
it names two separate defects. **GitHub-only**: nothing about the project exists anywhere a curator
counts as independent. **Self-claimed benchmark signals**: every number we publish was produced by
us, measured by us, on a harness we wrote. We have been treating that as rigour. A curator reads it
as marketing, and he is not wrong to, because there is no way for him to tell the two apart from
outside.

The fix is not more benchmarks. It is making our numbers checkable by someone who does not trust us:
a third party able to re-run the claim without our machine, our data or our judge, and a result that
does not depend on any of the three. We already have one honest instrument for this and shipped it
without noticing what it was for. `probes/integrity_bench_revert.py --judge local` runs free, offline,
deterministically, and prints its own caveat that it is not comparable with the openai-judged figures.
That is the shape the whole benchmark surface should take.

### #2 — The one outsider who tried it hit a wall, and we never told him it was gone

`DanceNitra/inspeximus` issue #1, opened 2026-07-15 by @mioimotoai-lgtm: the benchmark command in our
own docs died with `FileNotFoundError: server/.env` on a clean clone, before argparse ran, because the
loader opened a path relative to the current directory that has never been in the repository. It also
overwrote a real `OPENAI_API_KEY` already in the environment.

**The code is fixed** and the fix credits him by name in its docstring. Verified tonight from a clean
directory: `--help` works, and `--systems inspeximus --judge local --n 3` completes free and offline
with `revert_success_rate 1.0` and an explicit non-comparability notice. **The issue is still open and
he has never been answered**, 41 days later. He wrote a careful, correct, reproducible report with a
proposed fix, and got silence. That is the entire population of people who have ever tried this
product hard enough to find something wrong.

### #3 — Nothing compounds after a release

Downloads track our own upload days at r = 0.96. We have no surface that keeps working once we stop
publishing. In order of likely payoff: being what an assistant answers when asked for memory that
handles corrections; being findable by the problem rather than by our name, which requires writing the
problem in the words people use when it happens to them; and the MCP server, which reaches Claude Code
and Cursor users without anyone making a `pip install` decision.

### #4 — The second minute is undefined

The first thirty seconds are excellent and end at `revert()`. What to do next is documented nowhere:
putting it under a real agent, the MCP install, behaviour at ten thousand records. 28 unique visitors
in fourteen days is a small enough number that every one of them who left with nowhere to go matters.

### #5 — core.py is 963 KB, and splitting it is NOT the priority I said it was

I had this first. An audit of the file refuted the premise: **56% of it is explanatory prose**, 296 KB
of comments and 244 KB of docstrings against 423 KB of executable code. The megabyte is not opacity, it
is the audit trail, and the problem is navigation. A full split is 14 to 20 hours, touches 56 names
imported from `inspeximus.core` across 70 test files (28 of them private), and carries one specific
trap: `perf/gate.py` patches `core._dump_store`, so if `_save` moves modules the patch silently stops
matching and the perf gate reports PASS over an unmeasured target. That is our own oldest failure
class, waiting inside a refactor nobody asked for.

Do the cheap 5% instead, about two hours: extract only the non-class 116 KB into `_crypto.py`,
`_extract.py` and `_constants.py`, which has no method fan-out, and generate `docs/CORE_MAP.md` from
the AST — every subsystem with its line range, size and public methods, regenerated in CI so it cannot
drift. That delivers the auditability claim as a map rather than an apology, and leaves the working
product alone.

## 5. What this plan is still missing

The competitive picture is being refreshed as this is written; the 2026-07-20 scan is five weeks old
and section 2's claim that mem0 keeps an LLM on the write path needs re-checking before it is repeated
anywhere public. Everything else above is measured, and each measurement names the command or the URL
that produced it.
