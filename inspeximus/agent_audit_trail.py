"""Export the action ledger in the IETF draft-sharif-agent-audit-trail-04 format, and verify such a file.

The draft (Sharif, CyberSecAI, 2026-09-15, expires 2027-03-19) defines a JSONL record with twelve
mandatory fields, a hash chain (`prev_hash` = SHA-256 of the RFC 8785 canonical JSON of the previous
record), and registries for action types, outcomes and trust levels. Auditors and tooling that read
that format can read an inspeximus ledger through this module; the ledger itself is unchanged.

WHAT MAPS AND WHAT DOES NOT, stated because a format conversion that silently drops meaning is a
worse artifact than none:

- An action entry becomes one `tool_call` record (`tool:*`), one `decision` record (`llm:*`, `api:*`,
  `call:*` and everything else), an oversight entry a `decision` record with `human_override`, an
  incident entry an `escalation`, an error action an `error`, and a disclosure, rights, retention or
  timestamp entry a `lifecycle` record whose `event` names it. The draft has no memory-state field:
  the memory digest and the recalled ids travel in `action_detail` under `inspeximus`, with the
  ledger's own hash, so a reader can walk back to the signed entry.
- `input_hash` and `output_hash` in the draft are plain SHA-256 of the payload. The ledger's digests
  are salted (a phone number must not be dictionary-attackable from the ledger), so they are exported
  under `action_detail.inspeximus` as `inputs_sha256_salted`, and the draft's fields are left out
  rather than filled with something that is not what the draft says.
- `record_id` is a fresh UUIDv4 per export, as the draft requires; the export is therefore not
  byte-stable across runs. `trust_level` is L1 when the ledger entries are signed with the store's
  key and L0 otherwise; the draft's L2 to L4 need an external authority this library does not have.
- `record_phase` is always `post_execution`: the ledger writes after the action.

    from inspeximus.agent_audit_trail import export_jsonl, verify_jsonl
    export_jsonl(led, "trail.jsonl", agent_id="urn:agent:support.acme.example", agent_version="1.4.0")
    ok, problems = verify_jsonl("trail.jsonl")

The chain rule is the draft's, so `verify_jsonl` rejects a line that was edited, removed or reordered
after export, whichever tool wrote the file.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Iterable

__all__ = ["to_records", "export_jsonl", "verify_jsonl", "canonical_json", "DRAFT"]

DRAFT = "draft-sharif-agent-audit-trail-04"
ACTION_TYPES = ("tool_call", "tool_response", "decision", "delegation", "escalation", "error", "lifecycle")
OUTCOMES = ("success", "failure", "timeout", "denied", "escalated")
TRUST_LEVELS = ("L0", "L1", "L2", "L3", "L4")
PHASES = ("pre_execution", "post_execution", "concurrent")


def canonical_json(obj: Any) -> bytes:
    """RFC 8785 canonical JSON for the values this exporter emits: object keys sorted by UTF-16 code
    units, no whitespace, integers as integers, strings with the shortest escapes. The exporter emits
    no non-integer numbers, which is where JCS and json.dumps differ; a float would be a defect here."""
    def _sort_key(k: str):
        return k.encode("utf-16-be")

    def _walk(v):
        if isinstance(v, dict):
            return {k: _walk(v[k]) for k in sorted(v, key=_sort_key)}
        if isinstance(v, list):
            return [_walk(x) for x in v]
        if isinstance(v, float):
            if v.is_integer():
                return int(v)
            raise ValueError(f"a non-integer number ({v!r}) has no single canonical form here; round it")
        return v
    return json.dumps(_walk(obj), separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _record_hash(rec: dict) -> str:
    return hashlib.sha256(canonical_json({k: v for k, v in rec.items() if k != "signature"})).hexdigest()


def _rfc3339(ts: float | None) -> str:
    ts = float(ts or 0.0)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + ".%03dZ" % int(round((ts - int(ts)) * 1000))


def _outcome(e: dict) -> str:
    kind = e.get("kind", "action")
    if kind == "oversight":
        return {"refuse": "denied", "stop": "denied", "override": "escalated", "review": "success",
                "approve": "success"}.get(e.get("event"), "success")
    if kind == "incident":
        return "escalated"
    return "failure" if e.get("status") == "error" else "success"


def _action_type_and_detail(e: dict) -> tuple[str, dict]:
    kind = e.get("kind", "action")
    action = str(e.get("action") or "")
    name = action.split(":", 1)[1] if ":" in action else action
    if kind == "action":
        if e.get("status") == "error":
            return "error", {"error_code": "action_error", "error_message": str(e.get("error") or "")[:500],
                             "error_category": "internal", "recoverable": True}
        if action.startswith("tool:"):
            return "tool_call", {"tool_name": name, "parameters_hash": e.get("inputs_sha256") or ""}
        return "decision", {"decision_type": action.split(":", 1)[0] if ":" in action else "generate"}
    if kind == "oversight":
        return "decision", {"decision_type": e.get("event") or "review"}
    if kind == "incident":
        return "escalation", {"escalation_reason": "policy_requires_human",
                              "escalation_target": e.get("reported_to") or e.get("actor") or "operator",
                              "urgency": {"serious": "high", "widespread": "critical", "death": "critical"}.get(e.get("severity"), "medium")}
    return "lifecycle", {"event": "configuration_change", "trigger": "policy",
                         "new_state": f"{kind}:{e.get('event') or e.get('disclosure_kind') or name}"}


def to_records(entries: Iterable[dict], agent_id: str, agent_version: str, session_id: str | None = None,
               trust_level: str | None = None) -> list[dict]:
    """The ledger entries as draft records, chained per the draft's rule. `session_id` defaults to a
    fresh UUIDv4 for the export; an entry's own `session` field is carried in action_detail."""
    entries = list(entries)
    if not agent_id or not agent_version:
        raise ValueError("agent_id (a URI) and agent_version (semver) are required by the draft")
    signed = any("sig" in e for e in entries)
    level = trust_level or ("L1" if signed else "L0")
    if level not in TRUST_LEVELS:
        raise ValueError(f"trust_level must be one of {TRUST_LEVELS}")
    session = session_id or str(uuid.uuid4())
    out: list[dict] = []
    prev: dict | None = None
    for e in entries:
        action_type, detail = _action_type_and_detail(e)
        ms = e.get("memory_state") or {}
        detail["inspeximus"] = {
            "seq": e.get("seq"), "hash": e.get("hash"), "kind": e.get("kind", "action"), "action": e.get("action"),
            "memory_digest": ms.get("digest"), "recalled_ids": list(ms.get("recalled") or []),
            "inputs_sha256_salted": e.get("inputs_sha256"), "output_sha256_salted": e.get("output_sha256"),
            "session": e.get("session"), "principal": e.get("principal"), "actor": e.get("actor"),
        }
        rec: dict = {
            "record_id": str(uuid.uuid4()),
            "timestamp": _rfc3339(e.get("ts")),
            "agent_id": agent_id,
            "agent_version": agent_version,
            "session_id": session,
            "action_type": action_type,
            "action_detail": detail,
            "outcome": _outcome(e),
            "trust_level": level,
            "parent_record_id": prev["record_id"] if prev else None,
            "prev_hash": _record_hash(prev) if prev else None,
            "record_phase": "post_execution",
        }
        if e.get("model"):
            rec["model_id"] = e["model"]
        if e.get("kind") == "oversight":
            rec["human_override"] = {"operator_id": e.get("actor"), "reason": e.get("reason") or "",
                                     "original_action": {"refers_to_seq": (e.get("refers_to") or {}).get("seq")}}
        if isinstance(e.get("started"), (int, float)) and isinstance(e.get("ts"), (int, float)) and e["ts"] >= e["started"]:
            rec["latency_ms"] = int(round((e["ts"] - e["started"]) * 1000))
        out.append(rec)
        prev = rec
    return out


