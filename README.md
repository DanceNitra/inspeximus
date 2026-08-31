# inspeximus — the agent memory that takes it back

**Your agent's most expensive failure is not forgetting. It is confidently remembering the old
answer.**

Long-term memory for AI agents in one zero-dependency Python file, plus an MCP server for any
client and a one-line config install for Claude Code, Cursor, Windsurf, Codex and Cline.

Correcting a fact is not the hard part, and this field already does it. Graphiti invalidates facts
and leads with it; cognee ships `forget` as one of its four operations. When we measured mem0 and
Graphiti, both kept the corrected value, which is the right thing to do. What neither has is a
channel to undo that correction on command, from an instruction that names no value. Here a fact
that was wrong, or true on Monday and outdated by Friday, gets corrected once, and you can still put
it back afterwards.

The benchmarks ask which of two conflicting facts wins. The question after that one is whether you
can take the correction back, and whether you can show what changed.

The name is from medieval charters. A king, bishop, abbot or town council opened with *inspeximus*,
"we have inspected", reciting an older document in full to record that they had examined it, usually
confirming it, and sealing the result so a later reader could check. It attested that the copy
faithfully matched the original, not that the original was true. Same guarantee here, and
`provenance()` says so in a `limits` field rather than leaving you to find out.

[![PyPI](https://img.shields.io/pypi/v/inspeximus?color=2563eb&label=pypi)](https://pypi.org/project/inspeximus/)
[![Downloads](https://img.shields.io/pypi/dm/inspeximus?color=2563eb)](https://pypistats.org/packages/inspeximus)
[![CI](https://github.com/DanceNitra/inspeximus/actions/workflows/ci.yml/badge.svg)](https://github.com/DanceNitra/inspeximus/actions/workflows/ci.yml)
[![Claims audit](https://github.com/DanceNitra/inspeximus/actions/workflows/audit.yml/badge.svg)](https://github.com/DanceNitra/inspeximus/actions/workflows/audit.yml)
[![Python](https://img.shields.io/pypi/pyversions/inspeximus)](https://pypi.org/project/inspeximus/)
[![Zero dependencies](https://img.shields.io/badge/dependencies-0-2563eb)](https://pypi.org/project/inspeximus/)
[![Tests](https://img.shields.io/badge/tests-2600%2B-2563eb)](#how-this-is-tested)
[![License](https://img.shields.io/pypi/l/inspeximus)](LICENSE)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.21708778.svg)](https://doi.org/10.5281/zenodo.21708778)

```bash
pip install inspeximus
```

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/correction-dark.svg">
  <img alt="After you correct a fact, how often does the old value come back? inspeximus 0%, Graphiti 0.x 13.3%, mem0 2.0.11 46.7%, and inspeximus with its guard disabled 100% — n=30 per system, each on its own native configuration." src="docs/assets/correction-light.svg">
</picture>

---

## The 30 seconds that matter

Every memory library can store and retrieve. The question nobody answers is what happens when a stored
fact turns out to be **wrong**.

```python
from inspeximus import Inspeximus

m = Inspeximus("memory.json")

m.remember("The staging database is db-3.internal", key="staging-db")
m.remember("The staging database is db-7.internal", key="staging-db")   # a correction

m.recall("which staging database")[0]["text"]
# 'The staging database is db-7.internal'          <- the correction wins, every time

m.revert("staging-db")                              # and it is reversible
m.recall("which staging database")[0]["text"]
# 'The staging database is db-3.internal'
```

No embedding drift, no "the LLM usually picks the newer one". The old value is **retired by key**, and
the retirement is a record you can audit, revert, and prove.

**Say the old value again and it still does not come back.** That is the part a recency rule cannot
do, and it is where most stores differ from this one: writing `db-3` a third time, under the same
key, leaves `db-7` current. Going back is a decision you make on purpose, with
`remember(..., reaffirm=True)` — the guard cannot un-supersede on its own.

**The limit, because it is keyed:** a statement written with *no* key is a new fact, not a
correction, and it is outside the guard. If your pipeline re-ingests a stale document without keys,
that text competes on its own merits. Both behaviours are measured in
[`probes/does_a_restatement_take_the_key_back.py`](probes/does_a_restatement_take_the_key_back.py),
which runs offline in a second.

---

## When someone asks you to prove it

Turn receipts on and every write joins a hash chain. The values alone cannot tell you whether
somebody edited the file behind the library's back. The chain can.

```python
from inspeximus import Inspeximus

m = Inspeximus("memory.json", receipts=True)
m.remember("The staging database is db-3.internal", key="staging-db")
m.remember("The staging database is db-7.internal", key="staging-db")

m.verify_writes()[0]        # nothing has been touched yet
# True

# now somebody edits memory.json directly, turning db-7 into db-9
raw = open("memory.json", encoding="utf-8").read()
open("memory.json", "w", encoding="utf-8").write(raw.replace("db-7", "db-9"))

Inspeximus("memory.json", receipts=True).verify_writes()[1][0].split(": ", 1)[1]
# 'its TEXT or KEY no longer matches its write receipt (edited after write)'
```

`provenance(key=...)` answers the rest in one call: every value the key has held and the policy that
retired each one, where the current value came from including taint inherited through summaries,
whether the record still matches what its receipt committed to, and a `limits` field naming what none
of it proves. Erasure works the same way. `forget_subject()` hard-deletes every memory attributable
to a person, including the summaries that inherited it through lineage, and leaves a signed
content-free tombstone, so a later reader can tell a deliberate erasure from tampering.
`erasure_certificate()` makes that checkable by a third party with no private key and no reason to
trust us.

`inspeximus compliance` prints the same evidence labelled by article, with its own scope attached:
the agent-memory slice only, not the whole system, and not a certification.

### Proving when, and whether the clock belonged to anyone

Every clock in the system belongs to the operator being audited, so `timestamp.py` gets an RFC 3161
token from a third party instead. Under eIDAS Article 41 a QUALIFIED timestamp carries a rebuttable
presumption of the time it shows, and an ordinary one carries none. Nothing in a token says which
you have.

`inspeximus timestamp trusted-lists` builds an offline cache of the EU trusted lists, and
`inspeximus timestamp qualified <token> --trusted-list <cache> --when <the date it was made>`
answers for one token. The exit code separates qualified from not qualified from undetermined.

Pass the date the token was made, not today. Qualified standing is granted and withdrawn over time:
of the 1477 qualified timestamp services published across 25 territories, 570 (39%) have held both
a qualified and a non-qualified status. One real Austrian service returns four different answers
from one certificate with only the date changing.

It reports membership and nothing else. It does not check the signature on the trusted list, it says
nothing about whether the token is authentic (`verify_with_openssl` does that, and both must pass),
and before a list's earliest record it answers UNKNOWN rather than "no".

**What this is not.** It is not compliance, and none of it is due yet. When the EU AI Act's
high-risk obligations take effect, on 2 December 2027 for standalone Annex III systems and 2 August
2028 for those embedded in regulated products, the Act will ask for automatic event logging
(Art. 12), retention of those logs (Art. 19), and accuracy, robustness and cybersecurity (Art. 15).
None of those articles names memory, provenance or tamper-evidence, so what is here goes past the
text rather than implementing it. [docs/AI_ACT.md](docs/AI_ACT.md) maps what the store already keeps
onto the logging duty, and says where the mapping stops.

---

## The next five minutes

The demo above ends at `revert()`. Here is what to do with it.

**Put it under a real agent.** Nothing to wire: `remember` on the way in, `recall` on the way out.
The point is the key, because that is what makes a later correction land on the same fact instead of
becoming a second one.

```python
from inspeximus import Inspeximus

m = Inspeximus("memory.json")
user_id, choice, user_question = "u-1", "dark mode", "what does this user prefer"

m.remember(f"user prefers {choice}", key=f"pref::{user_id}")      # correcting later needs the key

context = [hit["text"] for hit in m.recall(user_question, k=5)]
print(context[0])
# user prefers dark mode
```

If you use a framework, there are adapters for LangChain, LangGraph, LlamaIndex, CrewAI, AutoGen,
Haystack, Google ADK, OpenAI Agents and Pydantic-AI — with a ledger recording which are verified
against a live install and which are recorded broken, rather than a wall of logos:
[docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).

**Work through the examples in order.** They run offline with no key, each one printing what it did:

| | |
|---|---|
| [`01_basics.py`](examples/01_basics.py) | remember, recall, correct, and read the history of a key |
| [`02_correction_and_erasure.py`](examples/02_correction_and_erasure.py) | correction and erasure as separate channels, which they are |
| [`03_semantic_recall.py`](examples/03_semantic_recall.py) | bring your own embedder |
| [`06_gdpr_erasure_receipt.py`](examples/06_gdpr_erasure_receipt.py) | prove a deletion happened, to someone who does not trust you |

**Find your way around the code.** [docs/CORE_MAP.md](docs/CORE_MAP.md) lists every public method and
the line it starts on, generated from the AST and re-checked in CI.

**Then decide whether to believe any of it**, using the two commands under
[Check us without trusting us](#check-us-without-trusting-us).

---

## The receipts

We measured the one thing the others do not publish: **how often a corrected fact comes back.**

Each system was run on its own native configuration, same task, same 30 trials:

| system | keeps the correction | resurrects the old value |
|---|---|---|
| **inspeximus** | **100%** | **0%** |
| Graphiti 0.x (Neo4j + OpenAI) | 86.7% | 13.3%&nbsp;&nbsp;<sub>95% CI [3.3, 26.7]</sub> |
| mem0 2.0.11 (OpenAI native) | 53.3% | **46.7%**&nbsp;&nbsp;<sub>95% CI [30.0, 63.3]</sub> |
| inspeximus, guard disabled | 0% | — <sub>the control: this is what the guard is doing</sub> |

<sub>n = 30 per system. mem0 measured at **2.0.11** (2026-07); mem0 is now on 2.0.18 and we have not
re-run it — the version is stamped rather than the claim being restated as current. Full method,
raw arrays and the re-runnable harness:
[RAMR](https://github.com/DanceNitra/ramr) · `echo_resistance_backends_result.json`</sub>

> **Read the Graphiti row correctly — its echo defense did not fail.** Our own raw output records
> `echo_attributable_flips: 0` out of **26** corrections that were extracted correctly before the echo
> ran. Graphiti's bi-temporal invalidation held every one of them. The 13.3% above is four *pre-echo
> extraction misses* — the correction never made it into the graph — which is a different failure from
> the one this table is about. Stated as the mechanism rather than the headline: on echo-attributable
> resurrection, Graphiti scores **0%**, the same as us, by keeping the supersession link at write time.
> That is the real finding here: what separates these systems is whether the link is recorded, not who
> recorded it.

### Two numbers you can check in three seconds, with no API key

Measured 2026-08-25 against **Hindsight 0.9.2** (vectorize-io, 21k stars) and mem0, each in its own native
config, n=20. These two need no judge at all — they read the raw recall payload, so nothing depends on a
model reading well:

| | inspeximus 2.21.0 | Hindsight 0.9.2 | mem0 |
|---|---|---|---|
| after a correction, recall returns the new value and **not** the old one | **20 / 20** | 0 / 20 | 1 / 20 |
| identical writes twice — same stored state? | **byte-identical** | 20 / 20 differ | — |
| model calls to do it | **0** | 60 | 60 |

Both competitors return the corrected value *and* the retired one, and leave the choice to the caller. That is
a defensible design — a bitemporal store handing back old and new with validity markers is being honest — but it
is a different promise from ours, and the difference is whose job disambiguation is.

The first row is free to verify. No key, no server, no network:

```bash
git clone https://github.com/DanceNitra/inspeximus && cd inspeximus
python probes/integrity_bench_store_resolves.py --systems inspeximus
```

It finishes in milliseconds and prints `store-resolved=1.00 (resolved=20 both=0 stale=0 neither=0, n=20)`.
Adding `,mem0` or `,hindsight` reproduces their columns and costs their own extractor calls.
[Method, caveats and the cells where we do **not** win](probes/INTEGRITY_BENCHMARK.md).

The bottom row is the point. Turn our guard off and we score **zero** — so the number is the mechanism,
not the benchmark being kind to us.

---

## Use it in Claude Code (one line)

```bash
inspeximus install --ide claude     # also: cursor, windsurf, codex, cline
```

That wires an MCP server with **73 tools** and three hooks. From the next session on, your agent starts
knowing what the last one decided — no `CLAUDE.md` editing, no re-explaining:

- **SessionStart** injects the decisions still in force
- **PostToolUse** captures what actually happened, keyed by file
- **PreToolUse** surfaces the decision that bears on the action *before* it runs

---

## What you get

**Correction as a first-class operation.** `remember(key=...)` retires the previous value for that key.
`revert(key)` restores it. `history(key)` shows the chain. All deterministic, all auditable.

**Erasure that can be proven.** `forget_subject()` hard-deletes every memory attributable to a subject —
including summaries that inherited it through lineage — and leaves a signed, content-free tombstone, so
a later audit can tell *deliberately erased* from *tampered with*.

**A deletion check that reads the bytes, on any store.** `delete()` returning success tells you a row
is gone from an index. It does not tell you the value has left the disk, and for an erasure obligation
that is the part that matters. `scan_residue(root, values)` searches a directory for values that are
supposed to be gone and separates three outcomes that are usually collapsed into one: `LIVE` (a table
still holds it in a row), `UNRECLAIMED` (the bytes are there but in no live row, because the storage
engine has not reused the page yet, which is a property of the engine and not a vendor defect), and
`PLAIN` (a log, trace or backup file still contains it). Nothing about it is specific to inspeximus:
point it at a vector database, a SQLite history, a JSONL trace, or another library's data directory,
and it answers for that deployment.

`residue_certificate()` turns one of those scans into a document somebody else can check.
It records a SHA-256 for every file it read, so a third party re-walks the same directory with
`verify_residue_certificate()` and confirms both that the search covered the bytes it claims and that
they have not changed since. The signature identifies the scanner without making the finding true;
what makes it evidence is that anyone can re-run it. From the shell: `inspeximus residue --root DIR
--value SECRET --cert-out cert.json`, then `inspeximus residue-verify cert.json --root DIR`.

Read the scope before treating a clean result as an all-clear. The match is literal and
case-sensitive, so a lowercased or re-spaced copy of the value is missed by design; a file the scan
could not read is reported and keeps the verdict negative, because "clean" must never mean "we did not
look there". Both limits travel inside the signed certificate.

**Provenance you can check, not just store.** `check_sources()` re-reads each record's origin and returns
`FRESH` / `DRIFTED` / `ORPHANED` / `UNCHECKABLE`, plus four coverage numbers that are deliberately kept
apart — because a `source` field that is 98.3% populated and 0.01% re-fetchable is a schema, not a
guarantee. (Those two numbers are ours, measured on our own production store.)

**Current-state applicability.** `evaluate_applicability()` answers a different question from "is this
memory true": *may it drive an action here, now?* Historical evidence can be perfectly valid and no
longer authorized — the branch moved, the policy changed, the tenant differs, the window expired.
Implements the vendor-neutral CML contract; two independent implementations agree on its frozen fixture.

**Multi-tenant isolation.** `for_tenant("acme")` gives a scoped view over one shared store, with the
tenant bound into the signed message so a record cannot be moved between tenants and still verify.

**An audit trail in formats an auditor already reads.** A hash chain proves your records were not
edited. It does not tell a third party who wrote them, what they are about, or when, and those are the
three things somebody checking your system actually asks. Four IETF standards answer them, and
inspeximus emits all four with no dependencies:

| you want to show | the artifact | the standard |
|---|---|---|
| this record is in the log | a Receipt of Inclusion | RFC 9942 (COSE Receipts) |
| I said it, and it is about this | a Signed Statement | RFC 9943 (SCITT) |
| under these published rules | a Registration Policy, as entry 0 of the log itself | RFC 9943 s5.1.1 |
| at this time, per a third party | an RFC 3161 timestamp | RFC 3161 |

```python
from inspeximus import Inspeximus, new_receipt_keypair, verify_transparent_statement

secret, public = new_receipt_keypair()
m = Inspeximus("memory.json", receipts=True, receipt_key=secret)
m.remember("The staging database is db-7.internal", key="staging-db")

doc = m.transparent_statement(0, issuer="did:web:your-company.example")
# -> a COSE_Sign1 carrying your claim AND its inclusion proof, checkable by anyone
```

`inspeximus.transparency.TransparencyService` registers statements from other parties under a policy
it publishes inside its own log, and `python -m inspeximus.scrapi` serves that over the HTTP surface
SCITT clients speak (draft-ietf-scitt-scrapi-11), so a tool nobody here wrote can use it.

**What signing does not buy you, stated up front.** A Receipt proves inclusion in *a* log. It cannot
prove that log is the only one you showed people; that needs independent witnesses, which is why
`witnessed_head()` collects k-of-n co-signatures and treats a refusal as the alarm rather than an
error. A timestamp says a third party saw a digest at a time; full verification of the token is
delegated to `openssl ts -verify` rather than hand-rolled, because a partial CMS parser that answered
"valid" would pass tokens a real verifier rejects. And none of this is compliance: no regulation
requires a signed ledger. It is evidentiary quality for a duty to demonstrate, and it is worded that
way everywhere.

**Zero dependencies.** One file. Semantic recall is optional (`embed=your_model`); the lexical fallback
needs nothing. The MCP server, encryption and framework adapters are all opt-in extras.

---

## Works with

`langchain` · `langgraph-store` · `llamaindex` · `haystack` · `autogen` · `pydantic-ai` ·
`google-adk` · `memoryagentbench`

**13 of 13 verified against current upstream, 0 recorded broken.** Three were broken a day ago and
the list said so, which is the only reason you can believe this line: `openai-agents` was missing an
attribute the SDK type-checks on, the store's single-writer guard was firing on this process's own
threads under `langgraph-checkpointer`, and CrewAI replaced its storage protocol wholesale, so that
one needed a second class rather than a repair. The
counts are read from [`docs/integration_conformance.json`](docs/integration_conformance.json) by the
claims audit, so this line cannot drift from what the runner last measured.

A "works with" list that only names successes is a logo wall. This one tells you which adapter will
break before you build on it.

---

## How this is tested

**2,600+ tests**, and a mutation gate that is the reason to believe them: 175 seeded defects, **175
killed, 0 survived**. A test suite that passes is not evidence; a suite that catches every deliberate
break is.

**Every number on this page is registered in [docs/CLAIMS.md](docs/CLAIMS.md)**, with the exact command
that recomputes it. If one disagrees with your run, that is a bug report we want.

### Check us without trusting us

Two commands. Neither needs an API key, a service, or any data of ours.

```bash
python claims_audit.py
```

Forty seconds. It reads every number we publish across the README, the docs and the site, and
reports whether each one is registered, whether its pin still resolves, and whether a committed
command recomputes it. It ends either with a list of problems or with one line:

```
every published number is registered, every pin resolves, every command names a real file
```

The counts are deliberately not quoted here. Quoting the audit's own totals inside a file the audit
reads makes them change every time the documentation does, and the first draft of this section did
exactly that and published stale figures. Run it and read the current ones.

What the run will show you: a handful of rows marked **WITHDRAWN**. Those are figures we published
and then could not reproduce, kept in the register beside the probe that refutes them rather than
deleted. A benchmark table is a claim about a competitor; that register is a claim about us, and it
is the one we would rather you checked first.

```bash
python probes/integrity_bench_revert.py --systems inspeximus --judge local --n 5
```

Free, offline, deterministic, and it prints its own caveat that a local judge is **not** comparable
with the OpenAI-judged figures in the table above. The honest instrument and the flattering one should
not be the same instrument.

---

## Documentation

| | |
|---|---|
| **[Project site →](https://dancenitra.github.io/inspeximus/)** | **the guided tour: the benchmark, the MCP surface, the governance story** |
| [Measured vs mem0 & Graphiti](https://dancenitra.github.io/inspeximus/compare.html) | the resurrection table in full, with the control and the honest scope |
| [Claude Code setup](https://dancenitra.github.io/inspeximus/claude-code.html) | the one-line MCP install, and what each of the three hooks does |
| [The long version](docs/DEEP_DIVE.md) | every mechanism, every measurement, and the ones that failed |
| [Full API](docs/API.md) | every method, with the failure it exists to prevent |
| [Erasure & GDPR](docs/ERASURE.md) | right-to-erasure across derived summaries, with receipts |
| [EU AI Act evidence](docs/AI_ACT.md) | Article 12 logging, mapped to what the store already keeps |
| [MCP tools](MCP_LISTINGS.md) | all 73, and what each is for |
| [Claims ledger](docs/CLAIMS.md) | every published number, and the command that recomputes it |
| [core.py, mapped](docs/CORE_MAP.md) | every public method and where it lives, generated from the AST and checked in CI |
| [Runnable examples](examples/) | working scripts rather than snippets |
| [Framework adapters](docs/INTEGRATIONS.md) | which are verified against a live install, and which are recorded broken |
| [Changelog](CHANGELOG.md) | what changed and why, including what we got wrong |

---

## Who this is for

You are building an agent that runs for weeks, not minutes. It will learn something, and then that
thing will change — a config value, a policy, a person's preference, a fact. The failure that will cost
you is not the agent forgetting. It is the agent **confidently remembering the old answer**.

That is the failure this library is built around, and the only one we benchmark ourselves on. Most
demos in this space show the write. This one shows the retraction, because that is the operation
your agent will be judged by.

---

## Citing

Archived on Zenodo with a version-independent DOI — [10.5281/zenodo.21708778](https://doi.org/10.5281/zenodo.21708778).
Machine-readable metadata is in [CITATION.cff](CITATION.cff), so GitHub's "Cite this repository" button
gives you BibTeX and APA directly.

---

MIT licensed. Built by [Agora](https://github.com/DanceNitra/agora), an autonomous research
organisation that publishes its failed replications next to its successful ones.

<!-- MCP registry ownership proof. The registry reads this out of the README PUBLISHED TO PyPI and
     refuses the listing without it; it is not decoration. tests/test_mcp_registry_ownership.py guards it. -->
mcp-name: io.github.DanceNitra/inspeximus
