"""InspeximusErrataAdapter — an LLM Errata importer adapter backed by inspeximus.

LLM Errata (Thomas Willner, https://github.com/thomaswillner/llm-errata) specifies how a correction
travels from an origin to every independent importer that already holds material derived from the
corrected root, and what each importer may honestly attest afterwards.

Written against the published `StoreAdapter` protocol and `INDEPENDENT_IMPLEMENTATION.md` at frozen
commit `ac4468faf73c2cc7949dd29b2a2a151f5bd23116`, canonical G2 surface digest
`7e0d6c88c1ca3a87743ac70ba2a3dfea0b350d112d2d3c59a3c6cbb537568f12`.

REBOUND FROM `a477fe4f`, where the protocol declared six methods and the repair path needed six more.
We implemented far enough to hit each missing one, reported them, and the surface is now complete and
documented: `quarantine_coverage`, `source_artifact`, `repair_inputs` and `snapshot` are declared, and
`retire` has a single keyword-only signature. The `register_into` helper this file used to carry is
GONE, because the coupling it worked around is gone: `RebuildStrategy` now asks the adapter for
`source_artifact()` and `repair_inputs()` instead of requiring an external store to register its graph
in the reference `LineageLedger`. That workaround existing at all was the evidence for the report.

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
    quarantine_coverage    what we may claim BEFORE repair, distinct from the final verdict
    source_artifact(id)    the record IS its lineage node here; ids are content-addressed
    repair_inputs(id)      declared parents that still exist, store-owned, no external ledger
    rebuild(...)           APPEND corrected records; the quarantined ones stay superseded
    snapshot()             content-free per-record digest, bound into the state roots
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
SPEC_COMMIT = "ac4468faf73c2cc7949dd29b2a2a151f5bd23116"
SPEC_G2_DIGEST = "7e0d6c88c1ca3a87743ac70ba2a3dfea0b350d112d2d3c59a3c6cbb537568f12"


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
        #: Propositions a destructive retire failed to remove completely, either because `forget()`
        #: removed nothing or because a copy survives in another record. Non-empty means this adapter
        #: may not claim `verified`.
        self._erasure_residue: list = []

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

    def retire(self, artifact_id: str, *, superseded_at: str | None = None) -> None:
        """Retire an artifact whose ORIGIN was superseded, rather than rebuilding it.

        Keyword-only, matching the single documented signature at `ac4468f`. At `a477fe4` `retire` was
        absent from the protocol and the strategy called it two ways, trying `superseded_at=` and
        falling back on `TypeError`; we reported that and it is now one form.

        Retired is not quarantined: this record is not waiting to be rebuilt, its origin is gone. So the
        rederivation flag is cleared while the record itself is kept.

        `superseded_at` DECIDES WHETHER THE CONTENT SURVIVES, and getting that wrong was a real defect
        we shipped. The protocol supplies it only for a supersession, where the proposition was true
        until an instant and the history is worth keeping. Its ABSENCE means correction or erasure,
        where the reference contract requires the content to be destroyed and IDEA.md is explicit that
        "a receipt must not preserve the secret it claims to erase".

        Until 2.9.1 this method demoted the record and kept its text in both cases. An `erase` erratum
        therefore returned `aggregate: verified` while the erased proposition sat in the store
        verbatim, and on disk -- a success-shaped non-erasure, in the product we sell on memory
        integrity. It was found by our own candidate conformance case once that case was strengthened
        to search the PERSISTED state rather than present-tense recall, which is the only view in
        which concealment and erasure look different.

        So the destructive branch now calls `forget()`, the store's verified-forgetting primitive: it
        hard-deletes the record, scrubs its id from every surviving record's links and supersession
        pointers, drops the cached vectors, and records a content-free tombstone. Demotion was never
        enough for this operation and the capability was there the whole time; the adapter simply
        never reached for it.
        """
        if superseded_at:
            for rec in self._records():
                if rec["id"] == artifact_id:
                    rec["status"] = "superseded"
                    meta = rec.setdefault("meta", {})
                    meta.pop("needs_rederivation", None)
                    meta["retired_by"] = "llm-errata"
                    meta["superseded_at"] = superseded_at
            self._flush()
            return

        doomed = None
        for rec in self._records():
            if rec["id"] == artifact_id:
                doomed = rec.get("text")
                meta = rec.setdefault("meta", {})
                meta.pop("needs_rederivation", None)
                meta["retired_by"] = "llm-errata"
        self._flush()

        result = self.store.forget(ids=artifact_id, basis="llm-errata correction or erasure",
                                   request_id="llm-errata")

        # `forget()` RETURNS HOW MANY IT REMOVED, AND IT CAN BE ZERO. It reports `forgotten: 0`
        # without raising when the id is outside the caller's tenant rows. The first version of this
        # branch discarded that result and no longer set a status, so a no-op erasure left the record
        # ACTIVE -- strictly worse than the demotion it replaced. A fix that can silently do nothing
        # is the same defect this method exists to correct, reintroduced one layer down.
        if not (result or {}).get("forgotten"):
            for rec in self._records():
                if rec["id"] == artifact_id:
                    rec["status"] = "superseded"
            self._flush()
            self._erasure_residue.append(
                {"proposition": doomed, "reason": "forget() removed nothing", "record": artifact_id})
            return

        # What SURVIVED, rather than trusting that removing one record removed the claim. `forget()`
        # documents its completeness on the premise that "consolidation never copies raw text into
        # other records"; that premise does not hold for `remember(derived=True)`, which copies the
        # text verbatim. So a derivative can still hold the erased proposition after a successful
        # forget, and the record that would have noticed is the one just destroyed.
        if doomed:
            survivors = [r["id"] for r in self._records() if doomed in (r.get("text") or "")]
            if survivors:
                self._erasure_residue.append(
                    {"proposition": doomed, "surviving_records": survivors,
                     "reason": "a derived record retains a verbatim copy"})

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
            src = by_id.get(src_id) or {}
            payload = src.get("text")
            if not payload:
                continue
            # ONLY re-assert an input that was itself gated. A surviving input is still active and
            # still recallable, so copying its text here would leave the store asserting the same
            # proposition twice -- measured: "prefers quiet restaurants" and "moderate budget" each
            # appeared twice in the active set after a repair. The preservation check passes either
            # way, which is exactly why this had to be caught by reading the store rather than the
            # receipt: a duplicate is invisible to a probe that only asks whether a term is recallable.
            if src.get("status") in (None, "active"):
                written.append(payload)
                continue
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
        if self._erasure_residue:
            # A DESTRUCTIVE RETIRE THAT LEFT COPIES BEHIND MUST NOT REPORT `verified`.
            #
            # `forget()` removes the record it is given. It does not remove the same proposition
            # where a summariser copied it into a DERIVED record's text, because that record is a
            # different fact with different provenance and destroying it would take collateral with
            # it. Measured: with one derived record holding "is vegetarian; prefers quiet
            # restaurants", an erase erratum removed the root and returned `verified` while the
            # erased proposition sat on disk inside the derivative.
            #
            # This is the conservative half of the fix and it is deliberately the half that ships
            # first. Widening the deletion is a data-loss decision that needs its own review; saying
            # so out loud costs nothing and is required by the spec's own rule that missing or
            # incomplete disposal remains `partial` or `unknown` rather than silently complete.
            return _coverage("partial")
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

    # ---- the rest of the runtime surface, declared at ac4468f ----------------------------------
    def quarantine_coverage(self, root: str):
        """Coverage at the durable checkpoint, BEFORE any repair.

        Deliberately distinct from `coverage(root)`, which reports final dispositions. At `08b95263`
        the checkpoint inferred `verified` from a successful enumeration and never asked the adapter;
        we reported that an adapter answering `partial` had its answer overwritten, and this method is
        the contract that replaced the inference. It answers the only question we can honestly answer
        before repair: could the walk have been complete for this root.
        """
        return _coverage("verified" if self.lineage_complete(root) else "unknown")

    def source_artifact(self, artifact_id: str) -> str:
        """The stable lineage node one artifact represents.

        In this store a record IS the node: ids are content-addressed and never reused, so an artifact
        and its lineage identity coincide. A store that fanned one proposition across several rows
        would return the shared node here instead.
        """
        return artifact_id

    def repair_inputs(self, artifact_id: str) -> tuple:
        """The store's OWN direct inputs for an artifact: its declared parents, still present.

        This is the method that replaced registering our graph into the reference `LineageLedger`. We
        reported that coupling after a repair silently retired every gated artifact and rebuilt none,
        with no exception raised anywhere; the strategy now asks the adapter instead. Parents that no
        longer exist are omitted rather than returned as dangling ids, because the strategy uses this
        set to decide what a rebuild may be composed FROM.
        """
        present = {r["id"] for r in self._records()}
        for rec in self._records():
            if rec["id"] == artifact_id:
                return tuple(p for p in (rec.get("derived_from") or []) if p in present)
        return ()

    def snapshot(self) -> dict:
        """Inspectable state bound into checkpoint and receipt state roots.

        Content-free and deterministic: a short digest over each record's text and status, so the root
        moves when a repair changes what the store serves, and does not move when only bookkeeping
        changes. Ordering is the caller's concern (the controller sorts), but the mapping must be
        stable across processes, so nothing here depends on dict insertion order or object identity.
        """
        import hashlib
        out = {}
        for rec in self._records():
            payload = "%s|%s" % (rec.get("text") or "", rec.get("status") or "active")
            out[rec["id"]] = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
        return out

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
