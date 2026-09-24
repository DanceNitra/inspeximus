# `inspeximus demo`: adversarial review

**Date:** 2026-09-24
**Branch:** `review-demo`
**Subject:** `inspeximus demo` as shipped in 3.9.0 (`2b3ba57`), reviewed at 3.9.1 (`21b6cdd`). `inspeximus/demo.py` and `tests/test_the_demo_can_fail_at_every_step.py` have not changed since 3.9.0.
**Changed:** nothing in `inspeximus/`. This review adds this file and `tests/test_the_demo_review_2026_09_24.py`.
**Environment:** Python 3.11, cryptography 50.0.1, pytest with xdist.

```
pytest tests/test_the_demo_review_2026_09_24.py -rxX        # 9 passed, 11 xfailed
```

## The question and the answer

> Can the demo print PASS for a claim that is false?

**Yes.** Each step does exercise the mechanism it names. The echo guard, keyed supersession, the tombstone, `secure_delete` and the receipt text hash each turn their step into FAIL when removed (eight product mutations plus the three shipped controls, all caught; tables below). What prints PASS is everything around those mechanisms:

1. **The demo is unsigned, though its documentation says it is signed.** The CHANGELOG says the demo "verifies the signed erasure certificate" and runs "with a temporary receipt key". The demo sets `INSPEXIMUS_KEY_HOME` but never passes `receipt_key=`. Its certificate therefore has `"pubkey": null`, the verifier reports `UNSIGNED` under `limits`, and the demo reads only `valid`. A certificate anyone can write by hand, for an erasure that never happened, prints `certificate: valid` (**F1**).
2. **Because nothing is signed, step 3's "tamper" is the weakest one.** An editor who changes the text *and* recomputes the public SHA-256 receipt chain, with no key, gets `verify_writes() == (True, [])` and `recall` serves the forged value (**F2**).
3. **Step 2 passes three wrong erasures:**
   - an over-erasure that also deletes the unrelated record (**F3**);
   - an erasure that leaves the subject's name on disk, because the byte scan searches only for her email (**F4**);
   - a byte scan that read zero files (**F7**).
4. **Two things the demo says are never checked:**
   - "recorded as a blocked echo" is `route()`'s own return value, never looked up in the store (**F5**);
   - "with the record named" is not part of the verdict (**F6**).
5. **One of the shipped negative controls is over-determined.** `forget=False` fails on `erased >= 1`, the forget's own return value, so the certificate check and the byte scan can both be deleted from step 2's verdict and `tests/test_the_demo_can_fail_at_every_step.py` stays 10/10 green (**F8**).

| ID | Sev. | Step | What prints PASS (or "valid") | Reproducing test (`xfail(strict=True)`) |
|---|---|---|---|---|
| F1 | HIGH | 2 | An unsigned certificate, described as signed; a keyless forgery reads `valid` | `test_F1_the_demo_certificate_is_signed`, `test_F1_a_certificate_forged_without_a_key_does_not_read_valid` |
| F2 | HIGH | 3 | A tamper that also rewrites the `.receipts` sidecar, with no key | `test_F2_an_edit_that_also_rewrites_the_receipts_is_refused_by_step_3`, `test_F2_the_demo_stores_are_signed` |
| F3 | MED | 2 | An erasure that also destroys an unrelated record ("2 record(s) erased", PASS) | `test_F3_an_erasure_that_takes_an_unrelated_record_with_it_fails_step_2` |
| F4 | MED | 2 | "0 trace(s) of the subject" with "Jana Novak" still on disk | `test_F4_a_subject_left_on_disk_under_her_name_fails_step_2` |
| F8 | MED | 2 | The shipped suite stays green when step 2's verdict is only the forget's own count | `test_F8_the_shipped_tests_notice_a_step_2_verdict_that_trusts_the_forget_alone` |
| F5 | LOW | 1 | "the restatement was: echo, blocked" when no restatement was recorded | `test_F5_a_restatement_that_is_never_recorded_fails_step_1` |
| F6 | LOW | 3 | A refusal that names a record nobody edited | `test_F6_a_refusal_that_names_the_wrong_record_fails_step_3` |
| F7 | LOW | 2 | "0 trace(s) of the subject in 0 file(s)" | `test_F7_a_byte_scan_that_read_no_file_fails_step_2` |
| N1 | note | 3 | Library, documented limit: the chain head kept outside the store is skipped when a rewrite starts at receipt 0 | `test_N1_the_head_kept_outside_does_not_cover_a_rewrite_from_the_first_receipt` |

