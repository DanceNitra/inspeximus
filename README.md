# inspeximus

<img alt="A dark archive hall of suspended glass record panels receding into haze. One panel is struck through by a line of amber light, which arcs forward to a later panel. A sealed paper receipt rests on the floor beneath it." src="https://raw.githubusercontent.com/DanceNitra/inspeximus/main/docs/assets/hero.jpg">

**Tamper-evident long-term memory for AI agents. Correct a fact once and the old value stays retired; erase a person and prove it; show an auditor what the agent knew when it acted. One zero-dependency Python file, plus an MCP server.**

<p align="center">
  <a href="https://dancenitra.github.io/inspeximus/quickstart.html">Quickstart</a> ·
  <a href="docs/DEEP_DIVE.md">Docs</a> ·
  <a href="https://dancenitra.github.io/inspeximus/compare.html">vs mem0 and Graphiti</a> ·
  <a href="https://dancenitra.github.io/inspeximus/migrate-from-mem0.html">Migrate from mem0</a> ·
  <a href="https://dancenitra.github.io/inspeximus/ai-act.html">EU AI Act and GDPR evidence</a> ·
  <a href="https://dancenitra.github.io/inspeximus/claude-code.html">Claude Code, one line</a> ·
  <a href="https://dancenitra.github.io/inspeximus/transparency/">Transparency log</a> ·
  <a href="https://pypi.org/project/inspeximus/">PyPI</a>
</p>

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
pip install "inspeximus[crypto]"
inspeximus demo          # a first result: offline, touches nothing of yours
```

```python
from inspeximus import Inspeximus

