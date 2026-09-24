# Adversarial review: `docs/verify/index.html` at 6117486

**Question:** can the browser verifier say VALID for a file the CLI fails, or INVALID for one it passes?

**Scope:** branch `verifier-page` at `6117486`. The branch head, `928b044`, adds only a `workflow_dispatch`
trigger to `ci.yml`. The page and its tests are identical at both commits.
**Oracle:** `inspeximus erasure-verify` / `audit-verify` with no options, meaning `verify_erasure_certificate(cert)`
and `verify_bundle(bundle)` at their defaults.
**Reproducers:** `tests/test_verifier_page_adversarial.py`, on branch `verifier-page-review`. No change was
made to the page or to the `verifier-page` branch.
**Environment:** Python 3.11.15, `cryptography` 50.0.1 (OpenSSL 4.0.2), Playwright 1.63.0, HeadlessChrome 141.

## Verdict

- **The verification logic holds.** The canonical encoding, the parser and the Ed25519 check matched Python
  on all 111 hand-built cases from the brief, plus the branch's own fuzzing. That covers reordered keys,
  NFC/NFD, float edge cases, duplicate keys, extra fields, wrong key types, truncated signatures, signatures
  from another key, and empty and huge input. None of them produced a wrong VALID or a wrong INVALID.
- **Wrong VALIDs come from around the logic, not from it.** The page picks which verifier to run from fields
  the file controls (F1). It also leaves a verdict on screen after the input changes (F2, F3).
- **No wrong INVALID was found.** Where the page and Python still disagree, the page almost always says
  CANNOT VERIFY HERE (F4, F5, F6). In one case the page says INVALID where Python crashes (F8).
- **Network:** no request after load and no external script. The CSP stops every request channel at a live
  server. It does not cover navigation, but the page has no path for injected script to run (I3).

## Findings

Severity follows the brief: a wrong VALID is critical.

