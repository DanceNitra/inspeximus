#!/usr/bin/env python
"""Run the assessor pack on a real store, as a customer would, and record the onboarding friction.

WHY RUN IT ON OUR OWN STORE. We sell an evidence layer and we are our own first deployer. A pack
that renders on a fixture says nothing about the day a customer runs it on the store they actually
have. The deliverable here is not "it worked": it is the LIST of fields a person has to sit down and
answer before the pack is complete, because that list is the question every buyer asks.

    python probes/what_a_real_operator_must_answer.py --store path/to/memory.json

It copies the store and every sidecar to a temporary directory first and works on the copy. A probe
that writes to the store it is measuring has changed the thing it reports on.

Writes a receipt beside this file: the field list, the timings, and what the pack could not build.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from inspeximus import Inspeximus                                      # noqa: E402
from inspeximus.assessor_pack import (answers_from_form, assessor_pack,  # noqa: E402
                                      intake_form, render_markdown)

DEFAULT_STORE = os.path.join(os.path.expanduser("~"), ".inspeximus", "mcp_memory_chain.json")


def copy_store(src: str) -> str:
    """The store AND every sidecar. Receipts and tombstones are the evidence; a copy without them
    is a different store, and the pack would report on something the operator does not have."""
    tmp = tempfile.mkdtemp(prefix="assessor-pack-")
    folder, base = os.path.dirname(src), os.path.basename(src)
    copied = [f for f in os.listdir(folder) if f.startswith(base)]
    for f in copied:
        shutil.copy2(os.path.join(folder, f), os.path.join(tmp, f))
    return os.path.join(tmp, base)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", default=DEFAULT_STORE)
    ap.add_argument("--out", default=os.path.join(HERE, "what_a_real_operator_must_answer.result.json"))
    a = ap.parse_args(argv)

    if not os.path.exists(a.store):
        print("no store at %s" % a.store)
        return 2

    t0 = time.time()
    copy = copy_store(a.store)
    t_copy = time.time() - t0

    t0 = time.time()
    store = Inspeximus(copy, receipts=True)
    t_open = time.time() - t0

    form = intake_form()

    t0 = time.time()
    empty = assessor_pack(store)
    t_empty = time.time() - t0

    # The same pack with every field answered, to show the gaps are the only thing standing between
    # this store and a complete pack. The answers are placeholders and say so; this proves the
    # mechanism, it does not produce a filing.
    answered = dict(form)
    for row in answered["fields"]:
        row["value"] = "ANSWERED BY THE OPERATOR: %s" % row["field"]
    t0 = time.time()
    full = assessor_pack(store, operator=answers_from_form(answered))
    t_full = time.time() - t0

    fields = sorted({r["field"] for r in empty["unfilled"]})
    by_document: dict = {}
    for row in empty["unfilled"]:
        by_document.setdefault(row["document"], []).append(row["field"])

    receipt = {
        "kind": "inspeximus.assessor-pack-dogfood/1",
        "measured_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "store": a.store,
        # No public record count exists, which is itself a small finding from running this as a
        # customer: the first question a person asks of their own store has no API. The
        # private list is read here rather than calling memory_report(), which samples
        # neighbours and costs far more than a count.
        "records": len(getattr(store, "_Inspeximus__items", [])),
        "intake_fields": form["count"],
        "distinct_fields_unanswered": len(fields),
        "rows_unanswered": len(empty["unfilled"]),
        "placeholders_in_output": len(empty["placeholders_in_output"]),
        "documents_built": sorted(empty["documents"]),
        "documents_that_failed": empty["errors"],
        "complete_when_empty": empty["complete"],
        "complete_when_answered": full["complete"],
        "fields_a_person_must_answer": fields,
        "fields_by_document": {k: sorted(v) for k, v in sorted(by_document.items())},
        "seconds": {"copy": round(t_copy, 2), "open": round(t_open, 2),
                    "pack_empty": round(t_empty, 2), "pack_answered": round(t_full, 2)},
        "scope": ("The fields are what only a provider or a deployer can write. Everything else in "
                  "the pack is generated from the store and the ledger. The answered run uses "
                  "placeholder text to show the pack completes; it is not a filing."),
    }
    with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(receipt, fh, indent=2, sort_keys=True)

    print("store          : %s (%d records)" % (a.store, receipt["records"]))
    print("intake form    : %d fields" % receipt["intake_fields"])
    print("unanswered     : %d distinct fields across %d document rows"
          % (receipt["distinct_fields_unanswered"], receipt["rows_unanswered"]))
    print("documents      : %d built, %d failed"
          % (len(receipt["documents_built"]), len(receipt["documents_that_failed"])))
    print("complete       : %s empty, %s answered"
          % (receipt["complete_when_empty"], receipt["complete_when_answered"]))
    print("seconds        : %s" % receipt["seconds"])
    print("receipt        : %s" % a.out)
    if empty["errors"]:
        for name, err in empty["errors"].items():
            print("  COULD NOT BUILD %s: %s" % (name, err))
        return 1
    if not full["complete"]:
        print("  the pack did not complete even with every field answered")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
