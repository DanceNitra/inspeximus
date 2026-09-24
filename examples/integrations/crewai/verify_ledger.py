"""Verify a CrewAI run's action ledger afterwards. Exit 0 only when every check passes, 1 otherwise.

    python verify_ledger.py run                           # pins read from run/receipt.pub and run/run.json
    python verify_ledger.py run --expected-pubkey HEX --expected-tail HASH --expected-entries N

What is checked, in order, and what each one catches:

  1. BYTES. The ledger file must be exactly what `ActionLedger` writes (`json.dumps(indent=1)`, LF or
     CRLF), and every hash, signature and key must be lowercase hex of the right length. The chain
     check alone does not see an edit that leaves the parsed entries equal: a space turned into a tab
     between two tokens, or one hex digit of a signature switched to upper case, which decodes to the
     same bytes and still verifies. Either would pass step 2. Neither passes this step.
  2. CHAIN. `inspeximus.actions.verify_file`: every entry's hash recomputed from its content, every
     `prev` link, every Ed25519 signature, and the key they were made with, offline.
  3. PINS. The number of entries and the hash of the last one, when known. The chain cannot see its
     own tail cut off (the ledger's documented limit); a pinned count and tail can.
  4. MEMORY BINDING. `ActionLedger.verify()` with the store open: each entry's `last_receipt` must be
     the tail of the store's receipt chain at the count it recorded. Then `verify_writes()`, which
     re-hashes every stored memory against its write receipt, so a memory the agent acted on cannot be
     rewritten afterwards either. This is what `inspeximus actions verify` runs. Last, the ledger must be
     signed by the same key as the store's receipts, so a ledger re-signed end to end with a new key
     fails even when no public key was pinned.
  5. BASIS. Every `tool:` entry the CrewAI recorder wrote must say what it was based on: the memory
     records CrewAI's recall put in the prompt (each must exist in the store, be covered by a write
     receipt and still match it, or have been erased later with a tombstone left behind), the store's
     state digest, and the earlier tool calls of the same task by seq and hash (each must resolve to
     that entry). `memory_state.recalled` must list the same ids.

PINS BESIDE THE LEDGER. By default the public key and the tail come from receipt.pub and run.json in the
same directory. That catches any edit to the ledger. It does not catch someone who can rewrite those two
files as well; keep a copy of both somewhere else and pass them with --expected-pubkey / --expected-tail.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Any

STORE_NAME = "memory.json"
_HEX = {"hash": 64, "prev": 64, "sig": 128, "pubkey": 64, "inputs_sha256": 64, "output_sha256": 64}
_LOWER_HEX = re.compile(r"^[0-9a-f]+$")


def _canonical_bytes(data: Any) -> list[bytes]:
    """The exact bytes `ActionLedger._save` produces for `data`, with LF and with CRLF line ends
    (Path.write_text translates newlines on Windows)."""
    text = json.dumps(data, indent=1, ensure_ascii=False)
    return [text.encode("utf-8"), text.replace("\n", "\r\n").encode("utf-8")]


def check_bytes(raw: bytes) -> tuple[Any, list[str]]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        return None, [f"bytes: the ledger is not valid UTF-8 JSON ({type(e).__name__}: {e})"]
    problems = []
    if raw not in _canonical_bytes(data):
        problems.append("bytes: the file is not in the form ActionLedger writes; it was edited outside the ledger")
    if not isinstance(data, list):
        return data, problems + ["bytes: the ledger is not a JSON array"]
    for i, e in enumerate(data):
        if not isinstance(e, dict):
            problems.append(f"bytes: element {i} is not an object")
            continue
        for field, n in _HEX.items():
            v = e.get(field)
            if v is None:
                continue
            if not isinstance(v, str) or len(v) != n or not _LOWER_HEX.match(v):
                problems.append(f"bytes: seq {e.get('seq', i)}: {field} is not {n} lowercase hex digits")
        lr = (e.get("memory_state") or {}).get("last_receipt")
        if lr is not None and (not isinstance(lr, str) or len(lr) != 64 or not _LOWER_HEX.match(lr)):
            problems.append(f"bytes: seq {e.get('seq', i)}: memory_state.last_receipt is not 64 lowercase hex digits")
    return data, problems


def check_basis(entries: list[dict], store) -> tuple[list[str], list[str]]:
    """Step 5: every CrewAI tool entry names what it was based on, and each name resolves.

    A memory erased AFTER the call is not a failure when the store holds a tombstone for it: CrewAI's
    consolidation may update or delete a record later, and the adapter does both through `forget()`,
    which leaves a signed, content-free tombstone that `verify_writes()` checks. A memory that is simply
    gone, with no tombstone, is a failure. Returns (problems, notes)."""
    problems, notes = [], []
    tombstoned = {t.get("memory_id") for t in (getattr(store, "_tombstones", None) or [])}
    by_seq = {e.get("seq"): e for e in entries}
    tool_entries = [e for e in entries if str(e.get("action", "")).startswith("tool:")]
    if not tool_entries:
        problems.append("basis: the ledger records no tool call at all")
    for e in tool_entries:
        seq = e.get("seq")
        meta = e.get("meta") or {}
        if e.get("status") == "blocked":
            continue                           # a hook blocked it: nothing ran, so nothing was acted on
        ms = e.get("memory_state") or {}
        basis = meta.get("based_on")
        if meta.get("framework") != "crewai" or not isinstance(basis, dict):
            problems.append(f"basis: seq {seq}: {e.get('action')} carries no based_on record")
            continue
        if not ms.get("digest"):
            problems.append(f"basis: seq {seq}: no memory state digest")
        memories = basis.get("memories") or []
        if not memories:
            problems.append(f"basis: seq {seq}: {e.get('action')} was based on no memory at all")
        ids = [m.get("id") for m in memories]
        if list(ms.get("recalled") or []) != [i for i in ids if i][:64]:
            problems.append(f"basis: seq {seq}: memory_state.recalled does not match based_on.memories")
        for m in memories:
            if not m.get("id"):
                problems.append(f"basis: seq {seq}: CrewAI record {m.get('crewai_id')} maps to no inspeximus record")
                continue
            p = store.provenance(id=m["id"])
            integ = p.get("integrity") or {}
            if not p.get("found"):
                if m["id"] in tombstoned:
                    notes.append(f"basis: seq {seq}: memory {m['id']} was erased after the call; its tombstone is in the store")
                else:
                    problems.append(f"basis: seq {seq}: memory {m['id']} is not in the store and left no tombstone")
            elif not (integ.get("receipted") and integ.get("content_matches_receipt")):
                problems.append(f"basis: seq {seq}: memory {m['id']} does not match its write receipt")
        for ref in basis.get("earlier_tool_calls") or []:
            target = by_seq.get(ref.get("seq"))
            if (target is None or not isinstance(ref.get("seq"), int) or ref["seq"] >= seq
                    or target.get("hash") != ref.get("hash")):
                problems.append(f"basis: seq {seq}: earlier tool call {ref} does not resolve to that entry")
            elif (target.get("meta") or {}).get("task_id") != meta.get("task_id"):
                problems.append(f"basis: seq {seq}: earlier tool call seq {ref['seq']} belongs to another task")
    return problems, notes


def verify(run_dir, expected_pubkey: str | None = None, expected_tail: str | None = None,
           expected_entries: int | None = None) -> dict:
    """Run every check. Returns {ok, problems, notes, entries}. Never raises on a bad ledger."""
    from inspeximus import Inspeximus
    from inspeximus.actions import ActionLedger, verify_file

    run = pathlib.Path(run_dir)
    store_path = run / STORE_NAME
    ledger_path = run / (STORE_NAME + ".actions.json")
    notes, problems = [], []
    for p in (store_path, ledger_path):
        if not p.exists():
            return {"ok": False, "problems": [f"missing: {p}"], "notes": notes, "entries": 0}

    # Pins not given on the command line come from the run's own files, and the report says so.
    if expected_pubkey is None and (run / "receipt.pub").exists():
        expected_pubkey = (run / "receipt.pub").read_text(encoding="utf-8").strip()
        notes.append("public key pinned from receipt.pub beside the ledger")
    summary = {}
    if (run / "run.json").exists():
        try:
            summary = json.loads((run / "run.json").read_text(encoding="utf-8"))
        except ValueError:
            problems.append("pins: run.json is not JSON")
    if expected_tail is None and summary.get("ledger_tail"):
        expected_tail = summary["ledger_tail"]
        notes.append("tail hash pinned from run.json beside the ledger")
    if expected_entries is None and isinstance(summary.get("ledger_entries"), int):
        expected_entries = summary["ledger_entries"]
        notes.append("entry count pinned from run.json beside the ledger")
    if not expected_pubkey:
        notes.append("NO public key pinned: a chain re-signed end to end with another key would pass")

    # 1. bytes
    data, p1 = check_bytes(ledger_path.read_bytes())
    problems += p1
    if not isinstance(data, list):
        return {"ok": False, "problems": problems, "notes": notes, "entries": 0}
    entries = [e for e in data if isinstance(e, dict) and e.get("kind") != "checkpoint"]

    # 2. chain, offline
    ok2, p2 = verify_file(ledger_path, expected_pubkey=expected_pubkey)
    problems += ["chain: " + x for x in p2]

    # 3. pins
    if expected_entries is not None and len(entries) != expected_entries:
        problems.append(f"pins: {len(entries)} entries, expected {expected_entries}")
    if expected_tail is not None and (not entries or entries[-1].get("hash") != expected_tail):
        problems.append(f"pins: the last entry is not the pinned tail {expected_tail[:16]}")

    # 4. memory binding, and the memories themselves against their receipts
    try:
        store = Inspeximus(path=str(store_path), receipts=True)
        led = ActionLedger(store, path=ledger_path)
        _ok, p4 = led.verify(expected_pubkey=expected_pubkey)
        problems += ["binding: " + x for x in p4 if x not in p2]      # the chain part was step 2
        _ok, pw = store.verify_writes(expected_pubkey=expected_pubkey)
        problems += ["memory: " + x for x in pw]
        # One key for both chains: the ledger is signed with the store's receipt key, so an entry
        # signed by any other key was not written by the process that wrote these memories.
        store_keys = {r.get("pubkey") for r in (getattr(store, "_receipts", None) or []) if r.get("pubkey")}
        ledger_keys = {e.get("pubkey") for e in entries if e.get("pubkey")}
        if len(store_keys) == 1 and ledger_keys and ledger_keys != store_keys:
            problems.append("binding: the ledger is not signed by the key that signs the store's receipts")
        # 5. what each tool call was based on
        p5, n5 = check_basis(entries, store)
        problems += p5
        notes += n5
    except Exception as e:                     # noqa: BLE001 - a broken store is a failed check
        problems.append(f"binding: cannot open or read the store: {type(e).__name__}: {e}")
    return {"ok": not problems, "problems": problems, "notes": notes, "entries": len(entries)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Verify a CrewAI run's inspeximus action ledger.")
    ap.add_argument("run_dir", help="the directory crew.py wrote (memory.json and its ledger)")
    ap.add_argument("--expected-pubkey", default=None, help="hex Ed25519 key every entry must be signed by")
    ap.add_argument("--expected-tail", default=None, help="hash the last ledger entry must have")
    ap.add_argument("--expected-entries", type=int, default=None, help="number of entries the ledger must hold")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    a = ap.parse_args(argv)
    r = verify(a.run_dir, a.expected_pubkey, a.expected_tail, a.expected_entries)
    if a.json:
        print(json.dumps(r, indent=1))
    else:
        for n in r["notes"]:
            print("  note " + n)
        for p in r["problems"]:
            print("  FAIL " + p)
        print(("OK " if r["ok"] else "FAIL ") + f"action ledger in {a.run_dir} ({r['entries']} entries)")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
