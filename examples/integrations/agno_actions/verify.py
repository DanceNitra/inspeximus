"""Verify a run directory written by run.py, offline. Exit 0 when every check holds, 1 otherwise.

    python examples/integrations/agno_actions/verify.py agno_actions_run [--pubkey HEX]

What is checked, and against what:

  1. the ledger file, and the store's write-receipt file, are byte for byte the form inspeximus
     writes them in. JSON parsing forgives a changed space or newline, and a 17-digit timestamp often
     parses to the same float after its last digit changes; this check does not forgive either, so no
     single byte of either file can change unnoticed.
  2. the ledger chain: every entry's hash, every link to the previous entry, every Ed25519 signature
     against the pinned public key, and every entry's memory_state against the store's receipt chain
     (ActionLedger.verify).
  3. the store: every record still matches its signed write receipt (Inspeximus.verify_writes).
  4. the transcript, agno's own record of the tool calls (RunOutput.tools), byte for byte in the form
     run.py writes it, and one ledger entry for each tool call in it whose salted digests match the
     call's arguments and result (ActionLedger.matches). A tool call agno ran that the ledger does
     not hold fails here, and so does a ledger entry agno never reported.
     A call is named by agno's run_id and the tool_call_id; the session and user ids must match too.
  5. every tool entry says what it was based on: a store state digest and a receipt it is bound to.
  6. the store is as the run left it: its state digest (ids, status, timestamps, keys, content) equals
     the one the closing lifecycle:stop entry recorded. The write receipts in 3 cover content, not
     status, so without this a superseded fact could be flipped back to active unnoticed.

The public key is read from `<run>/ledger.pub` unless --pubkey is given. In production, pin it from
somewhere the ledger's writer cannot edit; a key read from the same directory as the ledger only
proves the files agree with each other.

What this does not check: that the ledger is complete at its tail. Whoever holds the signing key can
drop the last entries and re-sign. Anchoring the tail with an independent witness closes that
(`Inspeximus.anchor()`, docs/TRANSPARENCY.md); this example does not.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import List, Optional

from inspeximus import Inspeximus
from inspeximus.actions import ActionLedger

STORE = "memory.json"
TRANSCRIPT = "transcript.json"
PUBKEY = "ledger.pub"


#: The fields run.py writes for each tool call. A renamed key reads as a missing one, and a missing
#: `tool_call_error` would read as False, so the set is checked exactly.
TRANSCRIPT_FIELDS = {"run_id", "session_id", "user_id", "tool_call_id", "tool_name", "tool_args",
                     "result", "tool_call_error"}


def _canonical(raw: bytes, indent: int, crlf_ok: bool = False) -> bool:
    """True when `raw` is exactly json.dumps(json.loads(raw), indent=indent, ensure_ascii=False).

    JSON parsing forgives a changed space or newline, and a float parses to the same value from more
    than one spelling (the last digit of a 17-digit timestamp often does not change the double), so a
    hash over the parsed value cannot see those edits. Re-serialising and comparing bytes can.
    `crlf_ok` accepts CRLF line endings, which Path.write_text produces on Windows; a one-byte edit
    cannot turn one ending into the other."""
    try:
        canon = json.dumps(json.loads(raw.decode("utf-8")), indent=indent, ensure_ascii=False).encode("utf-8")
    except ValueError:
        return False
    return raw == canon or (crlf_ok and raw == canon.replace(b"\n", b"\r\n"))


def _transcript(raw: bytes, problems: List[str]) -> list:
    try:
        rows = json.loads(raw.decode("utf-8"))
    except ValueError as e:
        problems.append(f"transcript: not JSON ({e})")
        return []
    canon = (json.dumps(rows, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    if raw != canon:
        problems.append("transcript: the bytes differ from the form run.py writes (edited after the run)")
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        problems.append("transcript: not a list of tool calls")
        return []
    for i, row in enumerate(rows):
        if set(row) != TRANSCRIPT_FIELDS:
            problems.append(f"transcript row {i}: fields {sorted(set(row) ^ TRANSCRIPT_FIELDS)} renamed, "
                            f"missing or added")
    return rows


def verify_run(run_dir, pubkey: Optional[str] = None) -> List[str]:
    """Every problem found in the run directory; an empty list means it verifies. A file damaged so
    badly that a check cannot run is reported as a problem, never raised."""
    try:
        return _verify_run(Path(run_dir), pubkey)
    except Exception as e:  # noqa: BLE001 - a verifier that crashes on damaged input has not verified it
        return [f"cannot verify: {type(e).__name__}: {e}"]


def _verify_run(run: Path, pubkey: Optional[str]) -> List[str]:
    problems: List[str] = []
    if pubkey is None:
        try:
            text = (run / PUBKEY).read_bytes().decode("ascii")
        except (OSError, UnicodeDecodeError) as e:
            return [f"no public key to verify against ({e}); pass --pubkey"]
        if not re.fullmatch(r"[0-9a-f]{64}\n", text):
            return [f"{PUBKEY}: not one lowercase hex Ed25519 public key and a newline"]
        pubkey = text[:-1]

    store_path = run / STORE
    if not store_path.exists():
        return [f"no store at {store_path}"]
    store = Inspeximus(str(store_path), receipts=True)
    ledger = ActionLedger(store)
    if not ledger.path.exists():
        return [f"no ledger at {ledger.path}"]

    # 1. the bytes of the ledger (ActionLedger writes indent=1 through Path.write_text) and of the
    #    store's receipt file (inspeximus writes it compact since 3.13.0, and with indent=2 before).
    #    Either form is an exact re-serialisation, so a one-byte edit still fails both.
    if not _canonical(ledger.path.read_bytes(), indent=1, crlf_ok=True):
        problems.append("ledger: the bytes differ from the form ActionLedger writes (edited after the run)")
    receipts = Path(str(store_path) + ".receipts.json")
    _rb = receipts.read_bytes() if receipts.exists() else None
    if _rb is None or not (_canonical(_rb, indent=None) or _canonical(_rb, indent=2)):
        problems.append(f"store: {receipts.name} is missing, or its bytes differ from the form inspeximus "
                        f"writes (edited after the run)")

    # 2. the chain, the signatures and the memory binding
    _, chain_problems = ledger.verify(expected_pubkey=pubkey)
    problems += ["ledger: " + p for p in chain_problems]

    # 3. the store against its own receipts
    _, store_problems = store.verify_writes(expected_pubkey=pubkey)
    problems += ["store: " + p for p in store_problems]

    # 4. the transcript against the ledger, one entry per tool call and back
    try:
        rows = _transcript((run / TRANSCRIPT).read_bytes(), problems)
    except OSError as e:
        rows = []
        problems.append(f"transcript: cannot read ({e})")
    tool_entries = [e for e in ledger.entries()
                    if e.get("kind") == "action" and str(e.get("action", "")).startswith("tool:")
                    and (e.get("meta") or {}).get("framework") == "agno"]
    # A tool call is named by its run and the model's tool_call_id: a provider's ids are unique
    # within a run, and agno mints a uuid for a provider that sends none.
    by_id = {}
    for e in tool_entries:
        call = ((e.get("meta") or {}).get("run_id"), (e.get("meta") or {}).get("tool_call_id"))
        if not all(call):
            problems.append(f"ledger seq {e.get('seq')}: a tool entry with no run_id or tool_call_id")
        elif call in by_id:
            problems.append(f"ledger seq {e.get('seq')}: tool call {call[1]} of run {call[0]} recorded twice")
        else:
            by_id[call] = e
    seen = set()
    for row in rows:
        call = (row.get("run_id"), row.get("tool_call_id"))
        if call in seen:
            problems.append(f"transcript {call[1]}: the same tool call listed twice")
            continue
        e = by_id.get(call)
        if e is None:
            problems.append(f"transcript {call[1]} {row.get('tool_name')}: agno ran this tool call and the "
                            f"ledger has no entry for it")
            continue
        seen.add(call)
        where = f"ledger seq {e['seq']} ({call[1]})"
        if e.get("action") != "tool:" + str(row.get("tool_name")):
            problems.append(f"{where}: records {e.get('action')}, agno reports {row.get('tool_name')}")
        for field, recorded in (("session_id", e.get("session")), ("user_id", e.get("principal"))):
            if row.get(field) != recorded:
                problems.append(f"{where}: {field} is {recorded!r} in the ledger, {row.get(field)!r} in the transcript")
        try:
            m = ledger.matches(e["seq"], inputs=row.get("tool_args") or {},
                               output=None if row.get("tool_call_error") else row.get("result"))
        except (OSError, ValueError) as ex:
            problems.append(f"{where}: cannot compare with the transcript ({ex})")
            continue
        if m["inputs"] is not True:
            problems.append(f"{where}: the arguments in the transcript do not match the ledger's digest")
        if row.get("tool_call_error"):
            if e.get("status") != "error":
                problems.append(f"{where}: agno reports an error, the ledger says {e.get('status')}")
        elif m["output"] is not True:
            problems.append(f"{where}: the result in the transcript does not match the ledger's digest")
    for call, e in by_id.items():
        if call not in seen:
            problems.append(f"ledger seq {e['seq']} ({call[1]}): a tool call agno never reported")

    # 5. what each call was based on
    for e in tool_entries:
        ms = e.get("memory_state") or {}
        if not ms.get("digest") or not ms.get("last_receipt"):
            problems.append(f"ledger seq {e.get('seq')}: no memory state, so nothing says what it was based on")

    # 6. the store as the run left it
    entries = ledger.entries()
    if not entries or entries[-1].get("action") != "lifecycle:stop":
        problems.append("ledger: the run is not closed by a lifecycle:stop entry, so the store's final state "
                        "is not pinned")
    elif store.state_digest() != (entries[-1].get("memory_state") or {}).get("digest"):
        problems.append(f"store: its state differs from the one ledger seq {entries[-1]['seq']} recorded when "
                        f"the run closed (changed after the run)")
    return problems


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Verify an agno action-receipts run directory.")
    ap.add_argument("run_dir")
    ap.add_argument("--pubkey", help="the Ed25519 public key (hex) every entry must be signed with")
    a = ap.parse_args(argv)
    problems = verify_run(a.run_dir, a.pubkey)
    if problems:
        print(f"FAIL: {len(problems)} problem(s) in {a.run_dir}")
        for p in problems:
            print("  - " + p)
        return 1
    ledger = ActionLedger(path=Path(a.run_dir) / (STORE + ".actions.json"))
    print(f"OK: {len(ledger)} ledger entries in {a.run_dir}: chain, signatures, memory binding, store "
          f"receipts and the agno transcript all verify")
    return 0


if __name__ == "__main__":
    sys.exit(main())
