# Changelog

All notable changes to inspeximus (`inspeximus`). Format loosely follows Keep a Changelog; versioning is semver
(MAJOR = stable/breaking, MINOR = features, PATCH = fixes).

## 1.26.1 — the MCP server could not start (shadowed its own SDK)

1.26.0 renamed `mnemo_mcp.py` to `mcp.py`. That file also carried an old line inserting its own
package directory onto `sys.path` so it could be run as a loose script. Harmless under the old name;
fatal under the new one: with the package directory on the path, the module became importable as
top-level `mcp` and shadowed the MCP SDK, so `from mcp.server.fastmcp import FastMCP` resolved to
itself and every launch died with `'mcp' is not a package`.

- the module is now `inspeximus/mcp_server.py` (console script `inspeximus-mcp` unchanged), a name
  that cannot collide with the SDK
- the `sys.path` insertion is gone

Found by the acceptance test for `inspeximus install`: the config was written correctly and Claude
Code listed the server, but it reported "Failed to connect" -- which is exactly the failure an
installer must be tested against rather than assumed away.

## 1.26.0 — the name is gone from the code, not just the label

1.25.0 renamed the distribution but kept the old name alive inside: the core class, two module names,
a compatibility alias package, the environment variable and the store filename. That was a
backwards-compatibility argument for an installed base that measurement had already shown does not
exist, so it bought nothing and left the product half-renamed.

**Breaking, deliberately and all at once:**

- `Mnemo` -> `Inspeximus`; every integration class follows (`MnemoStore` -> `InspeximusStore`,
  `MnemoSaver` -> `InspeximusSaver`, and the rest).
- `inspeximus.mnemo` -> `inspeximus.core`; `inspeximus.mnemo_mcp` -> `inspeximus.mcp_server`.
- the `mnemo` compatibility alias package is **removed**, as are the `mnemo` / `mnemo-mcp` console
  scripts. `pip install inspeximus`, `from inspeximus import Inspeximus`.
- `MNEMO_PATH` -> `INSPEXIMUS_PATH`; default store `mnemo_memory.json` -> `inspeximus_memory.json`;
  the Claude Code plugin store `.mnemo/` -> `.inspeximus/`.
- the encrypted-store magic changes from `MNMO` to `INSP`, so a store encrypted before this
  release must be rewritten.

**Fixed:** the MCP module wrote "needs the MCP SDK" to stderr at import time. Anything that walked the
package's submodules therefore printed it on unrelated output. It now raises with that message
instead, where the caller who actually tried to start the server sees it.

## 1.25.0 — renamed to inspeximus

The package is now **`inspeximus`** (`pip install inspeximus`, `import inspeximus`). The name is the
medieval charter that recites an earlier charter verbatim and attests it unaltered — the same act this
library performs on a corrected fact.

- `pip install agora-inspeximus` -> `pip install inspeximus`; console scripts `inspeximus` and
  `inspeximus-mcp`.
- **`import inspeximus` keeps working** and resolves to the *identical* objects, not copies, so
  `isinstance` checks, monkeypatching and module state behave the same across both namespaces. The
  alias is deprecated and will be removed in 2.0.
- Old console scripts `inspeximus` / `inspeximus-mcp` remain as deprecated aliases.
- **Unchanged on purpose:** the default store file (`inspeximus_memory.json`), the `INSPEXIMUS_PATH` environment
  variable, the plugin's `.inspeximus/memory.json` project store, and the public class names
  (`InspeximusStore`, `InspeximusSaver`, ...). Renaming any of them would orphan existing stores or break
  callers for no benefit; they can follow in 2.0.
- Repository and homepage moved to `DanceNitra/inspeximus`; GitHub redirects the old paths.

## 1.24.4

Adds `examples/trust_is_not_truth.py` — a standalone, pip-installable demonstration that the provenance
gate is an authorization control and not a truth detector: a trusted key signing a false fact returns
the false fact at full weight, and a correct fact signed by an unknown key is dropped. The earlier
version of that test lived in a gitignored directory and reached into a sibling checkout, so nobody
outside this machine could run it — for a test whose whole point is "check us", that made it worthless.

First release published through GitHub Actions with PyPI Trusted Publishing, so the wheel carries a
signed attestation binding it to this repository, this workflow and this commit.

## 1.24.3

**BUGFIX (regression from 1.24.0): double deletion receipts.** `forget_subject()` and `forget_pii()`
call `forget()` and then emitted their own tombstones. Once `forget()` started emitting in 1.24.0 that
produced **two receipts per erased record** — one carrying the caller's real basis, one carrying a
generic `basis="forget"` — so an auditor saw a single deletion twice, with conflicting reasons. Both
now pass `request_id` / `basis` / `authorized_by` / `authorization` through `forget()` and emit once.

**New: `governance_audit.py`.** Attacks the claim "tell it to forget everything about a subject and it
can prove it" across three scenarios x three repeats: erasure through `derived_from` lineage, absence
from records, from recall under several phrasings, and from the BYTES of every file including sidecars;
exactly one receipt per record carrying the caller's basis; tamper detection; survival across a reload;
unrelated records intact; identical end state every run. `GOV_FALSIFY=1` skips the erasure and 7 of 11
checks must fail — a test that cannot fail is a demo.

That audit is what caught the double-receipt regression, and only after its own first version was
tightened: it asserted "at least one receipt per record", which passed the bug.

## 1.24.2

**Docs only: the landing page is readable again.** The README had grown to 124 KB / 1587 lines — ten
times mem0's — with a 600-line API reference and a 300-line integration catalogue sitting between the
pitch and the proof. Nothing was deleted: those blocks moved verbatim to `docs/API.md`,
`docs/INTEGRATIONS.md` and `docs/SECOND_BRAIN.md`, leaving pointers. README is now 31 KB.

Also fixes stale version strings that had been shipping for months: the header still said v1.12.1, the
CLI section v1.12.4, and `server.json` — the manifest the official MCP registry reads — was pinned at
1.12.2 while the live registry entry still advertised **0.7.19** and pointed at the wrong repository.

## 1.24.1

**Docs only.** `claims_audit.py` is now the first thing the README offers: one command downloads the
published wheel and checks every claim on the page against that artifact, printing raw evidence per
claim. Claims about other systems are listed separately and never counted as passing.

Adds the measured write cost from the MemOps run (600-730 s of LLM extraction per scenario for an
LLM-on-write pipeline against zero model calls here) **together with the finding that answer accuracy
was statistically indistinguishable** — the honest claim is same answers at no write-time model cost,
not better answers.

## 1.24.0

**`forget()` now emits a deletion receipt, like every other erasure path.** Previously only
`forget_subject()` and `forget_pii()` wrote a hash-chained tombstone. A record removed with plain
`forget(ids=…, where=…)` was therefore deleted correctly — gone from the store and from the bytes on disk —
but *unaccounted for*: `verify_writes()` found a write receipt whose record no longer existed, with nothing
explaining the absence, and reported `deleted out-of-band`, which is precisely the signature of someone
editing the store behind its back. The store flagged its own legitimate API call as tampering.