m = Inspeximus("memory.json")
m.remember("The staging database is db-3.internal", key="staging-db")
m.remember("The staging database is db-7.internal", key="staging-db")   # a correction
m.recall("which staging database")[0]["text"]   # 'The staging database is db-7.internal'
m.revert("staging-db")                            # and it is reversible, on purpose
```

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/correction-dark.svg">
  <img alt="After you correct a fact, how often does the old value come back? inspeximus 0%, Graphiti 0.x 13.3%, mem0 2.0.11 46.7%, and inspeximus with its guard disabled 100% — n=30 per system, each on its own native configuration." src="docs/assets/correction-light.svg">
</picture>

| | What you get |
|---|---|
| **A correction that holds** | `remember(key=...)` retires the old value by key. Restating the stale text does not bring it back; `revert()` does, as a recorded decision. |
| **Erasure you can prove** | `forget_subject()` removes every record about a person, leaves a signed content-free tombstone, and `erasure_certificate()` lets a third party check it with no key. |
| **What the agent knew when it acted** | A signed, hash-chained action ledger; `matches()` binds a retained transcript to its entry. |
| **When it happened** | RFC 3161 timestamps, and a check whether the authority was on the EU trusted list on that date. |
| **Evidence an auditor can read** | `inspeximus compliance` labels the evidence by article; export as a draft-sharif-agent-audit-trail-04 file. |
| **Any agent, one line** | MCP server for Claude Code, Cursor, Windsurf, Codex and Cline; adapters for LangChain, LangGraph, ADK and more. |

---

## Why inspeximus

Use it when the agent runs for days and the facts it holds will change under it, and when
somebody can later ask what it knew and what it erased. That is the whole design brief.

| You have | Reach for |
|---|---|
| An agent that keeps confidently repeating a value you already corrected | `remember(key=...)`: the correction wins, the restatement does not bring the old value back, `revert()` is a recorded decision |
| A right-to-erasure request, or an auditor asking what the agent knew when it acted | `forget_subject()` with `erasure_certificate()`; the signed action ledger with `matches()` |
| Claude Code, Cursor, Windsurf, Codex or Cline, and no memory between sessions | the MCP server, one config line |
| A framework (LangChain, LangGraph, ADK, Hermes, Haystack) and no way to prove a memory write happened | the adapters and the receipt chain |

**agno is the one that is not ours.** It ships an official integration example in its own
cookbook, [`cookbook/11_memory/integrations/inspeximus_integration.py`](https://github.com/agno-agi/agno/blob/main/cookbook/11_memory/integrations/inspeximus_integration.py),
merged in [agno#10146](https://github.com/agno-agi/agno/pull/10146) on 2026-09-20, alongside
`mem0`, `zep`, `memori` and `dakera`. Their own README describes it as *"inspeximus for
corrections that stay corrected."* We did not write that file's home and we do not maintain
it, which is exactly why it is worth listing: it is one integration a reader can check
without taking our word for anything. The same holds for [agmi](https://github.com/tech4biz-yasha/agmi),
an agent-memory integrity scorecard whose maintainer merged our three-row adapter,
[`agmi/adapters/inspeximus_rows.py`](https://github.com/tech4biz-yasha/agmi/blob/main/agmi/adapters/inspeximus_rows.py),
in [agmi#1](https://github.com/tech4biz-yasha/agmi/pull/1) after reproducing it himself.

Not the right tool when you want a hosted service with a dashboard, a knowledge graph over
documents, or the highest score on a conversational-recall benchmark. mem0, Zep and cognee lead
there, and the [comparison page](https://dancenitra.github.io/inspeximus/compare.html) says where
each of us wins and where we do not.

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

---

## EU AI Act and GDPR evidence, built in

Every write, correction, erasure and agent action leaves a signed, hash-chained record. A third
party verifies it offline with the standard library, with no API key and no reason to trust the
operator. The evidence is exportable today; the EU AI Act's high-risk duties apply from
2 December 2027 (Annex III systems) and 2 August 2028 (systems embedded in regulated products),
and GDPR Article 17 has applied since 25 May 2018.

| Duty | What inspeximus keeps | How a reader checks it |
|---|---|---|
| EU AI Act Art. 12, automatic event logging | a signed action ledger recording what the memory store held when the agent acted; `matches()` binds a retained transcript to its entry | `inspeximus actions verify`, offline |
| Art. 19, log retention | an append-only receipt chain whose head lives outside the store, so a tail cut is reported | `verify_writes()` |
| GDPR Art. 17, right to erasure | `forget_subject()` removes every record attributable to a person, including summaries that inherited it, and leaves a signed content-free tombstone | `erasure_certificate()`, checkable with no private key |
| GDPR Art. 15 and 16, access and rectification | `export_subject()`, `rectify()` with a receipt naming the actor and the reason | the export's manifest hash sits in the ledger |
| Art. 26, deployer duties | `deployer_report`, counts by default, no personal data in the report | |
| When it happened | RFC 3161 timestamps from a third party; `inspeximus timestamp qualified` says whether that authority was on the EU trusted list on that date (eIDAS Art. 41) | an offline cache of the trusted lists |
| Hand it to an auditor | export as a draft-sharif-agent-audit-trail-04 file, Ed25519 carried under `action_detail` | any conformant verifier |

`inspeximus compliance` prints the evidence labelled by article. The full mapping, with the
boundary of every row, is on the **[EU AI Act evidence page](https://dancenitra.github.io/inspeximus/ai-act.html)**
and in [docs/AI_ACT.md](docs/AI_ACT.md). Scope in one sentence: `inspeximus coverage` lists 36 of 36 in-scope provider and deployer duties
covered on a fresh store (3.12.0), stated per article; a certification is a separate act by someone else.

## The 30 seconds that matter

Every memory library can store and retrieve. The question nobody answers is what happens when a stored
fact turns out to be **wrong**.

```python
from inspeximus import Inspeximus

m = Inspeximus("correction.json")

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
do: writing `db-3` a third time, under the same
key, leaves `db-7` current. Going back is a decision you make on purpose, with
`remember(..., reaffirm=True)` — the guard cannot un-supersede on its own.
After a keyed write, read `m.last_write["blocked"]`, or pass `raise_on_block=True` to get a
`WriteBlocked` error instead of an id when a guard kept the old value.

**The limit, because it is keyed:** a statement written with *no* key is a new fact, not a
correction, and it is outside the guard. If your pipeline re-ingests a stale document without keys,
that text competes on its own merits. Both behaviours are measured in
[`probes/does_a_restatement_take_the_key_back.py`](probes/does_a_restatement_take_the_key_back.py),
which runs offline in a second.

