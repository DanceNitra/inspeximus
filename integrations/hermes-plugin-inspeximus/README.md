# inspeximus memory provider for Hermes Agent

Memory with keyed corrections you can undo. A correction retires the old value by key, so it stops
coming back as context, and `revert(key)` restores it later from the key alone. The caller never has
to retype what the record used to say.

## Install

```sh
hermes plugins install inspeximus
hermes memory setup
```

Then set `memory.provider: inspeximus` in `config.yaml` and restart Hermes.

A second route exists for anyone who installs into Hermes' virtual environment themselves: the
`inspeximus` package publishes the `hermes_agent.memory_providers` entry point, so Hermes discovers
it without this plugin directory. Both load the same class.

## Tools

| Tool | What it does |
|---|---|
| `inspeximus_remember` | Store a fact, optionally under a stable key. |
| `inspeximus_correct` | Supersede that key. The old value stops being recalled. |
| `inspeximus_revert` | Put the previous value back, by key. |
| `inspeximus_forget` | Erase a subject and leave a receipt saying so. |
| `inspeximus_forget_me` | Erase what the person speaking in this turn wrote, in a shared session. |

## Recall

Hermes calls `prefetch(query)` before each turn. This provider answers it with retrieval minus what a
later correction retired: superseded and quarantined records stay hidden, so a value corrected three
sessions ago cannot return as context.

## What it talks to

The provider makes no network calls of its own and carries no telemetry: the store is a local file,
and `remember`, `correct`, `revert`, `forget` and recall all read and write that file.

Semantic recall is optional and off unless an embedder is configured. When one is, inspeximus posts
the text being stored or queried to the embedding URL the operator sets, which is a loopback Ollama
by default and whatever host they name otherwise. With no embedder configured, recall is lexical and
nothing leaves the machine.

## Links

- Source: https://github.com/DanceNitra/inspeximus
- Integration notes: https://github.com/DanceNitra/inspeximus/blob/main/docs/INTEGRATIONS.md
- Licence: MIT
