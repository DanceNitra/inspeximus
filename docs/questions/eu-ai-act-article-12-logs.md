# What does the EU AI Act require from an AI agent's logs?

<!--
REVIEW BEFORE PUBLISHING. The text of Article 12 quoted below was written from the drafter's knowledge
of the Official Journal text. It has NOT been compared with EUR-Lex, because the drafting environment's
network policy blocked eur-lex.europa.eu. Compare it word for word with
https://eur-lex.europa.eu/eli/reg/2024/1689/oj (Article 12) and correct it before this page is published.
-->

## Short answer

The logging duty is in Article 12 of Regulation (EU) 2024/1689, and it applies to high-risk AI
systems. Paragraph 1 requires that such a system technically allows automatic recording of events
(logs) over its lifetime. Paragraph 2 sets what that logging must enable. Paragraph 3 sets a minimum
content for the systems referred to in point 1(a) of Annex III.

The text of Article 12 does not name a log format, a storage technology, or an integrity mechanism
such as signing. Whether your agent is a high-risk AI system is decided by other provisions of the
Regulation, not by Article 12. This page does not cover those provisions, and it does not cover any
other article, harmonised standard, guidance or national law.

inspeximus is a component, not a compliance guarantee. Its action ledger records events for an agent
and lets a third party check the record for some kinds of alteration. It does not decide which events
are relevant, it does not make a system compliant, and this page is not legal advice.

## The text of Article 12

