<div align="center">

<img src="https://raw.githubusercontent.com/DanceNitra/inspeximus/main/assets_readme/hero_banner.png" alt="inspeximus — a glowing digital memory layer resting on a robust machined-steel base" width="800">

# inspeximus — a zero-dependency Python agent-memory library

**Ask any memory where it came from — and check the answer.** `provenance(key)` returns the declared
source, the lineage it inherited, the evidence grade, every value the fact has held and which policy
retired each one, and whether the record still matches what its write receipt committed to. Delete the
fact and `erasure_certificate()` is the same answer for the deletion — a receipt an auditor verifies
offline, without the live store and without trusting us. Because a delete that returns success is not a
delete: we measured one that left the data recoverable in **five of six** places the application had put it
(`python probes/forget_verification_bench.py` — soft delete scores 0.17 and names the five leaking stores; the wired hard delete scores 1.00).

*"We have inspected" — the medieval charter that recites an earlier one word for word and
attests it unaltered. The self-correcting memory layer for AI agents.*

*Correct a fact once and it stays corrected: the store serves the new value and refuses to let the old
one creep back — deterministically, with no LLM on the write path, from a single zero-dependency file.
`revert(key)` puts a correction back on command, which is the cheapest proof that what sits underneath is
a real state model and not a log. Extracted from an autonomous research OS that has run it daily over a private ~10,000-note vault (our own deployment — you cannot re-run that one; every number you CAN re-run
is listed in [docs/CLAIMS.md](CLAIMS.md) with its command).*

