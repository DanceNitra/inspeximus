"""A minimal OpenAI Agents SDK agent whose memory is inspeximus, with every tool call in the action ledger.

    pip install -r requirements.txt
    python agent.py                 # stub model: no API key, no network
    python verify_ledger.py         # exit 0; change one byte of the ledger and it exits 1

Memory is inspeximus twice over, in one store with Ed25519-signed write receipts:
  * the conversation is an `InspeximusSession`, the SDK's own `Session` slot, so the turns (tool calls
    and their outputs included) persist across runs and restarts;
  * the facts the agent acts on are keyed inspeximus records. `recall_memory` reads them, and
    `remember_fact` corrects one, so the retired value stops coming back from recall.

Three turns: send an invoice (the agent looks up the billing email first), correct the email, send the
next invoice. The stub model reads the address out of the recall_memory output it was given, so what
`send_invoice` receives really is what memory returned, and the ledger shows the first invoice went out
on a value that has since been superseded, and the second on the correction.

`--model gpt-...` runs the same agent on a real model through the SDK's default provider and needs
OPENAI_API_KEY. That path is not exercised by the tests, which use the stub only.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import uuid

from agents import Agent, RunConfig, Runner, function_tool
from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call

from inspeximus import Inspeximus
from inspeximus.actions import ActionLedger
from inspeximus.core import receipt_key_for
from inspeximus.integrations.openai_agents import InspeximusSession

from ledger_hooks import InspeximusLedgerHooks, as_item

SESSION_ID = "billing-desk:ana"
ACTOR = "invoice-agent"
PRINCIPAL = "user:billing-desk"
EMAIL_KEY = "customer:ana:email"
FACT = {"kind": "fact"}                 # recall filters on this, so session turns never answer a lookup

TURNS = (
    "Please send Ana her invoice INV-1001.",
    "Ana's billing email changed to ana@new-mail.example.",
    "Please send Ana her invoice INV-1002.",
)

INSTRUCTIONS = ("You are a billing assistant. Before acting on a customer detail, look it up with "
                "recall_memory. When the user corrects a detail, record it with remember_fact under the "
                "same key. Send invoices only with send_invoice.")


# ------------------------------------------------------------------ memory
def paths(data_dir: str) -> dict:
    store = os.path.join(data_dir, "memory.json")
    return {"store": store, "ledger": store + ".actions.json", "outbox": os.path.join(data_dir, "outbox.jsonl")}


def open_memory(data_dir: str):
    """The store (signed receipts, key kept outside `data_dir`) and the action ledger beside it."""
    os.makedirs(data_dir, exist_ok=True)
    p = paths(data_dir)
    store = Inspeximus(p["store"], receipts=True, receipt_key=receipt_key_for(p["store"]))
    return store, ActionLedger(store, actor=ACTOR)


def seed(store) -> None:
    """Onboarding data, written before the agent runs: a store write with a receipt, not a tool call."""
    if not any(r.get("key") == EMAIL_KEY for r in store.items):
        store.remember("Ana Horvat's billing email is ana@old-mail.example", key=EMAIL_KEY,
                       object="ana@old-mail.example", meta=dict(FACT), mtype="semantic")


# ------------------------------------------------------------------ the agent
def build_agent(store, hooks: InspeximusLedgerHooks, outbox: str, model) -> Agent:
    @function_tool(failure_error_function=hooks.tool_error)
    def recall_memory(query: str) -> str:
        """Look up current facts in memory. Returns a JSON list of {id, key, value, text}."""
        hits = store.recall(query, k=3, where=dict(FACT))
        full = {r["id"]: r for r in store.items}            # recall returns a projection without key/object
        recs = [full.get(h["id"], h) for h in hits]
        return json.dumps([{"id": r["id"], "key": r.get("key"), "value": r.get("object"), "text": r["text"]}
                           for r in recs], ensure_ascii=False)

    @function_tool(failure_error_function=hooks.tool_error)
    def remember_fact(key: str, value: str) -> str:
        """Record a fact, or correct one: a new value under an existing key supersedes the old value."""
        store.remember(f"{key} is {value}", key=key, object=value, meta=dict(FACT), mtype="semantic")
        w = store.last_write or {}
        return json.dumps({"id": w.get("id"), "status": w.get("status"),
                           "superseded": (w.get("previous") or {}).get("id")})

    @function_tool(failure_error_function=hooks.tool_error)
    def send_invoice(to: str, invoice_id: str) -> str:
        """Send an invoice by email. (Here: append it to the outbox file.)"""
        with open(outbox, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"to": to, "invoice_id": invoice_id}) + "\n")
        return f"queued {invoice_id} to {to}"

    return Agent(name="billing-assistant", instructions=INSTRUCTIONS, model=model,
                 tools=[recall_memory, remember_fact, send_invoice])


# ------------------------------------------------------------------ the stub model
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_INVOICE = re.compile(r"INV-\d+", re.I)


def _text(item: dict) -> str:
    c = item.get("content")
    if isinstance(c, str):
        return c
    return " ".join(p.get("text", "") for p in c or [] if isinstance(p, dict))


def _call(name: str, args: dict):
    # The Model contract: one call id per invocation for the life of the conversation, which the
    # session replays into later runs, so a counter that restarts with the process would collide.
    return [function_call(name, args, call_id="call_" + uuid.uuid4().hex[:16])]


def policy(call) -> list:
    """What the stub model answers, read off the input the SDK gave it. It asks for a lookup before
    acting, and takes the address for send_invoice from the recall_memory output, never from a constant."""
    items = [as_item(i) for i in call.input] if isinstance(call.input, list) else [{"role": "user", "content": call.input}]
    last = max(i for i, it in enumerate(items) if it.get("role") == "user")
    ask = _text(items[last])
    turn = items[last + 1:]
    calls = {it.get("name"): it for it in turn if it.get("type") == "function_call"}
    outs = {it.get("call_id"): it.get("output") for it in turn if it.get("type") == "function_call_output"}

    def out(name):
        c = calls.get(name)
        return outs.get(c.get("call_id")) if c else None

    if "changed to" in ask.lower():
        if "remember_fact" not in calls:
            return _call("remember_fact", {"key": EMAIL_KEY, "value": _EMAIL.search(ask).group(0)})
        return [assistant_message("Noted, the billing email is updated.")]
    inv = _INVOICE.search(ask)
    if inv:
        if "recall_memory" not in calls:
            return _call("recall_memory", {"query": "Ana billing email"})
        if "send_invoice" not in calls:
            facts = json.loads(out("recall_memory") or "[]")
            to = next((f["value"] for f in facts if f.get("key") == EMAIL_KEY), None)
            if to is None:
                return [assistant_message("I have no billing email for Ana on file.")]
            return _call("send_invoice", {"to": to, "invoice_id": inv.group(0).upper()})
        return [assistant_message(f"Done: {out('send_invoice')}.")]
    return [assistant_message("I can look up a customer, record a correction, or send an invoice.")]


def stub_model() -> ScriptedModel:
    """The SDK's own deterministic test double, answering from `policy` on every call."""
    model = ScriptedModel()

    def step(call):
        model.enqueue(ModelStep.respond(step))         # one answer always queued: a policy, not a script
        return policy(call)
    model.enqueue(ModelStep.respond(step))
    return model


