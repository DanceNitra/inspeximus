"""Every tool call of an agno agent, recorded in the inspeximus action ledger with what it was based on.

The agent has two tools: `recall_memory`, which asks the inspeximus store what it knows, and
`run_migration`, which acts on it. One tool hook on the agent (ledger_hook.py) writes each call into
the action ledger as a signed, hash-chained entry that also carries the store's state and the ids
the last recall returned. So the entry for `run_migration` says which remembered fact the agent was
acting on when it chose the database.

The run goes: remember the staging database (db-3), let the agent run the migration, correct the
fact (db-7), let the agent run it again. The ledger then shows two migrations based on two different
memory states, and `what_it_knew()` shows that the fact behind the first one has since been
superseded. verify.py checks the whole run offline and fails on a one-byte edit.

    python examples/integrations/agno_actions/run.py --out agno_actions_run
    python examples/integrations/agno_actions/verify.py agno_actions_run

No API key: the model is ScriptedModel (stub_model.py). Pass any agno model to `build_agent()` to run
the same agent against a real one.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, List, Optional

from agno.agent import Agent

from inspeximus import Inspeximus, receipt_key_for
from inspeximus.actions import ActionLedger

from ledger_hook import ledger_hook
from stub_model import ScriptedModel

STORE = "memory.json"
TRANSCRIPT = "transcript.json"
PUBKEY = "ledger.pub"
REQUEST = "Run the pending migration on the staging database."
ACTOR = "agno:ops-agent"


def open_store(out_dir: Path) -> Inspeximus:
    """A receipted, signed store. The signing key is kept outside `out_dir` (receipt_key_for), so a
    copy of the run directory cannot re-sign an edited ledger."""
    path = str(out_dir / STORE)
    return Inspeximus(path, receipts=True, receipt_key=receipt_key_for(path))


def build_agent(store: Inspeximus, ledger: ActionLedger, model: Any = None) -> Agent:
    def recall_memory(query: str) -> str:
        """Return what the agent currently remembers about a topic."""
        hits = store.recall(query, k=3)
        return "\n".join(h["text"] for h in hits) or "nothing remembered"

    def run_migration(database: str) -> str:
        """Apply the pending schema migration to a database host."""
        return f"migration 0042 applied on {database}"

    return Agent(
        name="ops-agent",
        model=model if model is not None else ScriptedModel(),
        tools=[recall_memory, run_migration],
        tool_hooks=[ledger_hook(ledger)],
        user_id="ops@example.com",
        session_id="staging-maintenance",
        telemetry=False,
    )


def transcript_rows(run: Any) -> List[dict]:
    """agno's own record of the tool calls in a run (RunOutput.tools): what verify.py checks the
    ledger's digests against."""
    return [{"run_id": run.run_id, "session_id": run.session_id, "user_id": run.user_id,
             "tool_call_id": t.tool_call_id, "tool_name": t.tool_name, "tool_args": t.tool_args,
             "result": t.result, "tool_call_error": bool(t.tool_call_error)} for t in (run.tools or [])]


def write_canonical(path: Path, obj: Any) -> None:
    """The one byte form verify.py accepts for the transcript."""
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n")


def fresh_dir(out_dir: Path) -> None:
    """Start from an empty run directory, removing only the files a previous run of this script left."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in os.listdir(out_dir):
        if name.startswith(STORE) or name in (TRANSCRIPT, PUBKEY):
            os.remove(out_dir / name)


def _records(n: int) -> str:
    return f"{n} record" + ("" if n == 1 else "s")


def show(ledger: ActionLedger, rows: List[dict]) -> None:
    by_call = {r["tool_call_id"]: r for r in rows}
    for e in ledger.entries():
        ms = e["memory_state"]
        if e.get("kind") == "lifecycle":
            print(f"seq {e['seq']}  {e['action']}  store {ms['digest'][:12]}, {_records(ms['records'])}")
            continue
        row = by_call.get((e.get("meta") or {}).get("tool_call_id")) or {}
        print(f"seq {e['seq']}  {e['action']}({json.dumps(row.get('tool_args'))})  {e['status']}")
        print(f"       based on: store {ms['digest'][:12]}, {_records(ms['records'])}, "
              f"receipt {str(ms['last_receipt'])[:12]}")
        facts = ledger.what_it_knew(e["seq"])["recalled_now"]
        if not facts:
            print("       recalled: nothing since the previous entry")
        for fact in facts:
            rec = fact.get("current") or {}
            line = f"       recalled: {rec.get('id')} {rec.get('text')!r}, {rec.get('status')} now"
            if rec.get("status") == "superseded":
                nxt = next((t for t in fact.get("timeline", []) if t.get("status") == "active"), None)
                line += f", replaced by {nxt['text']!r}" if nxt else ""
            print(line)


def main(argv: Optional[List[str]] = None) -> Path:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default="agno_actions_run", help="run directory (default: ./agno_actions_run)")
    out_dir = Path(ap.parse_args(argv).out)
    fresh_dir(out_dir)

    store = open_store(out_dir)
    ledger = ActionLedger(store, actor=ACTOR)
    agent = build_agent(store, ledger)

    ledger.lifecycle("start", actor=ACTOR, note="agno ops-agent, staging maintenance")
    store.remember("The staging database is db-3.internal", key="staging-db")
    first = agent.run(REQUEST)
    # The correction, under the same key: db-3 is retired, not deleted.
    store.remember("The staging database is db-7.internal", key="staging-db")
    second = agent.run(REQUEST)
    # Closes the run. Its memory_state pins the store as the run left it, which verify.py compares.
    ledger.lifecycle("stop", actor=ACTOR, note="run finished")

    rows = transcript_rows(first) + transcript_rows(second)
    write_canonical(out_dir / TRANSCRIPT, rows)
    with open(out_dir / PUBKEY, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(store.receipt_pubkey + "\n")

    print(f"agent, before the correction: {first.content}")
    print(f"agent, after the correction:  {second.content}\n")
    show(ledger, rows)
    calls = sum(1 for e in ledger.entries() if e["action"].startswith("tool:"))
    print(f"\n{calls} tool calls, {len(ledger)} entries in {ledger.path}")
    verify = Path(__file__).with_name("verify.py")
    try:
        verify = Path(os.path.relpath(verify))
    except ValueError:                                  # another drive on Windows
        pass
    print(f"check it: python {verify} {out_dir}")
    return out_dir


if __name__ == "__main__":
    main()