| ID | Severity | Finding | Page | CLI | Reproducing test |
|---|---|---|---|---|---|
| F1 | **Critical** | **The file picks its own verifier.** `detectKind` sends any document with `inspeximus_erasure_certificate` to the certificate verifier, even if it also carries `kind`/`bundle_hash`. A tampered bundle (rewritten write receipt) with a certificate added, built entirely from its own tombstone chain and needing no key, reads VALID. The reverse also works: a failing certificate plus a valid bundle's fields reads VALID "audit bundle". Only the grey kind label and the reason line show which verifier ran. The branch's oracle `_kind()` copies this routing, which is why its suite cannot see it. | VALID | `audit-verify`: FAIL (`erasure-verify`: PASS) | `test_F1_a_tampered_bundle_with_certificate_fields_is_not_valid`, `test_F1_a_failing_certificate_with_bundle_fields_is_not_valid` |
| F2 | **High** | **Stale VALID.** Nothing listens for `input`. Verify a good certificate, paste a tampered one, skip clicking Verify, and the green VALID stays beside the tampered text. | VALID on screen | FAIL | `test_F2_replacing_the_text_clears_a_valid_verdict` |
| F3 | Medium | **An older run overwrites a newer verdict.** `runFile`'s not-UTF-8 branch calls `show()` without bumping `runId`, and so does Clear. A run still in flight (a large file) then paints its VALID over the CANNOT for the file just chosen, or over an empty input after Clear. | VALID for the wrong file | n/a | `test_F3_an_older_run_cannot_overwrite_the_verdict_for_a_newer_file`, `test_F3_clear_during_a_run_stays_clear` |
| F7 | Medium (shared: Python-side, the page matches it) | **An unsigned tombstone appended without the key verifies.** Append an unsigned tombstone for an id that was never erased to a fully signed certificate, recompute the unsigned anchor, and it reads VALID "4 erasure(s) attested" with a PARTIALLY SIGNED note. `verify_bundle` calls partial signing a failure; `verify_erasure_certificate` calls it a note. The page's "What it does not check" also understates the anchor: trimming the tail and re-anchoring needs no key at all, not "whoever holds the signing key". | VALID | PASS | `test_F7_an_unsigned_tombstone_appended_without_the_key_is_not_valid`, `test_F7_a_tail_trimmed_and_reanchored_without_the_key_is_valid_in_both` |
| F4 | Low | **Stricter types than Python, with a false reason.** The page stops on non-string ids, on `scope_excludes`/`erased_memory_ids`/`request_ids` given as an object or a string, on float `erased` counts, and on int grant ids. Its reason says "the Python verifier stops with an error", but Python returns a verdict: VALID in 8 of the 11 cases, INVALID in 3. | CANNOT | VALID / INVALID | `test_F4_python_verdicts_are_not_reported_as_python_errors[*]` (11 cases) |
| F5 | Low | **Depth limit 200.** `json.loads` goes to about 1000, so a certificate with a 201–900-deep *unhashed* field is VALID in Python. | CANNOT | PASS | `test_F5_a_deep_unhashed_field_does_not_block_a_verdict[201,500,900]` |
| F6 | Low | **An unsettled signature skips a key check that needs no crypto.** When OpenSSL would accept a small-order signature, the page defers to the CLI and never runs the "signed by an unexpected key" string comparison, which Python runs and fails. | CANNOT | FAIL | `test_F6_a_degenerate_signature_under_the_wrong_key_is_invalid` |
| F8 | Low | **Python has one NaN object.** `json.loads` returns the same `float('nan')` for every `NaN`, so Python's `[NaN] == [NaN]` is True (list equality tries identity first). `pyEq` says False. With `scoped_to: [NaN]`, Python puts the tombstone in scope and crashes hashing its list id; the page leaves it out and says INVALID. That breaks the page's own rule of saying CANNOT whenever Python has no verdict. | INVALID | traceback | `test_F8_a_nan_inside_a_list_scope_is_cannot_verify` |
| I1 | Info | The page's oracle is the *function*. The CLI then prints the problems, and a problem quoting a lone surrogate (e.g. `"count": "\ud800"`) kills `print`: traceback, no VERDICT line, exit 1. | INVALID | traceback | `test_I1_the_cli_prints_a_traceback_where_the_function_returns_invalid` |
| I2 | Info | The page models the CLI **with** `cryptography` installed. On a base install the CLI fails every signed certificate ("cannot verify signatures") and skips bundle signature checks. | VALID | FAIL on a base install | `test_I2_without_cryptography_the_cli_fails_every_signed_certificate` |
| I3 | Info | CSP governs requests, not navigation: script in the page *could* leave via `location.href`/`window.open`. No script can be injected, because every file-derived string reaches the DOM via `textContent`. That source-level fact is what holds up "enforced by the browser". Tightening `script-src 'unsafe-inline'` to a hash would add depth. | n/a | n/a | `test_navigation_is_outside_the_policy`, `test_the_page_has_no_html_sink_for_untrusted_text` |
| I5 | Info (known) | Bundle tombstone signatures are never checked: the exporter drops `pubkey`. The branch already records this as matched behaviour. | VALID | PASS | branch test `test_a_resealed_bundle_is_judged_on_its_chains_and_signatures` ("a tombstone signature forged") |

F1–F6 and F8 are page defects, written as `xfail(strict=True, raises=AssertionError)`. Each one asserts the
correct behaviour and fails today. Preconditions (Python's verdict, the CLI's verdict) use `pytest.fail`, so a
broken precondition fails the run instead of hiding inside the expected failure. F7 is a Python-side gap and
is encoded the same way, asserting INVALID from both.

**The reproducers were checked against a fix.** With the patch in
`verifier-page-suggested-fix.diff` applied to a copy of the page, all six F1/F2/F3/F6 tests XPASS, and the
branch's own 13 tests still pass. The patch was not applied to either branch. It makes a document carrying
markers of both kinds unroutable, invalidates the verdict on `input` and Clear, bumps `runId` on the non-UTF-8
path, and runs the unexpected-key check before deferring. F4, F5 and F8 want either Python's semantics
(`list(dict)`, `set(str)`, the shared NaN, `sum` over floats) or a CANNOT reason that does not claim Python
raises.

