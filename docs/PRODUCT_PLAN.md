# inspeximus product plan — why anyone downloads this

Written 2026-08-25, overnight. Every number was measured that night and the command or URL that
produced it is in the text, so this can be re-run rather than believed. It is the first plan in this
repo; the strategy memory it replaces had gone 35 days and 426 commits without an update while still
naming "get a LOCOMO number" as gap #1, which is how the same retired experiment got started three
times.

Two of my own first-pass conclusions are corrected below rather than quietly dropped. That is the
point of writing the evidence beside the claim.

---

## 1. Where we actually are

**The product works.** The README's headline demo, run verbatim in a clean directory:

```
import time      : 181 ms      after correction : 'db-7.internal'   (matches)
total wall time  : 192 ms      after revert     : 'db-3.internal'   (matches)
```

Zero core dependencies, Python 3.8+, no key, no service, no network, no LLM.

**We are the only one of five a stranger can run with nothing.** Checked against each vendor's own
quickstart:

| | what the first snippet needs |
|---|---|
| mem0 | cloud signup + API key (OSS path needs `OPENAI_API_KEY`, spawns Qdrant) |
| Zep | account + `ZEP_API_KEY`; community edition discontinued |
| Letta | Node 22.19+, your own provider key; Python server archived 2026-08-16 |
| Cognee | `LLM_API_KEY` required, OpenAI by default |
| **inspeximus** | **nothing** |

**But the download number is not what I first said it was.** I wrote that we are shrinking, citing
weekly 6,495 / 2,595 / 2,355 / 2,847 / 2,469 and a 0.58x ratio. That was an invention on top of a
real number: **the first upload to PyPI was 2026-07-21**, so week 30 is week one. The "decay" is our
own release cadence — 113 versions in five weeks, 40 uploads on 2026-07-25 alone, downloads tracking
upload days at r = 0.96.

**And most of those downloads are not people.** 75.0% report no Python version, against 2.7% for
mem0ai and 0.6% for letta. Pip-shaped traffic is ~120/day, 69% of it one Python minor version, which
is a CI image. **Zero packages depend on us** anywhere on GitHub. Repo traffic over 14 days: 37 views,
28 unique visitors, against roughly 10,000 "downloads".

The honest state is **pre-adoption**. Three people outside this project have ever engaged: one filed
a bug, one posted to HN (2 points), one curator looked and declined.

## 2. The finding the plan turns on

We built the answer to every objection a sceptic could raise, then linked it from nowhere.

| asset | what it is | in README | on the site |
|---|---|---|---|
| `claims_audit.py` | audits our own published numbers: 361 tokens, 199 quantitative claims, 105 registry rows, 80 reproducible by a committed command. **40 s, no key** | **no** | **no** |
| `docs/INTEGRATIONS.md` | 11 framework adapters with an honest "10 of 13 verified" ledger | **no** | **no** |
| `examples/` | 15 runnable files | **no** | **no** |

And `index.html`, our own front page, contains **zero occurrences of `remember(`**. There is no code
on it. Both hero calls to action say "Star on GitHub" and the docs link goes to the GitHub README.
Every competitor is one click from runnable code; we are infinitely many.

A curator wrote down why he passed: `Snseam/awesome-agent-memory` issue #19, 2026-08-03, lists
inspeximus among entries deliberately not promoted, reason attached — *"GitHub-only or vendor/
self-claimed benchmark signals"*. From where he stands he is right, and the refutation was in the
repo the whole time, unlinked.

## 3. What we are not going to do

**Not another benchmark number.** Two of our own adversarial passes retired that axis, and the
download table is the argument: mem0 does not have 4M installs because of its LOCOMO score. To a
curator, a number we publish is one more self-claimed signal.

**Not a rewrite.** Everything below lands on what already exists.

## 4. The work, ordered by evidence

### #1 — Make the claims checkable by someone who does not trust us (~1 day)

Not more numbers. One command a stranger runs that verifies *our own honesty*, with no key and none
of our data.

`python claims_audit.py` already does it in 40 seconds: every published number, whether it is
registered, whether its pin resolves, whether a committed command reproduces it — including 2 rows
marked WITHDRAWN. A register that admits withdrawals is the opposite of a marketing signal, and
nobody outside has seen it.