What the severities mean:
- **HIGH:** the demo prints PASS for a statement that its own documentation makes and that is false as shipped.
- **MED:** one realistic product defect prints as PASS, or the shipped tests let a check degrade silently.
- **LOW:** the verdict leaves out something the demo's text claims, and only a contrived defect reaches it.

## Method

- **Mutations run in-process, with no source edits.** Each product mutation is a `monkeypatch` of the library inside the test process, and the demo is then run exactly as the command runs it (`run_demo()`, or `run_demo(keep=...)` to inspect the stores it wrote). No source file is edited, so the tests are safe under xdist. Three runs at `-n 4` gave the same result each time.
- **CONFIRMED tests pass today.** Each asserts that a mutation turns its step into FAIL, and where it matters, *which* check caught it.
- **Findings are strict xfails.** They are marked `xfail(strict=True, raises=AssertionError)`, and only the last `assert` states the expected behaviour. Each finding's premises are checked with `pytest.fail`, which is not an `AssertionError`. So a finding whose premise stops holding shows up as a FAILURE; it cannot hide as an expected xfail. That caught one of my own mistakes during the review. My first over-erasure mutation called `forget_subject("id:<id>")` on a record that has a source, which erases nothing, so it only claimed to over-erase. The F3 premise failed it; the mutation now really erases the record.
- **Each xfail was checked in both directions.**
  - On this branch all 11 xfail.
  - In a throwaway worktree with the sketch fix in the appendix applied to `demo.py` (not committed), all 9 demo findings go **XPASS(strict)**, and the 9 CONFIRMED tests and the 10 shipped demo tests still pass.
  - F8 goes XPASS(strict) once the shipped test file gains one control, a forget that only reports.
  - N1 stays xfail under the demo fix, as it should: it is a library property.
- **The CONFIRMED tests guard the demo's own verdict.** I mutated `demo.py` five ways in the worktree:

  | Mutation of `demo.py` | Killed by these CONFIRMED tests | Shipped test file |
  |---|---|---|
  | D5: step 1 `ok = True` | 3 step-1 tests | killed (trusting control) |
  | D2: step 2 without the certificate | a delete without a tombstone | **10 passed** |
  | D3: step 2 without the byte scan | `secure_delete` off | **10 passed** |
  | D6: step 2 `ok = erased >= 1` | a forget that only reports; a delete without a tombstone; `secure_delete` off | **10 passed** (F8) |
  | D4: step 3 without the edited copy | 2 step-3 tests | killed (no-edit control) |

## Claim by claim

### 1. A correction holds

The demo stores "deploy region is frankfurt", corrects it to "ohio" under the same key, then routes the unmarked restatement "just to confirm, deploy region is frankfurt" with `policy="safe"`. It passes if `recall("deploy region", k=1)` contains `ohio` and not `frankfurt`.

| Mutation (product) | Step 1 | Notes |
|---|---|---|
| Echo guard off (`INSPEXIMUS_ECHO_GUARD=0`) | **FAIL** | Recall answers the restatement. The guard is what holds the correction. |
| Keyed supersession off (`remember` drops `key`/`object`) | **FAIL** | |
| Recall serves superseded records (`include_superseded=True`) | **FAIL** | Answers "deploy region is frankfurt" |
| `echo_policy="trusting"` (shipped control) | **FAIL** | |
| `route()` records nothing and returns `{"intent": "echo", "action": "blocked"}` | **PASS** | **F5** |