Quoted from Regulation (EU) 2024/1689 as published in the
[Official Journal of the European Union](https://eur-lex.europa.eu/eli/reg/2024/1689/oj):

> **Article 12**
>
> **Record-keeping**
>
> 1\. High-risk AI systems shall technically allow for the automatic recording of events (logs) over
> the lifetime of the system.
>
> 2\. In order to ensure a level of traceability of the functioning of a high-risk AI system that is
> appropriate to the intended purpose of the system, logging capabilities shall enable the recording
> of events relevant for:
>
> (a) identifying situations that may result in the high-risk AI system presenting a risk within the
> meaning of Article 79(1) or in a substantial modification;
>
> (b) facilitating the post-market monitoring referred to in Article 72; and
>
> (c) monitoring the operation of high-risk AI systems referred to in Article 26(5).
>
> 3\. For high-risk AI systems referred to in point 1 (a), of Annex III, the logging capabilities shall
> provide, at a minimum:
>
> (a) recording of the period of each use of the system (start date and time and end date and time of
> each use);
>
> (b) the reference database against which input data has been checked by the system;
>
> (c) the input data for which the search has led to a match;
>
> (d) the identification of the natural persons involved in the verification of the results, as
> referred to in Article 14(5).

## Example

The example records three events for an agent: a tool call that succeeds, a tool call that raises,
and a person's review of the failed call. It then checks the ledger, and shows one alteration the
check catches and one it does not.

Needs the `cryptography` package for the signing keys: `pip install "inspeximus[crypto]"`. Run it in
an empty directory. It creates `memory.json`, the ledger `memory.json.actions.json` and their
sidecar files there. With receipts on, the library also writes a small head file under your user
config directory.

```python
import json

from inspeximus import Inspeximus, new_receipt_keypair
from inspeximus.actions import ActionLedger

sk, pk = new_receipt_keypair()
m = Inspeximus("memory.json", receipts=True, receipt_key=sk, receipt_pubkey=pk)
led = ActionLedger(m, actor="triage-agent")       # appends to memory.json.actions.json


@led.wrap("tool:lookup_order")                     # every call becomes one entry, errors included
def lookup_order(order_id):
    if order_id < 0:
        raise ValueError("no such order")
    return {"order": order_id, "state": "shipped"}


m.remember("Orders ship within two working days", key="policy::shipping")
m.recall("when does my order ship", k=1)
lookup_order(4711)
try:
    lookup_order(-1)
except ValueError:
    pass
led.oversight("review", actor="ops-lead", refers_to=1, reason="failed lookup checked by a person")

for e in led.entries():
    print(e["seq"], e["action"], e["status"], e["actor"], e.get("error") or "-",
          "| started<=ts:", e["started"] <= e["ts"],
          "| recalled ids:", len(e["memory_state"]["recalled"]),
          "| refers_to:", (e.get("refers_to") or {}).get("seq"))
print("verifies:", led.verify(expected_pubkey=pk))

# One alteration the verifier catches, and one it does not.
original = led.path.read_text(encoding="utf-8")
entries = json.loads(original)

led.path.write_text(json.dumps(entries[:1] + entries[2:]), encoding="utf-8")
print("middle entry removed, verifies:", ActionLedger(m).verify(expected_pubkey=pk)[0])

led.path.write_text(json.dumps(entries[:-1]), encoding="utf-8")
print("newest entry removed, verifies:", ActionLedger(m).verify(expected_pubkey=pk)[0])

led.path.write_text(original, encoding="utf-8")
```

Output:

```text
0 tool:lookup_order ok triage-agent - | started<=ts: True | recalled ids: 1 | refers_to: None
1 tool:lookup_order error triage-agent ValueError: no such order | started<=ts: True | recalled ids: 0 | refers_to: None
2 oversight:review ok ops-lead - | started<=ts: True | recalled ids: 0 | refers_to: 1
verifies: (True, [])
middle entry removed, verifies: False
newest entry removed, verifies: True
```

## How the example relates to each part of Article 12

This table says what the ledger in the example records next to each part of the text. It does not
say that any part is met. That depends on your system, and on provisions this page does not cover.

| Article 12 | What the ledger in the example records | What it leaves to you |
|---|---|---|
| Art. 12(1), automatic recording of events over the lifetime | `wrap` writes one entry per call, including a call that raises, without the tool's code logging anything. | Route every relevant call through the ledger: a call that bypasses it is not recorded. Keep the ledger and its sidecar files; the library does not decide how long. |
| Art. 12(2), events relevant for points (a), (b) and (c) | Each entry has the time, the actor, the action, the status, any error, and the memory state (for a `wrap` call, taken before the call ran). An oversight entry refers to the entry it reviewed. | Decide which events are relevant for points (a), (b) and (c), and record them. The library does not decide relevance. |
| Art. 12(3)(a), period of each use | Each entry has `started` and `ts`: the start and end of the recorded action. | Define what one use of your system is, and record its start and end. An action is not necessarily a use. |
| Art. 12(3)(b), reference database | Nothing, unless you put it in the inputs or in `meta`. | Record it. |
| Art. 12(3)(c), input data that led to a match | A salted digest of the inputs. The input itself only if the ledger is built with `keep_content=True`. | Decide whether the log must hold the input itself. If it does, the ledger file then holds that data. |
| Art. 12(3)(d), natural persons involved in verifying results | `oversight(..., actor=...)` records the name or role you pass. | Identify the person. The library does not authenticate who the actor is. |

## Limits: what this does not do

- **It is not a compliance assessment.** It does not tell you whether your system is high-risk,
  whether Article 12 applies to you, or whether your logs meet it.
- **Removing the newest entries is not detected.** The last line of the output shows this: the
  ledger with its newest entry removed still verifies. An operator who holds the signing key can also
  rewrite and re-sign the ledger and the store's receipt chain consistently. Detecting either needs
  the ledger's last hash held somewhere the operator cannot rewrite, and compared later.
- **It records only what goes through it.** Tool calls, model calls and decisions that do not pass
  through `wrap`, `action`, `record` or `oversight` are not in the log.
- **Timestamps come from the machine's clock.** The ledger does not show that the clock was right.
  `timestamp_tail(url)` adds a token from an external timestamp authority to the chain.
- **It does not enforce retention.** The ledger is a set of files on your disk. How long they are kept,
  and who can delete them, is up to you.
- **Actor names and reasons are stored as written.** The inputs and outputs are digests by default,
  but `actor`, `reason` and `meta` are kept in clear. With `keep_content=True`, so are the inputs and
  outputs.

## See also

- [docs/API.md](../API.md): "The action ledger", including oversight events, retention and export.
- [docs/AI_ACT.md](../AI_ACT.md): the project's article-by-article evidence mapping.
