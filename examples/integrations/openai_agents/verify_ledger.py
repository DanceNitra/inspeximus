"""Verify the action ledger agent.py wrote, and fail if a single entry was changed.

    python verify_ledger.py --dir agent_data            # exit 0: every check held
    python verify_ledger.py --dir agent_data --pubkey HEX   # an auditor who does not hold the key

Exit 0 when every check holds, 1 when any fails (each problem is printed), 2 on a usage error.

THE CHECKS, in order. A failure in the first two stops the run, because a chain that does not verify
makes every later answer about it meaningless.
  1. FORM. The file is the exact bytes ActionLedger writes for its own content. This is what catches a
     one-byte edit that no entry-level check can see: a space swapped for a tab between two JSON tokens
     changes no entry, and a verifier that only parses the file calls that intact.
  2. CHAIN. Every entry's hash is recomputed, every `prev` link and `seq` followed, and every Ed25519
     signature checked against ONE pinned public key (`--pubkey`, else derived from the store's receipt
     key, which receipt_key_for keeps outside the data directory).
  3. MEMORY. The store's own write-receipt chain verifies (`verify_writes`) under the same key, and each
     entry's memory_state names the receipt that was the chain's tail at the count it recorded.
  4. BASED ON. Every tool entry carries a memory digest, and every `meta.based_on` reference resolves to
     an earlier entry with that exact hash.
  5. TRANSCRIPT. The session transcript (held in the same receipted store) and the ledger account for the
     same tool calls: each call in the transcript has exactly one entry, whose salted digests match the
     call's arguments and the output the model was given, and each tool entry is in the transcript. The
     one exception is a call the run raised on (meta.flushed): the SDK saves nothing of that turn.

LIMITS. The operator who holds the signing key can rewrite the store and the ledger consistently, and
nothing in these files can show that. What does is a copy of the chain's tail held by someone else:
`ActionLedger.timestamp_tail()`, or `anchor()` co-signed by independent witnesses.
"""
from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import json
import os
import sys

from inspeximus import Inspeximus
from inspeximus.actions import ActionLedger
from inspeximus.core import receipt_key_for
from inspeximus.integrations.openai_agents import InspeximusSession

from ledger_hooks import HOSTED_CALL_TYPES, as_item, call_arguments

DEFAULT_SESSION = "billing-desk:ana"