**Opt in to authority, and a weaker source stops overwriting a stronger one.** By default the later
keyed write wins. With `Inspeximus(path, supersession="authority")` a keyed write whose
`source={"doc": ..., "authority": 0.3}` is below the current value's authority is retired on arrival
and the current value stands; the verdict is on the record and in `m.last_write`. A summary carries
its weakest parent's authority through `derived_from`, so restating a rumour at full authority does not launder
it. Authority decides only when both sides declare one, so turning it on over an existing store
changes nothing until your writers start declaring. Replayed through the store on the MemTX corpus, the
default serves the labelled belief in 278 of 318 cases and authority in 307 of 318 cases, with none going the other way.
Most of the stale-write cases are already caught by the echo guard, which retires a restated old value
whatever its authority; what authority adds is a weaker source writing a value the key never held.
The 11 it still misses are lost updates between writers of equal authority, which no authority rule
can decide, and the rule is wrong in one shape worth knowing: a fact the system seeds at full authority
can never be corrected by an agent writing below it. Both are in the docstring of `_supersede_by_key`.
On a benchmark we did not write (MemTX, 318 replayable cases), the default mode serves the labelled belief in 278 of 318 cases and `supersession="authority"` in 307 of 318.
The first number already contains the echo guard, a mechanism that shipped in 1.87.0, before anyone measured it on this corpus.

---

## When someone asks you to prove it

Turn receipts on and every write joins a hash chain. The values alone cannot tell you whether
somebody edited the file behind the library's back. The chain can.

```python
from inspeximus import Inspeximus

m = Inspeximus("receipts.json", receipts=True)
m.remember("The staging database is db-3.internal", key="staging-db")
m.remember("The staging database is db-7.internal", key="staging-db")

m.verify_writes()[0]        # nothing has been touched yet
# True

# now somebody edits the store directly, turning db-7 into db-9
from inspeximus import sqlite_store
items = sqlite_store.load("receipts.json")
before = sqlite_store.snapshot(items)
edited = next(r for r in items if "db-7" in r["text"])
edited["text"] = edited["text"].replace("db-7", "db-9")
sqlite_store.save("receipts.json", items, before)

Inspeximus("receipts.json", receipts=True).verify_writes()[1][0].split(": ", 1)[1]
# 'its TEXT or KEY no longer matches its write receipt (edited after write)'
```

A store that already holds records with no chain, or a chain that started part-way, is covered
in one call. Each record the chain does not name gets a receipt over the record as it stands
now, marked `backfill` inside its hash and carrying the Merkle root of the batch. From that call
on, an edit fails `verify_writes()` like any other; what happened before it, no later receipt
can reach.

```python
from inspeximus import Inspeximus

m = Inspeximus("older.json")            # a store written with receipts off
m.remember("The on-call rota is in the wiki", key="on-call")
m.verify_writes()[1][0].split(":", 1)[0]
# 'write receipts are DISABLED'

m.enable_receipts()["anchored_records"]
# 1
m.verify_writes()[0]
# True
```

Or from the shell: `inspeximus receipts enable --backfill`. The CHANGELOG entry for 3.0.0
carries the measurement on our own store.

A key that no longer applies is ended with `retire`, not with a placeholder write: a keyed write
replaces, so a placeholder would become the key's new active value. `retire` leaves nothing
active, keeps every value in `history(key)` with the reason, and declares itself in the receipt
chain.

```python
from inspeximus import Inspeximus

m = Inspeximus("rota.json")
m.remember("The on-call rota is in the wiki", key="on-call")
m.retire("on-call", "the rota moved to the pager tool")
m.current("on-call")
# None
m.history("on-call")[0]["reason"]
# 'the rota moved to the pager tool'
```

### Many processes, one store

The row writer appends a content-free row to `memory_events` inside the transaction that writes
the rows, so another process tails the table by `seq` and sees a commit on its next call, with
no reload and no broker. `current(key)` answers repeat reads from an L1 keyed by (tenant, agent,
key), so a hit primed by one agent is never served to another.

