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

Duck-typed: this module does NOT import CrewAI, so `pip install inspeximus` alone is enough to use it against
an installed CrewAI. `InspeximusStorage` matches the `Storage` protocol structurally; `import inspeximus` stays
zero-dependency. For semantic recall pass an embedder to the store: `InspeximusStorage(embed=my_embed_fn)`; without
one, recall is lexical (zero-dependency fallback).
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional


from .governance import ComplianceMixin
from .._surface import open_store          # one surface posture; see _surface.py


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
            store = open_store(path, embed=embed, receipts=receipts, resolve=False)
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
                            # NAMESPACED: the bare tag defaults to "crewai", which erased a
                            # user's own note sourced with that word.
                            source={"doc": "crewai::" + str(self._tag)})

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


# ──────────────────────────────────────────────────────────────────────────────────────────────────
# CrewAI 1.x: a different protocol, not a broken adapter
# ──────────────────────────────────────────────────────────────────────────────────────────────────


class InspeximusMemoryBackend(ComplianceMixin):
    """`crewai.memory.storage.backend.StorageBackend`, backed by inspeximus.

    WHY A SECOND CLASS. `InspeximusStorage` above implements CrewAI's ORIGINAL storage interface
    (`save(value, metadata)` / `search(query, limit)`). CrewAI replaced that wholesale: the 1.x
    backend takes `MemoryRecord` objects, searches by a caller-supplied embedding vector, and adds
    scopes, categories, record CRUD and async variants -- fourteen members against the old four. The
    old class is not broken, it answers a protocol CrewAI no longer asks for, so it stays for anyone
    pinned to an older release and this one is what 1.x gets.

    THE SEMANTICS ARE COPIED, NOT INVENTED. Scope prefixes, immediate-child listing and the empty
    `ScopeInfo` shape are taken from CrewAI's own `lancedb_storage`, the reference backend they ship,
    because a hand-written expectation turns a correct adapter red -- which is the lesson the
    checkpointer conformance in this repository already paid for.

    WHAT INSPEXIMUS ADDS over a plain vector backend, and it is not retrieval quality:
      - `delete()` and `reset()` really erase. They call `forget()`, which removes the value from the
        file and leaves a signed, content-free tombstone, so the deletion is provable and does not
        read as tampering. The old class had a bug of exactly this shape once: it set a status flag
        in memory and never wrote, so a reload brought every "deleted" record back.
      - the compliance surface is on the same object (`compliance_report`, `audit_bundle`,
        `retention`), so the evidence for a deletion comes from the store that performed it.

    `crewai` is never imported at module scope. The classes it needs are resolved inside the methods
    that return them, so a bare install neither imports it nor pretends it is there.
    """

    #: Records this backend owns carry it, so one store can hold several backends and other writers.
    TAG = "crewai-backend"

    def __init__(self, path: str | None = None, store: Any = None, embed=None,
                 tag: str = TAG, receipts: bool = False):
        if store is None:
            store = open_store(path, embed=embed, receipts=receipts, resolve=False)
        self.store = store
        self._tag = tag

    # ── internals ────────────────────────────────────────────────────────────────────────────────
    def _rows(self):
        """Active records this backend wrote, oldest first."""
        out = [r for r in list(getattr(self.store, "items", []))
               if r.get("status") == "active" and self._tag in (r.get("tags") or [])
               and isinstance((r.get("meta") or {}).get("crew"), dict)]
        out.sort(key=lambda r: (r.get("meta") or {}).get("crew", {}).get("created_at") or "")
        return out

    @staticmethod
    def _norm_scope(scope):
        """`/a/b` from `a/b/`, `/` from `` or `/`. CrewAI's own normalisation."""
        s = str(scope or "/").rstrip("/")
        if not s:
            return "/"
        return s if s.startswith("/") else "/" + s

    @classmethod
    def _in_scope(cls, scope, prefix):
        if prefix is None or str(prefix).strip("/") == "":
            return True
        p = cls._norm_scope(prefix)
        s = cls._norm_scope(scope)
        return s == p or s.startswith(p.rstrip("/") + "/")

    def _to_record(self, row):
        from crewai.memory.storage.backend import MemoryRecord
        c = (row.get("meta") or {}).get("crew") or {}
        from datetime import datetime, timezone

        def _dt(v):
            if isinstance(v, str):
                try:
                    return datetime.fromisoformat(v)
                except ValueError:
                    pass
            return datetime.now(timezone.utc)

        return MemoryRecord(
            id=c.get("id") or row.get("id"),
            content=row.get("text", ""),
            scope=c.get("scope", "/"),
            categories=list(c.get("categories") or []),
            metadata=dict(c.get("metadata") or {}),
            importance=float(c.get("importance", 0.5)),
            created_at=_dt(c.get("created_at")),
            last_accessed=_dt(c.get("last_accessed")),
            embedding=c.get("embedding"),
            source=c.get("source"),
            private=bool(c.get("private", False)),
        )

    def _match(self, row, scope_prefix=None, categories=None, metadata_filter=None,
               record_ids=None, older_than=None):
        c = (row.get("meta") or {}).get("crew") or {}
        if not self._in_scope(c.get("scope", "/"), scope_prefix):
            return False
        if categories and not (set(categories) & set(c.get("categories") or [])):
            return False
        if metadata_filter:
            md = c.get("metadata") or {}
            if any(md.get(k) != v for k, v in metadata_filter.items()):
                return False
        if record_ids and c.get("id") not in set(record_ids):
            return False
        if older_than is not None:
            from datetime import datetime
            try:
                made = datetime.fromisoformat(str(c.get("created_at")))
            except ValueError:
                return False
            # Compare naive to naive and aware to aware; CrewAI passes either.
            ref = older_than
            if (made.tzinfo is None) != (ref.tzinfo is None):
                made = made.replace(tzinfo=None)
                ref = ref.replace(tzinfo=None)
            if made >= ref:
                return False
        return True

    # ── the protocol ─────────────────────────────────────────────────────────────────────────────
    def save(self, records) -> None:
        for rec in records:
            crew = {
                "id": rec.id,
                "scope": self._norm_scope(getattr(rec, "scope", "/")),
                "categories": list(getattr(rec, "categories", []) or []),
                "metadata": dict(getattr(rec, "metadata", {}) or {}),
                "importance": float(getattr(rec, "importance", 0.5)),
                "created_at": getattr(rec, "created_at", None) and rec.created_at.isoformat(),
                "last_accessed": getattr(rec, "last_accessed", None) and rec.last_accessed.isoformat(),
                "embedding": list(rec.embedding) if getattr(rec, "embedding", None) else None,
                "source": getattr(rec, "source", None),
                "private": bool(getattr(rec, "private", False)),
            }
            # source= is what makes these erasable by subject later; without it forget_subject()
            # for a namespace matches nothing.
            self.store.remember(rec.content, tags=[self._tag], meta={"crew": crew},
                                source={"doc": "crewai::" + str(self._tag)})
        self.store.flush()

    def update(self, record) -> None:
        """Replace a record in place, keeping CrewAI's id.

        Erase-then-write rather than mutating the stored row: `items` refuses whole-list writes on
        purpose, and going through forget() keeps the tombstone honest about what was removed.
        """
        gone = [r["id"] for r in self._rows()
                if ((r.get("meta") or {}).get("crew") or {}).get("id") == record.id]
        if gone:
            self.store.forget(ids=gone, basis="crewai_backend_update")
        self.save([record])

    def get_record(self, record_id: str):
        for r in self._rows():
            if ((r.get("meta") or {}).get("crew") or {}).get("id") == record_id:
                return self._to_record(r)
        return None

    def search(self, query_embedding, scope_prefix=None, categories=None,
               metadata_filter=None, limit: int = 10, min_score: float = 0.0):
        """Cosine over the embeddings CrewAI stored, filtered first.

        A record saved without an embedding cannot be scored and is skipped rather than given a
        zero: a zero would sort it beside a genuinely orthogonal match and `min_score=0.0`, the
        protocol default, would then return it as a hit.
        """
        from ..core import _cosine
        hits = []
        for row in self._rows():
            if not self._match(row, scope_prefix, categories, metadata_filter):
                continue
            vec = ((row.get("meta") or {}).get("crew") or {}).get("embedding")
            if not vec:
                continue
            score = float(_cosine(query_embedding, vec))
            if score < min_score:
                continue
            hits.append((self._to_record(row), score))
        hits.sort(key=lambda rs: -rs[1])
        return hits[:int(limit)]

    def delete(self, scope_prefix=None, categories=None, record_ids=None,
               older_than=None, metadata_filter=None) -> int:
        """Erase, and return how many. An unfiltered call deletes everything, as the protocol says."""
        doomed = [r["id"] for r in self._rows()
                  if self._match(r, scope_prefix, categories, metadata_filter, record_ids, older_than)]
        if doomed:
            self.store.forget(ids=doomed, basis="crewai_backend_delete")
            self.store.flush()
        return len(doomed)

    def reset(self, scope_prefix=None) -> None:
        self.delete(scope_prefix=scope_prefix)

    def count(self, scope_prefix=None) -> int:
        return sum(1 for r in self._rows() if self._match(r, scope_prefix))

    def list_records(self, scope_prefix=None, limit: int = 200, offset: int = 0):
        rows = [r for r in self._rows() if self._match(r, scope_prefix)]
        return [self._to_record(r) for r in rows[int(offset):int(offset) + int(limit)]]

    def list_categories(self, scope_prefix=None):
        out = {}
        for r in self._rows():
            if not self._match(r, scope_prefix):
                continue
            for cat in ((r.get("meta") or {}).get("crew") or {}).get("categories") or []:
                out[cat] = out.get(cat, 0) + 1
        return out

    def list_scopes(self, parent: str = "/"):
        """Immediate children of `parent`, the way lancedb_storage does it."""
        par = str(parent or "/").rstrip("/")
        prefix = (par + "/") if par else "/"
        children = set()
        for r in self._rows():
            sc = self._norm_scope(((r.get("meta") or {}).get("crew") or {}).get("scope", "/"))
            if sc.startswith(prefix) and sc != (prefix.rstrip("/") or "/"):
                first = sc[len(prefix):].split("/", 1)[0]
                if first:
                    children.add(prefix + first)
        return sorted(children)

    def get_scope_info(self, scope: str):
        from crewai.memory.storage.backend import ScopeInfo
        from datetime import datetime
        path = self._norm_scope(scope)
        rows = [r for r in self._rows() if self._match(r, path)]
        if not rows:
            return ScopeInfo(path=path, record_count=0, categories=[],
                             oldest_record=None, newest_record=None, child_scopes=[])
        cats, stamps = set(), []
        for r in rows:
            c = (r.get("meta") or {}).get("crew") or {}
            cats.update(c.get("categories") or [])
            try:
                stamps.append(datetime.fromisoformat(str(c.get("created_at"))))
            except ValueError:
                pass
        return ScopeInfo(path=path, record_count=len(rows), categories=sorted(cats),
                         oldest_record=min(stamps) if stamps else None,
                         newest_record=max(stamps) if stamps else None,
                         child_scopes=self.list_scopes(path))

    # ── async variants ───────────────────────────────────────────────────────────────────────────
    # Thin, and deliberately so. The store is a local file with a lock; there is no I/O to overlap,
    # and an executor here would buy nothing but a thread. Declared because the protocol requires
    # them, and honest about being the same call.
    async def asave(self, records) -> None:
        self.save(records)

    async def asearch(self, query_embedding, scope_prefix=None, categories=None,
                      metadata_filter=None, limit: int = 10, min_score: float = 0.0):
        return self.search(query_embedding, scope_prefix, categories, metadata_filter, limit, min_score)

    async def adelete(self, scope_prefix=None, categories=None, record_ids=None,
                      older_than=None, metadata_filter=None) -> int:
        return self.delete(scope_prefix, categories, record_ids, older_than, metadata_filter)
