"""Does the action ledger show which fact the agent acted on, and does it fail when history is rewritten?

The claim under test: two actions around one correction carry two different memory digests and two
different recalled-id sets, so a reader can tell the first action used the old value and the second
the new one. And the verifier is a verifier: a rewritten action, a rewritten memory history and a
forged signature each fail, and each control is run, not assumed.

Runs in under a second with no network and no key file (a fresh Ed25519 key per run).

    python probes/what_the_agent_knew_when_it_acted.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from _receipt import write_receipt  # noqa: E402

from inspeximus import Inspeximus, new_receipt_keypair  # noqa: E402
from inspeximus.actions import ActionLedger, verify_file  # noqa: E402


def main() -> dict:
    work = tempfile.mkdtemp(prefix="inspeximus-actions-")
    t0 = time.time()
    try:
        sk, pk = new_receipt_keypair()
        path = os.path.join(work, "mem.json")
        m = Inspeximus(path, receipts=True, receipt_key=sk)
        m.remember("staging database is db-3.internal", key="staging-db")
        led = ActionLedger(m, actor="deploy-agent")

        hits = m.recall("staging database")
        with led.action("tool:deploy", inputs={"target": hits[0]["text"]}) as a:
            a.output({"deployed_to": hits[0]["text"]})
        m.remember("staging database is db-7.internal", key="staging-db")   # the correction
        hits = m.recall("staging database")
        with led.action("tool:deploy", inputs={"target": hits[0]["text"]}) as a:
            a.output({"deployed_to": hits[0]["text"]})

        e = led.entries()
        first_knew = led.what_it_knew(0)["recalled_now"][0]
        second_knew = led.what_it_knew(1)["recalled_now"][0]
        out = {
            "probe": os.path.basename(__file__),
            "inspeximus": __import__("inspeximus").__version__,
            "entries": len(e),
            "digests_differ": e[0]["memory_state"]["digest"] != e[1]["memory_state"]["digest"],
            "recalled_ids_differ": e[0]["memory_state"]["recalled"] != e[1]["memory_state"]["recalled"],
            # the fact the first action acted on is superseded TODAY; the second action's fact is active.
            "first_action_recalled_status": (first_knew.get("current") or {}).get("status"),
            "second_action_recalled_status": (second_knew.get("current") or {}).get("status"),
            "first_acted_on_a_value_since_superseded": (first_knew.get("current") or {}).get("status") == "superseded"
                                                       and (second_knew.get("current") or {}).get("status") == "active",
            "signed_by_the_store_key": all(x.get("pubkey") == pk for x in e),
            "clean_chain_verifies": led.verify(expected_pubkey=pk) == (True, []),
            "clean_file_verifies_offline": verify_file(led.path, expected_pubkey=pk)[0],
        }

        # CONTROL 1: rewrite one action on disk
        data = json.loads(led.path.read_text(encoding="utf-8"))
        data[0]["inputs_sha256"] = "00" * 32
        led.path.write_text(json.dumps(data), encoding="utf-8")
        ok, problems = verify_file(led.path, expected_pubkey=pk)
        out["control_rewritten_action_fails"] = (not ok) and any("hash" in p for p in problems)
        led.path.write_text(json.dumps(e), encoding="utf-8")

        # CONTROL 2: rewrite the memory history (drop the correction's receipt) and re-open
        rp = path + ".receipts.json"
        r = json.loads(open(rp, encoding="utf-8").read())
        open(rp, "w", encoding="utf-8").write(json.dumps(r[:-1]))
        m2 = Inspeximus(path, receipts=True, receipt_key=sk)
        ok2, problems2 = ActionLedger(m2).verify(expected_pubkey=pk)
        out["control_rewritten_memory_fails_from_the_action_side"] = (not ok2) and any(
            "last_receipt" in p for p in problems2)
        out["control_rewritten_memory_still_passes_offline"] = verify_file(led.path, expected_pubkey=pk)[0]
        open(rp, "w", encoding="utf-8").write(json.dumps(r))

        # CONTROL 3: forge a signature
        data = json.loads(led.path.read_text(encoding="utf-8"))
        data[1]["sig"] = "11" * 64
        led.path.write_text(json.dumps(data), encoding="utf-8")
        ok3, problems3 = verify_file(led.path, expected_pubkey=pk)
        out["control_forged_signature_fails"] = (not ok3) and any("signature" in p for p in problems3)

        # the compliance overlay reads the ledger through its verifier; the README publishes this count
        from inspeximus.compliance import compliance_report
        out["compliance_controls"] = len(compliance_report(m)["controls"])
        out["elapsed_s"] = round(time.time() - t0, 3)
        out["verdict"] = "PASS" if all(v is True for k, v in out.items() if k.startswith(("digests", "recalled",
                                       "first_acted", "signed", "clean", "control_rewritten_action", "control_rewritten_memory_fails",
                                       "control_rewritten_memory_still", "control_forged"))) else "FAIL"
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    result = main()
    print(json.dumps(result, indent=2))
    write_receipt(__file__, result)
    sys.exit(0 if result["verdict"] == "PASS" else 1)