```python
from inspeximus import Inspeximus

lead = Inspeximus("crew.json")
worker = Inspeximus("crew.json")
tip = worker.events_tip()

lead.remember("The plan is: ship on Friday", key="plan")
lead.publish_event("plan.updated", {"to": "worker"}, agent_id="lead")

[e["type"] for e in worker.poll_events(since_seq=tip)]
# ['record.added', 'plan.updated']
worker.current("plan")["text"]
# 'The plan is: ship on Friday'
```

### What the agent did, bound to what it knew

An audit-trail tool signs the agent's actions. The action ledger does that too, and binds each action
to the memory the agent held at that moment: the store's state digest and the ids the last recall
returned. A fact corrected between two actions gives the two actions two different digests, so a
reader can tell that the first ran on the old value and the second on the new one, from the chain,
not from anyone's account of it.

```python
from inspeximus import Inspeximus
from inspeximus.actions import ActionLedger

m = Inspeximus("deploys.json", receipts=True)
m.remember("The staging database is db-3.internal", key="staging-db")
led = ActionLedger(m, actor="deploy-agent")

target = m.recall("staging database")[0]["text"]
with led.action("tool:deploy", inputs={"target": target}) as a:
    a.output({"deployed_to": target})

m.remember("The staging database is db-7.internal", key="staging-db")   # the correction
target = m.recall("staging database")[0]["text"]
with led.action("tool:deploy", inputs={"target": target}) as a:
    a.output({"deployed_to": target})

led.what_it_knew(0)["recalled_now"][0]["current"]["status"]   # 'superseded'
led.what_it_knew(1)["recalled_now"][0]["current"]["status"]   # 'active'
led.verify()                                                   # (True, [])
```

Content-free by default: inputs and outputs are stored as salted SHA-256 digests. The operator who
kept the transcript can still ask `led.matches(seq, inputs=prompt, output=answer)` and get a yes or no
on whether that is what the model was given and what came back; one changed character is a no, and
the check needs the salt file, so nobody else can run it. Signed with the store's
receipt key when it has one. `inspeximus actions verify` checks the file offline; with the store
present it also checks that every entry's `last_receipt` still exists in the memory chain, so a
rewritten memory history is caught from the action side. `INSPEXIMUS_ACTIONS=1` makes the MCP
server record every tool call, and `inspeximus.integrations.langchain.InspeximusActionCallback`
records LangChain tool and model calls. Probe: `probes/what_the_agent_knew_when_it_acted.py`, three
tamper controls, each fails.

Two more event kinds share the chain. `led.oversight("override", "ops-lead", refers_to=3, reason=...)`
records a human decision about an action, with the person or role who made it (the Act's Art. 14 and
GDPR Art. 22 records); the reference must resolve and the verifier re-checks it. `led.disclosure("s1",
"You are chatting with an AI assistant.", channel="web")` records an Art. 50 disclosure per session.
`inspeximus.subject_rights.export_subject(m, "crm/alice", ledger=led)` answers a GDPR Art. 15 access
request with every record whose source resolves to the subject, exactly as `forget_subject` resolves
it, and `rectify(m, key=..., text=..., actor=..., reason=..., ledger=led)` is an Art. 16 correction
with a receipt naming who asked. `led.incident("...", "serious", "dpo", refers_to=[3, 4])` opens an Art. 73
record with the 15-day clock from the moment of awareness, and `incident_report(seq)` is the report
skeleton. `inspeximus compliance` reads all of it from the ledger, through its verifier,
into 22 article-labelled controls.

Two documents are generated from the same evidence. `inspeximus technical-documentation --out
annex_iv.md` is the Annex IV skeleton (Art. 11) with the sections evidence can fill written from the
store and ledger, the Art. 13(3)(f) instructions for use included, and 24 provider fields marked
OPERATOR INPUT REQUIRED. `inspeximus deployer-report --out deployer.md` is the deployer's side
(Art. 26): oversight recorded, incidents and the Art. 73 clock, the age of the oldest kept log entry
against the six-month floor (reported as not yet testable until the log is that old), disclosures,
plus a GDPR Art. 35(7) DPIA appendix and an Art. 27(1) FRIA appendix that cross-references it. Both
name every field they could not fill. `inspeximus registration-export --section A` writes the Annex VIII
fields for the EU database (Art. 49) the same way.