## 1. Page vs Python, byte for byte

| Step | Python | Page | Same? |
|---|---|---|---|
| Read file | `open(encoding="utf-8")`, universal newlines, `json.load` | `TextDecoder("utf-8", fatal, ignoreBOM)`; textarea normalizes CR/CRLF | Yes: line breaks only matter as whitespace, and raw CR/LF inside a string is invalid either way |
| BOM | `JSONDecodeError` (traceback) | CANNOT | Yes |
| Invalid UTF-8 / CESU-8 surrogates | `UnicodeDecodeError` | CANNOT | Yes |
| Strings | C scanner, strict: control chars < 0x20 refused, `\"\\/bfnrtu`, `\uXXXX` pairs combined, lone surrogates kept | same; pairs combine naturally in UTF-16 | Yes |
| Numbers | `-?(0\|[1-9]\d*)(\.\d+)?([eE][-+]?\d+)?`; int vs float kept; `float()` correctly rounded; int > 4300 digits raises (3.11+) | same regex; `BigInt` / `Number()` (correctly rounded); > 4300 digits → CANNOT | Yes (tested 4300/−4300/4301, `4e-324`, `1e400`, `10000000000000001.0`) |
| `NaN`/`Infinity` | accepted; **one shared NaN object** | accepted; separate values | **No**: F8, and one F4 case |
| Duplicate keys | last value wins | `Map.set`, last value wins | Yes (tested both orders in a tombstone, a signature, a hashed `auth` block, `bundle_hash`, the `tombstones` array) |
| Depth | about 1000 (recursion limit) | 200 | **No**: F5 |
| Canonical text | `json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False)` | `canonText` | Yes |
| Key order | `sorted(items)`, code point | `cmpCodePoints` | Yes (U+E000..U+FFFF vs astral covered by the branch fuzz) |
| Escapes | `\"` `\\` `\b\f\n\r\t`, other < 0x20 as `\u00xx` lowercase, DEL raw, U+2028 raw | same | Yes |
| Floats | `repr`: shortest round-trip; exponent when decpt ≤ −4 or > 16; `e±XX`; `.0` kept; `NaN`, `Infinity`, `-0.0` | `pyFloatRepr` on `Number#toString` digits | Yes (19 boundary values, plus respellings) |
| Ints | exact | `BigInt` | Yes (2^53+1, 2^64, 23-digit) |
| Encode | UTF-8; lone surrogate raises | `TextEncoder`; lone surrogate in *hashed* text → CANNOT | Yes (a surrogate in an unhashed certificate field is VALID in both) |
| Preimages | `_tombstone_core`, `_chain_core` (`amends`, `amend_reason`, `backfill`, `auth` only when truthy) | `tombstoneCore`, `chainCore` | Yes (a falsy `auth: 0` is unhashed in both) |
| Merkle | RFC 6962, leaf `0x00`, node `0x01`, split at the largest power of 2 below n | same | Yes |
| `sth_hash` / `root_hash` | the 4 STH fields / the 5 root fields | same, from the constants block | Yes |
| `bytes.fromhex` | ASCII only; skips `\t\n\v\f\r ` between pairs; `\x1c`–`\x1f` not skipped | `pyFromhex` | Yes (tested `\x0b` accepted, `\x1c` and full-width digits rejected) |
| Ed25519 | OpenSSL: S < L; lenient A decode (y mod p, −0); cofactorless; compares R bytes | S < L; WebCrypto only when A and R are canonical and prime-order; OpenSSL's equation re-implemented for degenerate points; CANNOT when OpenSSL would accept a degenerate one | Yes, except F6 |
| Certificate checks | chain, sigs, unexpected key, tip, `n_tombstones`, root, sth, summary, attests, scope | same order and conditions | Yes, except F4 (types), F6, F8 |
| Bundle checks | kind, `bundle_hash`, both chains + anchor, sth, roots, `root_hash`, signature coverage, unkeyed cosigs, refusals, `proof.verified`, `erasures_total`, `by_request`, `n_records`, empty, grants | same | Yes, except F4 (float `erased`, int ids) |
| Routing | the operator types the command | `detectKind` on file fields | **No**: F1 |
| Environment | `_HAVE_ED`; `print` can raise | not modelled | I1, I2 |