- **The shipped control works, but indirectly.** `trusting` does not turn the guard off. It sends the restatement down `route()`'s reaffirm branch, a sanctioned restore, where `echo_guard` is never consulted. The direct mutation, guard off, also fails the step, so the step's conclusion holds; the control just exercises a different code path from the one the step describes.
- **The step reads only recall.** A `route()` that silently drops the restatement still passes, and so would a lost write or a missing ledger entry. The demo claims the restatement "is recorded as a blocked echo".
- **The "echo, blocked" line is `route()`'s own report.** With the guard off, the demo prints `the restatement was: echo, blocked` directly above `recall answers: just to confirm, deploy region is frankfurt`. The reason: under `policy="safe"`, `route()` returns `"action": "blocked"` in both branches, whether the guard retires the record or it is written without a key (`core.py`, end of `route()`). The step still FAILs there, so this is a misleading line, not a false PASS.

### 2. An erasure can be checked

Verdict: `verify_erasure_certificate(cert, store_path=path)["valid"] and scan_residue(d, [SUBJECT_VALUE])["ok"] and erased >= 1`.

| Mutation (product) | Step 2 | Which check caught it |
|---|---|---|
| Forget skipped (`forget=False`, shipped control) | **FAIL** | All three at once (see F8) |
| `forget_subject` returns `erased: 1` and erases nothing | **FAIL** | Byte scan and certificate |
| Row deleted, no tombstone written | **FAIL** | **Certificate only** ("attests to ZERO erasures"); the byte scan is clean |
| SQLite `secure_delete` OFF | **FAIL** | **Byte scan only** (UNRECLAIMED bytes); the certificate is valid |
| Forget also erases the unrelated `ops-notes` record | **PASS** | **F3** |
| Forget erases the record but keeps "Jana Novak prefers contact at [erased]" | **PASS** | **F4** |
| Byte scan reads no file | **PASS** | **F7** |
| Forget skipped, certificate forged without a key | step FAILs, but prints `certificate: valid` | **F1** |

Both the certificate and the byte scan do work that the other cannot, as the two rows marked "only" show. The forget really is complete today: after an unmutated run, a case-insensitive search of every file the demo keeps finds none of `jana.novak@example.com`, `Jana Novak` or `customer:jana-novak`.

### 3. A tamper is caught

The demo writes two records to a receipts store, copies it twice, and replaces `is ohio` with `is oslo` in one copy's SQLite bytes. It passes if the untouched copy verifies and the edited copy does not.

| Mutation | Step 3 | Notes |
|---|---|---|
| Edit skipped (`tamper=False`, shipped control) | **FAIL** | |
| Receipts no longer commit the text (`_write_commit` without `immutable_sha256`/`content_sha256`) | **FAIL** | |
| `verify_writes` without its receipt-mismatch problem | **FAIL** | The refusal comes from that comparison alone; nothing else in `verify_writes` sees the edit |
| The editor also rewrites the `.receipts` sidecar, with no key | **PASS** | **F2** |
| `verify_writes` names the wrong record | **PASS** | **F6** |

## Findings

### F1 (HIGH): step 2 calls an unsigned certificate "signed", and a keyless forgery reads `valid`

**The claim, in three places:**
- `demo.py` docstring: "the signed erasure certificate verifies without the operator's key".
- CHANGELOG 3.9.0: "verifies the signed erasure certificate without the operator's key" and "runs in a temporary directory with a temporary receipt key".
- Printed line: `certificate: valid (checked without the operator's key)`.