A ledger kept for years is rotated rather than cut. `inspeximus actions archive --keep-days 400` moves the older
entries into an archive file beside the ledger and starts the live file with a signed checkpoint naming
the archive, its hash and the archived tail; the chain is unbroken, `actions verify` follows the
checkpoint into the archive, and the live file alone reports the archived range as not verified rather
than passing over it. `inspeximus actions attest --policy-days 183 --actor dpo` appends a signed
statement of the oldest entry the ledger accounts for and whether the six-month floor of Art. 19 and
Art. 26(6) has been observed. `inspeximus actions timestamp --url https://freetsa.org/tsr` asks an
RFC 3161 authority to stamp the tail and chains the token in, so an auditor has a third party's time
for everything before it, verifiable with `openssl ts -verify`.

`inspeximus actions timeline --session s1`
reconstructs one workflow from the chain, content-free: each step with the memory digest the agent held,
the model, the actor, and the oversight or incident that refers to it. `inspeximus actions export-trail`
writes the ledger in the IETF draft-sharif-agent-audit-trail-04 format, hash-chained per RFC 8785, for
tooling that reads that format; `--verify` checks any such file. `inspeximus actions lifecycle
substantial_modification --actor cto --note "..."` records the Art. 3(23) change that ends a grandfathered
system's Art. 111(2) exemption, and `lifecycle decommission --disposition erased` records what happened to
the memory at end of life.

Memory can be partitioned per agent and per process, the shape the CNIL's 2026 note on agentic AI asks
for: `inspeximus partitions open triage-2026-09-16 --kind context --max-age-days 1 --max-records 200`
opens a scope whose writes are tagged, `partitions sweep` applies every open partition's expiry and cap
with tombstones, and `partitions close NAME --actor` ends the process (a context partition erases its
records at close). `partitions report` shows what is past expiry now and how much memory sits outside
any partition.

### Where the store is written

You do not pick a storage format. A new store is written as rows, and an existing JSON store is
converted the first time this version opens it: the conversion re-reads what it wrote and refuses
unless the record count and the id order both survive, and it leaves the original beside the store as
`memory.json.pre-rows.bak`. Encrypted stores stay a single encrypted blob, because at-rest encryption
covers the whole file.

Rows are there because every write used to rewrite the whole file, and because a rewrite cannot merge
a concurrent writer's records the way a row write can.

One persisted write, both formats, three independent trials of thirty writes each
(`probes/one_write_two_formats_across_store_sizes.py`):

| records in the store | whole file | one row | |
|---|---|---|---|
| 1,000 | 0.0075 s | 0.0071 s | rows about 1.1x faster |
| 10,000 | 0.0818 s | 0.0422 s | rows about 1.9x faster |
| 30,000 | 0.2334 s | 0.1292 s | rows about 1.8x faster |

The gap is a function of file size: rewriting a file gets more expensive as the file grows and
writing one row does not, so the gain arrives with the records. Take the smallest row as the least
reliable one. At a thousand records the two are close enough that separate runs of this probe have
come out both ways, and in the run behind this table one of the three trials still did, which is why
the probe reports every trial rather than an average and says so when the direction is not stable. The table above is generated from the receipt the probe writes
(`tools/sync_store_format_table.py`), so it is what one run measured rather than what we remember.

Under concurrent writers, a caller that drops the store's own StoreChangedOnDisk instead of retrying landed 199 of 384 records in its worst trial at 48 processes, while the row store landed every record in 4 of 4 trials at every width tested. That gap belongs to the caller and not to the format: given the retry the error prescribes, the whole-file store keeps up (`probes/what_a_concurrent_writer_is_told_against_what_the_store_keeps.py`).
See `probes/twelve_writers_and_the_one_that_stopped_writing.py`. Both probes re-measure the
whole-file baseline on the machine they run on rather than quoting ours, so a slower machine reports
a smaller gap instead of a false one.

Two things to know before you upgrade:

- **A store written by this version cannot be read by 2.26.1 or earlier.** Those versions decode the
  file as UTF-8 and raise `UnicodeDecodeError`. To go back, rename `memory.json.pre-rows.bak` over
  the store and pin the older release.
- **The rollback copy is deleted by the first erasure.** `forget`, `forget_subject` and `forget_pii`
  remove it, because a copy this library made without being asked is not somewhere personal data gets
  to survive a deletion request. `erasure_certificate()` reports what happened to that file by name,
  so the end of your rollback window is recorded rather than silent. To keep the copy, set
  `INSPEXIMUS_KEEP_CONVERSION_BACKUP=1`: the certificate then declares the backup as data the erasure
  did not reach, which is the trade you are making.

`INSPEXIMUS_STORE_FORMAT=json` keeps the old format, for a store that other tooling reads directly.

`provenance(key=...)` answers the rest in one call: every value the key has held and the policy that
retired each one, where the current value came from including taint inherited through summaries,
whether the record still matches what its receipt committed to, and a `limits` field naming what none
of it proves. Erasure works the same way. `forget_subject()` hard-deletes every memory attributable
to a person, including the summaries that inherited it through lineage, and leaves a signed
content-free tombstone, so a later reader can tell a deliberate erasure from tampering.
`erasure_certificate()` makes that checkable by a third party with no private key and no reason to
trust us.

`inspeximus compliance` prints the same evidence labelled by article, with its own scope attached:
the duties `inspeximus coverage` lists, and not a certification.

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

**Scope.** The rows above are the duties `inspeximus coverage` lists, stated per article. The Act's high-risk
obligations apply from 2 December 2027 for standalone Annex III systems and 2 August 2028 for those
embedded in regulated products; the evidence they will ask for (Art. 12 event logging, Art. 19
retention, Art. 15 accuracy and robustness) is what the store already keeps and exports.
[docs/AI_ACT.md](docs/AI_ACT.md) maps each duty onto the store and marks where the mapping stops.

### A log the reader checks without asking you for anything

Everything above holds while you are honest. None of it stops you keeping two histories and showing
each reader the one that suits, because you serve the answer and you also wrote it.

So `tools/publish_static_log.py` writes the log as ordinary files instead: the head, the COSE key
set, every leaf hash, every receipt, the text of every entry, and a `verify.py` that runs on the
standard library alone. A reader downloads four files and checks the Merkle root against the leaves
themselves. This is where certificate transparency went, not a shortcut around it: C2SP's
static-ct-api serves a log as cacheable files because that is cheaper to run and harder to equivocate
with than an API.