def pubkey_of(store_path: str) -> str | None:
    """The public half of the store's receipt key, or None when that key is not on this machine."""
    try:
        sk = receipt_key_for(store_path, create=False)
    except Exception:  # noqa: BLE001 - no key here: the caller must pin one with --pubkey
        return None
    if not sk:
        return None
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    pk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(sk)).public_key()
    return pk.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def _session_items(session) -> list:
    """`Session.get_items()` is a coroutine. Run it here, or on a worker thread when the caller is itself
    inside an event loop (an agent app verifying its own ledger), where asyncio.run() refuses."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(session.get_items())
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, session.get_items()).result()


def check_form(ledger_path: str) -> list[str]:
    try:
        raw = open(ledger_path, "rb").read()
    except OSError as e:
        return [f"cannot read {ledger_path}: {e}"]
    try:
        text = raw.decode("utf-8")
        data = json.loads(text)
    except ValueError as e:
        return [f"{os.path.basename(ledger_path)} is not valid UTF-8 JSON: {e}"]
    if not isinstance(data, list):
        return [f"{os.path.basename(ledger_path)} is not a ledger (expected a JSON array)"]
    canon = json.dumps(data, indent=1, ensure_ascii=False)
    # Path.write_text translates newlines on Windows, so the writer's own bytes there end in \r\n.
    if text not in (canon, canon.replace("\n", "\r\n")):
        at = next((i for i, (a, b) in enumerate(zip(text, canon)) if a != b), min(len(text), len(canon)))
        return [f"{os.path.basename(ledger_path)} differs from the bytes ActionLedger writes for its own "
                f"content, first at character {at}: an edit outside every entry (whitespace, encoding)"]
    return []


def verify(data_dir: str, session_id: str = DEFAULT_SESSION, pubkey: str | None = None) -> tuple[list[str], dict]:
    """(problems, summary). Empty problems means every check held."""
    store_path = os.path.join(data_dir, "memory.json")
    ledger_path = store_path + ".actions.json"
    summary: dict = {"ledger": ledger_path}
    if not os.path.exists(ledger_path):
        return [f"no ledger at {ledger_path}"], summary

    problems = check_form(ledger_path)
    if problems:
        return problems, summary

    pubkey = pubkey or pubkey_of(store_path)
    if not pubkey:
        return ["no public key to verify against: pass --pubkey, or run where the store's receipt key is "
                "(INSPEXIMUS_KEY_HOME / INSPEXIMUS_RECEIPT_KEY)"], summary
    summary["pubkey"] = pubkey
    ok, chain = ActionLedger(None, path=ledger_path).verify(expected_pubkey=pubkey, bind_to_store=False)
    if not ok:
        return [f"chain: {p}" for p in chain], summary

    # 3. memory. Opened with receipts on and no key, so this handle can check signatures and cannot sign.
    store = Inspeximus(store_path, receipts=True)
    ok, writes = store.verify_writes(expected_pubkey=pubkey)
    problems += [f"memory: {p}" for p in writes]
    led = ActionLedger(store, path=ledger_path)
    ok, bound = led.verify(expected_pubkey=pubkey)
    problems += [f"binding: {p}" for p in bound]
    entries = led.entries()
    summary.update(entries=len(entries), receipts=len(getattr(store, "_receipts", None) or []))

    # 4. what each tool entry was based on
    by_seq = {e["seq"]: e for e in entries}
    tool_entries = [e for e in entries if str(e.get("action", "")).split(":")[0] in ("tool", "hosted", "handoff")]
    for e in tool_entries:
        if not (e.get("memory_state") or {}).get("digest"):
            problems.append(f"seq {e['seq']}: no memory digest, so nothing says what memory held when it ran")
        for ref in (e.get("meta") or {}).get("based_on") or []:
            j = ref.get("seq") if isinstance(ref, dict) else None
            if not isinstance(j, int) or j >= e["seq"] or by_seq.get(j, {}).get("hash") != ref.get("hash"):
                problems.append(f"seq {e['seq']}: based_on {ref!r} does not resolve to an earlier entry")

    # 5. the transcript and the ledger account for the same calls
    items = [as_item(i) for i in _session_items(InspeximusSession(session_id, store=store))]
    outputs = {i.get("call_id"): i.get("output") for i in items if str(i.get("type", "")).endswith("_output")}
    calls = {}
    for i in items:
        if i.get("type") == "function_call" and i.get("call_id"):
            calls[i["call_id"]] = ("fn", i)
        elif i.get("type") in HOSTED_CALL_TYPES and i.get("id"):
            calls[i["id"]] = ("hosted", i)
    mine = [e for e in tool_entries if e.get("session") == session_id]
    by_call: dict = {}
    for e in mine:
        by_call.setdefault((e.get("meta") or {}).get("call_id"), []).append(e)
    for cid, (kind, item) in calls.items():
        found = by_call.get(cid) or []
        if len(found) != 1:
            problems.append(f"transcript call {cid} ({item.get('name') or item.get('type')}) has "
                            f"{len(found)} ledger entries, expected 1")
            continue
        e = found[0]
        try:
            if kind == "fn":
                m = led.matches(e["seq"], inputs=call_arguments(item.get("arguments")), output=outputs.get(cid))
                want_out = outputs.get(cid) is not None and e.get("output_sha256") is not None
                if m["inputs"] is not True or (want_out and m["output"] is not True):
                    problems.append(f"seq {e['seq']}: digests do not match transcript call {cid} "
                                    f"(inputs {m['inputs']}, output {m['output']})")
            elif led.matches(e["seq"], inputs=item)["inputs"] is not True:
                problems.append(f"seq {e['seq']}: digest does not match hosted call {cid} in the transcript")
        except FileNotFoundError as ex:
            problems.append(str(ex))
            break
    raised = 0
    for cid, es in by_call.items():
        if cid in calls:
            continue
        if all((e.get("meta") or {}).get("flushed") and e.get("status") in ("error", "not_run") for e in es):
            # flush() wrote it: the tool's exception propagated, and the SDK saves nothing of a turn that
            # raised, so the ledger is the only record of the call. Signed, so it cannot be claimed later.
            raised += len(es)
            continue
        problems.append(f"seq {es[0]['seq']}: {es[0]['action']} (call {cid}) is not in the transcript")
    summary.update(tool_calls=len(calls), tool_entries=len(mine), raised=raised)
    return problems, summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", default="agent_data", help="the directory agent.py wrote")
    ap.add_argument("--session", default=DEFAULT_SESSION, help="the session whose tool calls to account for")
    ap.add_argument("--pubkey", default=None, help="pin the Ed25519 public key (hex) the ledger must be signed by")
    a = ap.parse_args(argv)
    problems, s = verify(a.dir, a.session, a.pubkey)
    if problems:
        print(f"FAIL {s['ledger']}: {len(problems)} problem(s)")
        for p in problems:
            print("  - " + p)
        return 1
    print(f"OK {s['ledger']}: {s['entries']} entries, every hash, link and signature verifies "
          f"(key {s['pubkey'][:16]}...)")
    print(f"   bound to {s['receipts']} signed memory receipts; {s['tool_calls']} tool calls in the transcript, "
          f"{s['tool_entries']} ledger entries, each digest matches")
    if s.get("raised"):
        print(f"   {s['raised']} call(s) the run raised on: in the ledger, not in the transcript (the SDK does "
              f"not save a turn that raised)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
