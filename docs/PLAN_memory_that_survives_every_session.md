# Memory that survives every session — implementation plan

Written 2026-08-05. Every number below was measured while writing this, not recalled.

## The gap, measured

The Claude Code integration ships and works. `inspeximus/claude_code.py` installs four hooks,
`SessionEnd` writes a deterministic ledger diff, `SessionStart` prints it, and the round trip
survives a cold process. That much is real: a fact written in session 1 comes back in session 2.

What comes back is the wrong thing.

| where | what is in the store |
|---|---|
| our own `server/.inspeximus/coding_memory.json`, months of use | **917 records. tags: `bash` 747, `file`/`edit` 170. Decisions: 0 (0.0%)** |
| the same project's file-based notes, same period | 313 notes, written by hand, none of them in the store |
| a fresh demo project, one session | 4 records: 2 session boundaries, 1 file state, 1 digest reading `[0 of 1 records kept` |
| `probes/dogfood_cross_session.result.json` | hit@5 0.846 across 13 facts, but `correction_case.current_decision_found_at: null` |

Three separate measurements, one shape. The write path captures **mechanics** — which file holds
which bytes, which command ran — and never captures the **decision and its because**. That is the
exact failure our own doctrine names: a command log is not a memory. It is now quantified at 0.0%.

Two more measured facts constrain the work:

* The session digest dropped the only substantive record it had (`0 of 1 kept`), so the filter that
  decides what is worth carrying across the boundary is rejecting the thing it exists to carry.
* The cross-session probe's own caveat: the synthetic fixture scores 11 of 13 where the real store
  scored 2 of 5. The fixture is easier than reality, so its PASS is a regression line, not evidence
  the product works.

## What the product has to become

Not "we store more". The claim we can defend is narrower and better:

> What survives a session boundary is the set of decisions still in force, each with its reason,
> re-resolved against the live store so a reversed decision is replaced rather than repeated.

Everything below serves that sentence. A unit that does not is out of scope.

## Constraints carried into every unit

* **No LLM on the write path.** It is the moat and the reason the digest is byte-reproducible. A
  unit that needs a model to decide what to store is rejected regardless of what it scores.
* **Zero required dependencies.**
* **Fail-open in hooks.** A hook that can block the agent is worse than no hook.
* **Every unit ships a measurement that can fail, plus a control.** The `0 of 1 kept` line existed
  in plain sight for months because nothing asserted on it.

---

## Unit A — capture decisions deterministically, with no model

**A1. Commit messages are decisions that are already written down.**
`inspeximus/claude_code.py`, `PostToolUse` on `Bash`.
A `git commit` is the one moment a coding agent states a choice and its reason in structured form.
Today the hook records the *command*. It should record the *message*: subject as the decision,
body as the `because`, changed paths as `derived_from`, commit SHA as the source. Deterministic,
no model, and the corpus already exists in every repo. Measured on our own last 200 commits:
**92% carry a substantive body and 80% state a reason in it.** That is the decision log the store
should have had all along, sitting in `git log` while the hook wrote down `git commit` as a Bash
event.
*Measure:* on our own last 200 agora commits, the fraction that produce a decision record with a
non-empty `because`. *Control:* a `git status` or `git log` Bash call must produce **no** decision
record — a capturer that fires on every git command has learned nothing. Size **M**.

**A2. An explicit decision hook that costs one line.**
`inspeximus/claude_code.py`, `UserPromptSubmit`.
`remember_decision` exists in the MCP surface and has been used a handful of times against the 313
hand-written notes the same project accumulated in the same period. The
adoption defect is that nothing makes it the default path. Add a deterministic trigger: a prompt or
an assistant turn containing a decision marker (`DECISION:`, `we chose`, `going with`, `dropped`)
writes one keyed decision record.
*Measure:* precision and recall of the trigger on a hand-labelled set of 60 real turns from our own
transcripts, 30 decisions and 30 not. *Control:* the 30 non-decisions must produce zero records; a
trigger that fires on everything scores perfect recall and is worthless. Size **M**.

