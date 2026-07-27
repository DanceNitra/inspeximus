"""One way to open a store from a SURFACE — the CLI, the MCP server, the editor hook, the adapters.

The library's own default for `echo_guard` is OFF, and that is deliberate: a direct API user gets exactly
what they construct. A SURFACE is different. The CLI and `inspeximus-mcp` are documented as sharing one
store, so they both turn the guard on from `INSPEXIMUS_ECHO_GUARD`, and cli._store carries a comment
explaining why they had to agree.

The nine framework adapters never got that memo. Each builds `Inspeximus(path=...)` directly, inheriting
the library default, and the consequence is not cosmetic — measured on one store file:

    1. CLI corrects the payout wallet 0xAAA -> 0xBBB      store serves 0xBBB
    2. an adapter restates the OLD value                  store serves 0xAAA   <- the correction is undone
    3. CLI corrects it again                              store serves 0xAAA   <- and now it is STUCK

Step 3 is the part that makes it serious. Once the retired value is active again, the honest re-correction
looks like an echo of the value the guard just retired, so the guard refuses it. The store cannot be put
right through the same surface that broke it, and "correct a fact once and it stays corrected" — the first
line of the README — is false through the ordinary integration path.

The receipts rule has the same shape: a store that already has a `.receipts.json` sidecar keeps receipts on,
so a surface write cannot silently punch a hole in the evidence chain. That rule also lived in cli._store
alone, while `mcp_server` read the env var only and `claude_code` passed nothing.

Both rules live here now, and every surface calls this. A default that has to be re-declared at each entry
point is a default that will be missed at one of them; it already was, at ten.
"""
from __future__ import annotations

import os


def echo_guard_default() -> bool:
    """Surfaces turn the echo guard ON unless `INSPEXIMUS_ECHO_GUARD=0`."""
    return os.environ.get("INSPEXIMUS_ECHO_GUARD", "1") != "0"


def resolve_path(path=None) -> str:
    """`--path`, else `$INSPEXIMUS_PATH`, else the documented default filename."""
    return path or os.environ.get("INSPEXIMUS_PATH") or "inspeximus_memory.json"


def open_store(path=None, *, receipts: bool = False, persist_vectors: bool = False, embed=None,
               resolve: bool = True, **kwargs):
    """Open a store the way a SURFACE should: shared echo-guard posture, receipts kept if already on.

    `resolve=False` keeps an explicit `path=None` (an in-memory store) instead of falling back to the
    default filename — the adapters that mean "no file" need that.
    """
    from inspeximus import Inspeximus

    p = resolve_path(path) if resolve else path
    # A store that ALREADY has a receipt chain keeps it. Detected from the sidecar rather than a flag,
    # because a user who enabled receipts in Python should not have to re-declare them at every call.
    if not receipts and p and os.path.exists(str(p) + ".receipts.json"):
        receipts = True

    store = Inspeximus(path=p, embed=embed, persist_vectors=persist_vectors, receipts=receipts, **kwargs)
    store.echo_guard = echo_guard_default()
    return store
