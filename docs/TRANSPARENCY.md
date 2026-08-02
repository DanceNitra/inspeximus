# Tamper-evident agent memory: anchors, witnesses, and split-view detection

Your agent's memory lives on a host. If that host is compromised — or simply operated by someone with an
interest in the answer — it can rewrite what the agent "remembered" and re-sign the result so the store's
own integrity check still passes. It can also do something subtler: show **one history to one reader and a
different history to another**, and stay internally consistent in both. That second attack is a *split
view*, and it is the one this page is about.

inspeximus ships three things for it. All are deterministic and involve no LLM; the verification logic
needs no network at all (a witness can be an in-process object), and the only dependency beyond the
standard library is `cryptography`, for the Ed25519 signatures themselves:

| | what it does |
|---|---|
| `anchor()` | a Certificate-Transparency-style **signed tree head**: one small JSON object committing to the entire write + erasure history |
| witness co-signing | **independent parties** sign that head, and refuse to sign a fork or rollback of one they already signed |
| `detect_split_view()` | given the two heads a store served two readers, **proves** a fork when a witness signed both |

**Prior art, credited rather than reinvented.** This is the Certificate Transparency model —
[RFC 6962](https://www.rfc-editor.org/rfc/rfc6962) (Laurie, Langley, Kasper): the log is untrusted, and
external witnesses plus consistency proofs make append-only violations detectable without trusting the
operator. [Sigstore](https://www.sigstore.dev/) and its Rekor transparency log run this design in
production at a scale this package does not approach, and CT itself has a real, multi-operator witness
network. The construction here is theirs. What our survey of agent-memory libraries did not find elsewhere
is a **co-signed, split-view-detecting anchor inside a zero-dependency single-file memory store** — every
qualifier in that sentence is load-bearing, and it is a statement about what we looked for and found, not
a claim about any product's roadmap.

---

## What the anchor proves — and what it does not

Read this before you rely on it. A verification tool that overstates its reach is worse than none.

**It proves:**

- The head commits to the write and erasure chains as they stood at that instant. Changing any earlier
  entry changes the tip, and the tip is in the head.
- With `verify_consistency(prior_anchor)`, that the log today is an **append-only extension** of a head
  someone recorded out of band.
- With k-of-n co-signatures, that **k allowlisted, independent parties saw this exact head**. An operator
  who forks must get k of them to sign the fork; honest witnesses refuse.
- With `detect_split_view`, that a specific named witness signed **two heads that cannot both be true** —
  cryptographic evidence of a fork, attributable to a key.

**It does not prove:**

- **That the content is true.** A log proves inclusion, never validity (RFC 6962 says this in its own
  introduction). A perfectly anchored store can be full of lies that were honestly appended.
- **That the store is serving what it committed to.** The anchor covers hashes. Use
  `audit-verify --store` / `bind_content` to bind an audit bundle to the text served today.
- **Anything about your vector index, prompt logs, or backups.** The anchor covers this store's chains.
- **That your witnesses are actually independent.** The allowlist is a *declared* grouping you own. The
  code collapses Sybil keys you declare to one class; it cannot prove two classes are causally
  independent. That judgement is yours and it is where this scheme really fails in practice.
- **Append-only between two heads of different sizes, from the heads alone.** That needs a consistency
  proof against a replica. `detect_split_view` reports it as `undetermined`, not as "no fork".
- **Anything at all, if the head covers an empty history.** An anchor over a store with no receipt chain
  is a valid signed head of *nothing*; witnesses will co-sign it and it will verify. The verdict carries
  `covers_history: false` and prints a `NOTE` for exactly this reason.

---

## Install

```
pip install inspeximus cryptography
```

inspeximus itself has **zero required dependencies**. Only the signing half of this page needs
`cryptography` (Ed25519); everything else in the library runs without it. If it is missing, these commands
exit `4` and say so rather than raising an ImportError.

> Running from a source checkout instead of an install? Every `inspeximus ...` below is
> `python -m inspeximus.cli ...`. That is exactly the substitution `tests/test_witness_quickstart.py`
> makes when it runs these commands, and the test also pins the console-script name in `pyproject.toml`
> so the two cannot drift apart.

---

## Quickstart: from an empty directory to a verified co-signed anchor

Work in a scratch directory. Every command below is executed by the test suite on every run, and the
output is what it actually printed — ids and key material differ per run and are shown as `<...>`.

**1. Write something worth anchoring.** Receipts are what build the tamper-evident chain; without them the
anchor commits to an empty history.

```console
$ inspeximus --receipts --path store.json remember "invoice 7 total is 100 EUR" --key inv7::total --object 100
remembered <...> [key=inv7::total]
$ inspeximus --receipts --path store.json remember "invoice 8 total is 250 EUR" --key inv8::total --object 250
remembered <...> [key=inv8::total]
```

**2. Emit the signed tree head.**

```console
$ inspeximus --path store.json anchor --out head.json
anchor -> head.json
  n_writes=2 n_tombstones=0 sth=<...>
```

That file is small, content-free, and safe to publish. Publishing it somewhere the operator cannot
retroactively edit is the whole point — a witness, a public log, an auditor's own records.

**3. Stand up three independent witnesses.** In production these are three *different parties on three
different hosts*; three key files in one directory demonstrate the mechanism, not the independence.

```console
$ inspeximus witness keygen --out witnessA.key --allowlist witnesses.txt
witness secret -> witnessA.key   (never share or commit this file)
witness pubkey: <...>
pubkey appended -> witnesses.txt
$ inspeximus witness keygen --out witnessB.key --allowlist witnesses.txt
witness secret -> witnessB.key   (never share or commit this file)
witness pubkey: <...>
pubkey appended -> witnesses.txt
$ inspeximus witness keygen --out witnessC.key --allowlist witnesses.txt
witness secret -> witnessC.key   (never share or commit this file)
witness pubkey: <...>
pubkey appended -> witnesses.txt
```

`witnesses.txt` is your **allowlist** — the trust decision, and the only reason a signature means anything.
Anyone can mint a valid Ed25519 key; validity is not standing.

**4. Each witness co-signs the head.** The `--state` file (default `<key>.state.json`) is where a witness
remembers the last head it signed per store. It is not optional bookkeeping: every CLI call is a fresh
process, so a witness with no state file has no memory and can never refuse anything.

```console
$ inspeximus witness cosign head.json --store-id acme-prod --key witnessA.key --out cosigA.json
co-signed head sth=<...> for store 'acme-prod'
  pubkey: <...>
  state:  witnessA.key.state.json
  -> cosigA.json
$ inspeximus witness cosign head.json --store-id acme-prod --key witnessB.key --out cosigB.json
co-signed head sth=<...> for store 'acme-prod'
  pubkey: <...>
  state:  witnessB.key.state.json
  -> cosigB.json
$ inspeximus witness cosign head.json --store-id acme-prod --key witnessC.key --out cosigC.json
co-signed head sth=<...> for store 'acme-prod'
  pubkey: <...>
  state:  witnessC.key.state.json
  -> cosigC.json
```

**5. Verify k-of-n as the client.** This needs no access to the store and no key.

```console
$ inspeximus witness verify head.json --cosig cosigA.json --cosig cosigB.json --cosig cosigC.json --witnesses-file witnesses.txt --threshold 3
  OK   3 of 3 allowlisted witnesses co-signed this exact head (threshold 3)
    signer: <...>
    signer: <...>
    signer: <...>
VERDICT: PASS
```

That is the five-minute path. Exit code `0`. Everything below is the part that decides whether the green
tick meant anything.

---

## Control 1 — a tampered anchor must FAIL

A signature covers a hash. The fields a reader actually consumes — `n_writes`, `writes_tip` — are only
protected if someone re-derives the hash from them. Substitute a tip and keep the genuine `sth_hash` and
the genuine signatures:

```console
$ python -c "import json;a=json.load(open('head.json'));a['writes_tip']='f'*64;json.dump(a,open('tampered.json','w'))"
$ inspeximus witness verify tampered.json --cosig cosigA.json --cosig cosigB.json --cosig cosigC.json --witnesses-file witnesses.txt --threshold 3
  FAIL anchor sth_hash does not commit to this anchor's own fields (n_writes/writes_tip/n_tombstones/tombstones_tip) - a co-signature over it would authenticate a head no witness saw; refusing to count it
  FAIL 0 of 3 allowlisted witnesses co-signed this exact head (threshold 3)
VERDICT: FAIL
# exit: 1
```

**This check did not exist until the change that added this page, and its absence inverted the
guarantee.** An operator could keep
a genuinely co-signed `sth_hash`, paste in the tip of a *rewritten* history, collect `ok: true` from 3 of 3
honest witnesses, and hand that to an auditor — who would then run `verify_consistency` against it, since
that function reads `writes_tip`, and get a clean **append-only** verdict on the rewritten store while the
*honest* store was reported as the fork. The check existed in `audit_bundle.verify_bundle` the whole time
and had simply never reached the primitive every other surface calls. There is one implementation now
(`core.sth_hash_of`), and `witness_cosign` refuses to sign an incoherent head as well.

---

## Control 2 — the fork must not reach threshold

Now the operator rewrites history: invoice 7 becomes 900 EUR. Same number of writes, different content, so
the tip differs at a size the witnesses have already signed.

```console
$ inspeximus --receipts --path rewritten.json remember "invoice 7 total is 900 EUR" --key inv7::total --object 900
remembered <...> [key=inv7::total]
$ inspeximus --receipts --path rewritten.json remember "invoice 8 total is 250 EUR" --key inv8::total --object 250
remembered <...> [key=inv8::total]
$ inspeximus --path rewritten.json anchor --out forked-head.json
anchor -> forked-head.json
  n_writes=2 n_tombstones=0 sth=<...>
```

Ask all three witnesses to co-sign it. Each refuses, from local memory alone, with no access to the log:

```console
$ inspeximus witness cosign forked-head.json --store-id acme-prod --key witnessA.key --out cosig-fork-A.json
  REFUSED refusing to co-sign: n_writes=2 but writes_tip differs from the prior head this witness signed (split-view / fork)
VERDICT: REFUSED TO CO-SIGN - the reason is above. A refusal is this layer working, not an error: an honest witness declines rather than signing something it cannot stand behind.
# exit: 2
$ inspeximus witness cosign forked-head.json --store-id acme-prod --key witnessB.key --out cosig-fork-B.json
  REFUSED refusing to co-sign: n_writes=2 but writes_tip differs from the prior head this witness signed (split-view / fork)
VERDICT: REFUSED TO CO-SIGN - the reason is above. A refusal is this layer working, not an error: an honest witness declines rather than signing something it cannot stand behind.
# exit: 2
$ inspeximus witness cosign forked-head.json --store-id acme-prod --key witnessC.key --out cosig-fork-C.json
  REFUSED refusing to co-sign: n_writes=2 but writes_tip differs from the prior head this witness signed (split-view / fork)
VERDICT: REFUSED TO CO-SIGN - the reason is above. A refusal is this layer working, not an error: an honest witness declines rather than signing something it cannot stand behind.
# exit: 2
```

So the forked head reaches a client with no signatures at all, and cannot make 2-of-3:

```console
$ inspeximus witness verify forked-head.json --witnesses-file witnesses.txt --threshold 2
  FAIL 0 of 3 allowlisted witnesses co-signed this exact head (threshold 2)
VERDICT: FAIL
# exit: 1
```

---

## Control 3 — and it must stay silent when nothing is wrong

A detector that always alarms is worthless. Two readers who fetch the *same* honest head, each with a
co-signature, must produce no alarm:

```console
$ inspeximus witness split-view --anchor-a head.json --cosig-a cosigA.json --anchor-b head.json --cosig-b cosigB.json --witnesses-file witnesses.txt
  head A: n_writes=2 tip=<...>
  head B: n_writes=2 tip=<...>
  no inconsistency: the heads agree at every shared log size
VERDICT: NO SPLIT VIEW
```

Exit `0`. Both directions are asserted in `tests/test_witness_quickstart.py`; neither is worth anything
without the other.

---

## Proving a split view when the defence is bypassed

Refusal only works while the witness remembers. Lose the state file — a crash, a redeploy from a stale
image, or a witness that is simply colluding — and it will sign the fork too:

```console
$ inspeximus witness cosign forked-head.json --store-id acme-prod --key witnessA.key --state restored-from-backup.json --out cosig-fork-A.json
co-signed head sth=<...> for store 'acme-prod'
  pubkey: <...>
  state:  restored-from-backup.json
  -> cosig-fork-A.json
```

That is the moment worth catching, and it is now *permanently attributable*. The auditor takes the two
heads the store served and compares them:

```console
$ inspeximus witness split-view --anchor-a head.json --cosig-a cosigA.json --anchor-b forked-head.json --cosig-b cosig-fork-A.json --witnesses-file witnesses.txt
  head A: n_writes=2 tip=<...>
  head B: n_writes=2 tip=<...>
  INCONSISTENT at n_writes: the same log size carries a different tip, so these two heads cannot both be the history of one store
  EVIDENCE witness <...> validly co-signed BOTH heads
VERDICT: SPLIT VIEW PROVEN
# exit: 1
```

Two heads, same log size, different tip, both carrying a valid signature from **one named key**. Those two
signatures are a non-repudiable statement that the store showed two different histories — or that the
witness is dishonest. Either way the fork is detected, and the evidence survives the operator deleting
everything, because the auditor holds it.

If the two heads diverge but *no single witness* signed both, you get the divergence without the
attribution — reported as `HEADS INCONSISTENT` rather than as proof. If the heads are of different sizes,
append-only-versus-fork is not decidable from tree heads alone and the tool says `UNDETERMINED` instead of
guessing.

---

## Running a real witness on another host

The point is independence, so in production each witness runs somewhere you do not control.

```
inspeximus witness serve --port 9700 --state witness.json --key witnessA.key
#   inspeximus witness on http://127.0.0.1:9700  pubkey=<hex>
```

Stdlib `http.server`, no web framework, no new dependency. `GET /pubkey`, `POST /cosign {store_id, anchor}`
→ `200 {pubkey, sig}` or **`409 {refused}`** on a fork or rollback. Bind it behind your own TLS. Clients
gather from a mix of local and remote witnesses:

```python
from inspeximus import Inspeximus
from inspeximus.witness_pool import collect_cosignatures, http_witness

anchor = store.anchor()
out = collect_cosignatures("acme-prod", anchor,
                           [http_witness("http://hostA:9700"), http_witness("http://hostB:9700")])
out["refused"]      # a witness that refused is a FORK ALARM, never silently dropped
Inspeximus.verify_cosigned_anchor(anchor, out["cosignatures"], allowlist, threshold=2)["ok"]
```

## From Python and from MCP

Everything above is `Inspeximus.anchor / verify_consistency / verify_cosigned_anchor / detect_split_view`
plus `inspeximus.witness_pool`. `examples/12_split_view_detection.py` runs the whole story — honest k-of-n,
the tampered anchor, the three-witness fork attempt, the proof, and the silent control — in one file:

```
python examples/12_split_view_detection.py
```

Over MCP the same capabilities are the `anchor`, `verify_consistency`, `verify_cosigned_anchor`,
`detect_split_view`, `audit_bundle` and `verify_audit_bundle` tools.

## Exit codes

`inspeximus witness` is meant to run unattended, so the exit code is the contract:

| code | meaning |
|---|---|
| 0 | passed — co-signed, or verified at threshold, or no split view |
| 1 | **failed** — below threshold, or a split view was proven |
| 2 | the witness **refused** to co-sign (a fork or rollback — the defence firing), or a usage error |
| 3 | undetermined — heads of different sizes, not decidable from tree heads alone |
| 4 | Ed25519 unavailable (`pip install cryptography`) |

Three usage errors are deliberate refusals rather than conveniences, because each one would otherwise
produce a verdict that reads like success:

- **No allowlist** (`--witnesses` / `--witnesses-file` both absent). Every head, honest or forged, scores 0
  against an empty allowlist.
- **`--threshold 0`** (or negative). A quorum of zero is satisfied by an anchor no witness ever signed.
  The library primitive refuses it too, not just the CLI.
- **Overwriting an existing witness secret** with `witness keygen --out`. Replacing the key silently
  invalidates every co-signature it ever made, and the old ones then read as forgeries rather than history.

And one verdict is true but incomplete, so it is annotated rather than refused: a co-signed anchor over a
store with **no receipt chain** covers zero writes and zero erasures. It verifies, `covers_history` is
`false`, and both the CLI and the API say so in words.