**What happens:**
- `run_demo()` points `INSPEXIMUS_KEY_HOME` at `<tmp>/keys` but opens every store with `receipts=True` and no `receipt_key=`.
- The temporary key home receives three chain-head files (`inspeximus/heads/*.json`) and no key.
- The certificate has `"pubkey": null` and no tombstone carries a `sig`.
- `verify_erasure_certificate(cert)` returns `valid: True, checks.signed: False, limits: ["UNSIGNED: no tombstone carries a signature ... the chain proves integrity, not authorship. Set receipt_key to sign."]`.
- The demo reads `valid` and nothing else.

**Why it matters:**
- "Checked without the operator's key" is trivially true of a document that has no key.
- The demo passes neither `expected_pubkey` nor `store_receipts`, so the certificate is bound to no key and to no store.
- `test_F1_a_certificate_forged_without_a_key_does_not_read_valid` replaces `erasure_certificate()` with a hand-made certificate: one tombstone for an id that never existed, one `sha256`, `pubkey: None`. It then runs `run_demo(forget=False)`. The demo prints `certificate: valid` for an erasure that never ran. The step still FAILs, but only because `erased` is 0 and the byte scan finds the email; the certificate line adds no evidence of its own.

**Fix direction:**
- Open the demo stores with `receipt_key=receipt_key_for(path)`.
- Verify with `expected_pubkey=m.receipt_pubkey` and `store_receipts=`.
- Require `checks["signed"] is True`.
- `receipt_key_for` refuses a key home inside the store's directory. `<tmp>/correction.json` sits directly under `<tmp>`, and so does `<tmp>/keys`, so the correction store has to move into a subdirectory (verified: `ValueError: refusing to keep the receipt key ...`).

### F2 (HIGH): step 3 catches only an editor who leaves the receipts alone

**The claim:** "The store is copied twice and one copy is edited behind the library's back ... the edited one is refused", plus "a temporary receipt key" from the CHANGELOG.

**What happens:**
- The stores are unsigned (the F1 root cause). The library's own `verify_writes(require_signed=True)` reports the demo's untouched copy as `UNSIGNED: ... an editor who can rewrite the .receipts sidecar can rewrite the chain with it`.
- The library documents this limit: see the `verify_writes` comment "a FULLY unsigned chain is legitimate -- ... catch an editor who cannot also rewrite the sidecar", and the `receipt_key_for` docstring, "Measured 2026-08-16".
- The demo shows exactly the one tamperer an unsigned chain stops, and presents it as "a tamper".

**Reproduction.** The attacker needs nothing secret:
1. Make the demo's own edit.
2. For every receipt, recompute `immutable_sha256` and `content_sha256` from the stored record. The nonce is in the record.
3. Drop any signature, then re-link `prev`/`hash` through `Inspeximus._chain_core`.

That is about 15 lines (`_rewrite_receipts_without_a_key` in the test). Result: `verify_writes() == (True, [])`, `verify_attribution()["ok"] is True`, and `recall("deploy region")` answers "deploy region is oslo". The test rewrites the sidecar just before the demo's own `verify_writes` call on the edited copy, so it checks the demo's actual verdict.

**Why the test strips signatures:**
- A fix that signs but does not pin still fails this test, and it should.
- An unpinned `verify_writes()` accepts a chain that is unsigned throughout.
- So the fix needs `expected_pubkey=` (or `require_signed=True`), not just a key.
- Verifying at the store's original path does not help either; see N1.

### F3 (MED): step 2 passes an erasure that destroys an unrelated record

- **What the step checks:** that the subject's bytes are gone. It never checks that "the deploy window is Tuesday" (source `ops-notes`) is still there.
- **The mutation:** a forget whose matcher is too broad also erases every active record's own source under the same request.
- **The result:** `forgot the subject: 2 record(s) erased`, a valid certificate covering both, a clean scan, and PASS.
- **Why it matters:** over-erasure is a real defect class that the library itself guards against elsewhere (`forget_subject(dry_run=True)` reports `also_carrying` for exactly this reason). Here it passes as a successful erasure.
- **Fix direction:** require `erased == 1` and that the `ops-notes` record is still active on disk.