`forget()` takes optional `request_id=` and `basis=`, both committed inside the tombstone hash, and returns
`tombstones` alongside `forgotten` / `ids` / `scrubbed_links`. Regression probe:
`probes/forget_emits_tombstone_probe.py`.

Found by installing the published wheel into a clean room and testing the README's own claim — the record
was gone, the bytes were gone, and the receipt count was zero.

**Probe fix (was reporting FAILED against correct code):** `trusted_only_poison_defense_probe` still
asserted the pre-1.19.0 fail-OPEN behaviour of `recall(trusted_only=True)`, which 1.19.0 deliberately
reversed. The code was right and the test was wrong, so the suite carried a permanent red — the kind that
teaches you to stop reading red.

## 1.23.1

**BUGFIX (silent data loss): `regex_extractor` minted keys from non-referring subjects.** On natural
conversational prose the copula patterns fire on pronouns, expletives and interrogatives — "It is
important to ...", "There is a growing ...", "These are just a few ...", "What is ...?" — producing the
keys `it`, `there`, `these`, `what`. Those keys collide across completely unrelated sentences, and keyed
supersession then RETIRES the earlier record, hiding it from recall. Measured on a real conversational
corpus (the MemOps dataset, arXiv 2607.12893) before the fix: **103 supersessions in one 3.7k-sentence
transcript, 83% of them driven by such a key** — a universal-basic-income sentence was retired because a
London-landmark sentence shared the subject `what`. The README advertises this extractor precisely so
"supersession engages over free text", so the exposure was real.

Fix: a subject that IS, or ENDS IN, a non-referring word yields no key, which is the extractor's already
documented fallback (return None -> plain append). Nothing that produced a key before loses one:
`my zip code`, `my manager`, `my current title`, `alice::email`, `france::capital`, `api rate limit` all
still key, and a real correction still supersedes. Spurious supersessions on the same corpus drop
**103 -> 18, 74 -> 13, 71 -> 17**.

Why no probe caught it: every existing extractor probe fed clean declarative statements. New regression
probe `probes/extractor_nonreferring_subject_probe.py` (16/16) ingests the failing shapes end to end.
Suite 148.

## 1.23.0

**Read-time conflict resolver: `recall(resolve_conflicts=True)` (default OFF → byte-identical legacy).**
The write-time guards (keyed supersession, echo_guard) cannot reach an UN-KEYED re-assertion of a retired
value — it lands as an independent record, embeds near-identically to the correction, and can out-rank it
(our own 1.21.0 validation demonstrated the failure; the mechanism matches the stale-serve findings in
arXiv 2606.01435, whose read-time deterministic resolution reports +10.8 pts single-hop). The resolver
clusters near-duplicate same-subject candidates in the top pool (token-Jaccard ≥ 0.6 or identical
normalized text) and resolves each cluster by **value birth**: a value's timestamp is its EARLIEST
assertion anywhere in the store, superseded rows included — so restating an old value never refreshes it
(the echo keeps its old birth and loses), while a genuinely new value wins as the newest birth. Losers are
demoted below the kept pool (backfilled, not hidden); the surviving hit carries `resolved_over: [ids]`.
Deterministic, zero-LLM, read-only. Documented limit (same as echo_guard): a deliberate un-keyed reversal
to an older value reads as an echo — use keys + `reaffirm=True` for authoritative reversals.

MCP: the `recall` tool takes `resolve_conflicts`, or set `INSPEXIMUS_READ_RESOLVER=1` server-wide.

Receipts: `probes/read_conflict_resolver_probe.py` (9/9 — incl. proof the failure EXISTS without the flag,
honest-update wins, keyed-superseded birth inheritance, no false clustering across subjects, determinism);
LoCoMo regression with the resolver ON is IDENTICAL to baseline on every k (0.397/0.582/0.668/0.750/0.839,
n=1536 — no conflicts to resolve there, and no damage from clustering). Suite 148.

## 1.22.1

