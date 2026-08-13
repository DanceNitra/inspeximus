"""InspeximusErrataAdapter — an LLM Errata importer adapter backed by inspeximus.

LLM Errata (Thomas Willner, https://github.com/thomaswillner/llm-errata) specifies how a correction
travels from an origin to every independent importer that already holds material derived from the
corrected root, and what each importer may honestly attest afterwards.

Written against the published `StoreAdapter` protocol and `INDEPENDENT_IMPLEMENTATION.md` at frozen
commit `a477fe4f5c86730031b6285d9505778fb8eec060`, G2 surface digest
`a6908d21a3fbfc71c11da85ff72634a3917205a06d0ec6c5e3f949756c04e3a3`.

PROVENANCE, STATED BECAUSE THE CONTRACT TURNS ON IT. The first version of this file, shipped in
inspeximus 2.6.1, was written with `prototype/adapters.py` open in order to get the interface right, and
its `rebuild` reproduced the reference implementation's algorithm line for line, including the arbitrary
`"; "` separator. That is exactly what `INDEPENDENT_IMPLEMENTATION.md` excludes ("Shared reference-adapter
code disqualifies the implementation as independent evidence"), so this file was rewritten from the
protocol signature and the prose requirements alone. The reference package is used to RUN conformance
vectors against, which the contract expects, and not as a source. The earlier version is in git history
under 2.6.1 and is superseded by this one.

The mapping is close to one-for-one, which is why we are a natural implementer rather than a clever one:

    enumerate(root)        records reachable from the root through DECLARED derived_from taint
    lineage_complete(root) below, and it is the method the contract actually cares about
    quarantine(ids)        demote to superseded + needs_rederivation, never delete
    rebuild(...)           APPEND corrected records; the quarantined ones stay superseded
    coverage(root)         verified only when completeness can be established for THIS root

WHY REBUILD APPENDS RATHER THAN REWRITES. inspeximus is append-only: history is evidence, and scrubbing
it is what makes an erasure audit read clean when it should not. So a rebuild does not edit the
quarantined record in place. It writes the correction, and re-asserts each named input's surviving
payload, as NEW active records carrying declared lineage to the corrected root, and leaves the demoted
originals readable under `include_superseded`. A store whose repair destroys the pre-repair state cannot
answer "what did you hold before the correction", which is half of what a receipt is for.

HOW ROOT-SPECIFIC LINEAGE COMPLETENESS IS ESTABLISHED, since the contract requires this to be stated.
Enumeration walks declared `derived_from` edges and inherited source taint. inspeximus marks a record
`orphan` when it ANNOUNCED derivation and resolved no parent. An orphan may or may not descend from this
root and nothing in the store can decide which, so a single orphan anywhere makes the walk incomplete for
EVERY root, and `lineage_complete` returns False. An empty artifact list therefore never counts as
evidence of absence by itself: it counts only when the store also carries no unresolved derivation
claims. This is deliberately conservative in the one direction that matters.

WHAT THIS CANNOT DO, stated here rather than discovered later. A derivative whose writer never declared
`derived_from` and never set `derived=True` is invisible to the walk and does not register as an orphan
either. No adapter over any store can recover that from the read side. `lineage_complete` bounds what we
claim; it does not make the store's writers honest.
"""
from __future__ import annotations

from typing import Any

#: The frozen specification surface this adapter was written against.
SPEC_COMMIT = "a477fe4f5c86730031b6285d9505778fb8eec060"
SPEC_G2_DIGEST = "a6908d21a3fbfc71c11da85ff72634a3917205a06d0ec6c5e3f949756c04e3a3"


def _coverage(value: str):
    """The spec's `Coverage` member when the package is importable, else the bare string.

    Lazy on purpose: `import inspeximus` must never require llm-errata.
    """
    try:
        from prototype.adapters import Coverage
    except Exception:                                        # pragma: no cover - spec not installed
        return value
    return Coverage(value)


class _Hit:
    """The shape `Controller._recalls` consumes: anything with `.content`."""

    __slots__ = ("artifact_id", "content", "score")

    def __init__(self, artifact_id: str, content: str, score: float = 1.0) -> None:
        self.artifact_id = artifact_id
        self.content = content
        self.score = score