### F4 (MED): the byte scan looks for one of the subject's identifiers

- **What the scan searches:** `scan_residue(d, [SUBJECT_VALUE])`, only the email. The printed line says "0 trace(s) of the subject".
- **The mutation:** a forget that erases the record (tombstone written, id absent, so the certificate is valid) but leaves "Jana Novak prefers contact at [erased]" under a new id.
- **The result:** PASS. The test's premise confirms `Jana Novak` is in the kept store's bytes.
- **Why it matters:** redaction mistaken for erasure is a realistic defect. The scan is literal and case-sensitive (`erasure_residue.py`'s own "MATCHING SCOPE" table), so it finds only what it is given.
- **Fix direction:** scan for `[SUBJECT_VALUE, "Jana Novak", SUBJECT]`. None of the three is on disk after a real forget, so today's PASS does not change.

### F8 (MED): the shipped negative control for step 2 is over-determined

`test_CONTROL_without_the_forget_the_subject_is_still_on_disk` runs `forget=False`, and three things then fail at once:
- `erased` is 0, and `erased >= 1` alone is enough to fail the step;
- the byte scan finds the email;
- the certificate attests to zero erasures.

The test asserts the `residue_findings` and `certificate_valid` **fields**, not that the **verdict** depends on them. Cut the verdict down to `erased >= 1`, which is the forget's own return value, and the shipped file stays green. D2, D3 and D6 above each gave 10 passed.

At that point a `forget_subject` that reports `erased: 1` and deletes nothing would print PASS, and CI's time-to-demo job (`tools/time_to_demo.py`, which reads the exit code and `ok`) would report success.

The reproducing test copies the shipped file into a temporary directory and runs it with a `conftest.py` that weakens `_step_erasure` in exactly that way. It deselects the two CLI tests, which start a fresh interpreter the patch cannot reach and which do not read step 2's verdict.

**Fix direction:** add controls that remove one mechanism at a time. The CONFIRMED tests here already do this: a forget that only reports, a delete without a tombstone, and `secure_delete` off.

### F5 (LOW): "recorded as a blocked echo" is `route()`'s self-report

See claim 1. **Fix direction:** look up `routed["id"]` in `m.items` and require the record to exist with a status other than `active`.

### F6 (LOW): the verdict does not check which record the refusal names

- **The claim:** the CHANGELOG says the edited copy is refused "with the record named".
- **The gap:** the verdict is "untouched verifies and edited does not". The shipped test checks only that some problem contains the substring `memory `.
- **The mutation:** a `verify_writes` that attributes the mismatch to `memory 0000000000`, an id that is not in the store.
- **The result:** it passes both the demo and the shipped test.
- **Today's behaviour is correct:** `test_CONFIRMED_the_edit_is_refused_as_a_receipt_mismatch_on_the_edited_record` pins the exact problem naming the edited record.
- **Fix direction:** capture the id `remember()` returns and require a problem that starts with `memory <that id>:`.

### F7 (LOW): step 2 accepts a byte scan that read no file

- **Why it passes:** `scan_residue()` reports an existing empty directory as `ok: True` ("a clean result about nothing", a deliberate choice in that module), and step 2's verdict does not require `files_scanned >= 1`.
- **What the shipped test covers:** it asserts `files_scanned > 0` on a normal run, but the demo's own verdict, the one users and CI see, does not.
- **The result:** a scan pointed at the wrong place prints `0 trace(s) of the subject in 0 file(s)` and PASS.
- **Fix direction:** require `residue["checked_files"] >= 1`, or better, that the store file is in `scan_residue(..., manifest=True)["manifest"]`.

### N1 (note, library, documented): the chain head kept outside the store does not cover a rewrite that starts at receipt 0

This is not a defect of the demo; its copies are verified at new paths that have no head at all. It is listed because it bounds the F2 fix.

- At the store's original path, the head in the key home records `{genesis, n_writes, writes_tip}`.
- `verify_writes` compares the head against the chain only when `head.genesis == receipts[0].hash`. The demo edits the first record written, so the keyless rewrite changes receipt 0, and the head is silently treated as belonging to another store.
- `core.py` documents this ("a store wiped to zero and written fresh gets a new genesis ... catching that needs a witness").

So the F2 fix has to be a pinned key; moving verification to the original path is not enough. The test is a strict xfail so that it retires itself if the library ever covers the case.

## Appendix: the sketch fix used to check that the xfails flip

This is **not applied and not a proposal to merge as is**. It exists to show that each finding's test measures the finding: with this applied to `demo.py` in a throwaway worktree, all 9 demo-finding tests go XPASS(strict), the 9 CONFIRMED tests and the 10 shipped demo tests still pass, and `inspeximus demo` still prints three PASSes. The output text (`render()`) and `--keep` would also need updating, since the correction store moves into `correction/`.

```diff
+def _open(path):
+    from .core import Inspeximus, receipt_key_for
+    return Inspeximus(path=path, receipts=True, receipt_key=receipt_key_for(path))
+
+
 def _step_correction(root, echo_policy):
-    from .core import Inspeximus
-    m = Inspeximus(path=os.path.join(root, "correction.json"), receipts=True)
+    os.makedirs(os.path.join(root, "correction"))
+    m = _open(os.path.join(root, "correction", "correction.json"))
 ...
-    ok = NEW in answer and OLD not in answer
+    rec = next((r for r in m.items if routed.get("id") and r.get("id") == routed["id"]), None)
+    ok = NEW in answer and OLD not in answer and rec is not None and rec.get("status") != "active"
 ...
-    m = Inspeximus(path=path, receipts=True)
+    m = _open(path)
 ...
-    verdict = verify_erasure_certificate(cert, store_path=path)
-    residue = scan_residue(d, [SUBJECT_VALUE])
-    ok = bool(verdict["valid"]) and bool(residue["ok"]) and erased >= 1
+    verdict = verify_erasure_certificate(cert, store_path=path, expected_pubkey=m.receipt_pubkey,
+                                         store_receipts=list(m._receipts))
+    residue = scan_residue(d, [SUBJECT_VALUE, "Jana Novak", SUBJECT])
+    kept = any("deploy window" in r["text"] for r in Inspeximus(path=path, receipts=True).items
+               if r.get("status") == "active")
+    ok = (bool(verdict["valid"]) and verdict["checks"].get("signed") is True and bool(residue["ok"])
+          and residue.get("checked_files", 0) >= 1 and erased == 1 and kept)
 ...
-    m = Inspeximus(path=os.path.join(src, "store.json"), receipts=True)
-    m.remember(f"deploy region is {NEW}", key=KEY, object=NEW)
+    m = _open(os.path.join(src, "store.json"))
+    pub = m.receipt_pubkey
+    edited_id = m.remember(f"deploy region is {NEW}", key=KEY, object=NEW)
 ...
-        ok, problems = Inspeximus(path=p, receipts=True).verify_writes()
+        ok, problems = Inspeximus(path=p, receipts=True).verify_writes(expected_pubkey=pub)
 ...
-    ok = results["untouched copy"]["verifies"] and not results["edited copy"]["verifies"]
+    ok = (results["untouched copy"]["verifies"] and not results["edited copy"]["verifies"]
+          and any(p.startswith(f"memory {edited_id}:") for p in results["edited copy"]["problems"]))
```

F8 flips when `tests/test_the_demo_can_fail_at_every_step.py` gains a control such as:

```python
def test_CONTROL_a_forget_that_only_reports_is_caught(monkeypatch):
    monkeypatch.setattr(Inspeximus, "forget_subject", lambda self, *a, **k: {"erased": 1, "ids": []})
    assert not run_demo()["steps"][1]["ok"]
```
