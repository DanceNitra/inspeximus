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

**And the mechanism is not what I assumed.** I wrote that langmem has 708K downloads "because
LangChain ships it". Checked against PyPI `requires_dist`: **langchain 1.3.17 and langgraph 1.2.11 do
not depend on langmem** — the edge runs the other way, langmem depends on them. No framework depends
on any of these packages, mem0 included. Installs come from being the **named default in the scaffold
people copy**: tutorials, an org namespace (`langchain-ai/` buys langmem 447 downloads per star
against mem0ai's 64), and academic benchmark harnesses that pin it — then multiplied 50-450x per real
adopter by Linux CI and container rebuilds. claude-mem shows a second channel invisible to package
stats entirely: 91,739 stars but only 66k npm downloads, because it installs via
`/plugin marketplace add`, and it is vendored into 25+ third-party plugin bundles.

Distribution is won at the template and marketplace layer, not in the dependency graph. That makes
our MCP server and the Claude Code plugin path more important than any adapter, and it is why #3
below is about being one click from runnable code rather than about integrations.

**A caution on our own number.** Ours is **67.4% mirrors** (51,530 with, 16,810 without over 180
days) against langmem's **0.3%**. Any raw comparison flatters us, and the 75% null-user-agent share
above is the same problem seen from the other side.

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

### #2 — DONE, and my description of it was wrong (corrected 2026-08-25)

I wrote that @mioimotoai-lgtm's issue #1 was "still open and he has never been told", 41 days late.
**False.** He was answered on 2026-08-22, twice: a long reply reproducing his report from a fresh
clone, taking both of the contracts he proposed rather than choosing, and then a second comment
correcting three numbers in the first one. The fix credits him in its docstring and shipped with a
test that runs the published command from outside the repo root with no key, plus a control that
restores his original loader and asserts the suite catches it.

I read `state: open` and inferred `never answered` without opening the comments. A real fact with an
invented reading on top, which is the same class as §1's "we are shrinking" and the langmem claim in
§3 — three of them in one plan.

What is actually outstanding is smaller and is his call, not ours: the reply ends by asking him
whether the refusal message reads as actionable from a cold start, which is the one thing we cannot
judge from inside the repo. The issue stays open until he answers. Closing it ourselves would be
taking his question off the table to tidy our own tracker.

### #3 — One click to runnable code (~half a day)

A quickstart page with the five-line correction demo, `pip install inspeximus`, the line "no API key,
no service, 192 ms", and the MCP install. Move the hero from "Star on GitHub" to it. Then link
`examples/`, `docs/INTEGRATIONS.md` and `claims_audit.py` from README and site — half an hour that
turns three invisible assets into visible ones.

### #4 — Define the second minute (~half a day)

After `revert()` the README changes subject. Add what comes next: putting it under a real agent, the
MCP install, behaviour at ten thousand records. With 28 unique visitors a fortnight, every one who
leaves with nowhere to go is a measurable loss.

### #5 — Re-aim: two competitors arrived on our ground in five weeks (~1 day)

**mem0 shipped supersession on 2026-08-04.** "Dream" flags a contradicted fact and links it to its
replacement, always-on, non-destructive, with `latest_only=true`. Four things keep our position, each
checked in their source and docs: it is **"Not available" in OSS** (`supersede` / `latest_only` absent
from `mem0/memory/main.py`, control: `vector_store` 96 hits); the decision mechanism is undisclosed
and no determinism is claimed; the underlying chain shipped 2026-05-27, before the window; and their
docs gate it to Pro at $249 while the blog says all plans.

More useful than any of that: **Dream created an erasure gap and they documented it rather than
closing it.** mem0's own 2026-08-12 governance post states `delete()` does not remove what Dream
superseded unless you pass `delete_linked=True`. Non-destructive supersession plus non-cascading
delete is our thesis, written by them. Their conflict-loss issue #4896 is also closed `not_planned`,
and their README still says *"Memories accumulate; nothing is overwritten."*

**And Cognee went further.**

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
- "langmem is big because LangChain ships it" (§3) — **false**, verified against PyPI metadata.
  No framework depends on any of these packages.
- "@mioimotoai-lgtm has never been told" (§4 #2) — **false**. He was answered twice on
  2026-08-22. I read `state: open` and inferred it, without opening the comments.
- "Split core.py first" is now done the cheap way: `docs/CORE_MAP.md`, generated from the AST
  and checked in CI. Measured while building it: 33% comments, 25% docstrings, 42% code.
- A competitor scan reported that our published "Zep LongMemEval 71.2%" was really the full-context
  baseline. **I read the source before changing anything: the table is `GPT-4o [Zep 71.2, Full-context
  60.2] | GPT-4o-mini [Zep 63.8, Full-context 55.4]`.** 71.2% is Zep. Our citation, in five places
  including `docs/CLAIMS.md` row 35, is correct and stays. Checking before correcting is the only
  reason a right number did not become a wrong one.