# ------------------------------------------------------------------ the run
async def run(data_dir: str, model=None, turns=TURNS) -> dict:
    store, ledger = open_memory(data_dir)
    seed(store)
    hooks = InspeximusLedgerHooks(ledger, principal=PRINCIPAL, session=SESSION_ID)
    session = InspeximusSession(SESSION_ID, store=store)
    agent = build_agent(store, hooks, paths(data_dir)["outbox"], model if model is not None else stub_model())
    config = RunConfig(tracing_disabled=not isinstance(model, str))   # a real model keeps SDK tracing
    replies = []
    for text in turns:
        result = None
        try:
            result = await Runner.run(agent, text, session=session, hooks=hooks, run_config=config)
            replies.append(result.final_output)
        finally:
            hooks.flush(result)
    store.flush()
    return {"store": store, "ledger": ledger, "replies": replies}


def describe(store, ledger) -> list[str]:
    """One line per entry: what it did and what it was based on, the recalled facts with their status now."""
    lines = []
    for e in ledger.entries():
        ms = e.get("memory_state") or {}
        knew = ledger.what_it_knew(e["seq"])
        facts = []
        for p in knew.get("recalled_now") or []:
            cur = p.get("current") or {}
            if cur.get("id"):
                facts.append(f"{cur['id']} {cur.get('object')!r} ({cur.get('status')} now)")
        refs = ",".join(str(r["seq"]) for r in (e.get("meta") or {}).get("based_on") or [])
        lines.append(f"  seq {e['seq']}  {e['action']:<20} {e['status']:<7} memory {str(ms.get('digest'))[:12]} "
                     f"receipt {str(ms.get('last_receipt'))[:12]}  outputs seen [{refs}]  recalled "
                     + ("; ".join(facts) if facts else "-"))
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", default="agent_data", help="where the store, ledger and outbox live")
    ap.add_argument("--model", default=None,
                    help="a real model name (needs OPENAI_API_KEY); default: the stub model")
    a = ap.parse_args(argv)
    out = asyncio.run(run(a.dir, model=a.model))
    for text, reply in zip(TURNS, out["replies"]):
        print(f"user:  {text}\nagent: {reply}")
    print(f"\n{len(out['ledger'])} ledger entries in {paths(a.dir)['ledger']}:")
    print("\n".join(describe(out["store"], out["ledger"])))
    print(f"\nnext: python {os.path.join(os.path.dirname(os.path.abspath(__file__)), 'verify_ledger.py')} --dir {a.dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
