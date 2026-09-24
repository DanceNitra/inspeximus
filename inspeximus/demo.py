"""`inspeximus demo`: three claims the library makes, checked on a throwaway store in about a second.

1. A correction holds. A fact is stated, corrected, and then the old value is restated the way a user
   or an attacker would. `recall` still answers with the correction, and the store file holds the
   restatement as a record that is not active and is marked as a blocked echo.
2. An erasure can be checked. A subject is forgotten. The erasure certificate is signed with the store's
   temporary receipt key and verifies against that public key, pinned, and against the store's own
   receipt chain. The one unrelated record is still active with its text unchanged. A byte scan that
   reads the store file finds none of the subject's email, name or subject id in the store directory.
3. A tamper is caught. The signed store is copied twice and one copy is edited behind the library's
   back. Both copies are verified against the pinned public key: the untouched copy verifies, and the
   edited one is refused by a problem that names the edited record.

Everything runs in a temporary directory, each store signed with a receipt key minted for it and never
written to disk, and makes no network request. Signing needs the `cryptography` package (the `crypto`
extra); without it the demo says so and does not run, because an unsigned run cannot make these checks.
`run_demo()` takes three switches that exist only so the tests can show each step is able to FAIL: a
demo that cannot fail is an advertisement, and those switches are what keep this one a check.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time

OLD, NEW = "frankfurt", "ohio"
KEY = "deploy::region"
SUBJECT = "customer:jana-novak"
SUBJECT_NAME = "Jana Novak"
SUBJECT_VALUE = "jana.novak@example.com"
UNRELATED = "the deploy window is Tuesday"


def cannot_run() -> str | None:
    """Why the demo cannot run here, or None. The one reason is a missing `cryptography`."""
    from .core import _HAVE_ED
    if _HAVE_ED:
        return None
    return ("inspeximus demo signs its stores with a temporary Ed25519 receipt key, which needs the "
            "`cryptography` package: pip install \"inspeximus[crypto]\"")


def _signed_store(path):
    """A store that signs its receipts with a key minted for it and held in memory only, and the public
    half every check pins. Not receipt_key_for(): that honours INSPEXIMUS_RECEIPT_KEY, so a user with
    their own key configured would have the demo sign with it."""
    from .core import Inspeximus, new_ed25519_keypair
    sk, pub = new_ed25519_keypair()
    return Inspeximus(path=path, receipts=True, receipt_key=sk, receipt_pubkey=pub), pub


def _on_disk(path):
    """The records as a fresh handle reads them from the file, not as the writing handle holds them."""
    from .core import Inspeximus
    return Inspeximus(path=path, receipts=True).items


def _step_correction(root, echo_policy):
    path = os.path.join(root, "correction.json")
    m, pub = _signed_store(path)
    m.remember(f"deploy region is {OLD}", key=KEY, object=OLD)
    m.remember(f"correction: deploy region is {NEW}", key=KEY, object=NEW)
    restatement = f"just to confirm, deploy region is {OLD}"
    routed = m.route(restatement, key=KEY, object=OLD, policy=echo_policy)
    top = m.recall("deploy region", k=1)
    answer = top[0]["text"] if top else ""
    m.flush()
    # route() reports what it did; the store file is what holds it. The claim is about the second: the
    # restatement is in the store, marked as a blocked echo. That it is not served is what recall shows.
    held = [r for r in _on_disk(path) if r.get("text") == restatement]
    recorded = bool(held) and all((r.get("meta") or {}).get("echo_blocked") is True for r in held)
    stored = {"records": len(held), "status": held[0].get("status") if held else None,
              "echo_blocked": recorded}
    ok = NEW in answer and OLD not in answer and recorded
    return {"step": "a correction holds", "ok": ok,
            "said": [f"deploy region is {OLD}", f"correction: deploy region is {NEW}", restatement],
            "restatement": {"intent": routed.get("intent"), "action": routed.get("action"),
                            "note": routed.get("note")},
            "restatement_in_store": stored,
            "recall_answers": answer, "receipt_pubkey": pub}


def _step_erasure(root, forget):
    from .audit_bundle import load_store_receipts
    from .core import verify_erasure_certificate
    from .erasure_residue import scan_residue
    d = os.path.join(root, "erasure")
    os.makedirs(d)
    path = os.path.join(d, "erasure.json")
    m, pub = _signed_store(path)
    unrelated_id = m.remember(UNRELATED, source={"doc": "ops-notes"})
    m.remember(f"{SUBJECT_NAME} prefers contact at {SUBJECT_VALUE}", source={"doc": SUBJECT})
    erased = 0
    if forget:
        erased = m.forget_subject(SUBJECT, request_id="demo-erasure-1", basis="demo")["erased"]
    cert = m.erasure_certificate(request_id="demo-erasure-1")
    m.flush()
    with open(os.path.join(root, "erasure_certificate.json"), "w", encoding="utf-8") as fh:
        json.dump(cert, fh, indent=2)
    # Pinned to the key the demo minted, so an unsigned tombstone or one signed by any other key fails,
    # and bound to this store's receipt chain as read from disk, so a certificate issued from another
    # store fails. `store_bound` is None when no chain was handed over, and that is not a pass.
    verdict = verify_erasure_certificate(cert, store_path=path, expected_pubkey=pub,
                                         store_receipts=load_store_receipts(path))
    checks = verdict.get("checks") or {}
    cert_ok = bool(verdict["valid"]) and checks.get("store_bound") is True
    after = next((r for r in _on_disk(path) if r.get("id") == unrelated_id), None)
    intact = after is not None and after.get("status") == "active" and after.get("text") == UNRELATED
    # Every identifier of the subject, each as the demo wrote it: the scan is a literal byte match.
    residue = scan_residue(d, [SUBJECT_VALUE, SUBJECT_NAME, SUBJECT], manifest=True)
    # A scan that read no file, or not the store's, finds nothing by construction.
    scanned = os.path.basename(path) in {f.get("path") for f in residue.get("manifest") or []}
    ok = cert_ok and intact and bool(residue["ok"]) and scanned and erased == 1
    return {"step": "an erasure can be checked", "ok": ok, "erased": erased,
            "certificate_valid": cert_ok, "certificate_signed": checks.get("signed") is True,
            "certificate_problems": verdict.get("problems", []),
            "unrelated_record_intact": intact,
            "residue_findings": len(residue.get("findings", [])),
            "files_scanned": residue.get("checked_files", 0), "store_file_scanned": scanned,
            "receipt_pubkey": pub, "certificate": cert}


def _step_tamper(root, tamper):
    from .core import Inspeximus
    src = os.path.join(root, "tamper")
    os.makedirs(src)
    m, pub = _signed_store(os.path.join(src, "store.json"))
    edited_id = m.remember(f"deploy region is {NEW}", key=KEY, object=NEW)
    m.remember(UNRELATED)
    m.flush()
    results = {}
    for label, edit in (("untouched copy", False), ("edited copy", tamper)):
        c = os.path.join(root, label.replace(" ", "_"))
        shutil.copytree(src, c)
        p = os.path.join(c, "store.json")
        if edit:
            raw = open(p, "rb").read()
            open(p, "wb").write(raw.replace(f"is {NEW}".encode(), b"is oslo", 1))
        # Opened WITHOUT the key, as a verifier holds it, and pinned to the public half: an editor who
        # also rewrites the .receipts sidecar cannot re-sign it, and an unsigned chain is refused.
        ok, problems = Inspeximus(path=p, receipts=True).verify_writes(expected_pubkey=pub)
        results[label] = {"verifies": ok, "problems": problems}
    names_it = any(p.startswith(f"memory {edited_id}:") for p in results["edited copy"]["problems"])
    ok = results["untouched copy"]["verifies"] and not results["edited copy"]["verifies"] and names_it
    return {"step": "a tamper is caught", "ok": ok, "edited_record": edited_id,
            "refusal_names_the_edited_record": names_it, "receipt_pubkey": pub, **results}


def run_demo(keep: str | None = None, *, echo_policy: str = "safe", forget: bool = True,
             tamper: bool = True) -> dict:
    """Run the three steps and return {ok, seconds, steps}. `keep` copies the work directory there.
    Raises RuntimeError when `cannot_run()` gives a reason.

    The keyword switches are negative controls for the tests: `echo_policy="trusting"` must make step 1
    FAIL, `forget=False` step 2, and `tamper=False` step 3. Called without them it is the demo."""
    why = cannot_run()
    if why:
        raise RuntimeError(why)
    t0 = time.time()
    root = tempfile.mkdtemp(prefix="inspeximus-demo-")
    saved = {k: os.environ.get(k) for k in ("INSPEXIMUS_KEY_HOME", "INSPEXIMUS_NO_UPDATE_CHECK")}
    # A temporary key home, so the chain heads the stores keep outside their directories never land in
    # the user's real config directory, and no update check, so it makes no network request.
    os.environ["INSPEXIMUS_KEY_HOME"] = os.path.join(root, "keys")
    os.environ["INSPEXIMUS_NO_UPDATE_CHECK"] = "1"
    try:
        steps = [_step_correction(root, echo_policy), _step_erasure(root, forget), _step_tamper(root, tamper)]
        if keep:
            shutil.copytree(root, keep, dirs_exist_ok=True, ignore=shutil.ignore_patterns("keys"))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(root, ignore_errors=True)
    return {"ok": all(s["ok"] for s in steps), "seconds": round(time.time() - t0, 2), "steps": steps,
            "kept_in": keep}


def render(result: dict) -> str:
    """The text the command prints: one block per step, each ending in PASS or FAIL."""
    out = []
    s1, s2, s3 = result["steps"]
    out.append("1. A correction holds")
    for line in s1["said"]:
        out.append(f"   said:     {line}")
    held = s1["restatement_in_store"]
    if not held["records"]:
        where = "NOT IN THE STORE"
    else:
        where = f"{held['status']}, " + ("a blocked echo" if held["echo_blocked"] else "NOT a blocked echo")
    out.append(f"   restatement on disk: {where}")
    out.append(f"   recall answers:      {s1['recall_answers']}")
    out.append(f"   {'PASS' if s1['ok'] else 'FAIL'}")
    out.append("")
    out.append("2. An erasure can be checked")
    out.append(f"   forgot the subject:  {s2['erased']} record(s) erased")
    if s2["certificate_valid"]:
        cert = "valid, signed with the store's temporary key, checked with its public key and receipts"
    else:
        cert = "INVALID" + (f": {s2['certificate_problems'][0]}" if s2["certificate_problems"] else "")
    out.append(f"   certificate:         {cert}")
    kept = "intact" if s2["unrelated_record_intact"] else "MISSING OR CHANGED"
    out.append(f"   unrelated record:    {kept}")
    out.append(f"   byte scan of the store directory: {s2['residue_findings']} trace(s) of the subject's"
               f" email, name or id in {s2['files_scanned']} file(s)"
               + ("" if s2["store_file_scanned"] else ", and the store file was NOT read"))
    out.append(f"   {'PASS' if s2['ok'] else 'FAIL'}")
    out.append("")
    out.append("3. A tamper is caught")
    for label in ("untouched copy", "edited copy"):
        r = s3[label]
        why = f": {r['problems'][0]}" if r["problems"] else ""
        out.append(f"   {label + ':':16s} {'verifies' if r['verifies'] else 'REFUSED'}{why}")
    if not s3["edited copy"]["verifies"] and not s3["refusal_names_the_edited_record"]:
        out.append(f"   the refusal does NOT name the edited record, {s3['edited_record']}")
    out.append(f"   {'PASS' if s3['ok'] else 'FAIL'}")
    out.append("")
    out.append(f"{'All three checks passed' if result['ok'] else 'A check FAILED'} in {result['seconds']} s.")
    out.append("Each store was signed with a receipt key made for this run and never written to disk; the"
               " certificate and both copies were checked against its public key.")
    if result.get("kept_in"):
        out.append(f"The stores, the erasure certificate and the edited copy are in {result['kept_in']}.")
    return "\n".join(out)
