# Security model

Honest scope: inspeximus has had a **first internal security pass** (2026-07-13), not a formal third-party
pentest. This document states what inspeximus defends, what it does not, and the known residual footguns — so you
can decide what to rely on. If you find an issue, open a GitHub issue or security advisory.

## What inspeximus is (threat model)

inspeximus is an **in-process Python library** plus an optional **stdio MCP server** (`inspeximus-mcp`). It is not a
network service and does not open sockets (the MCP server speaks over stdio and trusts the host that launched
it). The realistic adversary is therefore **the content you store** — a memory written from an untrusted user
conversation, a poisoned document, a compromised tool result — not a remote attacker hitting an endpoint.

That content threat is the thing inspeximus is actually built to address: keyed supersession, `echo_guard`,
`retract_lineage`, the corroboration-gated influence path (`recall(influence_only=True)`), the authorized-revert
channel, and the tamper-evident write receipts. See the README and `probes/` for the measured behaviour and its
honest limits.

## What is clean (verified in the first pass)

- **No remote-code-execution surface.** The store is JSON only — no `pickle`, `eval`, `exec`, `yaml.load`,
  `subprocess`, or `os.system` anywhere. Loading an untrusted store file cannot execute code.
- **No ReDoS from content.** The router regexes are fixed, module-level, and use bounded quantifiers; any
  user-derived string used in a pattern is `re.escape`d.
- **Constant-time secret comparison.** Revert capabilities (HMAC) are checked with `hmac.compare_digest`;
  signatures use Ed25519.

## Known residual footguns (be explicit)

1. **`verify_writes()` without `expected_pubkey` is not operator-adversarial-safe.** A signed receipt carries
   its own `pubkey`; the signature is verified against that self-contained key. An attacker who can rewrite the
   store file can replace the signature **and** the pubkey with their own keypair, and `verify_writes()` will
   pass. To detect a rewrite you must either **pin `expected_pubkey`** (`verify_writes(expected_pubkey=...)`) or,
   against a key-holder who can re-sign, use the external **`anchor()` / `verify_consistency()`** (a
   Certificate-Transparency-style witness — see the governance section). The bare `verify_writes()` proves only
   internal chain consistency, not authenticity.

2. **No resource limits by default.** A single `remember()` accepts arbitrarily large text, and the active set
   grows unbounded unless you set `capacity=`. Malicious or runaway content can exhaust memory/CPU (`recall()`
   scales with the active set). If you ingest untrusted content, set `capacity=` and cap input size at the
   application boundary. (A future minor release may add an opt-in per-record size guard.)

   **But `capacity=` is not a clean mitigation — it converts a growth attack into a TARGETED DELETE.**
   Eviction ranks by `value`, which is caller-supplied and unbounded, and the two-tier policy protects the
   top `protect_frac` *by raw value* — exactly what an attacker buys. Measured: with `capacity=10`, fifty
   writes at `value=1000.0` evicted **5 of 5** victim records held at `value=1.0`. So if writes come from
   somewhere untrusted, capping the store size hands the attacker a way to delete specific memories, which
   may be worse than the exhaustion it prevents. Clamp `value` at your application boundary too, or keep
   untrusted writes in a separate store.

3. **Erasure attests the act, not physical destruction.** `forget_subject()` / the deletion manifest / the
   erasure auditor make deletion tamper-evident and cross-store-auditable; they do not prove bytes were
   destroyed, and a retained embedding elsewhere can reconstruct content (see the erasure auditor + README).
   The compliant fix for a leaking store is hard-delete + reindex or crypto-shredding.

## Reporting

Open a GitHub issue (or a private security advisory) on https://github.com/DanceNitra/inspeximus. This is an
open-source project maintained on a best-effort basis; there is no formal SLA.
