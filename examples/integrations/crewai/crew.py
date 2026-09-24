"""A minimal CrewAI crew whose agent memory is inspeximus, with every tool call in the action ledger.

One agent, one task, two tools. The agent's memory is CrewAI's own `Memory` with an inspeximus
`InspeximusMemoryBackend` as its storage, so everything CrewAI remembers (the seeded policy, and the
result it saves after the task) is an inspeximus record with a signed write receipt. Every tool call
the agent makes is one signed entry in the inspeximus action ledger beside the store, carrying the
store's state digest, the receipt it was bound to, and the memory records CrewAI's recall put into
the prompt before the call (see tool_ledger.py).

No API key, no network: the LLM is the scripted `StubLLM` and the embedder is a hashing function
(stub_llm.py). CrewAI's telemetry and trace upload are switched off below, before crewai is imported.

    python crew.py --out run          # writes run/memory.json, run/memory.json.actions.json, ...
    python verify_ledger.py run       # exit 0: the ledger verifies; edit one byte and it is 1

Tested against crewai 1.15.22 (pinned in requirements.txt; see README.md).
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

# Off before crewai is imported: no telemetry, no trace upload, no first-run prompt on a terminal.
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from crewai import Agent, Crew, Process, Task                      # noqa: E402
from crewai.events.event_bus import crewai_event_bus               # noqa: E402
from crewai.events.types.tool_usage_events import ToolUsageStartedEvent  # noqa: E402
from crewai.tools import tool                                      # noqa: E402

from inspeximus import Inspeximus, new_receipt_keypair             # noqa: E402
from inspeximus.actions import ActionLedger                        # noqa: E402
from inspeximus.integrations.crewai import InspeximusMemoryBackend  # noqa: E402

from stub_llm import StubLLM, stub_embedder                        # noqa: E402
from tool_ledger import CrewToolLedger, TrackedMemory              # noqa: E402

STORE_NAME = "memory.json"

#: What actually ran, counted by the tools themselves. The recorder never sees this list; the run
#: compares it with the ledger afterwards, so "every tool call is recorded" is checked, not assumed.
EXECUTED: list[dict] = []


@tool("lookup_order")
def lookup_order(order_id: str) -> str:
    """Look up an order: its items, total, and the price of any item reported damaged."""
    EXECUTED.append({"tool": "lookup_order", "order_id": order_id})
    return f"order {order_id}: 3 items, total 120 EUR; the damaged item is 40 EUR"


@tool("issue_refund")
def issue_refund(order_id: str, amount_eur: float, reason: str) -> str:
    """Refund part of an order to the customer's original payment method."""
    EXECUTED.append({"tool": "issue_refund", "order_id": order_id, "amount_eur": amount_eur})
    return f"refund R-{len(EXECUTED):04d} issued: {amount_eur} EUR on order {order_id} ({reason})"


def run(out_dir: str | os.PathLike, policy: str | None = None, principal: str = "customer:ACME",
        receipt_key: tuple[str, str] | None = None) -> dict:
    """Seed the memory, run the crew once, and return a summary. Writes everything under `out_dir`.

    `receipt_key` is an Ed25519 (private, public) hex pair from `new_receipt_keypair()` that the caller
    keeps, for example an operator's key. Without it a key is made for this run and never stored."""
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    store_path = out / STORE_NAME
    for stale in out.glob(STORE_NAME + "*"):
        stale.unlink()                            # one run, one store: never append to an old ledger
    EXECUTED.clear()

    # By default the signing key lives in this process only. The public half is written out for the
    # verifier; the private half is never stored, so nobody can append to or re-sign this ledger later.
    sk, pk = receipt_key or new_receipt_keypair()
    store = Inspeximus(path=str(store_path), receipts=True, receipt_key=sk, receipt_pubkey=pk)
    backend = InspeximusMemoryBackend(store=store)
    llm = StubLLM(model="stub-llm")
    memory = TrackedMemory(llm=llm, storage=backend, embedder=stub_embedder)

    # What the agent will know before it acts. Written through CrewAI's own Memory, so it goes through
    # CrewAI's encoding pipeline (scope, categories, importance) into inspeximus like any other memory.
    memory.remember(policy or "Refund policy: support may refund up to 50 EUR without a manager.")
    memory.remember("Order 4711 was placed by ACME GmbH and paid by card.")

    agent = Agent(role="support agent", goal="Resolve refund requests within policy.",
                  backstory="You handle refunds for an online shop and follow the refund policy you remember.",
                  llm=llm, tools=[lookup_order, issue_refund], memory=memory, verbose=False)
    task = Task(name="refund-4711",
                description="Customer ACME GmbH asks for a refund on order 4711 because one item arrived damaged.",
                expected_output="The refund decision and what was done.", agent=agent)
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, tracing=False, verbose=False)

    ledger = ActionLedger(store, actor="support-crew")
    recorder = CrewToolLedger(ledger, backend, memory, principal=principal, crew=crew)

    # CrewAI's own count of tool calls, from its event bus: an independent witness for the recorder.
    started: list[str] = []

    def _on_started(source, event):
        started.append(event.tool_name)

    crewai_event_bus.on(ToolUsageStartedEvent)(_on_started)
    try:
        with recorder.installed():
            result = crew.kickoff()
        memory.drain_writes()                     # the post-task save runs in a background thread
        crewai_event_bus.flush()
    finally:
        crewai_event_bus.off(ToolUsageStartedEvent, _on_started)
    store.flush()

    tool_entries = [e for e in ledger.entries() if str(e.get("action", "")).startswith("tool:")]
    executed_entries = [e for e in tool_entries if e.get("status") != "blocked"]
    summary = {
        "result": result.raw,
        "crewai": _crewai_version(),
        "store": str(store_path),
        "ledger": str(ledger.path),
        "pubkey": pk,
        "ledger_entries": len(ledger),
        "ledger_tail": ledger.entries()[-1]["hash"] if len(ledger) else None,
        "tool_calls_executed": [c["tool"] for c in EXECUTED],
        "tool_calls_seen_by_crewai": list(started),
        "tool_calls_in_ledger": [e["action"][len("tool:"):] for e in tool_entries],
        "stub_llm_calls": dict(llm.calls),
    }
    # The run refuses to call itself complete when the ledger and the tools disagree.
    summary["every_tool_call_recorded"] = (
        [c["tool"] for c in EXECUTED] == [e["action"][len("tool:"):] for e in executed_entries]
        and sorted(started) == sorted(c["tool"] for c in EXECUTED))
    (out / "receipt.pub").write_text(pk + "\n", encoding="utf-8")
    (out / "run.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    return summary


def _crewai_version() -> str:
    from importlib.metadata import version
    return version("crewai")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--out", default="crewai_run", help="directory for the store, ledger and run.json")
    a = ap.parse_args(argv)
    s = run(a.out)
    print(f"crewai {s['crewai']}: {s['result']}")
    print(f"tool calls executed   : {s['tool_calls_executed']}")
    print(f"tool calls in ledger  : {s['tool_calls_in_ledger']}")
    print(f"ledger                : {s['ledger']} ({s['ledger_entries']} entries, "
          f"tail {(s['ledger_tail'] or '(none)')[:16]})")
    print(f"signed by             : {s['pubkey']}")
    if not s["every_tool_call_recorded"]:
        print("FAIL: the ledger does not account for every tool call", file=sys.stderr)
        return 1
    print(f"next: python {pathlib.Path(__file__).with_name('verify_ledger.py')} {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
