"""`inspeximus demo`: three claims the library makes, checked on a throwaway store in about a second.

1. A correction holds. A fact is stated, corrected, and then the old value is restated the way a user
   or an attacker would. `recall` still answers with the correction, and the restatement is recorded
   as a blocked echo.
2. An erasure can be checked. A subject is forgotten, the signed erasure certificate verifies without
   the operator's key, and a byte scan of the store directory finds nothing of the subject.
3. A tamper is caught. The store is copied twice and one copy is edited behind the library's back: the
   untouched copy verifies, the edited one is refused and the report names the record.

Everything runs in a temporary directory, with a temporary receipt key, and makes no network request.
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
SUBJECT_VALUE = "jana.novak@example.com"


def _step_correction(root, echo_policy):
    from .core import Inspeximus
    m = Inspeximus(path=os.path.join(root, "correction.json"), receipts=True)
    m.remember(f"deploy region is {OLD}", key=KEY, object=OLD)
    m.remember(f"correction: deploy region is {NEW}", key=KEY, object=NEW)
    routed = m.route(f"just to confirm, deploy region is {OLD}", key=KEY, object=OLD, policy=echo_policy)
    top = m.recall("deploy region", k=1)
    answer = top[0]["text"] if top else ""
    ok = NEW in answer and OLD not in answer
    return {"step": "a correction holds", "ok": ok,
            "said": [f"deploy region is {OLD}", f"correction: deploy region is {NEW}",
                     f"just to confirm, deploy region is {OLD}"],
            "restatement": {"intent": routed.get("intent"), "action": routed.get("action"),
                            "note": routed.get("note")},
            "recall_answers": answer}


def _step_erasure(root, forget):
    from .core import Inspeximus, verify_erasure_certificate
    from .erasure_residue import scan_residue
    d = os.path.join(root, "erasure")
    os.makedirs(d)
    path = os.path.join(d, "erasure.json")
    m = Inspeximus(path=path, receipts=True)
    m.remember("the deploy window is Tuesday", source={"doc": "ops-notes"})
    m.remember(f"Jana Novak prefers contact at {SUBJECT_VALUE}", source={"doc": SUBJECT})
    erased = 0
    if forget:
        erased = m.forget_subject(SUBJECT, request_id="demo-erasure-1", basis="demo")["erased"]
    cert = m.erasure_certificate(request_id="demo-erasure-1")
    with open(os.path.join(root, "erasure_certificate.json"), "w", encoding="utf-8") as fh:
        json.dump(cert, fh, indent=2)
    verdict = verify_erasure_certificate(cert, store_path=path)
    residue = scan_residue(d, [SUBJECT_VALUE])
    ok = bool(verdict["valid"]) and bool(residue["ok"]) and erased >= 1
    return {"step": "an erasure can be checked", "ok": ok, "erased": erased,
            "certificate_valid": bool(verdict["valid"]), "certificate_problems": verdict.get("problems", []),
            "residue_findings": len(residue.get("findings", [])),
            "files_scanned": residue.get("checked_files", 0), "certificate": cert}


def _step_tamper(root, tamper):
    from .core import Inspeximus
    src = os.path.join(root, "tamper")
    os.makedirs(src)
    m = Inspeximus(path=os.path.join(src, "store.json"), receipts=True)
    m.remember(f"deploy region is {NEW}", key=KEY, object=NEW)
    m.remember("the deploy window is Tuesday")
    results = {}
    for label, edit in (("untouched copy", False), ("edited copy", tamper)):
        c = os.path.join(root, label.replace(" ", "_"))
        shutil.copytree(src, c)
        p = os.path.join(c, "store.json")
        if edit:
            raw = open(p, "rb").read()
            open(p, "wb").write(raw.replace(f"is {NEW}".encode(), b"is oslo", 1))
        ok, problems = Inspeximus(path=p, receipts=True).verify_writes()
        results[label] = {"verifies": ok, "problems": problems}
    ok = results["untouched copy"]["verifies"] and not results["edited copy"]["verifies"]
    return {"step": "a tamper is caught", "ok": ok, **results}


def run_demo(keep: str | None = None, *, echo_policy: str = "safe", forget: bool = True,
             tamper: bool = True) -> dict:
    """Run the three steps and return {ok, seconds, steps}. `keep` copies the work directory there.

    The keyword switches are negative controls for the tests: `echo_policy="trusting"` must make step 1
    FAIL, `forget=False` step 2, and `tamper=False` step 3. Called without them it is the demo."""
    t0 = time.time()
    root = tempfile.mkdtemp(prefix="inspeximus-demo-")
    saved = {k: os.environ.get(k) for k in ("INSPEXIMUS_KEY_HOME", "INSPEXIMUS_NO_UPDATE_CHECK")}
    # A temporary key home, so the demo never writes a receipt key into the user's real config
    # directory, and no update check, so it makes no network request.
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
    out.append(f"   the restatement was: {s1['restatement']['intent']}, {s1['restatement']['action']}")
    out.append(f"   recall answers:      {s1['recall_answers']}")
    out.append(f"   {'PASS' if s1['ok'] else 'FAIL'}")
    out.append("")
    out.append("2. An erasure can be checked")
    out.append(f"   forgot the subject:  {s2['erased']} record(s) erased")
    out.append(f"   certificate:         {'valid' if s2['certificate_valid'] else 'INVALID'}"
               " (checked without the operator's key)")
    out.append(f"   byte scan of the store directory: {s2['residue_findings']} trace(s) of the subject"
               f" in {s2['files_scanned']} file(s)")
    out.append(f"   {'PASS' if s2['ok'] else 'FAIL'}")
    out.append("")
    out.append("3. A tamper is caught")
    for label in ("untouched copy", "edited copy"):
        r = s3[label]
        why = f": {r['problems'][0]}" if r["problems"] else ""
        out.append(f"   {label + ':':16s} {'verifies' if r['verifies'] else 'REFUSED'}{why}")
    out.append(f"   {'PASS' if s3['ok'] else 'FAIL'}")
    out.append("")
    out.append(f"{'All three checks passed' if result['ok'] else 'A check FAILED'} in {result['seconds']} s.")
    if result.get("kept_in"):
        out.append(f"The stores, the erasure certificate and the edited copy are in {result['kept_in']}.")
    return "\n".join(out)