**Measurement correction propagated to the shipped text (no code change).** The `Inspeximus` docstring and the README
still cited the 1.15.0 `recall_any@1 0.19 → 0.29` delta, which the 1.15.0 CHANGELOG correction had already
declared contaminated by the recall-reinforcement confound. Both now carry the clean, reinforcement-controlled
number (recall_any@1 **0.397** with nomic prefixes, LoCoMo n=1536) and point to the correction. The correction
note itself gained a paired-bootstrap re-verification (5000 resamples, fixed seed, Bonferroni across the 5 k's):
vs a raw-cosine baseline over the same embeddings, @1 is a statistical tie, k=3/k=5 are small Bonferroni-surviving
wins (Δ +0.023 / +0.032), @10/@25 positive but not significant. Receipts:
`agora_output/lab/locomo_recall_clean_reinforce.result.json` + `locomo_reinforce_flag_fair.result.json`
(both re-run 2026-07-19 evening and reproduced exactly — the pipeline is deterministic).

## 1.22.0

**MCP: the hydration-witness primitives are now tools.** `witness`, `verify_witness`, and `index_coherence`
are exposed over the MCP server, so any Claude/Cursor/agent client can pin an answer to the store revision it
was derived from ("this answer reflects store state as of revision X"), check later whether that answer
predates a change, and ask whether the derived semantic index agrees with the store — all deterministic,
zero-LLM, read-only (witness/verify) exactly as in the 1.21.0 core. Smoke-tested end to end through the MCP
module (witness → verify true on unchanged → false after a write; coherence report fields present).

## 1.21.0

**Hydration witness: `witness()` / `verify_witness()` / `state_digest()`.** A compact, deterministic receipt
of the store state an answer was derived from — "this answer reflects store state as of revision X".
`state_digest()` is an order-independent SHA-256 over exactly what retrieval can serve (id, status, ts, key,
tenant, content hash), so any write, supersession, revert, erasure, or out-of-band edit changes it;
`verify_witness()` later says whether the answer predates a change. With `receipts=True` the witness also
carries the write-receipt chain tip, anchoring the pinned state to the tamper-evident write history. Honest
scope: the witness pins THIS store and its view of its index inputs; it cannot attest external caches or
copies it never saw. Motivated by the shared-team-memory discussion (anthropics/claude-code#38536): governed,
git-backed stores are still read through a derived index, and provenance receipts need a cheap thing to pin to.

**Index coherence: `index_coherence()`.** Deterministic, read-only answer to "does the derived semantic index
agree with the store?" — reports active text records missing a vector while an embedder is configured (index
behind store), persisted-vector recipe vs the current `embed_id` (the sidecar guard's view), and the
`persist_vectors` regime. This operationalizes the exact bug class behind the 1.15–1.18 realign fixes as a
user-callable check instead of tribal knowledge.

**README honesty pass (from the same adversarial review):** the `echo_guard` bullet now states its real scope
(keyed or extractor-derived assertions — a free-text write nothing keys is an independent record), and the
org-wide erasure receipt heading no longer says "your WHOLE stack": the manifest is an auditable trail over
the stores you REGISTER, and cannot attest a copy nobody registered (unknown caches, backups, already-hydrated
contexts).

Probe: `probes/hydration_witness_probe.py` (12/12 — determinism, every retrieval-visible mutation flips the
digest, receipts-tip anchoring, lag + recipe-mismatch detection).

## 1.20.0

**Claude Code hooks are now LEXICAL by default (opt in to semantic with `INSPEXIMUS_EMBED_HOOKS=1` or config
`{"embed": {"hooks": true}}`).** The hooks run in the agent's hot path — PostToolUse after every
Edit/Write/Bash, UserPromptSubmit blocking prompt submission — and with a local GPU embedder each capture
cost one embedding call: ~2s on an idle GPU, unbounded on a busy one (this plugin's own dogfood machine runs
a 21GB LLM on the same card). The capture is deterministic and keyed either way, and on a coding store the
embedder buys little (its bulk is `ran: ...` mechanics, the least semantic content there is). Measured on the
dogfood store: 2.8s -> 0.65s per hook, zero GPU traffic. Semantic recall in the MCP server, CLI and library
is unchanged — this narrows only the hook hot path.

Two core guarantees added so a lexical open is a pure bystander on a semantic store: the plugin always opens
with `persist_vectors=True` (a vec-less open would otherwise strip every persisted vector on its first save),
and `_save()` leaves the `.embedid` sidecar untouched when `embed_id` is None (blanking it would make the
next semantic open see `''->recipe` and realign for nothing). Probe gains regressions 9/9b.

## 1.19.0

Security and correctness pass over everything 1.16.0–1.18.0 shipped, from an audit of the whole unreleased
range. Three of these contradicted guarantees this CHANGELOG had already made.

**SECURITY — stored XSS in the memory browser.** `render_html()` inlines the rows into an inline `<script>`
via `json.dumps`, which does not escape `<` `>` `&`. A memory containing `</script>` therefore closed the
element and everything after it was parsed as live HTML in the opened `file://` document; the JS-side `esc()`
never ran, because the breakout happens at parse time. Memory text is exactly what agents ingest from tools,
web pages and MCP callers, so this was reachable through ordinary use. Now escaped as `\uXXXX` — transport
only, text round-trips byte-identical.

**SECURITY — `route()` could hard-delete on a default store, by content alone.** The routed DELETE gated on
`_revert_authorized()`, which returns True when neither `revert_authority` nor `revert_pubkey` is configured
(the "legacy" rule — safe for revert, which only moves along the version graph). On a default store that let
`route("forget that address")` reach `forget()` and irreversibly destroy every active record for the key,
directly contradicting 1.17.0's claim that DELETE is "capability-gated: content alone can't destroy memory".
A routed delete now requires an authority to be **configured**, then satisfied; otherwise it returns
`authorization_required` and points at out-of-band `forget()`/`forget_subject()`.

**Delete no longer pre-empts corrections and reverts.** The delete vocabulary overlaps both, and was tested
first, so `route("drop the beta flag; region is now us-east")` (a correction) and `route("undo that, it is no
longer valid")` (a revert) were swallowed as deletes and their writes never happened. DELETE now requires the
utterance to carry no value and no revert marker.

**BEHAVIOR REVERSAL — `recall(trusted_only=True)` now fails CLOSED.** It previously skipped the filter
entirely when no `trust_seeds` were configured and returned the whole untrusted pool — deliberate ("fail-open,
not empty") but wrong for a security flag: it returned exactly the poisoned records the caller asked to
exclude, indistinguishable from a successful trusted recall. With no trust root nothing can be anchored to it,
so the honest answer is no trusted memories. Configure `trust_seeds` to get hits.

**Cross-tenant leak through the newer surface.** `_TenantView` forwards unlisted methods to the parent, where
they run parent-bound (`self.tenant` = None): `remember_decision()` and `distill_and_remember()` wrote records
with **no tenant stamp** (visible to every other view), `graph()`/`subgraph()` returned **every** tenant's
edges, and `route()`'s delete id-selection matched the wrong tenant. All five are now rebound.

**MMR was cubic in the pool.** The greedy loop selected the entire pool (only the first `k` survive) and
recomputed every pairwise cosine uncached: ~p³/6 similarity calls. Fine at the default p=50, ~1.3M at k=50,
~1e9 for a caller passing `rerank_pool=2000` — an effective hang. Now bounded to `k` and memoized.

**`reembed()` + `inspeximus reembed`.** The explicit counterpart to 1.18.0's bounded embed-recipe guard: when a
recipe change finds more than `INSPEXIMUS_REALIGN_MAX` stale vectors the guard drops them (lexical fallback) rather
than making every open pay a network call per record. This is how you deliberately rebuild that space —
foreground, with a count, `--batch`-able — instead of implicitly on a load path. `route()`'s NOOP now also
carries an explicit `"id": None`, so callers reading `["id"]` no longer `KeyError` on a duplicate write.

## 1.18.0

**Fix: the embed-recipe guard could re-embed the whole store on EVERY open.** With `persist_vectors=True` and a
stored recipe differing from the current `embed_id`, the realignment (a) re-embedded every record rather than only
the vector-bearing ones, and (b) recorded the new recipe only inside `_save()` — so any caller that never saves (a
read-only `recall()`, a session digest, a short-lived hook process) redid the entire realignment on every open.
Together that turned a one-time migration into a permanent per-open network storm: a 1214-record store issued 1214
embedding calls *per open*, forever — which froze a Claude Code session through the `inspeximus.claude_code` hooks
(~44 min per hook; the hook blocks prompt submission). The guard now realigns only vector-bearing records, persists
the realignment exactly once (vectors and sidecar together — never the sidecar alone, which would label old vectors
with a new recipe), and is bounded by `INSPEXIMUS_REALIGN_MAX` (default 256): past the cap it drops the stale vectors
(those records degrade to lexical recall and are re-embedded on their next write) instead of stalling the load
path. Measured on the affected store: 44 min -> 17 s once, then 2.6 s per open. Probe
`embed_recipe_migration_guard_probe.py` gains regressions 5/5b/6/6b/6c/7/7b.

**Fix: `inspeximus.claude_code._make_embedder` returned a bare `None` when unconfigured** while its caller unpacked three
values — a `TypeError` that the hook's fail-open swallowed, so in any project without `.inspeximus/config.json` the
plugin silently captured nothing at all.

**Deterministic knowledge graph (`graph()` + `subgraph()`).** Every keyed `(subject::relation, object)` memory is an edge subject-[relation]->object; `graph()` exports nodes+edges and `subgraph(entity, hops)` does multi-hop traversal — the graph-memory view mem0/Zep/cognee ship, but DERIVED deterministically from inspeximus's supersession triples (no LLM entity-extraction, no graph DB). Superseded facts drop out (graph = current truth). Probe `graph_layer_probe.py` (5 checks incl. supersession-drop + 2-hop).

## 1.17.0

**Named reranker menu (`recall(rerank_by=...)`).** A discoverable set of deterministic, zero-LLM reorderings of the
top relevant pool — `recency` (newest by event-time first), `value` (highest accrued importance), `reliability`
(best Beta good/bad track record — was-it-right, not just similar), `relevance` (explicit no-op). Complements the
`mmr=` diversity knob and the `rerank=` cross-encoder hook; exposed on the MCP recall tool. Probe
`rerank_menu_probe.py` (3 strategies engineered to pick 3 different top-1s, proving the menu discriminates).

**One-call `route()` now emits mem0-parity ADD / UPDATE / DELETE / NOOP.** The single-call write router decides the
ledger op deterministically (zero-LLM): a new keyed fact -> ADD, a new value for a key -> UPDATE (keyed
supersession), re-stating the current value -> **NOOP** (skips the duplicate write — directly attacks unbounded
growth), a deletion utterance ("forget that", "no longer true") -> **DELETE** (capability-gated: content alone can't
destroy memory, preserving the channel-separation moat), and a revert utterance -> REVERT. Each return carries an
`event` field so mem0's add()-reconcile mental model maps 1:1 — a deterministic drop-in. Probe
`route_add_update_delete_noop_probe.py` (6 checks incl. NOOP-writes-nothing + delete-moat).

**Memory hierarchy — `user_id` / `agent_id` / `session_id` scoping (mem0/Letta-style).** `remember(...)` stamps a
memory's scope; `recall(...)` filters by hierarchical visibility: a session query sees that session's memories PLUS
the user/agent-level shared ones, but never a peer session's; users are isolated from each other; a user-only query
sees all that user's own memories; unscoped = global. Deterministic, in-core (on top of the existing hard `tenant`
isolation + soft `scope`); exposed on the MCP `remember`/`recall` tools too. Probe `memory_hierarchy_probe.py`
(4 checks incl. peer-session + cross-user isolation).

## 1.16.0

**LangGraph checkpointer (`InspeximusSaver`).** The thread-state half of LangGraph memory (InspeximusStore was the long-term
half): a `BaseCheckpointSaver` so a graph can persist + resume, same contract as SqliteSaver/PostgresSaver but in a
single zero-dependency inspeximus file (no DB, no server). Checkpoints + pending writes serialized via LangGraph's own
serde, tagged so they never pollute recall. Sync + async; `inspeximus.integrations.langgraph.InspeximusSaver`.

**Offline memory browser (`inspeximus.browser` + `inspeximus browse`).** Renders the store to a SINGLE self-contained HTML
file (all data inlined, vanilla JS, inline CSS — no server, no build, works offline) with client-side search +
filters and a summary header (counts, cohorts, contradictions); shows active vs superseded so you can SEE
corrections. Read-only by design. The console every competitor ships and inspeximus lacked.

**Rich MCP server — resources + prompts + governance/integrity tools.** The MCP server was tools-only (19/60
methods). Now exposes the 3 MCP primitives: +8 governance/integrity tools (forget_subject, governance_report,
verify_writes, pii_report, forget_pii, influence_gate_report, why_recalled, supersession_report), 3 resources
(`inspeximus://digest`, `://contradictions`, `://governance`) + a `inspeximus://memory/{id}` template, and 3 prompts
(recall_before_answer, consolidate_session, review_contradictions); `recall` now takes `mmr` + `trusted_only`.
27 tools total.

**Fatter CLI (6 → 13 commands)** + **`default_distiller`.** New: `browse`, `decision`, `contradictions`,
`governance`, `consolidate`, `why`, `distill`. `inspeximus.default_distiller()` is a zero-dep urllib chat caller (any
OpenAI-compatible endpoint via `INSPEXIMUS_LLM_URL`) so `distill_and_remember` works out of the box — opt-in (the core
stays zero-LLM), raises a clear error if no endpoint is set.

**`recall(mmr=λ)` — result-level diversity / dedup.** A top-k that isn't dominated by near-identical memories, via
greedy Maximal Marginal Relevance (Carbonell & Goldstein 1998 — a standard IR technique, not novel here). The value
is that it is **in-core, zero-LLM, and works with OR without an embedder** (diversity by record vectors, falling back
to token-Jaccard so lexical recall dedups too) — the "unbounded redundant results" lever that mem0/Hindsight
explicitly declined. `next = argmax[λ·rel − (1−λ)·max cos(d, chosen)]`; `rel` is the composite score min-max
normalized over the reranked pool. `mmr=1.0` is a no-op (pure relevance); lower = more diverse. Default off (no
behavior change); composes after the `rerank` hook. Probe `mmr_result_dedup_probe.py` (5 checks incl. plain-returns-
duplicates so the test can fail).

## 1.15.0

**Asymmetric query embedder (`embed_query`) — a recall correctness fix for nomic-embed-text.** nomic-embed-text is
trained to prefix stored text with `search_document: ` and queries with `search_query: ` (Nomic's model card;
asymmetric prefixing is standard retrieval practice, cf. E5's `passage:`/`query:`). inspeximus was omitting the prefixes,
which is simply using the model wrong. `Inspeximus(embed=…, embed_query=…)` now lets the recall QUERY be embedded
differently from stored TEXT (defaults to `embed`, so existing setups are byte-identical); the MCP server
auto-applies the nomic prefixes when `INSPEXIMUS_EMBED_MODEL` contains `nomic` (opt out with `INSPEXIMUS_NOMIC_PREFIX=0`).
Impact, measured against our OWN prior (unprefixed) behavior on one LoCoMo config (`agora`'s `locomo_prefix_scale.py`,
deterministic, all 10 conversations, n=1536): **recall_any@1 0.193 → 0.294, @25 0.754 → 0.807**. Scope, stated
plainly: this is a self-comparison bug-fix on a single dataset/embedder; `recall_any` (≥1 gold turn retrieved) is a
retrieval upper bound, not end-to-end QA, and multi-hop full-recall barely moves. We make no cross-system claim here.

> **Correction (post-release, self-comparison only).** The `0.193 → 0.294` figures above were measured with a second
> defect still active: `recall()` reinforces each hit's value, and sweeping many queries against one store makes the
> ranking order-dependent (later queries see values shifted by earlier hits) — a confound that depresses benchmark
> recall_any by up to ~0.10 at low k. With reinforcement disabled (a new `recall(reinforce=False)` kwarg returns a
> non-mutating read — no value bump, decay-clock reset, or graduation; default `reinforce=True` is unchanged),
> re-measured on the same LoCoMo config against our OWN plain-cosine baseline over the same nomic embeddings, inspeximus is
> **indistinguishable from that cosine baseline within measurement noise** (recall_any@1 0.397 vs 0.390; single run,
> n≈1536, no confidence interval — read as "no measurable gap", not a proven win).
> *Re-verified 2026-07-19 with a paired bootstrap (n=1536, 5000 resamples, fixed seed, Bonferroni across the 5 k's
> tested): @1 remains statistically indistinguishable (Δ +0.007, 99% CI [−0.009, +0.024]); at k=3 and k=5 inspeximus's
> native ranking is a small but Bonferroni-surviving WIN over raw cosine (Δ +0.023, 99% CI [+0.005, +0.044] and
> Δ +0.032, 99% CI [+0.014, +0.051]); @10/@25 positive but not significant after correction. Same scope as above:
> one dataset, one embedder, retrieval upper bound, self-built baseline — no cross-system claim.* So the integrity core adds no
> detectable recall penalty *in this eval mode*; under the default reinforced path the number is lower (0.294), so that
> statement is scoped to `reinforce=False`. The two fixes (prefixes + reinforcement) substantially account for the
> earlier gap. We make no claim about any external system's retrieval — none was run here.

**Migration guard (persisted vectors).** Because a query and its stored vectors must live in the SAME embedding
space, changing the embed recipe (e.g. turning prefixes on) would silently mis-rank an existing `persist_vectors=True`
store. `Inspeximus(embed_id=…)` fingerprints the recipe into a `<path>.embedid` sidecar; on open with a different
`embed_id`, the persisted vectors are re-embedded once with the current embedder so the space realigns (RAM-only
default stores are unaffected). Probes: `probes/embed_query_asymmetric_probe.py`, `probes/embed_recipe_migration_guard_probe.py`. Suite 148/148.

## 1.14.0

**Compact MCP recall + progressive disclosure (standard context-economy practice, applied to inspeximus).** A memory
server that returns every internal field burns the agent's context on data it never reads. Over MCP, `recall` now
returns a **compact projection** — `{id, text, score, value, tags}` — dropping internal bookkeeping (links,
provenance, ISO stamps, relevance/reliability breakdown); `k` is hard-capped (`INSPEXIMUS_MAX_K`, default 50). **Full
text is kept by default** — snippet truncation is **opt-in** (`snippet_chars>0`), deliberately NOT the default,
because truncating a hit could cut off a corrected value that sits past the boundary and silently defeat inspeximus's
own supersession/echo-guard. Two companion tools do progressive disclosure: `get(id)` returns one full record,
`neighbors(id, k)` a bounded local expansion (excludes self). `token_report(query, k)` is a **deterministic,
no-LLM** payload-size estimate (~chars/4) comparing the compact projection to the FULL records for the **same k
hits** — the honest apples-to-apples baseline, explicitly **not** a whole-store comparison and **not** a measured
token/cost saving. None of these are novel (progressive disclosure / small-to-big retrieval are standard MCP/RAG
practice); inspeximus already never emitted embedding vectors in recall. Core library and on-disk format unchanged;
`recall(full=True)` returns complete records. Receipt: `probes/inspeximus_mcp_token_pack_probe.py` (7/7), suite 148/148.
Eighteen MCP tools total.

## 1.13.0

**Auditor-grade erasure certificate — independently verifiable, no trust in the operator.** `m.erasure_certificate(request_id=...)`
packages the signed deletion tombstones (full hash-chain), the request-scoped erased ids, the receipt public
key, and a CT-style anchor into ONE portable, content-free JSON document. A third party runs the new module
function `verify_erasure_certificate(cert, store_path=...)` — WITHOUT the private key and WITHOUT trusting the
operator — and gets a machine-checkable verdict: the tombstone chain re-derives, every Ed25519 signature
verifies (pinnable to an expected pubkey), the anchor commits to the chain tip, AND every erased id is genuinely
ABSENT from inspeximus's store records (the value is deleted, not soft-deleted or kept in a history table by design
as most libraries do). Tampering a tombstone, faking an "erased" id that is still present, or pinning the wrong
key all flip the verdict to INVALID. This is the erasure primitive built for a right-to-erasure demand (GDPR
Art.17) with an Art.30-style auditable record — a governance capability most agent-memory libraries do not
expose. Honest scope stays in-band: it proves erasure from THIS store's records (the ACT, not the content;
witness the anchor externally for an operator-adversarial audit) — it is NOT secure at-rest erasure against
raw-disk/backup forensics (a plaintext store of any library leaves bytes in free space/backups → use an
encrypted store + `shred()`, NIST SP 800-88 crypto-erasure) and NOT the app's own vector store/logs (register
`ErasureTarget`s for cross-store cascade). Receipts:
`inspeximus/probes/erasure_certificate_probe.py` (9/9) + `inspeximus/probes/erasure_raw_store_probe.py` (12/12).

