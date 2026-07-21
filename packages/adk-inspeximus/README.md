# adk-inspeximus

A [Google ADK](https://github.com/google/adk-python) memory service backed by
[inspeximus](https://github.com/DanceNitra/inspeximus).

ADK's built-in `InMemoryMemoryService` says what it is in its own docstring: *"for prototyping purpose
only. Uses keyword matching instead of semantic search."* The alternatives shipped with ADK are Vertex AI
services. This is the local one: a file on disk, no server, no cloud account.

```bash
pip install adk-inspeximus
```

```python
from google.adk.runners import Runner
from adk_inspeximus import InspeximusMemoryService

runner = Runner(
    agent=agent,
    app_name="my_app",
    session_service=session_service,
    memory_service=InspeximusMemoryService(path="memory.json"),
)
```

Or without writing any Python glue, if you launch through the ADK CLI:

```python
# services.py — loaded by `adk web`
from adk_inspeximus import register
register()                       # enables inspeximus://
```

```bash
adk web --memory_service_uri=inspeximus://memory.json
```

## What it does that the built-in does not

- **Survives a restart.** The built-in service holds everything in a dict; when the process exits, the
  memory is gone.
- **Erases one user on request.** `forget_subject_for(app_name, user_id, request_id=...)` hard-deletes
  that user's memories across every session and leaves a content-free tombstone behind, so the deletion
  is provable and the value is no longer in the bytes on disk. No ADK memory service offers this.
- **Ranked retrieval** instead of counting shared words.

## Parity

ADK ships no conformance suite for `BaseMemoryService`, so the claim "drop-in replacement" is checked
against ADK's own `InMemoryMemoryService` in [`adk_audit.py`](https://github.com/DanceNitra/inspeximus/blob/main/adk_audit.py):
eight scenarios, three repeats each, covering ingestion, user and app isolation, empty and missed
queries, repeated ingestion, incremental event deltas, and retrieval from a store crowded with other
users' memories. Running it with `ADK_FALSIFY=1` breaks the service on purpose and the checks must fail —
without that, the audit would only prove it can print "PASS".

Two of those scenarios failed when the audit was first written, and both are fixed: re-adding a session
stored it a second time (ADK documents that a session *"may be added multiple times during its
lifetime"*), and the incremental `add_events_to_memory` path was not implemented at all.

MIT licensed.