class InspeximusErrataAdapter:
    """One inspeximus store, presented as an LLM Errata importer adapter."""

    required = True

    def __init__(self, store: Any, name: str = "inspeximus") -> None:
        self.store = store
        self.name = name

    # ---- reading the store ---------------------------------------------------------------------
    def _records(self) -> list:
        return list(getattr(self.store, "items", []) or [])

    @staticmethod
    def _own_source(rec: dict):
        src = rec.get("source")
        if isinstance(src, dict):
            return src.get("doc")
        return src if isinstance(src, str) else None

    def _names_root(self, rec: dict, root: str) -> bool:
        """Does this record name `root` as its own source, key or id, or carry it as inherited taint?"""
        if root in (self._own_source(rec), rec.get("key"), rec.get("id")):
            return True
        return root in (rec.get("taint") or [])

    # ---- the protocol --------------------------------------------------------------------------
    def enumerate(self, root: str) -> tuple:
        """Every record reachable from `root` through declared lineage, transitively.

        Taint is followed as well as `derived_from` because a grandchild names its PARENT, not the
        root: a walk over direct edges alone would gate the child and leave the grandchild active.
        """
        recs = self._records()
        seen: set = set()
        frontier = [r["id"] for r in recs if self._names_root(r, root)]
        while frontier:
            rid = frontier.pop()
            if rid in seen:
                continue
            seen.add(rid)
            frontier.extend(r["id"] for r in recs
                            if r["id"] not in seen and rid in (r.get("derived_from") or []))
        return tuple(sorted(seen))

    def lineage_complete(self, root: str) -> bool:
        """Whether enumeration is complete under a root-specific authority.

        False whenever the store holds a record that claimed derivation and resolved no parent: such a
        record could descend from this root and the store cannot say it does not. Empty enumeration plus
        zero unresolved claims is the only combination that counts as evidence of absence.
        """
        return not any(r.get("orphan") for r in self._records())

    def quarantine(self, artifact_ids: tuple) -> None:
        """Demote, never delete. The payload entangled in a corrected lineage has to survive to be
        rebuilt FROM, and deleting here would satisfy the gate while destroying the repair's input."""
        ids = set(artifact_ids or ())
        if not ids:
            return
        for rec in self._records():
            if rec["id"] in ids:
                rec["status"] = "superseded"
                meta = rec.setdefault("meta", {})
                meta["needs_rederivation"] = True
                meta.setdefault("quarantined_by", "llm-errata")
        self._flush()

    def is_quarantined(self, artifact_id: str) -> bool:
        for rec in self._records():
            if rec["id"] == artifact_id:
                return rec.get("status") == "superseded" and bool(
                    (rec.get("meta") or {}).get("needs_rederivation"))
        return False                                          # absent is not gated; say so honestly

    def retire(self, artifact_id: str, superseded_at: str | None = None) -> None:
        """Retire an artifact whose ORIGIN was superseded, rather than rebuilding it.

        NOT in the published `StoreAdapter` protocol, and neither is `rebuild`: both are invoked by
        `strategies.py` during repair, so an implementer who writes to the protocol alone gets an
        AttributeError the first time a correction is applied. Reported upstream. The two-argument form
        is here because the reference strategy calls `retire(item, superseded_at=...)` first and falls
        back to `retire(item)` on TypeError, which an implementer also cannot discover from the protocol.

        Retired is not quarantined: this record is not waiting to be rebuilt, its origin is gone. So the
        rederivation flag is cleared while the record itself is kept, because destroying it would erase
        the evidence of what this importer held before the correction.
        """
        for rec in self._records():
            if rec["id"] == artifact_id:
                rec["status"] = "superseded"
                meta = rec.setdefault("meta", {})
                meta.pop("needs_rederivation", None)
                meta["retired_by"] = "llm-errata"
                if superseded_at:
                    meta["superseded_at"] = superseded_at
        self._flush()

    def rebuild(self, artifact_id: str, *, inputs: tuple, replacement: str | None) -> str:
        """Append the corrected assertion and the surviving payload as new active records.

        Returns a newline-joined transcript of what was written, one asserted fact per line, because
        this store keeps facts as separate records rather than as one concatenated blob.
        """
        by_id = {r["id"]: r for r in self._records()}
        origin = by_id.get(artifact_id)

        # The correction is asserted with NO local parent, and the preserved payload is then derived
        # from IT. Deriving the correction from the quarantined record instead is the obvious-looking
        # move and it is wrong: taint flows from a demoted parent to its children, so the repaired fact
        # inherits the demotion and the store comes back with nothing active. Measured on the first
        # version of this method, which returned aggregate=failed with zero active records.
        written: list = []
        anchor: list = []
        if replacement:
            new_id = self._assert(replacement, [])
            anchor = [new_id] if new_id else []
            written.append(replacement)
        for src_id in (inputs or ()):
            payload = (by_id.get(src_id) or {}).get("text")
            if payload:
                self._assert(payload, anchor)
                written.append(payload)

        if origin is not None:
            # The disposition lives on the record rather than in a set on the adapter, so it survives
            # a reload and an auditor reading the store directly sees it without asking us.
            (origin.setdefault("meta", {}))["rederived_by"] = "llm-errata"
        self._flush()
        return "\n".join(written)

    def coverage(self, root: str):
        if not self.lineage_complete(root):
            return _coverage("unknown")
        return _coverage("verified")

    def dispositions(self, root: str) -> dict:
        by_id = {r["id"]: r for r in self._records()}
        out = {}
        for rid in self.enumerate(root):
            rec = by_id.get(rid)
            if rec is None:
                out[rid] = "retired"
            elif (rec.get("meta") or {}).get("rederived_by"):
                out[rid] = "rebuilt"
            elif (rec.get("meta") or {}).get("needs_rederivation"):
                out[rid] = "quarantined-only"
            else:
                out[rid] = "retired" if rec.get("status") != "active" else "active"
        return out

    def recall(self, term: str):
        """Behavioural probe surface for the repair triad. Also absent from the published protocol.

        `Controller._recalls` skips any adapter without it, and a skipped adapter makes the negative
        check pass vacuously while positive and preserve both fail, so the receipt reads FAILED for a
        repair that actually worked. Measured before adding this.

        Only ACTIVE records answer. That is the whole point of the negative check: a superseded record
        must not surface, and inspeximus already hides it from default recall.
        """
        out = []
        needle = (term or "").lower()
        for rec in self._records():
            if rec.get("status") not in (None, "active"):
                continue
            text = rec.get("text") or ""
            if needle and needle in text.lower():
                out.append(_Hit(rec["id"], text))
        return tuple(out)

    # ---- registration, which the protocol does not mention and repair requires ------------------
    def register_into(self, ledger: Any, root: str) -> int:
        """Publish this store's lineage into the importer's `LineageLedger`. Returns records written.

        NOT documented in `INDEPENDENT_IMPLEMENTATION.md`, and repair does not work without it. The
        rebuild strategy decides retire-versus-rebuild from `ledger.artifact(source).inputs` and takes
        the rebuild inputs from `ledger.valid_inputs(...)`, so an adapter whose artifacts were never
        registered has every gated item fall into pass one, gets retired wholesale, and pass two rebuilds
        nothing. Measured before adding this: aggregate=failed with zero records written.

        Registration order matters and the ledger enforces it: `register_derivation` refuses inputs it
        has not seen, so roots go first and descendants follow in dependency order.
        """
        by_id = {r["id"]: r for r in self._records()}
        reachable = self.enumerate(root)
        written = 0
        pending = [rid for rid in reachable]
        for rid in list(pending):
            rec = by_id.get(rid)
            if rec is None or rec.get("derived_from"):
                continue
            ledger.register_import(root, rid, store=self.name, content=rec.get("text", ""))
            written += 1
            pending.remove(rid)
        # Descendants, repeatedly, until no further one can be satisfied. A record whose parents are
        # outside `reachable` can never be registered, and is left out rather than faked in.
        progress = True
        while pending and progress:
            progress = False
            for rid in list(pending):
                rec = by_id.get(rid) or {}
                parents = tuple(p for p in (rec.get("derived_from") or []) if p in reachable)
                if not parents or any(p in pending for p in parents):
                    continue
                ledger.register_derivation(rid, store=self.name, inputs=parents,
                                           content=rec.get("text", ""))
                written += 1
                pending.remove(rid)
                progress = True
        return written

    # ---- reported beside the verdict, not folded into it ---------------------------------------
    def coverage_detail(self, root: str) -> dict:
        """The numbers behind the word. Not part of the protocol; an importer that would rather show
        its work than assert a verdict can log this next to the receipt."""
        recs = self._records()
        cov = self.coverage(root)
        return {"records": len(recs),
                "reachable_from_root": len(self.enumerate(root)),
                "with_declared_lineage": sum(1 for r in recs if r.get("derived_from")),
                "unresolved_derivation_claims": sum(1 for r in recs if r.get("orphan")),
                "lineage_complete": self.lineage_complete(root),
                "verdict": getattr(cov, "value", cov),
                "spec_commit": SPEC_COMMIT}

    # ---- helpers -------------------------------------------------------------------------------
    def _assert(self, text: str, parents: list):
        """Write one corrected fact and return its id, or None if the store cannot take it."""
        remember = getattr(self.store, "remember", None)
        if not callable(remember):
            return None
        try:
            return remember(text, derived=bool(parents), derived_from=list(parents) or None)
        except TypeError:                                     # a store with a narrower signature
            return remember(text)

    def _flush(self) -> None:
        save = getattr(self.store, "_save", None)
        if callable(save):
            save()