Ship it as the visible answer: the command and its output in the README and on the site, above the
benchmark table. Same for `probes/integrity_bench_revert.py --judge local`, which runs free and
offline and prints its own caveat that it is not comparable with the openai-judged figures. Verified
tonight from a clean directory: `revert_success_rate 1.0`, no key, no network.

### #2 — Answer the one person who ever tried it (30 minutes, 41 days late)

`DanceNitra/inspeximus` issue #1, opened 2026-07-15 by @mioimotoai-lgtm: our documented benchmark
command died with `FileNotFoundError: server/.env` on a clean clone, before argparse ran, and
overwrote a real `OPENAI_API_KEY` if one was set. He wrote a precise report with a proposed fix.

**The code is fixed** and credits him by name in its docstring; `--judge local` is the second contract
he proposed. **The issue is still open and he has never been told.** That is the entire population of
people who tried this hard enough to find a fault.

### #3 — One click to runnable code (~half a day)

A quickstart page with the five-line correction demo, `pip install inspeximus`, the line "no API key,
no service, 192 ms", and the MCP install. Move the hero from "Star on GitHub" to it. Then link
`examples/`, `docs/INTEGRATIONS.md` and `claims_audit.py` from README and site — half an hour that
turns three invisible assets into visible ones.

### #4 — Define the second minute (~half a day)

After `revert()` the README changes subject. Add what comes next: putting it under a real agent, the
MCP install, behaviour at ten thousand records. With 28 unique visitors a fortnight, every one who
leaves with nowhere to go is a measurable loss.

### #5 — Sharpen the pitch against Cognee, who arrived on our ground (~1 day)

In the last five weeks Cognee shipped deterministic no-LLM supersession (PR #4084, 07-28) and an
append-only SHA-256-chained audit ledger with `verify_chain()` (PR #4476, 08-14), and renamed its API
to `remember` / `recall` / `improve` / `forget` — our verbs. The generic "determinism" pitch is gone.

What survives is specific, and their own source shows it.
`cognee/modules/graph/utils/temporal_conflict_resolver.py:76`, fetched and read:

```python
winners[key] = max(members, key=lambda i: _recency_key(i, edges[i][3]))
```

Recency wins. A restatement of a value a correction retired arrives later, becomes most recent, and
takes the key back. Their own open issue #4030 is that behaviour in the wild. Both new features also
default OFF (`provenance_tracking`, `contradiction_detection`), and supersession only fires on
relationships the caller declares.

So the claim is not "we are deterministic" but **value-keyed supersession that survives a restatement,
on by default, with nothing for the caller to declare** — testable in one command, which makes it the
same work as #1.

### #6 — core.py is 963 KB, and splitting it is NOT the priority I said it was

I had this first; an audit refuted the premise. **56% of the file is explanatory prose** — 296 KB of
comments and 244 KB of docstrings against 423 KB of executable code. The megabyte is the audit trail,
not opacity; the problem is navigation. A full split is 14 to 20 hours across 56 names imported from
`inspeximus.core` in 70 test files (28 of them private), and carries one specific trap: `perf/gate.py`
patches `core._dump_store`, so moving `_save` would make the patch silently stop matching and **the
perf gate would report PASS over a target it no longer sees** — our oldest failure class, waiting
inside a refactor nobody asked for.

Do the cheap 5% instead, about two hours: extract the non-class 116 KB into `_crypto.py`,
`_extract.py`, `_constants.py`, which has no method fan-out, and generate `docs/CORE_MAP.md` from the
AST — every subsystem with its line range, size and public methods, regenerated in CI so it cannot
drift. Auditability as a map rather than an apology.

## 5. Corrected here, and one thing that was not

- "We are shrinking" (§1) — **false**. The package was four days old.
- "Split core.py first" (§4 #6) — **refuted by measurement**, dropped to last.
- A competitor scan reported that our published "Zep LongMemEval 71.2%" was really the full-context
  baseline. **I read the source before changing anything: the table is `GPT-4o [Zep 71.2, Full-context
  60.2] | GPT-4o-mini [Zep 63.8, Full-context 55.4]`.** 71.2% is Zep. Our citation, in five places
  including `docs/CLAIMS.md` row 35, is correct and stays. Checking before correcting is the only
  reason a right number did not become a wrong one.