`pip install inspeximus` → `import inspeximus` · [PyPI](https://pypi.org/project/inspeximus/) · [Hugging Face](https://huggingface.co/Danchi17/inspeximus) · [DOI](https://doi.org/10.5281/zenodo.21708778) · [Homepage](https://dancenitra.github.io/inspeximus/) · MIT · v2.22.0

[![audit](https://github.com/DanceNitra/inspeximus/actions/workflows/audit.yml/badge.svg)](https://github.com/DanceNitra/inspeximus/actions/workflows/audit.yml)
[![Star on GitHub](https://img.shields.io/github/stars/DanceNitra/inspeximus?style=social)](https://github.com/DanceNitra/inspeximus)

*If inspeximus's saved you some time, a ⭐ would mean a lot — it's how other people find it. Thank you!*

<img src="assets_readme/correction_demo.svg" alt="A fact is corrected; later the old value is restated, yet recall still serves the correction — the restatement lands retired via echo_guard" width="720">

Built by **[Rastislav Drahoš](https://github.com/DanceNitra)** — extracted from [Agora](https://github.com/DanceNitra/agora), an autonomous research OS that runs it daily.

</div>

---

## Install into Claude Code in one line

```
/plugin marketplace add DanceNitra/inspeximus
/plugin install inspeximus@inspeximus
```

That registers this repository as a plugin marketplace and installs the MCP server, which then starts
with `uvx --from "inspeximus[mcp]" inspeximus-mcp` and keeps its store in `.inspeximus/memory.json` inside the
project. Nothing to configure by hand, and nothing to install globally.

Prefer the manual route? `pip install "inspeximus[mcp]"` and point your client at `inspeximus-mcp` — the
extra matters, because the core library is deliberately zero-dependency and the MCP server is the one
piece that needs a dependency.

## What you install, at a glance
### Your next session starts knowing what this one decided — with no LLM

`python -m inspeximus.claude_code --install` also wires the cross-session loop. On `SessionEnd` inspeximus
writes a **ledger diff** of what the session established — which keys changed value, which decisions were
recorded, what was erased, what is still open — read straight off its own supersession ledger. On
`SessionStart` it injects that, size-bounded and ranked. No transcript is sent anywhere, so it is instant,
free, and *byte-reproducible*: two stores replaying the same event log render an identical digest.

Measured on an 8-session, 2,606-record fixture (`probes/session_digest_multisession.py`): **1.000** of a
session's conclusions reach the next session, **1.0000** of below-threshold items stay out — and with the
salience bar removed that rejection collapses to **0.2213**, which is how you know the bar is doing the
work and the digest is not just the log again. Shell commands and file states are capped below the bar no
matter how much value they accrue. `close_session` costs 7 ms at that size.

Two things a frozen summary cannot do: the injected block is **re-resolved against the live store**, so a
decision reversed in a later session is replaced by the current one and an erased record leaves the
context too. Off switch: `INSPEXIMUS_SESSION_DIGEST=0`. Details in [docs/API.md](API.md).

## What you install, and what it does that others don't

A **mem0 alternative** built the opposite way: deterministic, not an LLM extracting facts on every write — a
memory that can say where a fact came from, keep a correction corrected, and show with an offline-verifiable
receipt what it erased. The right-hand column is what we found when we read the other libraries' current
source and docs ([the scan](AI_ACT.md)); it is a scan, not a proof of a universal negative, and we
correct it when someone shows us better.

| | **inspeximus** | what we found in mem0 / cognee / Zep&nbsp;·&nbsp;Graphiti |
|---|---|---|
| **Provenance — where did this fact come from?** | `provenance(key)`: declared source, inherited lineage, evidence grade, every value held + which policy retired it, and whether the record still matches its write receipt — **one call, no model** | bitemporal/graph provenance (Zep·Graphiti), a per-record history log (mem0); we found no single call that also checks the record against a receipt |
| **Why did recall return *this*?** | `why_recalled(id, query)` — the ranking decomposed, deterministically | *not assessed — the scan did not look for this* |
| **Correction — a fact changes, and you can audit what replaced what** | keyed supersession serves the current value and `history()` / `supersession_report()` show which value replaced which and under which policy; a restated stale value can't creep back (`echo_guard`) | LLM re-extract / bitemporal invalidation |
| **Verifiable erasure** | signed, content-free tombstone + `erasure_certificate` an auditor verifies **offline** | `delete()` — we found no receipt to verify |
| **Did the bytes actually go?** | **`inspeximus residue` — checks ANY store, exits non-zero on residue** | not offered in what we read |
| **Tamper-evident record-keeping** | hash-linked receipts + a signed anchor + **content bound across time** | SOC 2 audit logs (Zep — organisational, not cryptographic) |
| **EU AI Act / GDPR evidence** | **`inspeximus compliance` overlay + audit bundle** | not framed |
| **Revert — the proof the state model is real** | `revert(key)` rolls a corrected fact back to its predecessor: deterministic, no model call, no similarity guess | varies — see the note below |
| **Write path** | deterministic — **no LLM** | LLM extraction on every `add()` |
| **Dependencies** | **zero required — one pure-Python file's worth of core** | server / DB / vector / graph stack |
| **MCP server** | yes (one-command install) | varies |

On a scan of nine products, the only agent-memory library that ships verifiable erasure **and** tamper-evident
record-keeping **with zero required dependencies** — every qualifier load-bearing, and the scan is what we read
on those dates, not a proof about the field ([details](AI_ACT.md); honest: Zep has a real SOC 2/HIPAA surface,
just not verifiable erasure or AI-Act framing). The cross-system integrity numbers behind that — run by the same
harness through every system's native config, published whichever way they fall — are in
[`probes/INTEGRITY_BENCHMARK.md`](../probes/INTEGRITY_BENCHMARK.md), including the cell where inspeximus does *not*
win. (This sentence used to link to a section of this README that had been moved out; the link went nowhere and
the number it advertised was no longer on the page.)

**On the revert row specifically.** We used to write that no other agent-memory library
exposed a revert; our own pre-publication gate falsified it. So the claim here is not that others lack a
capability — it is that inspeximus does these **deterministically, with no model on the write path, from a
zero-dependency package, and verifiably without trusting us**. Every one of those four is something you can
check in a minute, which is the only reason to believe any of it. Where we describe another system we say
what we *found* when we read it, on a dated scan, and we would rather be corrected than be right by default.

## Where did this memory come from — and can you check?

This is the question people actually bring to an agent-memory layer, so it is the first thing this page
shows you how to answer. You rarely reach for provenance going forwards. You reach for it backwards — when
someone says *"delete that"*, and the fact is back next session because a summary that three other episodes
still support had absorbed it. `provenance()` answers that in one call, for one fact, in the order an
auditor asks:

```python
m.provenance(key="billing-api::auth")     # or provenance(id="…") for one record
```

```
fact      billing-api::auth
  now       oauth2  [active]
  source    adr-014  (not attested)
  lineage   primary observation
  trust     claimed
  history   2 value(s), 1 retired
              api-keys  ->  retired by keyed_lww
              oauth2  ->  active
  integrity content matches; attribution matches; chain ok; unsigned the write receipt
```

Same answer from the shell (`inspeximus provenance billing-api::auth`, `--json` for the full object) and over
MCP (`provenance`). It assembles what the store already carries, rather than adding a new claim layer:

| field | what it answers | built from |
|---|---|---|
| `origin` | the declared source; the taint it **inherited transitively** through summarization, so a derived note is never mistaken for a first-hand one; whether an **origin attestation** bound it to a verified key; the acting user/agent/session | `source` + `derived_from` + `attested_key` |
| `trust` | the evidence grade — `claimed` → `corroborated` → `verified` → `settled`, earned from corroboration and external ratifications and **never settable by the writer** | `grade()` |
| `timeline` | every value the fact has held, its validity interval, and **which policy retired each one** (`keyed_lww`, `echo_guard`, `state_toggle`, …) | `history()` |
| `integrity` | whether the record still matches the content **and sources** its write receipt committed to — so an out-of-band edit that rewrites a record's source **without also rewriting the receipts sidecar** is caught — plus the current anchor to pin the answer against | `verify_attribution()` + `anchor()` |

**What it does not prove**, returned in a `limits` field so a caller rendering this cannot quietly drop it:
this is tamper-**evident**, not **correct** — a source that was already wrong when it was written is
committed faithfully and nothing here can tell. And unsigned (the default), the receipt chain only catches
an editor who cannot *also* rewrite the `.receipts` sidecar — which sits next to the store, so an attacker
with that much file access simply recomputes it and passes. We ship that as a failing negative control, not
as prose (`test_provenance.py::test_an_attacker_who_rewrites_the_sidecar_too_is_NOT_caught`). Pass
`receipt_key=` with the key off the write path, or have `anchor()` witnessed externally, for the property to
mean anything against someone who owns the store. Note too that `attribution_matches_receipt` is a *change*
detector: a legitimate re-derivation upstream can flip it with no attacker present.

None of the machinery here is new. Binding the actor and attribution into a tamper-evident provenance chain
so retroactive relabeling is detectable is Hasan, Sion & Winslett, *The Case of the Fake Picasso: Preventing History
Forgery with Secure Provenance* (USENIX FAST 2009; journal version ACM TOS 5(4), 2009); answering provenance facets from one call is standard in provenance-aware
databases (Perm, ProvSQL, ProQL); signed, Merkle-logged lineage for LLM agent memory specifically is
MemLineage ([arXiv:2605.14421](https://arxiv.org/abs/2605.14421)), which inspeximus's lineage auto-stamping
already credits. What is ours is the packaging: all of it with zero third-party dependencies, on by default, with
the limits attached to the answer.

### The two neighbouring questions, same shape

**"Why did recall return *this* record?"** — `why_recalled(id, query)` decomposes the ranking that produced a
hit into its terms, deterministically, with no model in the loop. It is the retrieval-side twin of
`provenance`: one asks where the content came from, the other asks why it surfaced.

**"What replaced what, and under which rule?"** — this is the auditable half of correction, and it is a
separate question from whether the correction was *right*. `history(key)` lists every value the fact has
held, oldest to newest; `supersession_report()` does it across the store; each retirement names the policy
that caused it (`keyed_lww`, `echo_guard`, `state_toggle`, …). `verify_attribution()` then asks whether the
records still match what their write receipts committed to. None of the four calls a model.

`revert(key)` sits at the end of that chain, and it is deliberately not the headline: it is the cheapest
demonstration that the timeline above is a real state model — you can move along it in both directions —
rather than an append-only log with a nice renderer.

## Don't take our word for it — three commands that answer about YOUR stack

Every claim above is a claim. These are checks you run yourself, and two of the three work on stores we
did not write.

**1. Did the bytes actually go?** `delete()` returning success is not the same as the value being gone
from disk. Point this at any directory — a vector database, a sqlite history, a JSONL trace, another
library's data dir:

```
inspeximus residue --root ./deployment --value alice@example.com
#   PLAIN        trace.jsonl              fp=337961f64779
#   LIVE         v.sqlite [t.x x1]        fp=337961f64779
#   RESULT: residue found                 → exit 1
```

It separates three verdicts, and the distinction is the whole point: **LIVE** (a table still holds it — the
system retained it), **UNRECLAIMED** (in the bytes but in no row — the storage engine has not reclaimed the
page; run `VACUUM`, and this is *not* a vendor defect), **PLAIN** (a log or backup still has it). It never
echoes the value you gave it — findings carry a fingerprint, because a tool that hunts a secret and then
prints it into your terminal is itself the leak. It exits non-zero, so it works as a CI or DSAR gate.

We ran it against **mem0 2.0.11** with a local qdrant while building it: after its documented `delete()`
and `reset()`, **no live row anywhere held the value** — only unreclaimed sqlite pages, which is a storage
property, not a defect. We went looking for a difference and found an honest null. That is why this ships
as a measuring instrument and not as an argument.

**2. Prove the erasure at the only moment you can.** After `forget()` the value is gone with the row, so it
can never be searched for afterwards — the check has to run *during* the erasure:

```python
res = store.forget(ids=[rid], request_id="DSAR-1", verify_residue_in="./deployment")
res["residue"]["ok"]      # False if it survived anywhere under that root
```

**3. Bind an audit bundle to CONTENT, not just to a chain.** A hash chain proves nobody rewrote the past.
It does not prove the store is serving what it committed to — the bundle is content-free by design, so a
clean chain over substituted content verifies fine:

```python
from inspeximus.audit_bundle import build_bundle, bind_content
witnessed = build_bundle(store)                      # the auditor takes this away
bind_content(witnessed, list(store.items))["ok"]     # False if the content no longer matches
store.explain_growth(prior_anchor, writes=2)         # and did the chain grow by what you did?
```

`bind_content` compares against the **earliest** receipt for each record, not the latest — the latest is
precisely what an amendment would have rewritten. `explain_growth` supplies the denominator only your
application has. Prior art we build on rather than reinvent: [RFC 6962](https://www.rfc-editor.org/rfc/rfc6962)
(a log proves inclusion, never validity) and Schneier & Kelsey, *Secure Audit Logs to Support Computer
Forensics* (USENIX Security 1998) — post-compromise entries are attacker-chosen by construction. The
contribution isn't the principle; it's that the check is a function you can run.

## New — the EU AI Act compliance-evidence layer for agent memory

When the AI Act's high-risk obligations start applying (**2 Dec 2027** for standalone Annex III systems,
**2 Aug 2028** for Annex I product-embedded ones — deferred from 2 Aug 2026 by Regulation (EU) 2026/1744, the
[Digital Omnibus on AI](https://eur-lex.europa.eu/eli/reg/2026/1744/oj/eng), in force 27 Jul 2026), a provider
of one -- and, for log-keeping, its deployer under Art. 26(6) -- has to be able to *show* things about what its
agent **remembers**. The Act requires automatic event
logging over the system's lifetime (Art. 12) and log retention (Art. 19); accuracy, robustness and
cybersecurity including resilience to attempts to alter performance (Art. 15); and GDPR Art. 17 gives data
subjects a right to erasure. **None of those articles say memory, provenance or tamper-evidence** — mapping
them onto agent memory is our reading, not the text, and this is not legal advice.

Of the agent-memory libraries we read (mem0, Zep/Graphiti, Cognee, Letta, LangMem — source at `main`,
24 Jul 2026), we did not find verifiable erasure with a receipt or tamper-evident record-keeping in any of
them; inspeximus ships both, as a drop-in overlay rather than a rebuild. That is a statement about what our
scan found, not about what exists: we have not surveyed the whole field, smaller projects exist that we have
not read, and a library may well have added either since. Tell us if we missed one and we will correct this.

```bash
inspeximus compliance --out report.html     # article-labelled evidence, live counts from your store
inspeximus audit-build --out bundle.json    # hand an auditor the bundle; they verify it offline (audit-verify)
```

Evidence, not certification; the memory slice only, and the obligations bind the provider/deployer, not the library.
→ **[docs/AI_ACT.md](AI_ACT.md)**

A delete that returns success tells you the call ran, not that the data left. Three commands from a
deletion obligation to a receipt you can re-verify without trusting us — with the honest scope of what a
residue scan can and cannot see: → **[docs/ERASURE.md](ERASURE.md)**

## Every claim below is checked by a script you can run

```bash
python claims_audit.py          # downloads the published wheel from PyPI and audits THAT
python claims_audit.py --numbers  # audits every NUMBER on the reader-facing surface (offline)
```

It fetches the released artifact, prints its sha256, and runs each claim on it — never on the working
tree. The write-path claim is enforced rather than asserted: sockets are disabled for the duration, so a
write that reached for a model would fail the check instead of passing it quietly. Claims about *other*
systems are listed separately and marked untestable here; verifying those means running those systems,
so they are never counted as passing.

```
13 passed · 0 FAILED · 0 skipped · 5 not testable here
```

The second mode audits the *numbers*. Every figure printed on this page, in `MCP_LISTINGS.md` and on the
homepage is registered in **[docs/CLAIMS.md](CLAIMS.md)** against the exact command that reproduces it
and a status — REPRODUCIBLE, REPRODUCIBLE-WITH-DEPS, PENDING-HARNESS, EXTERNAL or WITHDRAWN. A number that
appears here without an entry fails the audit and fails CI (`tests/test_claims_coverage.py`), which is the
part that stops the drift from coming back.

This exists because the exercise pays for itself: the first time we ran a README sentence against the
published wheel, it failed. Erasure did delete the record and scrub the bytes, but plain `forget()` left
no receipt, so the store's own `verify_writes()` reported the deletion as out-of-band — flagging a
legitimate API call as tampering. Fixed in 1.24.0, with a regression probe, and the audit now covers it.
The tightened audit then caught a second one: `forget_subject()` was writing *two* receipts per record,
one of them with the wrong reason (fixed in 1.24.3).

**On certification.** There is no certification body for an agent-memory library, and anyone claiming
otherwise is selling a logo. SOC 2 and ISO 27001 certify organisations running services; no scheme
certifies that a Python file deletes what it says it deletes. So instead:

- **`governance_audit.py`** attacks the strongest claim here — *tell it to forget everything about a
  subject and it can prove it* — across three scenarios and three repeats each: erasure through
  `derived_from` lineage, absence from the records, from recall under several phrasings, and from the
  **bytes of every file the store wrote** including sidecars; exactly one receipt carrying the caller's
  stated basis; tamper detection; survival across a reload; unrelated records intact; and an identical
  end state on every run.
- **The audit must be able to fail.** `GOV_FALSIFY=1` skips the erasure, and CI requires the run to
  report CLAIM BROKEN. A green falsification control would mean the checks measure nothing, so it is
  treated as a build failure.
- **It runs where we cannot touch it** — every push and daily, on Linux, Windows and macOS, against
  both this source and the wheel published on PyPI. The badge above is the result. If it is red,
  believe the badge and not this paragraph.

What that does **not** certify: this store, not your vector index, prompt logs or backups; the receipt
proves the *act* of deletion, never the content; and an operator holding the receipt key can forge
receipts, so anchor the chain head externally if your adversary is the operator. Those limits are in the
docstrings too, and they are the reason the word "certified" does not appear anywhere else on this page.

### Witness network — the operator-adversarial layer

"Anchor the chain head externally" has a concrete, runnable form. `anchor()` emits a signed tree head; on its
own it catches a rewrite on **one** timeline (`verify_consistency`), but a compromised host can still show a
**different** history to a different client (a split-view / fork). Independent **witnesses** that co-sign the
head close that — an honest witness refuses to co-sign a fork, so a client requiring **k-of-n** cannot be shown
a forked head that reaches threshold. This is the operator-adversarial guarantee that a single-party receipt
cannot give on its own, whoever ships it: the property needs a second party by construction. Here it needs no
LLM, no GPU and no graph database.

```bash
# each independent party runs one witness (stdlib http server, zero framework):
python -m inspeximus.witness_server --port 9700 --state witnessA.json   # prints its pubkey
```
```python
from inspeximus import Inspeximus
from inspeximus.witness_pool import collect_cosignatures, http_witness, Witness

anchor = store.anchor()                                  # signed tree head of the whole history
witnesses = [http_witness("http://hostA:9700"), http_witness("http://hostB:9701"), Witness()]
out = collect_cosignatures("my-store", anchor, witnesses)
allowlist = ["<witness-A-pubkey-hex>", "<witness-B-pubkey-hex>"]   # your allowlist
ok = Inspeximus.verify_cosigned_anchor(anchor, out["cosignatures"],
                                       witnesses=allowlist, threshold=2)["ok"]
# a forked head -> honest witnesses REFUSE (surfaced in out["refused"]) -> it never reaches threshold;
# Inspeximus.detect_split_view(...) turns two co-signed inconsistent heads into a cryptographic fork proof.
```

**[docs/TRANSPARENCY.md](TRANSPARENCY.md) is the quickstart**: an empty directory to a verified
co-signed anchor in five shell commands, plus the worked split-view proof. Every command on that page is
executed by `tests/test_witness_quickstart.py` on every CI run, so it cannot rot. From the shell:

```bash
inspeximus --receipts remember "invoice 7 total is 100 EUR" --key inv7::total --object 100
inspeximus anchor --out head.json                     # the signed tree head, safe to publish
inspeximus witness keygen --out witnessA.key --allowlist witnesses.txt
inspeximus witness cosign head.json --store-id acme --key witnessA.key --out cosigA.json
inspeximus witness verify head.json --cosig cosigA.json --witnesses-file witnesses.txt --threshold 1
```

Add witnesses B and C, require `--threshold 2`, and a rewritten history can no longer reach the bar —
honest witnesses refuse to co-sign a fork of a head they already signed (exit `2`). When one is tricked or
colludes into signing both, `inspeximus witness split-view` turns the two co-signed heads into a fork
proof naming the key. The page below walks that through with real output.

`examples/12_split_view_detection.py` runs the whole story with its controls; `examples/07_witness_pool.py`
is the shorter pool-only version. Witnesses persist their per-store last-signed head, so the refusal
survives a restart. The design is Certificate Transparency's ([RFC 6962](https://www.rfc-editor.org/rfc/rfc6962));
[Sigstore](https://www.sigstore.dev/)/Rekor and the CT log ecosystem run it in production at a far larger
scale, with real multi-operator witness networks. Of the nine agent-memory libraries we scanned
([docs/AI_ACT.md](AI_ACT.md)) none shipped external witnessing at the time of the scan — that is a
statement about what we read, not about what is possible. What we did not find elsewhere is a co-signed,
split-view-detecting anchor **inside a zero-dependency single-file memory store**.

### Portable audit bundle — hand an auditor one file they verify offline

EU AI Act **Article 12** (record-keeping / logging; applies to high-risk systems from **2 Dec 2027**, Annex III)
and GDPR Art.17/30 ask an
operator to *produce*, on demand, a tamper-evident log of what the system recorded, what changed, and what was
erased — and to let an independent party verify it. inspeximus already computes every piece; `audit_bundle`
serialises them into **one content-free artifact** with a **standalone verifier that needs neither the live
store nor the receipt key**:

```bash
inspeximus --receipts remember "retention policy is 90 days" --key policy::retention --object 90d
inspeximus audit-build --out bundle.json        # operator exports (content-free: hashes + surrogate ids, no text)
inspeximus audit-verify bundle.json             # auditor runs this — offline, exit 0 = PASS, 1 = FAIL
inspeximus audit-verify bundle.json --store inspeximus_memory.json   # ...and bind it to the CONTENT served today
```

`audit-verify` re-walks the entire write **and** erasure history from genesis, confirms every hash and
prev-link, checks the tips/counts against the signed anchor, and fails on any alteration **of the chain** —
with nothing but the file. It does **not** check content: the bundle carries hashes and never text, so a
clean chain over substituted text reads PASS. That limit is now printed next to the verdict (`content NOT
checked`) instead of left to be inferred; pass `--store` to close it, and a record that no longer matches
its earliest receipt fails the verdict. Pass `--witnesses <pubkeys>` to also verify external co-signatures
(the operator-adversarial check from the witness network above). It is a tamper-evident **record-keeping artifact, not a compliance
certification** — it proves the *acts* (a write with this commitment at T; a record erased at T for request R)
and their append-only integrity, never the content (a hash of PII is still PII). Full demo:
`examples/09_audit_bundle.py`.

### Agent-to-agent memory grants — scoped, revocable, and in the same audit trail

Multi-agent is the ordinary deployment shape now, and the moment two agents share a store the questions are
immediate: **which agent may read which memories, who granted it, and how is it taken back.** Surveying the
agent-memory field we found no comparable scoped/revocable sharing primitive — that is an observation about
our search, not a claim about anyone's product. inspeximus does it **deterministically, zero-LLM, in the
single-file core**, and every grant and revocation lands in the write-receipt chain you already have.

```bash
inspeximus remember "the payout rotation runbook" --tags billing
inspeximus grant bob --tag billing            # scoped: --scope / --tag / --key / --ids
inspeximus recall runbook --as-agent bob      # -> the record
inspeximus recall runbook --as-agent eve      # -> nothing (fail-closed, no grant)
inspeximus revoke bob --tag billing           # effective on the next read; deletes nothing
inspeximus grants --log                       # every grant and revocation, newest first
```

```python
store.grant("bob", tag="billing")             # or scope= / key= / ids=
store.as_agent("bob").recall("runbook")       # only what bob owns or has been granted
store.revoke("bob", tag="billing")
store.can_read("bob", record_id)              # {"allowed": False, "reason": ..., "via": None, "problems": []}
```

Over MCP: `grant`, `revoke`, `grants`, `grant_log`, `can_read`, `recall_as`, `get_as`. Note that the MCP
server holds an **operator** handle — its other read tools see the whole store, and `recall_as`/`get_as` are
the scoped reads. To genuinely confine an agent, hand it a scoped store (`store.as_agent(...)`) rather than
relying on it to pick the scoped tool.

**A selector is `scope`, `tag`, `key` or `ids` — never a query.** Membership is exact-match on a stored
field, decidable in one pass with no embedder, so the set a grant authorises is the same tomorrow as today.
A query-shaped selector would make membership a function of a similarity score, and an ACL that silently
widens after a re-embed is not an ACL.

**It fails closed, and that is tested rather than asserted.** No grants means an agent reads only its own
writes; a grant that cannot be evaluated — unknown selector kind, missing granter, two active acts
disagreeing, or an *empty* selector value — authorises nothing. That last one is the sharp edge: `scope` and
`key` are compared with `==` against a field most records do not carry, so a grant whose value went missing
would otherwise degenerate to `None == None` and match every record *lacking* the field. The scoping lives in
the same `items` chokepoint as tenant isolation, so a method added tomorrow is access-controlled by
construction, and `tests/test_agent_grants.py` sweeps **every public method** from an ungranted handle rather
than checking `recall` alone.

**Honest scope.** This is logical isolation inside one store and one process, like tenancy — not a substitute
for separate stores/keys when the agents are mutually hostile and the process is the trust boundary. `by`
(the granting agent) is an identity the caller asserts, not one this library authenticates; that is the same
limit already stated for supersession. Revocation deletes nothing: the owner keeps the record, other agents
keep their own grants, and the withdrawn grant stays in `grant_log()` as evidence.

## Why inspeximus — a deterministic, zero-LLM write path

Everything above — a provenance answer you can check, a correction trail you can audit, an erasure receipt an
auditor verifies offline — rests on one design choice, and it is the one you can verify fastest.

Every mainstream agent-memory library we read puts an **LLM on the write path**: it calls a model to extract,
summarize, or build a graph *every time you store something*. mem0 runs LLM fact-extraction on `add()` by default;
Zep/Graphiti runs LLM entity/edge extraction on every `add_episode()`. That one choice is why their stored state is
**non-deterministic**, costs a model call per write, and can silently drop a fact.

**inspeximus has no LLM on the write path.** Storing a fact is a deterministic, zero-cost operation — and *that* is
what makes the three properties below cheap, checkable and repeatable here:

> **What that costs, measured on someone else's benchmark.** On the [MemOps](https://github.com/MemTensor/MemOps)
> long-context scenarios (24 scenarios, ~50 sessions each), ingesting one scenario through mem0's default
> pipeline took **519–917 s of LLM extraction** (median 606 s, n=24); inspeximus's write path made **zero model calls**. Read the rest
> before quoting that: on the same run, answer accuracy was **statistically indistinguishable** — inspeximus 0.593,
> a naive keep-all store 0.592, mem0 0.544, with every bootstrap CI crossing zero. So the honest claim is *same
> answers, no write-time model cost*, not *better answers*. About 2% of mem0's extraction calls failed to parse
> and those memories are missing from its store, which handicaps it slightly. MemOps is published by MemTensor,
> who also make a competing system. Harness, pre-registration and the full result:
> [agora/agora_output/lab/memops](https://github.com/DanceNitra/agora/tree/main/agora_output/lab/memops).

- **Corrections that stick, and an audit trail of what replaced what.** Write a new value for a key and it
  *supersedes* the old one; `echo_guard` blocks a later restatement of the retired value from resurfacing;
  `history(key)` and `supersession_report()` then show you every value the fact has held and the policy that
  retired each one. No config, no model call, and the same answer on every machine. Honest scope: this is
  **auditability, not accuracy** — the store can show you what it did and let you check it; it cannot tell you
  the new value is true. And the guard engages on **keyed or extractor-derived** assertions (the shipped
  extractors derive the key from raw text); a free-text write that nothing keys is stored as an independent
  record and ranks on its own.
- **Deletes the value, not just the pointer.** `forget_subject` removes the value from inspeximus's records (subject
  + its `derived_from` lineage) and leaves a **content-free**, tamper-evident signed receipt — so what remains is
  a proof-of-deletion, not the data. Since **1.24.0 every deletion path leaves that receipt**, including plain
  `forget(ids=…, where=…)`; before that only `forget_subject` and `forget_pii` did, so a record removed with
  `forget()` was erased correctly but unaccounted-for, and `verify_writes()` reported it as an out-of-band
  deletion — the store flagging its own legitimate API call as tampering. Pass `request_id=` / `basis=` to
  `forget()` to bind the reason into the receipt's committed hash. Most agent-memory libraries instead *retain the deleted value* by design:
  mem0 keeps it in its SQLite history table (a full `reset()` purges it); Graphiti stamps the old edge
  `invalid_at` and keeps it. For **secure erasure at rest** (against raw-disk/backup forensics — which a plaintext
  store of ANY library, inspeximus included, does not give you) use an encrypted store + `shred()` (NIST SP 800-88
  crypto-erasure: destroy the key and every at-rest copy dies).
- **Revert on command — the proof, not the pitch.** `m.revert(key)` rolls a corrected fact back to its
  predecessor: a deterministic move along the same timeline `history()` prints, with no model call and no
  similarity guess. Of the leading systems we checked — mem0, Zep/Graphiti, Letta, Cognee, Memobase,
  MemoryScope, LangMem, txtai — none exposes revert-to-predecessor as a first-class memory operation (mem0's
  `history()` is a read-only log; Graphiti invalidates but never un-invalidates). **Letta is the honest
  exception**: it has an engine-level checkpoint-undo (`undo_checkpoint_block` over `BlockHistory`),
  undocumented and not surfaced as a recall-integrity op — so the difference is that inspeximus exposes it
  deterministically as a named API, not that reverting is unavailable elsewhere. (An earlier version of this
  line said "Letta has no undo". That was wrong, and our own `claims_audit.py` already said so on the line
  below it.) It is listed last on purpose: revert is the *proof* that the correction timeline above is a real
  state model you can walk in both directions, not the headline reason to adopt this.

| | LLM on write | correction trail you can audit | revert to predecessor | deleted value retained? |
|---|---|---|---|---|
| **inspeximus** | **no — deterministic** | supersession + echo_guard + `history` / `supersession_report` | `revert(key)`, deterministic | no — value scrubbed, content-free receipt (+ `shred()` for at-rest) |
| mem0 | yes (by default) | LLM decides ADD/UPDATE; `history()` is a read-only log | ✗ history is read-only | ✗ kept in the history table by design |
| Zep / Graphiti | yes | temporal invalidation, bitemporal query | ✗ no un-invalidate | ✗ invalidated edge retained |
| Letta / MemGPT | yes | LLM rewrites the block | ~ engine-level `undo_checkpoint_block`, not a first-class memory op | ✗ |

*(Every cell in the right-hand columns records what we found reading that project's current source/docs — see
[the integrity benchmark](../probes/INTEGRITY_BENCHMARK.md), which also names each system that shares an individual
property. Cryptographic deletion receipts do exist in purpose-built provenance systems like Engram and Heartwood.
"Not found in what we read" is a statement about our scan and its date, never about what a project can do.)*

The mechanism underneath — **no LLM on the write path** — is what makes the state reproducible byte-for-byte, and
it is not something you can bolt onto an extraction pipeline: a re-extracting write path would have to give up
re-extraction to get it. Any of these systems could adopt it; the claim here is about the property, not about
who is able to ship it. It is also the one claim on this page you can falsify in ten seconds, because it is the
*absence* of a network call — `claims_audit.py` disables sockets for the duration and re-runs the write path
against the published wheel, so a write that reached for a model would fail the check instead of passing it
quietly.

## And it doesn't cost you recall

Integrity would be hollow if inspeximus retrieved worse. It doesn't. On the standard **LOCOMO** benchmark (full set,
n=1536), with the built-in tuned recipe (a semantic embedder + hybrid recall + a soft speaker prefilter),
inspeximus's **retrieval-recall@25 is 0.83** (a supporting turn is retrieved) / **0.70** (all supporting turns),
measured the honest way: **LLM-free**, with no LLM judge to inflate it. We have published no cross-system
retrieval comparison, so this page makes no claim about where that sits against anyone else.

```bash
python benchmarks/locomo/run.py --subset full --retrieval-only   # ~0.83 / 0.70, no model calls
```

> **The caveat this line carried for a year is now discharged (2026-08-01).** From 2026-07-25 this section said
> the harness behind its numbers was *"not currently in this repository"* and asked you to treat the pair as
> **reported, not independently reproducible**. It is now [`benchmarks/locomo/`](../benchmarks/locomo/) — one
> command, a pinned operating point, a committed result, and a test that fails when a re-run drifts.
>
> Two things changed in the numbers, and both are corrections in your favour rather than ours.
> **The old pair was 0.78 / 0.65, and it reproduces exactly** — at *its* operating point the harness measures
> 0.7839 / 0.6484 against the published 0.783 / 0.648, on the identical 1536-question denominator.
> **The pair above is higher because the benchmark pins `reinforce=False`** — which, as of 2.0.0, is simply
> the default. Before 2.0.0 `recall()` reinforced what it returned, so during a benchmark each query was
> answered by a store the previous queries had modified and the score depended on the order the questions
> were asked in. Turning that off makes the run deterministic and, it turns out, scores 4-5 points better.
> The old number was not optimistic; it was measuring a memory that was learning from the benchmark while
> being measured by it. That is also why the flag became the default: see CHANGELOG 2.0.0.
>
> The two arms, the controls, the judge gate and the exact reason the original probe could not run are all in
> [`benchmarks/locomo/README.md`](../benchmarks/locomo/README.md).

*(We deliberately don't headline an LLM-judged end-to-end QA score. Those are judge-dependent and not comparable
across harnesses — mem0 reports 66.9% and Zep 71.2% under their own judges — so a cross-system "we win" claim
would need running them through this harness, which we haven't done. We now **run** end-to-end QA — the same
harness scores it with one judge across six arms, including a naive-recency floor and a full-context ceiling,
and commits the result — but what we put in this README is the LLM-free number.)*

**Every number on this page is registered in [docs/CLAIMS.md](CLAIMS.md)** with the exact command that
reproduces it and one of five statuses, and the reproducible-vs-published ratio is printed there rather than
asserted here. `python claims_audit.py --numbers` fails on any figure that is not registered — including this
one, whose row moved from PENDING-HARNESS to a committed command the day `benchmarks/locomo/` landed.

*(This paragraph replaces a line that had been corrected twice and was still wrong. It first said* every *number
traces to a runnable probe; an audit narrowed that to "one exception"; a second audit found three. A third — this
one — found that the honest count is not three either, and that a prose sentence is the wrong instrument for it.
The count now comes from a script, so the next drift fails a test instead of needing a fourth apology.)*

## Quickstart (2 minutes)

```bash
pip install inspeximus                  # zero required dependencies
pip install "inspeximus[crypto]"        # only if you run the examples that SIGN something
```

Five examples use Ed25519 (`04_encryption`, `06_gdpr_erasure_receipt`, `07_witness_pool`,
`12_split_view_detection`, `trust_is_not_truth`) and raise `RuntimeError: signing write receipts needs
the cryptography package` on a base install. Everything else runs on the standard library alone, and
`tests/test_examples_run.py` now proves it by running every example with the optional imports blocked.

```python
from inspeximus import Inspeximus

m = Inspeximus("memory.json")                      # persists to JSON; drop the path for pure in-memory
m.remember("The API rate limit is 1000 req/min", key="api::rate_limit")
m.recall("what is the rate limit")            # -> [{"id": ..., "text": "The API rate limit is 1000 req/min", ...}]

# Correction is first-class: writing the same key supersedes the old value — no config, no LLM call.
m.remember("The API rate limit is 5000 req/min", key="api::rate_limit")
m.recall("rate limit")                        # -> [{"text": "...5000 req/min", ...}]  (only the current value)
m.revert("api::rate_limit")                   # roll back to the predecessor, on command
m.history("api::rate_limit")                  # full audit trail, oldest to newest
```

New in **1.11.0**: ready-made write-path extractors (`regex_extractor`, deterministic; `make_llm_extractor`,
opt-in) that can derive a key from text without an explicit one, and a first-class **LangChain**
integration (`from inspeximus.integrations.langchain import InspeximusRetriever` — a retriever that never hands a
superseded fact back to your chain). `pip install "inspeximus[langchain]"`.

**Honest scope of `regex_extractor` (re-measured 1.90.0).** It keys clean declarative statements — "My ZIP
code is 94107", "Alice's email is …", "The API rate limit is 500 rps" — and, as of 1.90.0, it holds one key
across a **conversational correction chain**: leading discourse markers ("actually", "correction:", "so"),
trailing time adverbials ("… now", "… last week"), `I'm` contractions, current-marking modifiers ("my
*current* title" == "my title"), a leading clause ("Dana left, so my manager is Priya now"), and
first-person rephrasing ("my employer is X" / "I work at X") all normalise to the same key. It does this
**deterministically, zero-LLM, in a single file** — closed-list surface normalisation, nothing on the write
path but regexes.

Measured on `benchmarks/chain_binding/` (run it: `python benchmarks/chain_binding/probe.py`) — 15 chains,
18 unrelated pairs, 60 prose sentences:

| | before 1.90.0 | 1.90.0 |
|---|---|---|
| correction chains that collapse to one record holding the final value | 2/15 | **9/15** |
| false binds on unrelated pairs (the control — a keyer that binds everything is worthless) | 1/18 | **0/18** |
| non-declarative prose keyed (conservative is the goal) | 8/60 | **4/60** |

**Where it stops, and why that is not a bug.** A key is derived only when the sentence NAMES the relation.
When a later turn names only the *value* and leaves the relation to world knowledge — "actually I'm a
Principal Engineer now" (that a Principal Engineer is a *title*), "I'm vegan now", "Dan is now an
engineering manager" — it returns None and the write is a plain append. No deterministic keyer crosses that
without an ontology or a model. Two surface shapes also remain unsolved: an English noun compound ("the
Project Atlas deadline") cannot be split into head and modifier without a lexicon, so it does not meet
"Project Atlas's deadline"; and the bare-copula path keys on the subject alone, so two attributes of one
entity share a key. **If you control the write, pass `key=` explicitly** — that is still the path where
corrections-stick, `revert` and the erasure guarantees hold unconditionally. `derive_key(text)` is exported
if you want the same canonical key elsewhere.

**Upgrading a live store:** first-person keys changed (`my title` → `self::title`). See CHANGELOG 1.90.0.

## Give your agent this memory in 60 seconds (MCP)

Using **Claude Code**? One command registers inspeximus as your agent's memory ([uv](https://docs.astral.sh/uv/) fetches it, nothing else to install):

```bash
claude mcp add inspeximus -e INSPEXIMUS_PATH=~/.inspeximus_memory.json -- uvx --from "inspeximus[mcp]" inspeximus-mcp
```

**Claude Desktop / Cursor / any MCP client** — add to your MCP config (`claude_desktop_config.json`, `.cursor/mcp.json`, …):

```json
{
  "mcpServers": {
    "inspeximus": {
      "command": "uvx",
      "args": ["--from", "inspeximus[mcp]", "inspeximus-mcp"],
      "env": { "INSPEXIMUS_PATH": "~/.inspeximus_memory.json" }
    }
  }
}
```

Your agent now has `remember` / `recall`, and — the part that matters when someone asks it *"where did you get
that?"* — `provenance`, `why_recalled`, `history` and `verify_attribution`, each answering in one call with no
model in the loop. Corrections stick on top of that: when a fact is superseded, recall serves the current value,
a restated stale value can't resurrect it (`echo_guard`), `supersession_report` shows what replaced what, and
`revert` / `route` undo a correction on an unmarked "go back". `recall` returns compact records by default (drops
internal fields; `get(id)` / `neighbors(id)` for detail on demand).
[Full tool list below](#use-it-as-an-mcp-server-any-claude--cursor--agent-client).

### For coding agents: stop resurrecting an API a refactor already deleted

The single most common way memory fails inside a **coding loop**: a refactor renamed or removed a function, but
the model re-emits the old call because the old signature is still all over its context. That is keyed
supersession + an echo check — inspeximus's core competence — shaped for code, as three MCP tools:

```python
from inspeximus import Inspeximus
from inspeximus.code_guard import deprecate_symbol, symbol_status, check_code
mem = Inspeximus(path=".inspeximus/memory.json")

deprecate_symbol(mem, "db.query", "db.execute", reason="query() removed in 3.0; execute() returns a cursor")
symbol_status(mem, "db.query")          # -> {'verdict':'superseded','replacement':'db.execute', ...}
check_code(mem, generated_snippet)      # -> [{'symbol':'db.query','replacement':'db.execute','occurrences':1}, ...]
```

`check_code` scans a whole generated snippet and flags every deprecated symbol it resurrects (whole-identifier
match, most-used first; empty = clean) so the agent rewrites *before* returning code. Over MCP the same three
are `deprecate_symbol` / `symbol_status` / `check_code`. Deterministic table lookup — no LLM, no embedding
similarity guess, no new storage. Full runnable demo: `examples/08_code_guard.py`. This is the vendor-abandoned
need behind Claude Code #14227 ("don't resurrect the old API after a refactor"), served by the primitive
inspeximus already ships.

**Enforce it in CI, not just in the loop.** The same guard is a shell command that exits non-zero when a
deprecated symbol reappears — drop it into a pre-commit hook or a CI step so a human (or an agent) cannot merge
a resurrected API:

```bash
inspeximus deprecate db.query db.execute --reason "query() removed in 3.0"   # record the refactor once
inspeximus check-code src/**/*.py                                            # exits 1 with file:line on any resurrection
```
```yaml
# .pre-commit-config.yaml  (point INSPEXIMUS_PATH at a store committed to the repo, e.g. .inspeximus/memory.json)
- repo: https://github.com/<owner>/inspeximus
  rev: v2.22.0
  hooks: [{ id: inspeximus-check-code }]
```

Commit the store (`.inspeximus/memory.json`) so every clone shares the refactor history; the guard is a
deterministic token scan, so the same commit is a pass or a fail on every machine.

**Jump to:** [Where did this memory come from?](#where-did-this-memory-come-from--and-can-you-check) ·
[Verify it yourself](#dont-take-our-word-for-it--three-commands-that-answer-about-your-stack) ·
[Every published number + its command](CLAIMS.md) ·
[Correction (measured)](../probes/INTEGRITY_BENCHMARK.md) ·
[Erasure & lineage](#delete-that--then-check-what-the-lineage-says-survived) ·
[EU AI Act evidence](#new--the-eu-ai-act-compliance-evidence-layer-for-agent-memory) ·
[Audit bundle](#portable-audit-bundle--hand-an-auditor-one-file-they-verify-offline) ·
[Zero-LLM write path](#why-inspeximus--a-deterministic-zero-llm-write-path) ·
[Quickstart](#quickstart-2-minutes) · [MCP server](#use-it-as-an-mcp-server-any-claude--cursor--agent-client) ·
[Shell CLI + API reference](API.md) ·
[Framework integrations](#framework-integrations) ·
[The four operations](#the-four-operations) · [Five rules](#five-rules-it-wont-break-each-one-cost-us-to-learn) ·
[Design receipts](#provenance--why-these-rules-with-receipts) ·
[Threat model](#threat-model--layered-defense-adversarial-memory-integrity)

Correction is measured across systems in
[docs/API.md](API.md#correction-is-a-first-class-operation-measured-across-systems).

## Use

The full API reference — every method, argument and return shape, with runnable examples —
lives in **[docs/API.md](API.md)**. The four operations you actually need are further down this
page; everything else is there when you need it.
## Framework integrations

Adapters for LangGraph, CrewAI, LangChain, LlamaIndex, AutoGen and the rest,
with copy-paste snippets: **[docs/INTEGRATIONS.md](INTEGRATIONS.md)**.

**Three of the twelve are currently BROKEN against current upstream**, and the ledger says so rather than
this page implying otherwise: `crewai` (1.15.6 — missing the async `StorageBackend` methods),
`openai-agents` (0.18.3 — the round trip works but the object no longer satisfies `agents.memory.Session`)
and `langgraph-checkpointer` (1.2.9). Run `python tools/integration_conformance.py` for the live three
counts; the ledger is `docs/integration_conformance.json`, and a test fails on drift in *either*
direction — an adapter that stops conforming, and one recorded broken that starts.

**Compliance-aware out of the box.** Every class-based adapter — LangGraph `InspeximusStore`, CrewAI
`InspeximusStorage`, LangChain `InspeximusRetriever` / `InspeximusChatMessageHistory`, LlamaIndex
`InspeximusMemoryBlock`, AutoGen `InspeximusMemory`, OpenAI-Agents `InspeximusSession`, Haystack
`InspeximusDocumentStore`, ADK `InspeximusMemoryService` — mixes in
`ComplianceMixin`, so the same object your agent writes memory to also produces the EU AI Act evidence —
`store.compliance_report()`, `store.compliance_check()`, `store.audit_bundle()`, `store.retention(...)`. Pass
`receipts=True` for the tamper-evident record-keeping chain those reports evidence. See
[docs/AI_ACT.md](AI_ACT.md). (Pydantic AI is the one exception: it exposes a *function* toolset,
`inspeximus_toolset(store)`, so there is no adapter object — call the evidence operations on the store you
passed in.)
## Use it as an MCP server (any Claude / Cursor / agent client)

`inspeximus` ships an [MCP](https://modelcontextprotocol.io) stdio server so any MCP-compatible agent can
use it as long-term memory. The provenance surface is first-class over MCP, not an afterthought:
`provenance` (where a fact came from, in one call), `why_recalled` (why this hit ranked), `history` and
`supersession_report` (what replaced what, under which policy), `verify_attribution` (does the record still
match its write receipt), `audit_bundle` / `verify_audit_bundle` (hand an auditor one offline-verifiable
file) and `erasure_certificate` / `erasure_residue` (proof of a deletion, and a check for what survived it).
Around that sit the ordinary memory operations — `remember` (with a per-type decay prior), value-ranked
`recall`, `consolidate`, `consolidate_clusters`, `contradictions`, `value_by_cohort`, `forget` (verified
erasure). Correction is first-class too: `revert` / `route` undo a correction on an unmarked "go back", and the
read-path review layer `observe` / `reopened` / `resolve_reopened` (1.9.2–1.9.5) reopens a settled record for
steward review on a *corroborated* contradiction (a lone restatement stays an echo, never an auto-change).
The MCP `remember` exposes `key` (deterministic supersession) plus `object` / `reaffirm`, and the server
runs with **`echo_guard` ON by default** (0.6.11) so a corrected fact stays corrected even if the old value
is re-stated later — the failure mode a plain keyed store shows on the ECHO-RESISTANCE cell of
[RAMR](https://github.com/DanceNitra/ramr) (keyed-without-guard 0.00, an add-based system 0.57, guard 1.00).
Those three figures come from RAMR, a **separate** repository; no script here produces them. This repo's own
cross-system cell measures a different quantity — *resurrection rate* — and finds no system systematically
resurrects the stale value ([`probes/INTEGRITY_BENCHMARK.md`](../probes/INTEGRITY_BENCHMARK.md), Cell 2: inspeximus
0.00, mem0 0.05, Graphiti 0.00). Read both before quoting either. Set `INSPEXIMUS_ECHO_GUARD=0` to disable.
Since **1.86.0 every SURFACE shares that posture** — the CLI, the MCP server, the Claude Code hook and all
nine framework adapters — because until then the adapters inherited the library default (OFF), and one
restatement through an adapter undid a correction and then wedged the store against being put right. The
LIBRARY default is unchanged: construct `Inspeximus` directly and you get exactly what you wrote.
Install and run the server straight from PyPI (the `[mcp]` extra pulls the MCP SDK; the core library stays
dependency-free):

```bash
pip install "inspeximus[mcp]"     # the library + the MCP server SDK
inspeximus-mcp                          # speaks MCP over stdio
```

Register it with any MCP client — Claude Code (`.mcp.json`), Claude Desktop
(`claude_desktop_config.json`), Cursor, Windsurf, Codex, Gemini. Zero-setup with `uvx` (installs on first run):

```json
{
  "mcpServers": {
    "inspeximus": {
      "command": "uvx",
      "args": ["--from", "inspeximus[mcp]", "inspeximus-mcp"],
      "env": { "INSPEXIMUS_PATH": "./inspeximus_memory.json" }
    }
  }
}
```

Or, after `pip install "inspeximus[mcp]"`, with the console script directly:

```json
{
  "mcpServers": {
    "inspeximus": {
      "command": "inspeximus-mcp",
      "env": { "INSPEXIMUS_PATH": "./inspeximus_memory.json" }
    }
  }
}
```

For **semantic** recall, point it at any OpenAI-compatible embeddings endpoint via
`INSPEXIMUS_EMBED_URL` / `INSPEXIMUS_EMBED_MODEL` / `INSPEXIMUS_EMBED_KEY`; with none set it uses the lexical
fallback. The agent then calls `recall(query)` before reasoning and `remember(fact)` as it learns —
its memory is value-ranked and append-only, not a recency buffer. If `INSPEXIMUS_EMBED_MODEL` contains
`nomic` (nomic-embed-text is asymmetric — see its model card; like E5's `passage:`/`query:`), inspeximus auto-applies its
required task prefixes — `search_document: ` for stored text, `search_query: ` for the query (opt out with
`INSPEXIMUS_NOMIC_PREFIX=0`). Omitting them was simply using the model wrong; with prefixes on, our own
reinforcement-controlled re-measure lands recall_any@1 at 0.397 (*not reproducible from this repository: the
LoCoMo probes need a dataset we cannot redistribute*) on one LoCoMo config (n=1536, deterministic
retrieval-recall — an upper bound, not end-to-end QA; a self-comparison, not a cross-system claim; the earlier
0.19→0.29 delta was contaminated by a since-fixed recall-reinforcement confound — see the 1.15.0 CHANGELOG correction). In the library, pass a separate `Inspeximus(embed=…, embed_query=…)` for any
asymmetric embedder. If you use `persist_vectors=True`, also pass `Inspeximus(embed_id="…")` (a recipe fingerprint): when
it changes, inspeximus re-embeds the persisted vectors once so a new-space query can't silently mis-match old vectors.

**Compact recall + progressive disclosure (1.14.0).** Over MCP, `recall` returns a compact projection — `{id,
text, score, value, tags}` — dropping internal bookkeeping fields the model doesn't reason over, and `k` is
hard-capped (`INSPEXIMUS_MAX_K`, default 50), so a recall drops cheaply into the prompt. **Full text is kept by
default**; snippet truncation is **opt-in** (`snippet_chars>0`) — off by default on purpose, since truncating a
hit could cut off a corrected value past the boundary and defeat the echo-guard. Pull detail on demand: `get(id)`
returns one full record, `neighbors(id, k)` a bounded local expansion (excludes self). `recall(full=True)` returns
complete records. `token_report(query, k)` is a **deterministic, no-LLM** (~chars/4) payload-size estimate
comparing the compact projection to the full records for the **same k hits** — an apples-to-apples sizing aid, not
a whole-store comparison and not a measured token saving. None of this is novel — it's standard MCP/RAG
context-economy practice (progressive disclosure / small-to-big retrieval); inspeximus never emitted embedding vectors.

## The four operations

| op | what it does |
|---|---|
| `remember(text, tags, value, mtype, key, source, derived_from)` | **append-only** raw capture, absolute UTC time, never edited; `source` is what makes a memory reachable by SUBJECT later (`forget_subject`, `erasure_audit`, `slash` all resolve through it) and `derived_from` carries that reach along lineage, so erasing a person's file also takes the summary built from it — a record with neither is attributable to nothing but its own id; `mtype` ∈ {episodic, semantic, procedural} sets the **decay prior** (events fade fast, durable facts slow, rules barely). Optional `key` = a **deterministic (subject, relation) supersession key**: a new value retires every active record with the same key — *no similarity threshold, no LLM* — so recall never serves the stale value (bi-temporal: a back-filled earlier value can't overwrite the current one) |
| `recall(query, k, where=…)` | **value-ranked** retrieval: relevance × value, **decayed by the memory's per-type half-life** (access resets the clock), so important durable memories beat both merely-similar and stale ones. Optional `where` = a **metadata pre-filter** (the cheap *filter-before-you-rank* lever): field → scalar / list / operator (`$gte $lte $gt $lt $in $nin $ne $contains`), matched top-level then `meta`, ALL fields AND-ed — e.g. a hard time-range `where={"valid_from":{"$gte":t0,"$lte":t1}}` or a closed-set entity `where={"speaker":{"$in":[…]}}`. Measured to beat retriever choice on LoCoMo (`probes/locomo_metadata_prefilter.py`); it's a HARD filter, so on lossy/predicted extraction keep it loose (a wrong filter hard-deletes the answer). Reinforcement is **relevance-weighted** (a bullseye hit reinforces value more than one that squeaked into top-k, so a weak-but-frequent false positive can't go immortal); a repeatedly-recalled episodic memory **graduates** to semantic **only when corroborated** — by an earned outcome, or by **≥2 distinct *canonical* sources** (entity-resolved before counting, so sybil variants of one origin — `Wikipedia` / `wikipedia.org` / a full URL — collapse to one and can't mint durability); and a memory whose source was later contradicted is **provenance-demoted** + flagged `stale_derived` |
| `consolidate(keep)` | the **dream pass**: flag universal-matcher *hubs*, link near-duplicates, apply the **state-toggle guard** (a polarity clash supersedes, doesn't merge), supersede the low-value surplus — only *adds* a derived layer |
| `consolidate_clusters(threshold)` | **cluster-triggered** consolidation: consolidate a semantic cluster only once it's grown past `threshold` — sparse topics keep their raw episodes, dense ones don't grow unbounded |
| `contradictions()` | flag mutually-incompatible **related** memories (similarity-gated) for human review |
| `forget(ids, where)` | the one op that **truly deletes** (the rest is append-only): hard-removes the matched records *and* scrubs their ids from every survivor's links + toggle pointers + the vec/token caches, so a forgotten memory can't resurface via recall, a consolidation link, or the dream pass. For erasure / right-to-be-forgotten, poison removal, or a hard correction. Measured across a six-store fan-out by `python probes/forget_verification_bench.py`: a wired hard delete scores **1.00** with a verifying signed receipt, the common soft delete scores **0.17** and names the five stores that still hold the value |

## "Delete that" — then check what the lineage says survived

Erasing the record is the easy half. The half that bites is the **summary built from it**, which no longer
resembles the subject's data at all — so a text-match delete walks straight past it and the fact is back next
session. `forget_subject()` already cascades along lineage; `erasure_audit()` is the separate question of
what survived:

```bash
inspeximus erasure-audit --subject user-42     # exit 1 when declared-lineage residue remains
```

```
scanned 1 record(s) for subject 'user-42'
  coverage  1/1 record(s) declare lineage (ratio 1.0)
  RESIDUE  3 finding(s) tied to a deliberate erasure:
    [subject_still_attributable] e3cf66c984
      still attributable to 'user-42' (status=active)
    [dangling_lineage] e3cf66c984
      declares parent 03dad5493e, which was deliberately erased
    [taint_without_origin] e3cf66c984
      carries inherited source 'user42', but no surviving record claims it: the origin was erased, this derivative was not
  limit  this is evidence about what the store RECORDED, not proof that no copy of the material remains
  limit  a derivative whose writer never declared derived_from carries no taint and is invisible here; read `coverage` before trusting a pass
  limit  covers THIS store only -- not your vector index, prompt logs, model weights or backups
  limit  does not discharge an erasure obligation; a party that stops declaring lineage always looks clean
```

**Read `coverage` before the verdict — the tool prints it first on purpose.** Every structural check walks
*declared* `derived_from` edges. A store whose writers never threaded lineage has no edges to walk, so it
would report nothing while having inspected nothing. That is a completely different statement from "checked,
nothing found", and collapsing the two into one reassuring boolean is how a deletion audit becomes a false
assurance. So when nothing is declared the verdict is **`unaudited`**, never a pass.

The demotion used to be a cliff at exactly zero, which is a narrower rule than the principle behind it.
Thomas Willner recorded the gap against us in the LLM Errata `PRIOR_ART.md` while we were reviewing his
spec: "Its tests force `unaudited` when declared lineage is zero, but a nonzero incomplete ratio can still
return `no_declared_residue`." One resolvable edge bought the pass verdict for a store that had announced
four hundred derivations and resolved none. Since 2.6.0 that case is **`partially_audited`**.

The gate is the orphan count and not the ratio, which matters more than it sounds. Most records are roots
and derive from nothing, so a healthy store's `declared_ratio` is low forever, and a threshold on it fires
on stores with no hole at all while missing the one described above. `undeclared_derived` counts records
that ANNOUNCED derivation the walk could not resolve, so it reports a hole of known size rather than a
proportion nobody can calibrate.

Coverage is also store-wide, and a store-wide number cannot vouch for a subject-scoped question. Our own
test carried the admission in its assertion comment ("lineage exists elsewhere, so not `unaudited`") — the
lineage that existed was about billing, and no edge the walk followed could have reached an erased
`user-42`. `coverage["subject_reachable_records"]` now counts what the walk could actually follow to the
subject asked about. It is reported rather than gated: after a correct cascade the derivatives are erased
too, so a reach of zero is also what success looks like, and tombstones are content-free by design, so
nothing here can separate "the cascade erased them" from "they were never declared". A gate a correct
erasure can never pass measures nothing.

Housekeeping deletions are separated out too: capacity eviction and the consolidation keep-budget hard-delete
for size reasons, and would otherwise masquerade as erasure residue in any bounded store. They are reported
as `advisory` with the reason attached, and never counted. What lands in `residue` is tied to a deliberate
erasure — one carrying a request id or a real basis, not the generic default housekeeping leaves behind.

**What this is and isn't.** It is evidence about what the store has *recorded*, not proof that no copy of the
material remains — and it does not discharge an erasure obligation. Three limits ship with every answer, in a
`limits` field a renderer cannot quietly drop:

- taint propagates along **declared** edges only, so a summary whose writer never declared its parents is
  invisible to every structural check. We ship that as a test asserting we do *not* find it. It is the
  overtainting/undertainting trade-off argued for dynamic taint analysis by Schwartz, Avgerinos & Brumley
  (IEEE S&P 2010); that is program analysis, not lineage, so we borrow the trade-off, not a result;
- it covers *this store* only — never your vector index, prompt logs, model weights or backups;
- it reads metadata the writer supplied, so a party that stops declaring lineage will always look clean.

The optional `--value` text scan is reported separately and labelled a heuristic, because matching text
proves neither presence (a paraphrase carries the fact without the string) nor absence.

Not a new idea: this is DELF-style deletion-correctness auditing (Cohn-Gordon et al., *DELF: Safeguarding
deletion correctness in Online Social Networks*, USENIX Security 2020) applied to an agent-memory store, with
the orphan/dangling half being classical referential-integrity checking.

## Five rules it won't break (each one cost us to learn)

1. **Raw capture is immutable.** Consolidation adds links and markers; it never overwrites the
   source. This is what stops the slow accuracy drift of LLM-rewritten memory.
2. **Absolute timestamps at write time.** Relative/derived times rot the moment they're consolidated.
3. **Value-ranked, type-aware decay.** Retention is `value × a per-type half-life`, not recency or
   access-frequency alone. A *uniform* access-reset clock keeps merely-*popular* memories while a
   load-bearing-but-cold fact — queried once a month, prevents a destructive action — starves; we
   measured exactly that failure. The fix is that the half-life is set by **kind**, not by read
   count: episodic events fade in days, semantic facts in months, procedural rules barely at all. A
   cold-but-critical fact survives by being **typed** semantic/procedural (long half-life × its high
   value), not by frequent reads; access only resets the clock *within* a type's window.

   One measured property worth stating plainly, because it surprises people who read "deterministic"
   as "frozen": **the ranking depends on when you ask.** Read the same untouched store twice a couple
   of seconds apart -- nothing written in between, no field, value or insertion position altered --
   and a large minority of the top-1 answers differ. Four LOCOMO conversations, 80 questions each,
   `reinforce=False`:

   * **Movement:** across a two-second gap, between 64 and 83 of 320 top-1 answers differ.
   * **Cause:** at a zero gap 0 of 80 differ, so it is elapsed time and not the act of reading.
   * **It saturates:** on one corpus 16 of 80 move at two seconds and the same count at ten seconds.
   * **It never leaves the tie band:** every moved answer moved between records the API reports at the
     same score (0.847 against 0.847) -- 100% of them, in all six insert orders tried. Records a
     caller can tell apart are not reordered.
   * **It has no direction:** over five randomised insert orders the hit@1 change runs +0.0094 to
     -0.0219, negative in 2 of 5. Natural conversation order alone reads -0.0062 and reproduces to
     four decimals every run, which is that fixture rather than the store: LOCOMO gold turns skew
     late, so gold records are newer and a fresh store flatters them.

   **What is NOT established is the mechanism.** The only clock-dependent input to ranking is the
   per-type decay term, which makes it the obvious candidate -- but a synthetic store built with the
   same age spread and the same tie structure does not reproduce the movement at all, so the
   explanation is incomplete and is not claimed here. Reproduce the measurement with
   `python probes/recall_over_a_time_gap.py`.

   Run-to-run determinism at a FIXED instant is a different property and it holds: two back-to-back
   reads are identical, and arm (a) of the reinforce ablation measures 0.0000 on every corpus.
   Time-invariance is not claimed and is not true.
4. **Value is reported at the cohort level** (tag / time-block), never per-memory.
5. **Contradictions are flagged, never auto-resolved.** Silent rewrites destroy trust in the whole
   memory.

## Provenance — why these rules, with receipts

<details>
<summary>Why these rules — the measured receipts behind inspeximus's design. Click to expand.</summary>

`inspeximus`'s design isn't taste; it's what Agora's lab *measured*:

- **Semantic recall beats keyword recall, and the gap widens with scale** — as the store grows to
  a corpus of several thousand notes, lexical `recall@5` decays from **0.94** (small store) to **0.25**,
  while semantic **holds at ~0.65** — ≈**2.6×** at full scale (Agora Lab `b4c260`); on paraphrase
  queries semantic `recall@5` is **0.86 vs 0.20** lexical (`3501f1`). The embedder is the real lever
  at scale; the lexical overlap match is the zero-dependency *floor* that still runs anywhere on a
  small store. (Honest footnote: pruning
  universal-matcher *hub* notes lifts **lexical** recall ~20% only when a store is link-spammed, and
  does **not** move semantic recall — it's a lexical/hybrid optimisation, not a headline.)
- **Value-ranked consolidation** — under a keep-budget, ranking *what to keep* by value beats
  FIFO/random, and the advantage **scales super-linearly as the budget shrinks** (≈1.8× at half
  budget → ≈4× at one-eighth), surviving heavy estimation noise.
- **Retention must blend value with recency, not decay on access alone** — we simulated a
  half-life-with-access-reset policy (a *popularity* signal) against a value-aware blend under a
  shrinking budget, with value made deliberately anti-correlated with access-frequency for a
  load-bearing-but-cold subset. At a 30% keep-budget the access-decay policy retained only **2.8%**
  of the high-value/low-frequency memories and **20%** of total value, vs **100%** and **64%** for
  the blend — about **3× more value kept** (the gap persists, ≈2.2× retained value, even at a 7%
  budget). Pure access-frequency decay starves the rarely-queried-but-critical memories; forgetting
  must consume an explicit value channel *separate from* access recency. (Agora Lab `19d802`.)
- **Supersession needs a deterministic key, not embedding similarity** — replicating an external
  result (MemStrata / Yadav, arXiv 2606.26511) on our own local `nomic` stack: a cosine-similarity
  classifier separating a *contradicted* fact from a *rephrased duplicate* scores **AUROC ~0.61**
  (near chance) — a contradiction is often *more* embedding-similar to the original than a true
  rephrase is. A similarity-based store therefore serves the **stale value ~42% of the time**; the
  deterministic `(subject, relation, object)` supersession key (`remember(..., key=...)`) drives that
  to **0%**. Re-run it: `python probes/supersession_replication.py` (needs a local `nomic-embed-text`)
  reproduced AUROC 0.613, stale-fact-error 41.7% under pure cosine and 0.0% under the SRO key on
  2026-08-01. (The earlier version of this line also claimed a "severe-test 8/8"; the probe reports
  0/24, no artifact here produces an 8/8, and that figure has been withdrawn.) This is *why*
  supersession is a key, not a threshold.
- **No single recall mechanism survives all operating points — only the layered store does** —
  head-to-head on a synthetic *evolving + contaminated* stream (stable / superseded / poisoned facts,
  local `nomic`): a naive **cosine top-1** store scores **42%** (fine on stable, but blind to
  supersession — **0/8** on updated facts — and fooled by repeated lies); a **recency** store **67%**
  (fixes supersession but serves the *freshest lie* — **0/8** on poison); `inspeximus` — deterministic
  supersession key **+** corroboration gate **+** value-ranking — is **100%**, robust across all three.
  Each single mechanism wins one regime and loses another (the *memory operating-point trap*), which is
  why the durable layer needs all three together (probe `probes/operating_point_memory.py`).
- **Cohort-level value** — per-memory outcome attribution is **statistically underpowered at n-of-1**
  (the best proxy reached only ~0.36 power at realistic sample sizes); the cohort is where the
  signal lives. Hence rule 4.
- **Contradiction detection** runs in production over the 10,000-note vault; the lesson that it must
  *flag, not auto-edit* (rule 5) is why silent rewrites are forbidden.

(Methods + numbers live in the Agora track record: <https://dancenitra.github.io/agora/>.)

</details>

## Threat model & layered defense (adversarial memory integrity)

<details>
<summary>The full adversarial threat model + layered defenses. Click to expand.</summary>

An untrusted-ingestion memory store cannot decide whether a written claim is *true*. inspeximus doesn't try to;
it makes the attacker **pay**, and the honest map of what each layer buys — worked to bedrock across a public
practitioner thread with adversarial review — is below. Every claim here has a runnable receipt in
[`probes/`](../probes/); this is textbook mechanism with a receipt, **not** a new theory.

**A defense the attacker can also write is a suggestion, not a defense.** Content-declared provenance is
theater: `Source: X` and `corroborated by N` are strings a writer controls, so default (distinct source
*strings*) corroboration falls to a sybil that mints two labels (~0.9 attack-success across 10 models —
[`memory_defense_layer_probe`](../probes/memory_defense_layer_probe.py)). Only channels the writer does **not**
control hold — distinct *verified keys* (`strict_corroboration`, Ed25519 `attest`) **whose issuance is itself
costly/rate-limited** (a free-to-mint key is just another string a sybil spends), an *earned* Beta(good,bad)
outcome credit a session can't self-grant, and *system write-history*.
That is Biba integrity (1977) / Cheng-Friedman (2005): no symmetric reputation is sybilproof; the escape is
an exogenous, un-writable anchor.

**The layers, and the exact residual each leaves:**
- **Provenance — did the call happen?** Bind standing to a *runtime* signature over the real `(tool, result)`,
  not the session's log ([`execution_receipt_gate`](../probes/execution_receipt_gate_probe.py)). Closes fabricated
  logs **iff the executor is a trust domain separate from the session**; in-process tools → the key is
  session-readable → theater.
- **Veracity — is a real call's output true?** No write-time oracle (a novel fact has no priors; attestation =
  authorship, not truth). Standing is *earned at use time* from an observed outcome
  ([`veracity_outcome_grounding`](../probes/veracity_outcome_grounding_probe.py)) — only helps where harm is observable.
- **The irreversible tail.** A provenance-scaled `spend_irreversible` budget makes a low-provenance memory bind an
  honest action's irreversible spend against itself ([`soft_influence_taint`](../probes/soft_influence_taint_probe.py));
  a reversibility checkpoint bounds a one-shot defector. Bounds, doesn't close (Sybil relocates it per identity).
- **The sub-threshold coordinated campaign.** A temporal change-detector is evaded for free at bias `h→0`, but
  `B≈N·h` forces a growing correlated low-provenance population a **cross-sectional** detector catches
  ([`two_detector_trap`](../probes/two_detector_trap_probe.py)); and randomized retrieval raises a *targeted*
  attacker's coverage from O(1) to ~Θ(fN) with no anchor
  ([`moving_target_retrieval`](../probes/moving_target_retrieval_probe.py)).

**The bedrock.** Checked from ~8 directions (detection, reputation, elicitation-without-ground-truth à la Bayesian
Truth Serum, robust aggregation, moving-target), every layer we examined reduces to one of two exogenous anchors —
a bounded corruption *count* (Byzantine; sybils break it) or a shared attacker-independent *prior* (peer-prediction;
a coalition coordinating its reports breaks it). *(A synthesis over those cases, not a proof.)* You cannot separate
a large coordinated coalition from genuine consensus from internal signals alone (Cheng-Friedman +
Lamport-Shostak-Pease 1982; and no internal truth-oracle, by analogy to Tarski's undefinability). What that
leaves is not "give up" but a shape: **localize the one exogenous check at the rare high-consequence irreversible
step** (a human, a separately-provenanced feed — a channel the poison can't reach), and **don't let evidence-free
consensus drive an irreversible action** (weight it ~0; on an observable target, require an independent evidentiary
provenance, which is super-linear to forge, not N reputations). The residual is the integrity of that one minimal
anchor — a standard, bounded problem, not the intractable verify-all-memory one.

Prior art credited throughout: Biba 1977 · Douceur 2002 · Cheng-Friedman 2005 · Friedman-Resnick 2001 ·
Lamport-Shostak-Pease 1982 · Lorden 1971 / Moustakides 1986 (CUSUM delay floor) ·
Tarski (undefinability of truth, used by analogy) · Doyle 1979 (truth-maintenance) · Garcia-Molina & Salem 1987 (Sagas) ·
Prelec 2004 (Bayesian Truth Serum) · Blanchard 2017 / Yin 2018 (Byzantine-robust aggregation) ·
PoisonedRAG (Zou 2024) · MINJA (Dong, arXiv:2503.03704) · AgentPoison (Chen, arXiv:2407.12784) ·
the shilling / Sybil-detection line (Mobasher-Burke 2007, Mehta-Nejdl 2009, SybilRank/Cao 2012, Viswanath 2010).

</details>

## The `second_brain` thinking layer

An optional layer on top of the store — dialectic, contradiction
surfacing, question generation: **[docs/SECOND_BRAIN.md](SECOND_BRAIN.md)**. Note the scope honestly:
`second_brain_mcp.py` and `maintain.py` are **not shipped in this package or repository** — that document
describes a companion the audit could not find here, and every number in it is unverifiable from this
checkout until the files land.

## Status

`v2.22.0` — the core, honest and runnable, with an MCP server (`inspeximus-mcp`, 73 tools) and a
deterministic supersession key (`remember(..., key=...)`) that closes the embedding *supersession blind
spot*. Roadmap: pluggable vector stores, a hosted tier. Open-core; the core stays free.

MIT-licensed · part of [Agora](https://github.com/DanceNitra/agora).

<!-- MCP registry ownership proof -->
mcp-name: io.github.DanceNitra/inspeximus