## 1.12.4

**`inspeximus` shell CLI.** A new console command to script the memory layer from the terminal — no Python and no
MCP server needed: `inspeximus remember "..." --key k`, `inspeximus recall "..."` (current-truth, superseded values
hidden), `inspeximus revert <key>`, `inspeximus forget --key/--id/--contains`, `inspeximus list`, `inspeximus stats`. Shares the
store with `inspeximus-mcp` (`--path` / `$INSPEXIMUS_PATH` / `./inspeximus_memory.json`); `--json` for scripting; lexical by
default, semantic when `$INSPEXIMUS_EMBED_URL` is set. Zero dependencies. Receipt: `inspeximus/probes/inspeximus_cli_probe.py`
(6/6).

## 1.12.3

**Optional reranker hook: `recall(rerank=callable, rerank_pool=N)`.** A retrieve-then-rerank extension point:
`rerank(query, records) -> list[float]` (one relevance score per record, higher=better) reorders the top
candidates before truncation to `k`. Model-agnostic (inspeximus imports no model) and moat-safe: no model runs
unless the caller supplies one, the WRITE path is untouched, default `None` = zero behavior change, and it
fails open (a broken or wrong-length reranker keeps the pre-rerank order). Honest scope: the lift is only as
good as the reranker — a model-READER reranker is the measured multi-hop lever (LoCoMo ~0.30->~0.48), whereas a
generic query-relevance cross-encoder does NOT help multi-hop (measured: it hurts, because 2nd-hop evidence
isn't directly query-relevant). Receipt: `inspeximus/probes/inspeximus_rerank_hook_probe.py` (5/5).

## 1.12.2

**Opt-out "a newer version is available" check.** When inspeximus runs (Claude Code `SessionStart`, or the MCP
server starting), it checks PyPI at most once per 24h and prints a single ASCII line if the installed version
is behind — the standard pip/npm/gh courtesy, so users who installed weeks ago learn about new integrity
features instead of silently staying on an old release. Fail-open (offline = silent), never blocks, and the
MCP server routes it to stderr so the stdio JSON-RPC channel is untouched. Silence with `INSPEXIMUS_NO_UPDATE_CHECK=1`.

## 1.12.1

**Claude Code plugin: a one-time, opt-out star nudge.** After inspeximus has actually been useful — 25 captured
writes in a project — the plugin prints a single, warm request to star the repo on the next prompt, then never
again. ASCII-only (safe on non-UTF-8 consoles), never blocks, and silenced anytime with `INSPEXIMUS_NO_NUDGE=1`.
Tied to a moment of demonstrated value, not to install time (which wheels can't run anyway).

## 1.12.0

Additive only, no breaking changes.

**CrewAI integration.** `inspeximus.integrations.crewai` ships `InspeximusStorage`, a drop-in CrewAI `Storage`
(`save`/`search`/`reset`) you hand to `ExternalMemory` (or any custom-storage slot). `search()` retrieves
through inspeximus's supersession-filtered `recall()`, so a corrected fact never returns into the crew's context.
Duck-typed — CrewAI is matched structurally and never imported, so the zero-dependency core is untouched.
Opt-in extra: `pip install "inspeximus[crewai]"`. Receipt: `inspeximus/probes/inspeximus_crewai_adapter_probe.py` (6/6).

**Claude Code plugin: optional semantic recall.** The auto-capture plugin (`inspeximus.claude_code`) now supports
SEMANTIC recall against any OpenAI-compatible `/embeddings` endpoint (e.g. local Ollama), configured by env
(`INSPEXIMUS_EMBED_URL` / `INSPEXIMUS_EMBED_MODEL`) or a per-project `.inspeximus/config.json`. Default stays deterministic
LEXICAL (runs anywhere, no service). Writes remain verbatim, keyed and no-LLM; the embedder only builds a
retrieval index and fails open (a down endpoint degrades to lexical, never drops a capture).

**New `Inspeximus(persist_vectors=True)` option.** By default embedding vectors are a RAM-only cache stripped on
save (keeps the file small and dodges the frozen-world GIL stall on large stores). `persist_vectors=True`
keeps them on disk — intended for a SMALL, frequently-reloaded store (the Claude Code plugin sets it when an
embedder is configured) so semantic recall survives a reload without re-embedding every item on each start.
Leave it off for large brain-scale stores.

**Docs.** The LangChain adapter (shipped in 1.11.0) now has a full entry in the framework-integrations table
and its own README section.

## 1.11.0

Three additive features, no breaking changes.

**Ready-made write-path extractors.** `regex_extractor` (deterministic, no LLM — keeps the zero-LLM-on-write
core) and `make_llm_extractor(call_fn)` (opt-in; puts an LLM on the write path in exchange for auto-capture of
unstructured text). Set `m.extractor = regex_extractor` and supersession/echo_guard/revert engage over free text
without the caller passing an explicit `key`. Both fail-open (a returned `None` falls back to a plain append).

**LangChain integration.** `inspeximus.integrations.langchain` ships `InspeximusRetriever` (a `BaseRetriever` whose
results are supersession-filtered — a corrected fact is never retrieved back into the prompt) and
`InspeximusChatMessageHistory`. Opt-in extra: `pip install "inspeximus[langchain]"`.

**Tuned recall recipe + a measured LOCOMO number.** `inspeximus/examples/recall_recipe_locomo.py` shows the built-in
levers (an embedder → lexical+semantic hybrid RRF; a soft speaker/entity prefilter via `recall(prefer=...)`) that
put inspeximus in the top tier on retrieval. Measured on the full LOCOMO benchmark (n=1536), LLM-free and reproducible:
retrieval-recall@25 = 0.783 (any evidence turn) / 0.648 (all). Run `inspeximus/probes/retrieval_recall_locomo.py`.

## 1.10.0

Claude Code integration: deterministic, no-LLM auto-capture of coding-agent memory. `python -m
inspeximus.claude_code --install` writes lifecycle hooks (`PostToolUse` / `UserPromptSubmit` / `SessionStart`) into
`.claude/settings.json`. `PostToolUse` captures Edit/Write/MultiEdit/Bash events into a deterministic keyed
store (`file:<path>`), so a corrected fact supersedes the stale one and `echo_guard` blocks its resurrection;
`UserPromptSubmit` injects the current-state memory; `SessionStart` digests the project's known files. No LLM on
the write path (unlike the LLM-summarizing coding memories, which drop facts, leak on erasure, and are
non-reproducible). Fail-open hooks, local JSON store at `.inspeximus/coding_memory.json`, `--uninstall` to remove.

## 1.9.0

Identity-confidence gate on supersession, with a candidate reconciliation queue. Prompted by a sharp reader
(marintkael): a keyed store supersedes on `(entity, field)`, but that is only correct if the identity the new
value attaches to is right. When identity is resolved fuzzily (an extractor / embedding match, not a caller
asserted key), a wrong match silently promotes into the authoritative record: a confident-but-WRONG ledger,
harder to catch than a set. Nobody in agent memory gates this: mem0, Zep/Graphiti and Letta all auto-commit an
ungated update.

**Not a new idea** (credited, not claimed): this is the record-linkage clerical-review zone (Fellegi & Sunter,
"A Theory for Record Linkage", JASA 1969: match / non-match / *possible match → review*) and MDM match-merge
stewardship (auto-merge above a threshold, route the intermediate band to a steward queue). The contribution is
the port into an agent-memory write path plus the measured prevention vs an ungated baseline.

- **`remember(..., identity_confidence=c)`** — `c` in [0,1] from your entity-resolution step. `c >= fork_below`
  (default 0.7, `Inspeximus.fork_below`) supersedes as before; **below it the write forks a CANDIDATE**
  (`status='candidate'`) that does NOT supersede and is excluded from authoritative resolution. Passing no
  `identity_confidence` = caller asserts identity = supersede, byte-identical legacy.
- **`candidates(key=None)`** — the reconciliation queue: each pending fork with its proposed key, value,
  confidence, and the current authoritative value it would replace.
- **`promote_candidate(id, capability=)`** — steward accepts: candidate becomes authoritative and supersedes the
  prior value. Takes the same capability as `revert()` when a revert authority is set (promoting a fuzzy match
  into authority is exactly the write to protect).
- **`discard_candidate(id, basis=)`** — steward rejects; authority never touched.
- Measured (probes/identity_gate_supersession_probe.py, deterministic, E=40, p_miss=0.2, 5 seeds): under noisy
  identity resolution an ungated auto-commit corrupts the authoritative ledger 13.5% of the time; the gate cuts
  that to **1.0% (a 93% reduction)** at the cost of a steward review queue (~65 candidates/run). Residual = mis
  resolutions that scored above the threshold (the gate is only as good as the confidence signal). Tenant-scoped;
  10 new tests; suite 99/99.

## 1.8.0

Cross-store erasure becomes a first-class operation. Motivated by a measured gap (audit report, July 2026):
a copy the application embedded into its OWN vector index survives every memory store's native delete (8/8 in
our cell, inspeximus included) — the store alone cannot fix that, because it cannot see infrastructure it was never
told about. 1.8.0 wires the fan-out into the erasure path:

- **`register_erasure_target(target)`** — register app-side stores (the app's vector index, embedding/response
  caches, retrieval logs) implementing the two-method `ErasureTarget` protocol (`erase(subject)`,
  `still_recoverable(subject, values)`). Targets are live client adapters, so they are RAM-only: re-register on
  process start.
- **`forget_subject(...)` cascades**: with targets registered it erases the store (as before), then every
  registered target, re-checks residual recoverability per target, and returns a hash-chained **`manifest`**
  in its result — honest by construction: `complete` is True only if EVERY store (inspeximus itself included, as the
  first self-checked target) verified the value no longer recoverable, and leaking stores are NAMED in
  `residual_targets`. Check values are captured automatically from the erased records (or pass `values=[...]`).
- Measured (deterministic cell, n=8): unwired external index leaks 8/8 after a store-native delete; wired it
  erases 0/8 with 8/8 `complete` manifests whose chains verify; a deliberately broken wiring produces ZERO
  falsely-complete receipts and names the leak 8/8. The receipt cannot lie about the fan-out it was given.
- **Honest scope (unchanged philosophy):** the manifest covers only REGISTERED targets — unknown copies stay
  unknown; it attests recoverability at check time, not physical destruction, and does not cover backups or
  embedding inversion of retained vectors. New tests: `tests/test_erasure_manifest_integration.py` (6).

## 1.7.0

Encryption-at-rest + crypto-shredding — the confidentiality leg of the governance layer (integrity +
provenance + erasure + **confidentiality**). Standard primitives only; we do not roll our own crypto.

- **`Inspeximus(path=..., encrypt_key=...)`** (raw 32-byte key from `new_encryption_key()`) or **`encrypt_passphrase=...`**
  (scrypt-stretched) encrypts the store at rest with **AES-256-GCM** (AEAD: confidentiality + tamper-detection),
  a fresh random 96-bit nonce per save, file layout `MAGIC(5)+salt(16)+nonce(12)+ciphertext` with the header
  authenticated as AAD. Opt-in, default OFF → byte-identical plaintext-JSON legacy. inspeximus never persists the
  key. A wrong key / tampered file **fails loud** (never a silent empty store). Needs the `cryptography` package.
- **`shred()`** — crypto-shredding: destroy the in-memory key so the on-disk ciphertext (and every at-rest
  backup of it) becomes permanently unrecoverable (NIST SP 800-88 key-destruction "Purge"), clearing plaintext
  from RAM. Supports a GDPR Art.17 erasure workflow.
- **Honest scope (documented, not overclaimed):** protects the store AT REST (a read file / stolen disk /
  backup); does NOT protect a compromised running process (key + plaintext in RAM), the key holder, or against
  malware — it is not end-to-end and not runtime protection. Prior art credited: SQLCipher, NIST SP 800-88,
  age/Fernet. Receipt: `probes/encryption_at_rest_probe.py`; 10 tests in `tests/test_encryption.py`.

## 1.6.0

Hard tenant isolation + a PII floor — logical multi-tenancy enforced by the store, fail-closed.

- **Tenant isolation, bound to the store (not a per-call arg).** `Inspeximus(tenant="acme")` binds a store to one
  tenant; `store.for_tenant(id)` hands out logically-isolated views over ONE shared physical store (shared
  items/file/caches, no duplication). Every write is tenant-stamped; recall, keyed supersession, the echo
  guard, erasure, **and the consolidation/dedup/contradictions/conflict paths** are hard-filtered to the
  acting tenant — so no forgotten parameter can leak or mutate another tenant's data. An unbound store is the
  admin view (sees all). Honest scope: logical in-process isolation, not a security boundary between hostile
  tenants. Measured receipt: `probes/tenant_isolation_probe.py` (cross-tenant read leak 0/20 with name+value
  detection, cross-tenant supersession 0, consolidation cross-links 0 — control shows 10 when unscoped —
  poisoning 0, over-erasure 0).
- **PII layer (a floor, not a DLP).** `detect_pii` / `redact_pii` module functions (regex; SSN/credit-card
  matched before the broad phone pattern); `remember(pii=...)` tagging or store-wide `pii_detect=True`;
  `recall(redact_pii=True)` masks PII in the RETURNED text only (the stored record is untouched);
  `forget_pii()` sweeps + tombstones PII rows for data minimization; `pii_report()` audits exposure. Regex
  catches structured formats and essentially no names — use a real DLP for detection; this is a
  zero-dependency default for reducing raw PII flow into prompts.
- README: 2-minute Quickstart up top; runnable `examples/` directory (basics, correction & erasure,
  bring-your-own-embedder semantic recall).

## 1.5.0

Provable forget + bitemporal audit — the governance/temporal pillar.

- **`ErasureAuditor.compliance_receipt(subject, values, sign=, pubkey=, request_id=, basis=)`** — runs the audit
  and packages it as a shareable, optionally-SIGNED proof-of-erasure receipt (the artifact a DPO hands a
  regulator under GDPR Art. 17 / EU AI Act record-keeping): which stores were checked, the per-store verdict,
  the request/basis, a timestamp, tamper-evident under your key. `verify_compliance_receipt(receipt, verify,
  expected_pubkey=)` re-checks it; `ed25519_signer(sk)` / `ed25519_verify` are BYO-key helpers (or plug an
  HSM/KMS). Crypto is a lazy import — the auditor framework stays dependency-free until you sign.
- **Bitemporal query** — `as_of(key, when, as_recorded=)` gains a second clock: pass a transaction-time
  `as_recorded` to reconstruct "what did we BELIEVE, at that recording time, was true at valid-time `when`",
  using only records written by then, so a correction recorded LATER can't leak into the earlier belief.
  **`believed_at(key, as_recorded)`** returns the value the agent would have acted on if frozen at that time —
  replay/audit without contamination. `as_recorded=None` is byte-identical to the prior valid-time `as_of`.
- **`probes/forget_verification_bench.py`** — an open benchmark for a capability no recall leaderboard scores:
  after a right-to-erasure deletion, does the value provably stop being recoverable across the 6-store fan-out
  (primary log, vector index, cache, Qdrant/pgvector/S3 soft-delete residue)? Scores soft-delete (the common
  "delete the row" bug: ~0.17 — five stores still leak) vs hard-delete (1.00, verified) and emits a signed receipt.

## 1.4.0

Soft-delete residual probes for the `ErasureAuditor` — from an r/RAG thread: a store reports a delete as DONE
(HTTP 200) while the data physically survives until a background compaction/vacuum/GC that may never trigger, so
"the API returned 200" and "it's gone" are two different things. Each probe calls only the client you pass —
inspeximus keeps ZERO external dependencies (no qdrant/psycopg/boto3 import).

- **`QdrantSoftDeleteProbe`** — deleted points sit in the bitmask until a segment crosses the optimizer's
  `deleted_threshold` (default 0.2, 1000-vector min); flags residue with compaction pending.
- **`PgVectorSoftDeleteProbe`** — MVCC dead tuples stay on disk (and the HNSW graph unrepaired) until VACUUM;
  reads `n_dead_tup` from `pg_stat_user_tables`.
- **`S3VersioningProbe`** — a "delete" on a versioned bucket is just a delete marker; the prior version is one
  `list_object_versions` call away.
- **`SoftDeleteProbe`** — generic escape hatch for the long tail (uncompacted Chroma segment, observability
  spans carrying full chunk text, CDC/Kafka topics, embedding-provider request logs): supply a `residual()` check.

## 1.3.0

Clean memory — a write-admission gate and an inspector, aimed at agent memory's #1 real-world failure:
indiscriminate writes (audited stores measured ~98% junk, one fact cloned 800+ times). All read-only or
opt-in; no change to existing `remember()`/`recall()` behaviour.

- **`admit(text, ..., dup_threshold=0.92, quality=True)`** — decide whether a candidate is worth storing BEFORE
  it bloats the store. Rejects empty / too-short / non-content (refusals, "no sources ..."), and skips a
  near-identical active memory (returns its id instead of appending a copy). A value UPDATE (same text, new
  number) is admitted so consolidation can supersede the stale value. Returns
  `{admitted, id, reason, duplicate_of, similarity}`. (Reliably kills exact/near-exact re-extraction bloat;
  paraphrase-level dedup is tunable via a lower `dup_threshold`, trading precision.)
- **`why_recalled(query, id=None)`** — inspector: the per-candidate score breakdown `recall()` ranks by
  (semantic cosine, lexical overlap, decayed effective value, corroboration good/bad, stale-derived flag, and
  the live rank), so "why did this surface / why not" stops being an archaeology dig.
- **`memory_report()`** — inspector overview: active/superseded, counts by type, consolidated, decayed, and a
  near-duplicate redundancy estimate — the surface that proves a store did NOT accumulate 800 copies of a fact.

## 1.2.0

Universal-executor gate — OPT-IN; default (`tool=None`) is byte-identical to 1.1.0 (verified by tests).

- **`is_universal_executor(tool, signature=None)`** — detect verb-polymorphic universal executors
  (shell/terminal, eval/exec, arbitrary SQL, generic HTTP, run-arbitrary-command) whose reversibility is NOT
  decidable from the tool signature.
- **`spend_irreversible(..., tool=, contained=)`** — when an irreversible action routes through a universal
  executor, a per-tool reversibility label is unsound and the executor's external harm-reach is bounded only by
  containment, so an *uncontained* universal executor is denied outright (the caller must sandbox it,
  `contained=True`, or route the effect through a specific signature-decidable tool). `contained=True` falls
  through to the normal per-source budget check.
  Motivation is measured (inspeximus lab, ToolEmu 330 tools, 2 labelers): tool reversibility is ~93% decidable from
  the signature (Cohen's κ=0.82); the ~7% undecidable residual is exactly the universal-executor class, whose
  realized harm-reach is environment-conditional (isolated executor ~0% external, networked ~0.66). Honest
  bound: the detector is a heuristic and `contained` is a caller assertion inspeximus cannot verify — it forces the
  declaration, it does not enforce the sandbox. Credits the reversibility×scope grid of arXiv:2607.07474.

## 1.1.0

Security hardening from the first internal security pass (see SECURITY.md). Both additions are OPT-IN; the
default behaviour is byte-identical to 1.0.0 (verified by tests).

- **`Inspeximus(max_text=N)`** — availability guard: `remember()` truncates a single record's text to N chars and
  stamps `meta["truncated_from"]`, so one runaway/malicious write can't exhaust memory. Default `None` =
  unbounded (legacy).
- **`verify_writes(warn_unpinned=True)`** — surfaces the self-referential-pubkey footgun: when signatures are
  present but no `expected_pubkey` is pinned, it reports a problem (a store-rewriter can swap sig+key and still
  pass). Default `False` = legacy. `governance_report()` now also states `proof.signature_authenticity`
  ("pinned to expected_pubkey" vs "self-referential — pin expected_pubkey or witness anchor() externally").

## 1.0.0

First stable release. The library matured over the 0.4–0.7 line into a real, shipped product; 1.0.0 marks a
**stable public API** (`inspeximus.__all__`), a **runnable test suite** (`tests/`, CI on every push), a documented
changelog, and the governance/erasure tooling consolidated. No functional change from 0.7.22 — this release is
about production-readiness and API stability, not new features.

- **Public API frozen** in `inspeximus.__all__`: `Inspeximus`, `new_receipt_keypair`, `new_source_keypair`, `sign_revert`,
  `sign_erasure`, `erasure_challenge`, `attest`. Governance/erasure tools live in submodules
  `inspeximus.deletion_manifest` and `inspeximus.erasure_auditor`.
- **Tests + CI** added (`tests/test_core.py`, `test_governance.py`, `test_erasure.py`) — core recall/supersession
  /revert/echo-guard/forget, tamper-evidence, the CT-anchor, authenticated-principal erasure, the deletion
  manifest, and the erasure auditor, all cloud-free and deterministic.

## 0.7.x (highlights)

- **0.7.22** — governance & cross-store erasure tools: `anchor()`/`verify_consistency()` (CT-style external
  anchor, RFC 6962 — catches a key-holder history rewrite), authenticated-principal + decision-basis erasure
  tombstones, `DeletionManifest` (honest-by-construction cross-store record), `ErasureAuditor` (adversarial
  "content still reconstructible?" audit across the fan-out).
- **0.7.20** — dedicated repo, branding page, docs.
- **0.7.0–0.7.19** — first-class correction/erasure channel (revert, `retract_lineage`, `echo_guard`,
  `route()` intent tagger, `classify_reversion`, `forget_subject` + tombstones, `verify_writes` hash-chained
  receipts), six framework adapters (OpenAI Agents, AutoGen, LangGraph, LlamaIndex, Google ADK, Pydantic AI),
  and the cross-system integrity benchmark.
- **0.4–0.6** — value-ranked recall, per-type decay, consolidation, lexical+semantic auto-mode (RRF),
  corroboration-gated influence, MCP server.