Ours is live at
[dancenitra.github.io/inspeximus/transparency](https://dancenitra.github.io/inspeximus/transparency/).
Each entry is one number this project publishes, with the sentence it appears in and the command that
reproduces it.

WHETHER IT HOLDS ALL OF THEM IS A THING YOU CHECK, NOT A THING WE ASSERT, and this paragraph used to
assert it. `python tools/seed_claims_log.py --log transparency/claims.log --check` compares the
registry against the log and names anything not yet recorded; it needs no key, and CI runs it on
every push, so a gap is visible to you at the same moment it is visible to us. There is a gap now:
four claims are registered and unlogged, because appending needs the signing key and the key is not
where the seeding happens. A log that is behind and says so is the point of the exercise; a log
described as complete while it is behind is the failure it exists to prevent.

What a static log cannot do, said here rather than discovered later: nothing accepts a registration
over HTTP. Writing happens where the signing key is. For a live endpoint, `scrapi.py` serves
draft-ietf-scitt-scrapi-11 and `deploy/` has the container images.

### The witness is the part you cannot run yourself

A log tells you it is internally consistent. It cannot tell you it is the same log somebody else was
shown, and no amount of signing by the operator fixes that. Only a party who REMEMBERS a previous
head can catch a rewrite, and only if that memory lives somewhere the operator cannot reach.

`inspeximus witness watch` is that party. It fetches a log it does not operate, recomputes the
root from the leaves rather than reading it out of the head, and compares against the head it last
accepted by rebuilding that head from the leaves published now. Verdicts are EXTENDS, FIRST_CONTACT
(which says out loud that it proves nothing yet), FORK, ROLLBACK, and MALFORMED for a log that
contradicts itself. A refusal does not update its memory, because a witness that forgets what it just
caught reports EXTENDS on the rewritten log next time.

Two commands are the whole setup, and the first run commits you to nothing:

```bash
pip install inspeximus
inspeximus witness watch --url https://dancenitra.github.io/inspeximus-log/log --state witness.json
```

`deploy/witness-template.yml` runs the same command daily from any public repository for nothing.
Running one against our log is the most useful thing an outsider can do here, and it commits you to nothing: you are not
vouching that any entry is true, only recording whether the history shown to you today extends the
one shown to you before.

### Checking the Bitcoin anchor, offline

Each published head is stamped with OpenTimestamps, and the receipt is a `.ots` file. The usual way
to check one is the `ots` command, which pulls in python-bitcoinlib; on Windows that import reaches
for libssl through ctypes and crashes before reading a byte of the proof. So the check is built in.

```bash
inspeximus ots upgrade <the .ots receipt>          # which block covers it? asks the calendars
inspeximus ots verify <the stamped file> --upgrade     --block-header <the 80-byte header as hex>     # ANCHORED, or MISMATCH
```

You supply the block header, from your own node or from any explorer. That is what makes it
offline: you choose where the block came from, and nothing about your data leaves the machine.
`--upgrade` is the only part that uses the network, and it asks a calendar about a digest the
calendar already holds.

Exit codes: 0 ANCHORED, 1 MISMATCH, 3 PENDING or INCOMPLETE. A proof with only calendar promises is
PENDING, which is neither an error nor a pass, so it has its own code rather than being folded into
either. An anchored proof says these exact bytes existed before that block was mined. It says
nothing about whether anything in them is true.

If the verdict is MISMATCH and your file came out of a git checkout on Windows, read the line about
line endings that the verifier prints: the receipts are stamped over LF bytes, and a converted copy
differs from them without anybody having tampered with anything.

### The key you check the log with, published twice

To verify our hosted log, you need our verification key. Fetching it from the host you are checking
means asking the host how to check the host: an operator who can rewrite the log can rewrite the key
beside it, and every signature still verifies. So the key is published here as well.

Both copies now live on GitHub under one account: this README, and the log's public mirror, which
publishes a snapshot only after a workflow verifies it against the key pinned in that repository.
That makes the two copies a check against a quiet split, not against GitHub or against us. The
reference that is independent of both is the raw public key,
`9fb780dd72894867c6dac8e140cc78d755617147d20cfd26bfe557190f65ac49`, which is also held on paper
off-line.

<!-- checkpoint-vkey:begin -->
```
92.5.74.17.sslip.io/log+41dfe27a+AZ+3gN1yiUhnxtrI4UDMeNdVYXFH0gz9Jr/lVxkPZaxJ
```
<!-- checkpoint-vkey:end -->

SHA-256 of that line: `1017ff229193fa867e1f73758df24e440ebfad1710e1af49e296a5fe4f00783c`

The same line is served at
[`/log/checkpoint.vkey`](https://dancenitra.github.io/inspeximus-log/log/checkpoint.vkey). **The two must be
byte-identical.** If they differ, do not trust either one, and open an issue. `41dfe27a` is the
four-byte key id from [c2sp.org/signed-note](https://c2sp.org/signed-note), which selects which key
to try and is not a security boundary.

```bash
diff <(curl -s https://dancenitra.github.io/inspeximus-log/log/checkpoint.vkey)      <(curl -s https://raw.githubusercontent.com/DanceNitra/inspeximus/main/README.md |
       sed -n '/checkpoint-vkey:begin/,/checkpoint-vkey:end/p' | sed -n '3p')
```

`tools/check_published_key.py` runs that comparison, and CI runs it daily, so a rotation cannot
split the two copies quietly. Two copies raise the cost of a silent swap from one write to two on
two systems. They do not make us trustworthy, because both copies are ours. Independence comes from
the witness, which remembers a head we cannot reach.

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

## Use it in Claude Code (one line)

From inside Claude Code, no pip, no config file:

```
/plugin marketplace add DanceNitra/inspeximus
/plugin install inspeximus@inspeximus
```

Or from a shell, after `pip install inspeximus`:

```bash
inspeximus install --ide claude     # also: cursor, windsurf, codex, cline
```

Both wire an MCP server with **133 tools** and the same hooks. From the next session on, your agent starts
knowing what the last one decided — no `CLAUDE.md` editing, no re-explaining:

- **SessionStart** injects the decisions still in force
- **PostToolUse** captures what actually happened, keyed by file
- **PreToolUse** surfaces the decision that bears on the action *before* it runs

Verified with the Claude Code CLI on a clean profile, both routes, across two sessions. The Code tab
of the Claude desktop app is not verified.

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

```bash
pip install "inspeximus[crypto]"
```

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

**Zero dependencies.** One file for the core: copy `inspeximus/core.py` anywhere and it imports and
runs with nothing installed. Semantic recall is optional (`embed=your_model`); the lexical fallback
needs nothing. The MCP server, encryption and the framework adapters are separate modules, all opt-in.

---

## Works with

`langchain` · `langgraph-store` · `llamaindex` · `haystack` · `autogen` · `pydantic-ai` ·
`google-adk` · `memoryagentbench` · `hermes-agent`

For Hermes Agent, install inspeximus into the venv Hermes runs from, not into your shell's Python
([how](docs/INTEGRATIONS.md#memory-provider-for-hermes-agent-inspeximusmemoryprovider-2272)). Install
Hermes with its own installer: the PyPI package `hermes-agent` is 0.19.0, which never loads the
provider.

**14 of 14 verified against current upstream, 0 recorded broken.** Three were broken a day ago and
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

The five at-rest attacks of the [agmi](https://github.com/tech4biz-yasha/agmi) conformance suite (tamper,
truncate, delete a middle entry, reorder, forge), run against the SQLite file behind a store the way its
attacker does: with receipts on and a key, **5 of 5 detected**, each with a reason that names it. When
the attacker also holds the receipts sidecar, which write access to the store's directory gives them,
and removes the receipt of every record they delete, still every one of them: after every receipt the store writes
the chain's head to the user's config home, outside the store's directory, and `verify_writes()` reports
a chain shorter than that head. An attacker who also holds the config home removes the head, and then
**4 of 5 detected**: a middle deletion still breaks the signed chain, a tail truncation does not, and
that case needs an `anchor()` held off the machine plus `verify_consistency()`. Detection is
`verify_writes()`, the audit call; `recall()` serves the altered record either way. With receipts off, the default, the verifier refuses to vouch for the store at all,
touched or not, which is scored as unverifiable rather than as detection. Probe:
`probes/five_at_rest_attacks_on_the_store_with_receipts_off_and_on.py`.

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
| [Erasure & GDPR](docs/ERASURE.md) | right-to-erasure across derived summaries, with receipts; the [erasure page](https://dancenitra.github.io/inspeximus/erasure.html) shows a real run end to end |
| [Migrate from mem0](https://dancenitra.github.io/inspeximus/migrate-from-mem0.html) | `inspeximus import-mem0 export.json`: one record per memory with the user as its subject and mem0's timestamp as the event time, safe to run twice; the API mapping and what the import cannot recover |
| [Audit trail page](https://dancenitra.github.io/inspeximus/audit-trail.html) | what the agent knew when it acted: a signed ledger entry, a transcript match, and the IETF draft export, as a real run |
| [EU AI Act evidence page](https://dancenitra.github.io/inspeximus/ai-act.html) | Article 12 logging and Article 17 erasure, mapped to what the store already keeps; the mapping's text is [docs/AI_ACT.md](docs/AI_ACT.md) |
| [MCP tools](MCP_LISTINGS.md) | all 133, and what each is for |
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

## The name

The name is from medieval charters. A king, bishop, abbot or town council opened with *inspeximus*,
"we have inspected", reciting an older document in full to record that they had examined it, usually
confirming it, and sealing the result so a later reader could check. It attested that the copy
faithfully matched the original, not that the original was true. Same guarantee here, and
`provenance()` says so in a `limits` field rather than leaving you to find out.

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
