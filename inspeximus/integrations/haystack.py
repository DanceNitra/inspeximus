"""InspeximusDocumentStore — a Haystack `DocumentStore` backed by inspeximus.

Haystack pipelines read and write through a `DocumentStore` (write_documents / filter_documents /
delete_documents / count_documents). This is a faithful, drop-in replacement for `InMemoryDocumentStore`
that persists to a file instead of a process-lifetime dict, and whose delete removes the value from the
bytes on disk rather than dropping a reference.

    from inspeximus.integrations.haystack import InspeximusDocumentStore
    store = InspeximusDocumentStore(path="docs.json")
    store.write_documents([Document(content="the invoice is due in March")])
    store.filter_documents({"field": "meta.kind", "operator": "==", "value": "invoice"})

WHAT IT MATCHES (checked against InMemoryDocumentStore, operation by operation):
  - write_documents honours DuplicatePolicy: SKIP keeps the old copy, OVERWRITE replaces it, NONE/FAIL
    raise DuplicateDocumentError on a repeated id. The return value is the number of documents written.
  - filter_documents uses Haystack's own `document_matches_filter`, so filter semantics are identical.
  - delete_documents on an unknown id is a no-op, not an error.
  - Documents round-trip unchanged (content, meta, embedding, score, blob).

WHAT IT ADDS (inspeximus-specific, does not change the protocol):
  - Persistence: the store is a file; reopening it returns the same documents.
  - Erasure that leaves nothing behind: delete_documents hard-removes the value from disk, and with
    receipts enabled leaves a signed, content-free tombstone so the deletion is provable.

Subclassing nothing and importing haystack only here keeps `import inspeximus` zero-dependency.
"""
from __future__ import annotations
from typing import Any

from haystack.dataclasses.document import Document
from haystack.document_stores.errors import DuplicateDocumentError, MissingDocumentError
from haystack.document_stores.types import DuplicatePolicy
from haystack.utils.filters import document_matches_filter

from .governance import ComplianceMixin
from .._surface import open_store          # one surface posture; see _surface.py


class InspeximusDocumentStore(ComplianceMixin):
    """A Haystack DocumentStore over a inspeximus store (persistent, erasure leaves the disk clean).

    Mixes in `ComplianceMixin`: the same document store yields the EU AI Act evidence (compliance_report /
    compliance_check / retention / audit_bundle) with no extra wiring. Enable `receipts=True` on the store."""

    def __init__(self, path: str | None = None, store: Any = None):
        if store is None:
            from inspeximus import Inspeximus
            store = open_store(path, resolve=False)
        self.store = store
        self.path = path

    # ── the DocumentStore protocol ──
    def _active(self) -> list[dict]:
        return [r for r in self.store.items
                if r.get("status") == "active" and (r.get("meta") or {}).get("hs_doc")]

    def _by_hs_id(self) -> dict[str, dict]:
        # Later active writes win, so the newest record for an id is the one returned.
        out: dict[str, dict] = {}
        for r in self._active():
            out[(r["meta"]["hs_doc"])["id"]] = r
        return out

    def count_documents(self) -> int:
        return len(self._by_hs_id())

    def write_documents(self, documents: list[Document],
                        policy: DuplicatePolicy = DuplicatePolicy.NONE) -> int:
        if not isinstance(documents, list) or any(not isinstance(d, Document) for d in documents):
            raise ValueError("write_documents expects a list of Documents")
        # NONE means "the store's default", and InMemoryDocumentStore's default is to fail on a duplicate.
        if policy in (DuplicatePolicy.NONE,):
            policy = DuplicatePolicy.FAIL

        existing = self._by_hs_id()
        written = 0
        for doc in documents:
            payload = doc.to_dict(flatten=False)
            hs_id = payload["id"]
            if hs_id in existing:
                if policy == DuplicatePolicy.SKIP:
                    continue
                if policy == DuplicatePolicy.FAIL:
                    raise DuplicateDocumentError(f"ID '{hs_id}' already exists")
                # OVERWRITE: retire the old record so the new one is the active copy for this id.
                self.store.forget(ids=[existing[hs_id]["id"]])
            text = payload.get("content") or ""
            self.store.remember(text if text else f"[document {hs_id}]",
                                # NAMESPACED. The raw document id as a subject collides with a
                                # real one: forget_subject("user_42") for a person also
                                # hard-deleted the corpus document whose id happened to be
                                # "user_42". Adding source= turned under-erasure into
                                # irreversible OVER-erasure until this prefix.
                                source={"doc": "haystack::" + str(hs_id)},
                                meta={"hs_doc": payload})
            written += 1
        if self.store.path:
            self.store._save(force=True)
        return written

    def filter_documents(self, filters: dict[str, Any] | None = None) -> list[Document]:
        docs = [Document.from_dict(r["meta"]["hs_doc"]) for r in self._by_hs_id().values()]
        if not filters:
            return docs
        return [d for d in docs if document_matches_filter(filters=filters, document=d)]

    def delete_documents(self, document_ids: list[str]) -> None:
        by_id = self._by_hs_id()
        record_ids = [by_id[i]["id"] for i in document_ids if i in by_id]
        if record_ids:
            self.store.forget(ids=record_ids)
            if self.store.path:
                self.store._save(force=True)

    def to_dict(self) -> dict[str, Any]:
        from haystack.core.serialization import default_to_dict
        return default_to_dict(self, path=self.path)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InspeximusDocumentStore":
        from haystack.core.serialization import default_from_dict
        return default_from_dict(cls, data)

    # ── erasure that leaves the disk clean (beyond the protocol) ──
    def erase_documents(self, document_ids: list[str], request_id: str | None = None) -> dict:
        """Like delete_documents, but returns the erasure record (signed tombstone when receipts are on),
        so a deletion made for a data-subject request is provable rather than merely done."""
        by_id = self._by_hs_id()
        record_ids = [by_id[i]["id"] for i in document_ids if i in by_id]
        result = self.store.forget(ids=record_ids, request_id=request_id)
        if self.store.path:
            self.store._save(force=True)
        return result
