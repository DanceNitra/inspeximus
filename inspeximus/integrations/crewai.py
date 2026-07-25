"""CrewAI integration for inspeximus — a custom memory `Storage` backed by inspeximus (current-truth recall).

CrewAI's memory (short-term, long-term, entity, external) delegates persistence to a `Storage` object with
three methods: `save(value, metadata)`, `search(query, limit, score_threshold)` and `reset()`. This module
provides `InspeximusStorage`, a drop-in Storage you hand to CrewAI's `ExternalMemory` (or any custom-storage slot):

    from crewai import Crew, Agent, Task
    from crewai.memory.external.external_memory import ExternalMemory
    from inspeximus.integrations.crewai import InspeximusStorage

    crew = Crew(
        agents=[...], tasks=[...],
        external_memory=ExternalMemory(storage=InspeximusStorage(path="crew_mem.json")),
    )

The honest differentiator vs CrewAI's default RAG storage: `search()` retrieves through inspeximus's `recall()`,
which hides SUPERSEDED values by default — once a fact is corrected via a keyed write, the stale value is
never returned back into the crew's context. For that to bite, writes must carry a supersession key: pass one
in the metadata (`storage.save(value, {"key": "user::tz"})`) or set an OPT-IN `extractor` (text -> (key, obj))
so plain `save()` calls are auto-keyed. Without a key, values are stored append-only like any RAG store.

Duck-typed: this module does NOT import CrewAI, so `pip install agora-inspeximus` alone is enough to use it against
an installed CrewAI. `InspeximusStorage` matches the `Storage` protocol structurally; `import inspeximus` stays
zero-dependency. For semantic recall pass an embedder to the store: `InspeximusStorage(embed=my_embed_fn)`; without
one, recall is lexical (zero-dependency fallback).
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional


from .governance import ComplianceMixin


class InspeximusStorage(ComplianceMixin):
    """A CrewAI `Storage` (duck-typed) backed by inspeximus with supersession-filtered, current-truth search.

    save(value, metadata) -> None            store a memory; metadata['key'] engages supersession
    search(query, limit, score_threshold)    return current-truth hits (superseded values omitted)
    reset() -> None                          soft-delete every stored memory

    Mixes in `ComplianceMixin`: the same storage object yields the EU AI Act evidence —
    `storage.compliance_report()`, `.compliance_check()`, `.audit_bundle()`, `.retention(...)`. Pass
    `receipts=True` for the tamper-evident record-keeping chain those reports evidence.
    """

    def __init__(self, path: str | None = None, store: Any = None,
                 embed=None, extractor=None, tag: str = "crewai", receipts: bool = False):
        if store is None:
            from inspeximus import Inspeximus
            store = Inspeximus(path=path, embed=embed, receipts=receipts)
        self.store = store
        self._tag = tag
        # OPT-IN extractor (text -> (key, object)): auto-keys save()d values so search() returns current-truth.
        if extractor is not None:
            self.store.extractor = extractor

    def save(self, value: Any, metadata: Optional[Dict[str, Any]] = None) -> None:
        metadata = dict(metadata or {})
        text = value if isinstance(value, str) else str(value)
        # A caller-supplied supersession key (or object) turns this into a correctable fact.
        key = metadata.pop("key", None)
        obj = metadata.pop("object", None)
        tags = [self._tag]
        extra_tags = metadata.pop("tags", None)
        if extra_tags:
            tags.extend(extra_tags if isinstance(extra_tags, (list, tuple)) else [extra_tags])
        # source= is what makes this record erasable by subject later. Without it every adapter write fell
        # back to `id:<record id>`, so forget_subject() for a user, a session or a namespace matched nothing.
        self.store.remember(text, key=key, object=obj, tags=tags, meta=metadata or None,
                            source={"doc": self._tag})

    def search(self, query: str, limit: int = 3,
               score_threshold: float = 0.35) -> List[Dict[str, Any]]:
        hits = self.store.recall(query, k=limit) or []
        out: List[Dict[str, Any]] = []
        for h in hits:
            score = h.get("score")
            if score is not None and score < score_threshold:
                continue
            out.append({
                "id": h.get("id"),
                "context": h.get("text", ""),
                "metadata": {"key": h.get("key"), **(h.get("meta") or {})},
                "score": score,
            })
        return out

    def reset(self) -> None:
        """Erase this storage's records — for real.

        It used to set `status="deleted"` in memory and stop: no forget(), no tombstone, no save. `search()`
        looked right because recall filters on status, then a reload returned every record `active` with the
        content still in the file — memory bleeding across CrewAI runs after an explicit reset."""
        ids = [r["id"] for r in list(getattr(self.store, "items", []))
               if self._tag in (r.get("tags") or [])]
        if ids:
            self.store.forget(ids=ids, basis="crewai_storage_reset")
