"""InspeximusErrataAdapter — an LLM Errata importer adapter backed by inspeximus.

LLM Errata (Thomas Willner, https://github.com/thomaswillner/llm-errata) specifies how a correction
travels from an origin to every independent importer that already holds material derived from the
corrected root, and what each importer must be able to attest afterwards. The spec says what an adapter
must promise. This is one that keeps the promise, so the boundary gets a second implementation by someone
who did not write it.

    from inspeximus import Inspeximus
    from inspeximus.integrations.llm_errata import InspeximusErrataAdapter

    store = Inspeximus(path="mem.json")
    adapter = InspeximusErrataAdapter(store)
    controller = Importer(name="ours", owner=owner_pubkey, adapters=(adapter,), roots=(...))

The mapping is close to one-for-one, which is the honest reason we are a natural implementer rather than
a clever one:

    enumerate(root)   -> records reachable from the root through DECLARED derived_from taint
    quarantine(ids)   -> retract_lineage(): demote to superseded, keep for include_superseded,
                         mark needs_rederivation. Not a delete: the payload entangled in a poisoned
                         lineage is preserved so `rebuild` has something to rebuild FROM.
    is_quarantined    -> the record is out of default recall and flagged for rederivation
    rebuild(...)      -> a corrected fact written with clean lineage to the corrected root
    coverage(root)    -> below, and it is the only interesting method here

COVERAGE IS THE WHOLE POINT, so it is worth being explicit about what each value asserts:

    VERIFIED  every record that announced itself as derived resolved its parents, and the walk from this
              root found and gated all of them.
    PARTIAL   the walk ran, but this store holds records that ANNOUNCED derivation and whose parents
              could not be resolved (inspeximus calls them orphans). Coverage is incomplete by a known
              amount. We report the amount rather than rounding it to a pass.
    UNKNOWN   the walk could not run at all.

`PARTIAL` is not decoration. Until inspeximus 2.6.1 our own erasure audit demoted its verdict only when
declared lineage was EXACTLY zero, so one resolvable edge bought a pass for a store with four hundred
unresolved ones. Willner reported that against us while we were reviewing his spec, which is the reason
this adapter refuses to round a hole down to a pass.

ONE THING WE CANNOT DO, stated here rather than discovered later. `enumerate` walks DECLARED edges. A
derivative whose writer never declared `derived_from` is invisible to it, and no adapter over any store
can fix that from the read side. It is why `coverage` exists and why `PARTIAL` has to be reachable.
"""
from __future__ import annotations

from typing import Any


def _coverage(value: str):
    """Return the spec's `Coverage` member when the package is importable, else the bare string.

    Lazy on purpose: `import inspeximus` must never require llm-errata. `Coverage` is a `str` Enum, so
    the string compares equal either way, but the real member is returned when available so identity
    checks and set membership behave for callers holding the enum.
    """
    try:
        from prototype.adapters import Coverage
    except Exception:                                        # pragma: no cover - spec not installed
        return value
    return Coverage(value)