**A3. Topic keys, or supersession cannot fire.**
`inspeximus/core.py` (`regex_extractor` neighbourhood), `claude_code.py`.
A decision without a topic key sits beside its correction instead of retiring it. Derive the key
deterministically from the decision's subject.
*Measure:* on a 12-link correction chain, the number of links that bind to one key. Current
measured baseline for conversational text: **0 of 12**. *Negative control:* two unrelated decisions
must stay unbound. Size **L** — this is the hard one and it may not be solvable without a model, in
which case the honest deliverable is the boundary, published as one.

## Unit B — make the boundary carry what it collected

**B1. ~~The digest filter kept 0 of 1.~~ NOT A DEFECT — closed 2026-08-05 by running the test.**
The measurement that was going to justify the fix refuted it instead. A session containing one
decision, one correction on a key and twenty file edits: `considered=22, items=2, rejected=20`, and
the digest reads

```
decisions recorded:
  * DECISION: pin the reasoning budget to 24000
  * DECISION: run the release from the trusted publisher workflow — because: ...
[2 of 22 records kept at salience >= 2.5]
```

Both decisions survived, the correction survived as the current value, and zero file lines leaked
in. So `0 of 1 kept` in the demo project was correct: the only record there was a file state, which
is transcript rather than conclusion, and the filter exists to drop exactly that.

This closes B1 and sharpens the plan. Everything from the decision onward — the digest, the
re-resolution in `session_context`, the substitution of a corrected value, and now
`decisions_in_force()` — works. The pipeline is complete and correct and is being fed nothing but
mechanics. **Unit A is not one item on a list, it is the whole bottleneck.**

**B2. Decisions first, under a budget.**
`claude_code.py` (`SessionStart`).
The injected block is prompt budget spent before the user types. Order it: decisions in force,
then open corrections, then file state, and cap it.
*Measure:* injected characters, and the rank of a decision needed by the first prompt of the next
session. *Control:* with no decisions in the store the block must degrade to today's file list, not
to empty. Size **S**.

## Unit C — the correction case, which is the flagship claim

**C1. `current_decision_found_at: null`.**
`probes/dogfood_cross_session.py`, `inspeximus/core.py`.
The probe asks for the current value of a corrected decision across a session boundary and gets
nothing back. It also does not serve the retired one, so this is not a supersession failure — it is
a retrieval failure on the record that supersession left standing. Everything we sell rests on this
case working.
*Measure:* `current_decision_found_at` must be a rank, not null, and `retired_decision_served` must
stay false. Both directions, or the fix is trading one error for the other. Size **M**.

## Unit D — the fixture is easier than reality

**D1. A harness against a real store.**
`probes/dogfood_cross_session.py`.
11 of 13 synthetic against 2 of 5 real. Until that gap is explained the PASS means nothing. Build
the arm that runs against an actual project store with hand-labelled needs.
*Measure:* hit@5 on the real store, published whatever it is. *Control:* the synthetic arm must
still run beside it, so the difference between them is visible rather than assumed. Size **M**.

**D2. Adoption as a metric, not a hope.**
`inspeximus/claude_code.py`, a `--report` subcommand.
The single number that says whether this product works: decisions as a share of records. Today,
on the largest store we have, it is 0.0%.
*Measure:* the number, printed, per project. *Control:* a store with no decisions must report 0.0%
loudly rather than omitting the line. Size **S**.

## Unit E — surface

**E1. `recall_iterative` is absent from all 56 MCP tools and all 25 CLI subcommands.**
Our only working multi-hop lever is unreachable from every surface a user has. Size **S**.

**E2. Project scoping.**
`--project` so one agent's memories do not bleed across repos.
*Measure:* records written under A are unreachable from B. *Control:* without the flag they ARE
reachable, or the test is passing on an empty store. Size **S**.

---

## Order

C1 first — done, commit 50004b0. B1 second — opened, measured, and closed as not-a-defect without
a line of code changing; the digest keeps decisions and drops transcript exactly as designed.

That leaves A1 as the next unit and, after B1 dissolved, as the only thing standing between this
product and its claim. Then A2, then A3. D and E are independent and can land any time.

A3 is the risk. If a deterministic key cannot be derived from conversational decisions, the honest
outcome is a published boundary, not a quiet retreat to an LLM on the write path.

## What this plan deliberately does not do

Summarise sessions with a model, rank better, add a retrieval mechanism, or ship a hosted tier. The
first is the competitors' design and the reason their memory is not reproducible. The second and
third are measured nulls in our own lab. The fourth is killed.
