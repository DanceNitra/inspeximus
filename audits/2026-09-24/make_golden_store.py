"""Write the golden store that tests/test_evidence_survivors_write_receipts.py re-verifies.

Run once, on the release the fixture is FROM (3.9.1, commit 21b6cdd), and commit the output:

    python audits/2026-09-24/make_golden_store.py tests/fixtures/golden_store_3.9.1

It exercises every field a write receipt commits to, so a later change to how any of them is
hashed -- a renamed key, a changed default, a dropped nonce -- makes this store fail
`verify_writes()` the way it would make every store written today fail after an upgrade. Records
before the receipt chain existed are backfilled by `enable_receipts()`, one record is slashed (an
amendment with a reason), one is confirmed, one is superseded by a correction, one is erased.

The signing key is generated here and thrown away; only the public half is written beside the
store, so the fixture proves the chain verifies against that key and holds nothing secret.
"""
from __future__ import annotations

import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from inspeximus import Inspeximus, __version__, new_receipt_keypair  # noqa: E402


def main(out_dir: str) -> None:
    # JSON, not the row store: a fixture a reviewer can read and a diff can show.
    os.environ["INSPEXIMUS_STORE_FORMAT"] = "json"
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir)
    path = os.path.join(out_dir, "mem.json")
    sk, pk = new_receipt_keypair()

    # Written BEFORE the chain existed, then covered by a signed backfill.
    early = Inspeximus(path, receipts=False)
    early.remember("The staging database is db-1.internal.", key="staging::db", object="db-1.internal",
                   source={"doc": "runbook"}, valid_from=1788000000.0)
    early.flush()

    m = Inspeximus(path, receipts=True, receipt_key=sk)
    m.enable_receipts(reason="golden fixture: records written before receipts")
    m.remember("The staging database is db-7.internal.", key="staging::db", object="db-7.internal",
               source={"doc": "runbook-v2"})                                  # a correction: retires
    m.remember("Deploys freeze on Fridays.", key="deploy::freeze", object="friday",
               source={"doc": "policy"}, valid_from=1788100000.0)
    prov = m.remember("The on-call rota is in PagerDuty.", key="oncall::rota", object="pagerduty",
                      source={"doc": "chat"}, provisional=True)
    m.confirm(prov, by="ops-lead")
    bad = m.remember("Rotate keys every 400 days.", key="keys::rotation", object="400d",
                     source={"doc": "a-bad-wiki"}, mtype="semantic")
    m.slash([bad], scope="memory", reason="caught: contradicts the key-management policy")
    gone = m.remember("Alice's phone is +100.", key="alice::phone", object="+100", source={"doc": "crm/alice"})
    m.forget(where=lambda r: r["id"] == gone, request_id="DSAR-GOLDEN-1", basis="gdpr_art17")
    m.flush()

    ok, problems = m.verify_writes(pk)
    if not ok:
        raise SystemExit(f"the fixture does not verify on the release that wrote it: {problems}")
    with open(os.path.join(out_dir, "golden.json"), "w", encoding="utf-8") as fh:
        json.dump({"inspeximus_version": __version__, "receipt_pubkey": pk,
                   "records": len(m.items), "receipts": len(m._receipts),
                   "tombstones": len(m._tombstones)}, fh, indent=1)
    for name in sorted(os.listdir(out_dir)):
        print(name, os.path.getsize(os.path.join(out_dir, name)))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "tests", "fixtures", "golden_store_3.9.1"))