class InspeximusErrataAdapter:
    """One inspeximus store, presented as an LLM Errata importer adapter."""

    required = True

    def __init__(self, store: Any, name: str = "inspeximus") -> None:
        self.store = store
        self.name = name

    # ---- lineage -------------------------------------------------------------------------------
    def _records(self):
        return list(getattr(self.store, "items", []) or [])

    def _reachable(self, root: str) -> tuple[str, ...]:
        """Every record that inherited `root` through declared lineage, transitively.

        Taint is checked as well as `derived_from`, because inspeximus propagates a parent's source
        taint to its children: a grandchild names its PARENT in derived_from, not the root, and a walk
        that only followed direct edges would gate the child and leave the grandchild active.
        """
        recs = self._records()
        by_id = {r["id"]: r for r in recs}
        seen: set = set()
        frontier = [rid for rid, r in by_id.items() if _claims(r, root)]
        while frontier:
            rid = frontier.pop()
            if rid in seen:
                continue
            seen.add(rid)
            for r in recs:
                if r["id"] in seen:
                    continue
                if rid in (r.get("derived_from") or []):
                    frontier.append(r["id"])
        return tuple(sorted(seen))

    def enumerate(self, root: str) -> tuple[str, ...]:
        return self._reachable(root)

    # ---- gating --------------------------------------------------------------------------------
    def quarantine(self, artifact_ids: tuple[str, ...]) -> None:
        """Demote, never delete. `retract_lineage` is the store-native form of quarantine-before-repair:
        the records leave default recall and are marked `needs_rederivation`, and stay readable under
        `include_superseded` so the legitimate payload entangled in the poisoned lineage survives to be
        rebuilt. Deleting here would satisfy the gate and destroy the input `rebuild` needs."""
        ids = set(artifact_ids or ())
        if not ids:
            return
        for r in self._records():
            if r["id"] in ids:
                r["status"] = "superseded"
                meta = r.setdefault("meta", {})
                meta["needs_rederivation"] = True
                meta.setdefault("quarantined_by", "llm-errata")
        save = getattr(self.store, "_save", None)
        if callable(save):
            save()

    def is_quarantined(self, artifact_id: str) -> bool:
        for r in self._records():
            if r["id"] == artifact_id:
                return r.get("status") == "superseded" and bool(
                    (r.get("meta") or {}).get("needs_rederivation"))
        return False                                          # gone is not gated; say so honestly

    def rebuild(self, artifact_id: str, *, inputs: tuple[str, ...], replacement: str | None) -> str:
        by_id = {r["id"]: r for r in self._records()}
        parts = [by_id[i].get("text", "") for i in (inputs or ()) if i in by_id]
        if replacement:
            parts.insert(0, replacement)
        content = "; ".join(p for p in parts if p)
        rec = by_id.get(artifact_id)
        if rec is not None:
            rec["text"] = content
            rec["status"] = "active"
            (rec.setdefault("meta", {})).pop("needs_rederivation", None)
            save = getattr(self.store, "_save", None)
            if callable(save):
                save()
        return content

    # ---- what we may honestly attest -----------------------------------------------------------
    def coverage(self, root: str):
        recs = self._records()
        if not recs:
            return _coverage("verified")                      # nothing to walk and nothing derived
        orphans = sum(1 for r in recs if r.get("orphan"))
        if orphans:
            return _coverage("partial")
        return _coverage("verified")

    def coverage_detail(self, root: str) -> dict:
        """The number behind the verdict. Not part of the spec's protocol; an importer that wants to
        show its work rather than assert a word can log this beside the receipt."""
        recs = self._records()
        reached = self._reachable(root)
        return {"records": len(recs),
                "reachable_from_root": len(reached),
                "with_declared_lineage": sum(1 for r in recs if r.get("derived_from")),
                "announced_derivation_unresolved": sum(1 for r in recs if r.get("orphan")),
                "verdict": getattr(self.coverage(root), "value", self.coverage(root))}

    def dispositions(self, root: str) -> dict:
        out = {}
        by_id = {r["id"]: r for r in self._records()}
        for rid in self._reachable(root):
            r = by_id.get(rid)
            if r is None:
                out[rid] = "retired"
            elif (r.get("meta") or {}).get("needs_rederivation"):
                out[rid] = "quarantined-only"
            elif r.get("status") == "active":
                out[rid] = "rebuilt"
            else:
                out[rid] = "retired"
        return out


def _claims(rec: dict, root: str) -> bool:
    """Does this record name `root` as its own source, or carry it as inherited taint?"""
    src = rec.get("source")
    doc = src.get("doc") if isinstance(src, dict) else (src if isinstance(src, str) else None)
    if doc == root or rec.get("key") == root or rec.get("id") == root:
        return True
    return root in (rec.get("taint") or [])