## 2. Cases from the brief (all agree with Python)

| Case | Documents | Result |
|---|---|---|
| Reordered keys at every level | 5 (3 certificates, 2 bundles) | VALID both |
| Duplicate keys, fake first / fake last | 9 | fake last → INVALID both; fake first → VALID both |
| NFC vs NFD (plus NFKC) | 7 | as issued VALID; re-normalized text INVALID both; neither side normalizes |
| Floats: `1.0`/`1`, `-0.0`/`-0`/`0.0`, `1e16` vs the int, 2^53+1, 2^64, `1e21`, inf, NaN, subnormals | 20 | same double → VALID; different Python value → INVALID, both |
| Extra fields | 8 | unhashed (top level, tombstone, anchor, resealed bundle) → VALID; hashed or unresealed → INVALID, both |
| Wrong key type | 19 (int, list, object, null, "", true, `0x`, 31/33 bytes, odd, Ed448, X25519, secp256k1, full-width, `\x1c`, per-tombstone int; uppercase, spaces, `\x0b`) | same verdict; the three `fromhex`-legal spellings are VALID in both |
| Malformed signature | 15 (63/32/1/65 bytes, odd, "", null, int, zero, S+L, R/S bit flips; uppercase and spaced legal) | same verdict |
| Signature from another key | 5 | INVALID both; a chain re-signed end to end by another key is VALID in both (the documented unpinned limit) |
| Empty / trivial | 15, plus whitespace through the UI | INVALID both; whitespace shows nothing |
| Huge | 4300 / −4300 / 4301 digits, 1000 signed tombstones, a 10 000-item hashed list, a 40 MB unhashed string | same verdict. In-page time: about 1.5 s for 1000 signatures, 78 ms for 40 MB |
| `__proto__` / `constructor` keys in a hashed block | 1 | VALID both (`Map`, not an object) |

## 3. Network and scripts

- **After load: nothing.** A CDP `Network` trace recorded every renderer request across paste → Verify →
  Choose file → text drop → Clear. The only request was the page itself. No WebSocket, and no
  `securitypolicyviolation`, so the page never even tries anything its policy blocks.
- **No external script.** Two `<script>` elements, both inline (one `application/json`). The only
  `src`/`href` in the DOM is the `data:` favicon. The meta CSP precedes every script, style and link.
- **The policy holds against a live listener.** Script running in the page tried fetch, XHR, `sendBeacon`,
  WebSocket, EventSource, Worker, img, script, stylesheet, prefetch, preload, iframe, object, audio, `@import`,
  CSS `url()` and a form POST against a listening local server. Zero hits arrived. Playwright fires `request`
  even for CSP-blocked fetches, so these tests count arrivals instead.
- **Not covered by CSP:** navigation (I3). During one run the sandbox proxy logged Chromium connecting to
  `www.google.com` and `android.clients.google.com`. It did not recur in three reruns, and the CDP trace shows
  no request from the page, so it was the browser process's own traffic.

## Running

```
pip install pytest pytest-xdist cryptography playwright && python -m playwright install chromium
VERIFIER_PAGE_REQUIRED=1 python -m pytest tests/test_verifier_page_adversarial.py -n 0 -rxX
```

Expected at this commit: 18 passed, 22 xfailed. When a fix lands, its tests XPASS(strict) and fail; remove their
`finding(...)` marker. The CI `verifier-page` job runs only `tests/test_verifier_page.py`; add this file to that
job's command to run the reproducers in CI.

**Limits of this review.** Only Chromium was run. The Firefox (NSS) and Safari rows in the page's support
table are untested here. Their Ed25519 answers only matter for canonical prime-order points, where every
implementation agrees with OpenSSL.