def export_jsonl(ledger, path, agent_id: str, agent_version: str, session_id: str | None = None,
                 trust_level: str | None = None) -> dict:
    """Write the ledger as a JSONL trail, archives included (a rotated ledger is one chain). Returns
    {path, records, draft, session_id, tail_hash}."""
    entries = ledger.all_entries() if hasattr(ledger, "all_entries") else ledger.entries()   # archives too
    recs = to_records(entries, agent_id, agent_version, session_id, trust_level)
    with open(path, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {"path": str(path), "records": len(recs), "draft": DRAFT,
            "session_id": recs[0]["session_id"] if recs else session_id,
            "tail_hash": _record_hash(recs[-1]) if recs else None}


def verify_jsonl(path) -> tuple[bool, list[str]]:
    """Check a trail file against the draft: mandatory fields, registry values, one session, the
    parent_record_id link and the prev_hash chain per RFC 8785. Whoever wrote the file."""
    problems: list[str] = []
    prev: dict | None = None
    seen: set = set()
    session = None
    n = 0
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            n += 1
            try:
                r = json.loads(line)
            except ValueError as ex:
                problems.append(f"line {lineno}: not JSON ({ex})")
                prev = None
                continue
            for k in ("record_id", "timestamp", "agent_id", "agent_version", "session_id", "action_type",
                      "action_detail", "outcome", "trust_level", "parent_record_id", "prev_hash", "record_phase"):
                if k not in r:
                    problems.append(f"line {lineno}: missing mandatory field {k}")
            if r.get("action_type") not in ACTION_TYPES:
                problems.append(f"line {lineno}: action_type {r.get('action_type')!r} is not registered")
            if r.get("outcome") not in OUTCOMES:
                problems.append(f"line {lineno}: outcome {r.get('outcome')!r} is not registered")
            if r.get("trust_level") not in TRUST_LEVELS:
                problems.append(f"line {lineno}: trust_level {r.get('trust_level')!r} is not registered")
            if r.get("record_phase") not in PHASES:
                problems.append(f"line {lineno}: record_phase {r.get('record_phase')!r} is not registered")
            rid = r.get("record_id")
            if rid in seen:
                problems.append(f"line {lineno}: duplicate record_id {rid}")
            seen.add(rid)
            if session is None:
                session = r.get("session_id")
            elif r.get("session_id") != session:
                problems.append(f"line {lineno}: session_id changes within the file")
            if prev is None:
                if r.get("parent_record_id") is not None or r.get("prev_hash") is not None:
                    problems.append(f"line {lineno}: the first record must have null parent_record_id and prev_hash")
            else:
                if r.get("parent_record_id") != prev.get("record_id"):
                    problems.append(f"line {lineno}: parent_record_id does not name the previous record")
                if r.get("prev_hash") != _record_hash(prev):
                    problems.append(f"line {lineno}: prev_hash does not match the previous record's canonical JSON")
            prev = r
    if n == 0:
        problems.append("the file holds no records")
    return (not problems), problems
