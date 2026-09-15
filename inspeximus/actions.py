"""The action ledger: a signed, hash-chained record of what an agent DID, bound to what it KNEW.

Every audit-trail tool for agents signs actions. This ledger does that too, and adds the one field
none of them carries: `memory_state`, the inspeximus store's state digest and the ids recall
returned before the action. An auditor can then answer two questions from one chain: what the agent
did, and which facts were current in its memory at that moment. A fact corrected between two actions
produces two different digests, so the record shows the agent acted on the old value before the
correction and on the new one after it. Serves EU AI Act Art. 12 and Art. 19 record-keeping and, with
the oversight events that build on it, Art. 14. Evidence, not certification.

Content-free by default: inputs and outputs are stored as SHA-256 digests. Pass `keep_content=True`
to keep them in the clear, or hand the ledger a `redact` callable.

Zero dependencies. Ed25519 signing is optional and uses the store's receipt key when the store has
one, so one key covers the memory chain and the action chain.

    from inspeximus import Inspeximus
    from inspeximus.actions import ActionLedger

    m = Inspeximus("memory.json", receipts=True)
    led = ActionLedger(m, actor="support-agent")

    with led.action("tool:refund", inputs={"order": 4711}) as a:
        a.output(refund(4711))

    ok, problems = led.verify()

Two more event kinds share the chain. `oversight()` records a human decision about an action
(approve, refuse, override, stop, review) with the person or role who made it, for EU AI Act Art. 14
and GDPR Art. 22. `disclosure()` records that a user was told they are interacting with an AI system,
or that generated content was marked, for Art. 50, which applies from 2 August 2026. Every entry has
a `kind`: "action" (the default), "oversight", "disclosure", "rights" (a data-subject request served
through `inspeximus.subject_rights`: an Art. 15 export or an Art. 16 rectification), or "incident"
(`incident()`: a serious-incident record with the Art. 73 reporting clock, and `incident_report()`
for the report skeleton).

The ledger lives beside the store as `<store>.actions.json`. `inspeximus actions verify` checks it
offline; `verify()` also checks that every `memory_state.last_receipt` still resolves in the store's
receipt chain, so a rewritten memory history is caught from the action side as well.
"""
from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Iterable

try:  # optional, only to sign
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey as _SK, Ed25519PublicKey as _PK)
    from cryptography.hazmat.primitives import serialization as _ser
    _HAVE_ED = True
except Exception:  # pragma: no cover - exercised only where cryptography is absent
    _HAVE_ED = False

__all__ = ["ActionLedger", "ActionContext", "verify_file", "GENESIS", "LEDGER_VERSION",
           "OVERSIGHT_EVENTS", "DISCLOSURE_KINDS", "INCIDENT_SEVERITIES", "INCIDENT_DEADLINES_DAYS"]

# Art. 73(2) to (4): a serious incident is reported immediately and no later than 15 days after the
# provider becomes aware of it; 2 days for a widespread infringement or a serious incident concerning
# critical infrastructure; 10 days for the death of a person. Days here are calendar days from `aware_ts`.
INCIDENT_SEVERITIES = ("serious", "widespread", "death", "other")
INCIDENT_DEADLINES_DAYS = {"serious": 15, "widespread": 2, "death": 10, "other": None}

OVERSIGHT_EVENTS = ("approve", "refuse", "override", "stop", "review")
DISCLOSURE_KINDS = ("interaction", "generated_content", "emotion_recognition", "biometric_categorisation",
                    "deepfake", "public_interest_text")

GENESIS = "0" * 64
LEDGER_VERSION = 1


def six_months_before(ts: float) -> float:
    """The unix time exactly six calendar months before `ts` (UTC), the day clamped to the month's
    length. "At least six months" (Art. 19(1), Art. 26(6)) is a calendar period: 181 to 184 days
    depending on the start date, so a fixed 183 claimed the floor a day early for logs that began
    in March through August (red team, 2026-09-16)."""
    import calendar
    import datetime as _dt
    d = _dt.datetime.fromtimestamp(float(ts), tz=_dt.timezone.utc)
    month = d.month - 6
    year = d.year
    if month <= 0:
        month += 12
        year -= 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return d.replace(year=year, month=month, day=day).timestamp()
_SIGNED_FIELDS_EXCLUDED = ("hash", "sig", "pubkey")


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _content_hash(obj: Any, salt: bytes = b"") -> str:
    """Digest of any JSON-serialisable value, prefixed with the ledger's salt. Non-serialisable values
    are digested by their repr.

    WHY A SALT. Inputs to an agent action are often low-entropy personal data: a phone number, an
    order id, an email. A plain SHA-256 of {"phone": "+100"} is a dictionary-attackable fingerprint,
    and a ledger that keeps such fingerprints after the subject was erased is not content-free. A
    32-byte random salt, held in a sidecar the ledger file does not contain, makes the digest useless
    without the salt and still lets the operator prove that a given input matches an entry."""
    try:
        body = _canon(obj)
    except (TypeError, ValueError):
        body = repr(obj).encode("utf-8")
    return _sha256_hex(salt + body)


def _entry_hash(entry: dict) -> str:
    core = {k: v for k, v in entry.items() if k not in _SIGNED_FIELDS_EXCLUDED}
    return _sha256_hex(_canon(core))


def _sign(sk_hex: str, digest_hex: str) -> tuple[str, str]:
    if not _HAVE_ED:
        raise RuntimeError("signing the action ledger needs the `cryptography` package")
    sk = _SK.from_private_bytes(bytes.fromhex(sk_hex))
    pub = sk.public_key().public_bytes(_ser.Encoding.Raw, _ser.PublicFormat.Raw).hex()
    return sk.sign(bytes.fromhex(digest_hex)).hex(), pub


def _verify_sig(pub_hex: str, sig_hex: str, digest_hex: str) -> bool:
    if not _HAVE_ED:
        return False
    try:
        _PK.from_public_bytes(bytes.fromhex(pub_hex)).verify(bytes.fromhex(sig_hex), bytes.fromhex(digest_hex))
        return True
    except Exception:
        return False


class ActionContext:
    """What `ActionLedger.action()` yields. Call `output()` with the result, or `fail()` with the
    error; leaving the block without either records status `ok` with no output digest."""

    def __init__(self, ledger: "ActionLedger", action: str, inputs: Any, meta: dict | None):
        self._ledger = ledger
        self.action = action
        self.inputs = inputs
        self.meta = dict(meta or {})
        self.started = time.time()
        self._output: Any = None
        self._has_output = False
        self._error: str | None = None
        self.entry: dict | None = None

    def output(self, value: Any) -> Any:
        self._output = value
        self._has_output = True
        return value

    def fail(self, error: BaseException | str) -> None:
        self._error = error if isinstance(error, str) else f"{type(error).__name__}: {error}"


class ActionLedger:
    """A hash-chained, optionally signed ledger of agent actions bound to the memory state."""

    def __init__(self, store=None, path: str | os.PathLike | None = None, actor: str | None = None,
                 signing_key: str | None = None, keep_content: bool = False,
                 redact: Callable[[Any], Any] | None = None):
        if store is None and path is None:
            raise ValueError("ActionLedger needs a store or a path")
        self.store = store
        if path is None:
            spath = getattr(store, "path", None)
            if spath is None:
                raise ValueError("the store has no path; pass path= for the ledger file")
            spath = Path(spath)
            path = spath.parent / (spath.name + ".actions.json")
        self.path = Path(path)
        self.actor = actor
        self.keep_content = bool(keep_content)
        self.redact = redact
        self._sk = signing_key if signing_key is not None else getattr(store, "_receipt_sk", None)
        self._entries: list[dict] = []
        self._checkpoint: dict | None = None       # the header a rotated ledger starts with, see archive()
        self._salt: bytes | None = None
        self._seen_recall: int | None = None       # id() of the recall window the last entry consumed
        self._load()

    @property
    def salt_path(self):
        return self.path.with_name(self.path.name + ".salt")

    def _salt_bytes(self) -> bytes:
        """The per-ledger digest salt, minted on first use and kept beside the ledger in its own file.
        Not part of the ledger, so a copy of the ledger alone cannot be dictionary-attacked."""
        if self._salt is None:
            p = self.salt_path
            if p.exists():
                self._salt = bytes.fromhex(p.read_text(encoding="utf-8").strip())
            else:
                self._salt = os.urandom(32)
                p.write_text(self._salt.hex(), encoding="utf-8")
        return self._salt

    # ----------------------------------------------------------------- persistence
    def _stat_sig(self):
        try:
            st = os.stat(self.path)
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _load(self) -> None:
        self._checkpoint = None
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = []
            data = list(data) if isinstance(data, list) else []
            if data and isinstance(data[0], dict) and data[0].get("kind") == "checkpoint":
                self._checkpoint = data[0]
                data = data[1:]
            self._entries = data
        else:
            self._entries = []
        self._sig = self._stat_sig()

    def _refresh_if_changed(self) -> None:
        """Re-read the file when another handle wrote it since this one loaded. A handle that appended
        onto entries it loaded before a peer rotated the ledger used to write the un-rotated chain back
        over the checkpoint, orphaning the archive, and the peer's next write then dropped that entry.
        Measured 2026-09-16 (red team, mutation 13). Same rule as the store's refresh(): the file wins."""
        if self._stat_sig() != getattr(self, "_sig", None):
            self._load()

    def _save(self) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp.%d" % os.getpid())
        body = ([self._checkpoint] if self._checkpoint else []) + self._entries
        tmp.write_text(json.dumps(body, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)
        self._sig = self._stat_sig()

    @property
    def base_seq(self) -> int:
        """The seq of the first entry in the live file: 0, or one past the last archived entry."""
        if self._checkpoint:
            n = self._checkpoint.get("archived_through")
            if not isinstance(n, int) or isinstance(n, bool) or n < -1:
                raise ValueError(f"corrupt checkpoint: archived_through is {n!r}, not a non-negative integer")
            return n + 1
        return 0

    @property
    def archived(self) -> dict | None:
        """The checkpoint header when the ledger has been rotated: which archive holds the earlier entries,
        its hash, and how many entries it covers. None for a ledger that starts at genesis."""
        return dict(self._checkpoint) if self._checkpoint else None

    def _at(self, seq: int) -> dict:
        i = seq - self.base_seq if isinstance(seq, int) else -1
        if i < 0 or i >= len(self._entries):
            if self._checkpoint and isinstance(seq, int) and 0 <= seq <= self._checkpoint["archived_through"]:
                raise ValueError(f"seq {seq} is archived (the live ledger starts at {self.base_seq}); read it from "
                                 f"{self._checkpoint['archive_file']}")
            raise ValueError(f"seq {seq} is not in the ledger ({len(self._entries)} live entries from {self.base_seq})")
        return self._entries[i]

    def oldest_ts(self) -> float | None:
        """The timestamp of the oldest entry the ledger still accounts for, archives included."""
        if self._checkpoint and self._checkpoint.get("archived_first_ts") is not None:
            return self._checkpoint["archived_first_ts"]
        return self._entries[0].get("ts") if self._entries else None

    def reload(self) -> None:
        """Re-read the ledger file. A long-lived handle answers from what it loaded at open; call this
        before `verify()` or `entries()` when another process may have appended."""
        self._load()

    # ----------------------------------------------------------------- memory binding
    def memory_state(self) -> dict:
        """The store's state at this moment: digest, record count, tail of the receipt chain, and the
        ids the last recall returned. Empty fields when the ledger has no store.

        `recalled` is the recall window of THIS store handle, in this process. A recall made through
        another handle (an MCP server in another process, a second adapter) is not visible here, and
        the entry says so: `recall_scope: "this handle"`. A recall older than the previous ledger
        entry is reported as `recall_before_previous_entry: true` rather than re-attributed to this
        action. Both fields exist so a reader cannot mistake an empty or inherited window for a
        measured one."""
        st = self.store
        if st is None:
            return {"digest": None, "records": None, "last_receipt": None, "receipts": 0,
                    "recalled": [], "recalled_at": None, "recall_scope": "no store"}
        digest = None
        try:
            digest = st.state_digest()
        except Exception:
            digest = None
        receipts = list(getattr(st, "_receipts", None) or [])
        recalled = list(getattr(st, "_last_recall", None) or [])
        recalled_at = getattr(st, "_last_recall_at", None) or None
        try:
            n = len(list(st.items))
        except Exception:
            n = None
        # A recall is "new" when the store handle produced a new window since this ledger's last entry.
        # recall() assigns a fresh list each call, so the object's identity is the tell; the timestamp
        # is only kept when the store observes recalls, so it is a fallback, not the rule.
        cur = getattr(st, "_last_recall", None)
        stale = bool(recalled) and (id(cur) == self._seen_recall) if cur is not None else False
        prev_ts = self._entries[-1].get("ts") if self._entries else None
        if not stale and recalled and recalled_at and prev_ts and recalled_at <= prev_ts:
            stale = True
        return {"digest": digest, "records": n,
                "last_receipt": receipts[-1].get("hash") if receipts else None,
                "receipts": len(receipts),
                "recalled": [] if stale else recalled[:64], "recalled_at": recalled_at,
                "recall_scope": "this handle", "recall_before_previous_entry": stale}

    # ----------------------------------------------------------------- recording
    def record(self, action: str, inputs: Any = None, output: Any = None, status: str = "ok",
               error: str | None = None, meta: dict | None = None, started: float | None = None,
               actor: str | None = None, kind: str = "action", extra: dict | None = None,
               memory_state: dict | None = None, model: str | None = None,
               principal: str | None = None) -> dict:
        """Append one entry. Returns it as stored (with hash, and sig when a key is set). `kind` is
        "action" for what the agent did; `oversight()` and `disclosure()` set the other two.

        `model` names the model version behind a model call and `principal` the person or account on
        whose behalf the agent acted. Both are what an ISO/IEC 42001 or SOC 2 reviewer asks for on every
        action (identity-tied attribution, model version per call) and neither is inferred: absent when
        the caller did not say."""
        if not isinstance(action, str) or not action:
            raise ValueError("action must be a non-empty string, for example 'tool:search'")
        if kind not in ("action", "oversight", "disclosure", "rights", "incident", "retention"):
            raise ValueError("kind must be action, oversight, disclosure, rights, incident or retention")
        self._refresh_if_changed()
        now = time.time()
        inp = self.redact(inputs) if (self.redact and inputs is not None) else inputs
        out = self.redact(output) if (self.redact and output is not None) else output
        entry: dict = {
            "v": LEDGER_VERSION,
            "kind": kind,
            "seq": (self._entries[-1]["seq"] + 1) if self._entries else self.base_seq,
            "prev": (self._entries[-1]["hash"] if self._entries
                     else (self._checkpoint["archived_tail_hash"] if self._checkpoint else GENESIS)),
            "ts": now,
            "started": started if started is not None else now,
            "actor": actor if actor is not None else self.actor,
            "action": action,
            "status": status,
            "digest": "sha256(salt || canonical json); salt in <ledger>.salt",
            "inputs_sha256": _content_hash(inp, self._salt_bytes()) if inp is not None else None,
            "output_sha256": _content_hash(out, self._salt_bytes()) if out is not None else None,
            # captured BEFORE the action ran when the caller went through action() or wrap(); a plain
            # record() captures now, which is after whatever the caller did.
            "memory_state": memory_state if memory_state is not None else self.memory_state(),
        }
        if error:
            entry["error"] = str(error)[:2000]
        if model:
            entry["model"] = str(model)[:200]
        if principal:
            entry["principal"] = str(principal)[:200]
        if meta:
            entry["meta"] = meta
        if extra:
            entry.update(extra)
        if self.keep_content:
            entry["inputs"] = inp
            entry["output"] = out
        entry["hash"] = _entry_hash(entry)
        if self._sk:
            entry["sig"], entry["pubkey"] = _sign(self._sk, entry["hash"])
        self._entries.append(entry)
        self._save()
        cur = getattr(self.store, "_last_recall", None) if self.store is not None else None
        self._seen_recall = id(cur) if cur is not None else None
        return entry

    @contextlib.contextmanager
    def action(self, action: str, inputs: Any = None, meta: dict | None = None, actor: str | None = None,
               model: str | None = None, principal: str | None = None):
        """Record an action around a block of code. The block's exception, if any, is recorded as the
        action's error and re-raised. `model` and `principal` are recorded as on record()."""
        ctx = ActionContext(self, action, inputs, meta)
        before = self.memory_state()          # what the agent knew BEFORE it acted, not after
        try:
            yield ctx
        except BaseException as e:  # noqa: BLE001 - recorded, then re-raised
            ctx.fail(e)
            ctx.entry = self.record(action, inputs, None, status="error", error=ctx._error,
                                    meta=ctx.meta or None, started=ctx.started, actor=actor,
                                    memory_state=before, model=model, principal=principal)
            raise
        status = "error" if ctx._error else "ok"
        ctx.entry = self.record(action, inputs, ctx._output if ctx._has_output else None,
                                status=status, error=ctx._error, meta=ctx.meta or None,
                                started=ctx.started, actor=actor, memory_state=before,
                                model=model, principal=principal)

    def wrap(self, name: str | None = None, actor: str | None = None, model: str | None = None,
             principal: str | None = None):
        """Decorator: every call of the function becomes one action; positional and keyword arguments
        are the inputs and the return value is the output."""
        def deco(fn):
            action = name or f"call:{getattr(fn, '__qualname__', getattr(fn, '__name__', 'fn'))}"

            @functools.wraps(fn)
            def inner(*a, **k):
                with self.action(action, inputs={"args": list(a), "kwargs": k}, actor=actor,
                                 model=model, principal=principal) as ctx:
                    return ctx.output(fn(*a, **k))
            return inner
        return deco

    # ----------------------------------------------------------------- oversight and disclosure
    def oversight(self, event: str, actor: str, reason: str | None = None, refers_to: int | str | None = None,
                  decision: Any = None, meta: dict | None = None) -> dict:
        """Record a human decision about the agent's work: approve, refuse, override, stop or review.

        `actor` is the person or role that decided; it is required, because an oversight event with no
        one behind it is what Art. 14 asks to rule out. `refers_to` names the action it concerns, as a
        seq number or an entry hash, and is checked against the ledger so the reference resolves at
        write time. `decision` is what the human substituted (an override) or the review outcome."""
        if event not in OVERSIGHT_EVENTS:
            raise ValueError(f"event must be one of {OVERSIGHT_EVENTS}")
        if not actor:
            raise ValueError("an oversight event needs an actor: the person or role who decided")
        ref = self._resolve_ref(refers_to)
        extra = {"event": event, "reason": reason, "refers_to": ref}
        if decision is not None:
            extra["decision_sha256"] = _content_hash(decision)
            if self.keep_content:
                extra["decision"] = decision
        return self.record(f"oversight:{event}", status="ok", actor=actor, meta=meta, kind="oversight",
                           extra=extra)

    def disclosure(self, session: str, shown: str, channel: str = "ui", kind: str = "interaction",
                   actor: str | None = None, locale: str | None = None, meta: dict | None = None,
                   agent: str | None = None, principal: str | None = None) -> dict:
        """Record an Art. 50 disclosure: what the user was shown, where, and in which session.

        `kind` is "interaction" (the user was told they interact with an AI system, Art. 50(1)),
        "generated_content" (the output was marked as generated, Art. 50(2)), or one of the other
        Art. 50 cases. The text shown is stored as a digest unless `keep_content` is set; the length
        is kept in the clear so an auditor can tell an empty banner from a real one."""
        if kind not in DISCLOSURE_KINDS:
            raise ValueError(f"kind must be one of {DISCLOSURE_KINDS}")
        if not session or not shown:
            raise ValueError("a disclosure needs a session id and the text that was shown")
        extra = {"session": session, "channel": channel, "disclosure_kind": kind,
                 "shown_sha256": _content_hash(shown), "shown_chars": len(shown)}
        if locale:
            extra["locale"] = locale
        # the Commission's Art. 50 guidelines (20 Jul 2026) ask each agent that interacts with a person
        # to disclose at each new interaction and to name its principal; both are recorded when given
        if agent:
            extra["agent"] = str(agent)[:200]
        if principal:
            extra["principal"] = str(principal)[:200]
        if self.keep_content:
            extra["shown"] = shown
        return self.record(f"disclosure:{kind}", status="ok", actor=actor, meta=meta, kind="disclosure",
                           extra=extra)

    def incident(self, title: str, severity: str, actor: str, description: str | None = None,
                 refers_to: list | None = None, aware_ts: float | None = None, subject: str | None = None,
                 corrective_actions: list | None = None, reported_to: str | None = None,
                 reported_ts: float | None = None, meta: dict | None = None) -> dict:
        """Record a serious incident (EU AI Act Art. 73) or another notable event with the reporting clock.

        `severity` is "serious" (15 days), "widespread" (2 days), "death" (10 days) or "other" (no
        statutory clock). `aware_ts` is when the provider became aware; the deadline is computed from it.
        `refers_to` lists the ledger entries (seqs or hashes) that are the evidence; each must resolve.
        `actor` is the person or role who opened the record. Call `incident_report(seq)` for the
        Art. 73 skeleton with the deadline and the linked evidence."""
        if severity not in INCIDENT_SEVERITIES:
            raise ValueError(f"severity must be one of {INCIDENT_SEVERITIES}")
        if not actor or not title:
            raise ValueError("an incident needs a title and an actor: the person or role who opened it")
        refs = [self._resolve_ref(r) for r in (refers_to or [])]
        aware = float(aware_ts) if aware_ts is not None else time.time()
        days = INCIDENT_DEADLINES_DAYS[severity]
        extra = {"title": title, "severity": severity, "description": description, "aware_ts": aware,
                 "report_deadline_ts": (aware + days * 86400) if days else None,
                 "report_deadline_days": days, "evidence": refs, "subject": subject,
                 "corrective_actions": list(corrective_actions or []),
                 "reported_to": reported_to, "reported_ts": reported_ts}
        return self.record(f"incident:{severity}", inputs={"title": title}, status="ok", actor=actor,
                           meta=meta, kind="incident", extra=extra)

    def incident_reported(self, seq: int, actor: str, reported_to: str, reported_ts: float | None = None,
                          note: str | None = None) -> dict:
        """Record that incident `seq` was reported: to whom and when. An incident entry is immutable, so
        the report is a later entry that names it; `incident_report`, `oversight_report` and `archive`
        all treat the incident as closed once this exists."""
        e = self._at(seq)
        if e.get("kind") != "incident":
            raise ValueError(f"entry {seq} is a {e.get('kind', 'action')}, not an incident")
        if not actor or not reported_to:
            raise ValueError("incident_reported needs an actor and reported_to")
        extra = {"title": e.get("title"), "severity": e.get("severity"), "event": "reported",
                 "evidence": [self._resolve_ref(seq)], "reported_to": reported_to,
                 "reported_ts": float(reported_ts) if reported_ts is not None else time.time(),
                 "aware_ts": e.get("aware_ts"), "report_deadline_ts": e.get("report_deadline_ts")}
        if note:
            extra["description"] = str(note)[:2000]
        return self.record("incident:reported", status="ok", actor=actor, kind="incident", extra=extra)

    def _reported_ts(self, e: dict) -> float | None:
        """When incident entry `e` was reported: its own reported_ts, or that of a later entry naming it."""
        if e.get("reported_ts"):
            return e["reported_ts"]
        for u in self._entries:
            if u.get("seq", -1) <= e.get("seq", -1) or not u.get("reported_ts"):
                continue
            refs = [u.get("refers_to")] if isinstance(u.get("refers_to"), dict) else list(u.get("evidence") or [])
            if any((r or {}).get("seq") == e.get("seq") for r in refs):
                return u["reported_ts"]
        return None

    def incident_report(self, seq: int, now: float | None = None) -> dict:
        """The Art. 73 report skeleton for incident `seq`: what the ledger can fill (dates, deadline,
        evidence entries with their memory state, oversight events on those actions, later updates that
        refer to this incident) and what the provider must add (system identification, affected persons,
        the assessment). Read-only."""
        e = self._at(seq)
        if e.get("kind") != "incident":
            raise ValueError(f"entry {seq} is a {e.get('kind', 'action')}, not an incident")
        now = time.time() if now is None else now
        deadline = e.get("report_deadline_ts")
        evidence = []
        for ref in e.get("evidence") or []:
            x = self._at(ref["seq"])
            row = {"seq": x["seq"], "kind": x.get("kind", "action"), "action": x.get("action"),
                   "status": x.get("status"), "ts": x.get("ts"), "actor": x.get("actor"),
                   "memory_state": x.get("memory_state")}
            row["oversight"] = [{"seq": o["seq"], "event": o["event"], "actor": o.get("actor"), "reason": o.get("reason")}
                                for o in self._entries if o.get("kind") == "oversight"
                                and (o.get("refers_to") or {}).get("seq") == x["seq"]]
            evidence.append(row)
        updates = [{"seq": u["seq"], "kind": u.get("kind"), "action": u.get("action"), "ts": u.get("ts"),
                    "actor": u.get("actor"), "event": u.get("event")}
                   for u in self._entries if u["seq"] > seq
                   if any((r or {}).get("seq") == seq for r in ([u.get("refers_to")] if isinstance(u.get("refers_to"), dict)
                                                                 else (u.get("evidence") or [])))]
        return {
            "kind": "inspeximus.incident_report/1",
            "incident": {k: e.get(k) for k in ("seq", "hash", "ts", "actor", "title", "severity", "description",
                                                "aware_ts", "subject", "corrective_actions", "reported_to",
                                                "reported_ts")},
            "clock": {"deadline_ts": deadline, "deadline_days": e.get("report_deadline_days"),
                      "days_left": (round((deadline - now) / 86400, 2) if deadline else None),
                      "overdue": (bool(deadline and now > deadline and not self._reported_ts(e))),
                      "reported": bool(self._reported_ts(e)),
                      "reported_ts": self._reported_ts(e)},
            "evidence": evidence,
            "updates": updates,
            "operator_must_add": ["identification of the AI system and the provider", "the persons or groups affected",
                                  "the assessment of the incident and the causal link to the system",
                                  "the market surveillance authority notified"],
            "basis": "Regulation (EU) 2024/1689 Art. 73; the clock runs from the moment of awareness. The ledger "
                     "supplies dates and evidence; it does not assess the incident.",
        }

    def _resolve_ref(self, refers_to):
        if refers_to is None:
            return None
        if isinstance(refers_to, int):
            return {"seq": refers_to, "hash": self._at(refers_to)["hash"]}
        for e in self._entries:
            if e.get("hash") == refers_to:
                return {"seq": e["seq"], "hash": e["hash"]}
        raise ValueError("refers_to hash is not in the ledger")

    def oversight_report(self) -> dict:
        """Counts an auditor asks for: actions, oversight events by type and actor, actions that
        received a review or override, error actions with no oversight after them, disclosures by
        session. Read-only."""
        actions = [e for e in self._entries if e.get("kind", "action") == "action"]
        overs = [e for e in self._entries if e.get("kind") == "oversight"]
        discs = [e for e in self._entries if e.get("kind") == "disclosure"]
        rights = [e for e in self._entries if e.get("kind") == "rights"]
        incidents = [e for e in self._entries if e.get("kind") == "incident"]
        now = time.time()
        by_event: dict = {}
        by_actor: dict = {}
        reviewed = set()
        for o in overs:
            by_event[o["event"]] = by_event.get(o["event"], 0) + 1
            by_actor[o.get("actor") or "?"] = by_actor.get(o.get("actor") or "?", 0) + 1
            if o.get("refers_to"):
                reviewed.add(o["refers_to"]["seq"])
        errors_unreviewed = [a["seq"] for a in actions if a.get("status") == "error" and a["seq"] not in reviewed]
        sessions: dict = {}
        for d in discs:
            sessions.setdefault(d["session"], []).append(d["disclosure_kind"])
        return {
            "actions": len(actions),
            "oversight_events": len(overs),
            "by_event": by_event,
            "by_actor": by_actor,
            "actions_with_oversight": len(reviewed),
            "error_actions_without_oversight": errors_unreviewed,
            "stops": by_event.get("stop", 0),
            "disclosures": len(discs),
            "sessions_disclosed": {k: sorted(set(v)) for k, v in sessions.items()},
            "rights_requests": {"export": sum(1 for r in rights if r.get("event") == "export"),
                                "rectify": sum(1 for r in rights if r.get("event") == "rectify")},
            "incidents": len([i for i in incidents if i.get("event") != "reported"]),
            "incidents_overdue": [i["seq"] for i in incidents if i.get("event") != "reported"
                                  and i.get("report_deadline_ts") and now > i["report_deadline_ts"]
                                  and not self._reported_ts(i)],
        }

    # ----------------------------------------------------------------- reading
    def entries(self) -> list[dict]:
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def what_it_knew(self, seq: int) -> dict:
        """The memory state recorded at action `seq`, plus, when the store is present, the current
        provenance of each id that recall had returned. Answers "what did the agent know when it did
        this" from the chain rather than from memory."""
        e = self._at(seq)
        out = {"seq": seq, "action": e.get("action"), "ts": e.get("ts"),
               "memory_state": dict(e.get("memory_state") or {}), "recalled_now": []}
        st = self.store
        if st is not None:
            for rid in (e.get("memory_state") or {}).get("recalled", []) or []:
                try:
                    out["recalled_now"].append(st.provenance(id=rid))
                except Exception as ex:
                    out["recalled_now"].append({"id": rid, "error": f"{type(ex).__name__}: {ex}"})
        return out

    # ----------------------------------------------------------------- retention
    def attest_retention(self, policy_days: float, actor: str, now: float | None = None,
                         note: str | None = None) -> dict:
        """Append a signed statement of what the ledger holds: the oldest entry it accounts for
        (archives included), the live and archived counts, and the retention policy in force. Art. 19
        and Art. 26(6) ask for logs kept at least six months; this is the periodic record that they
        were, made from the ledger rather than asserted."""
        if not actor:
            raise ValueError("attest_retention needs an actor")
        now = time.time() if now is None else now
        oldest = self.oldest_ts()
        if oldest is not None and (not isinstance(oldest, (int, float)) or isinstance(oldest, bool)):
            oldest = None
        cp = self._checkpoint or {}
        extra = {
            "event": "attest",
            "policy_days": float(policy_days),
            "oldest_ts": oldest,
            "oldest_age_days": round((now - oldest) / 86400.0, 3) if oldest is not None else None,
            "live_entries": len(self._entries),
            "archived_entries": int(cp.get("archived_through", -1)) + 1 if cp else 0,
            "archives": list(cp.get("archive_chain", [])) if cp else [],
            "floor": "six calendar months before the attestation time",
            "floor_observed": bool(oldest is not None and oldest <= six_months_before(now)),
        }
        if oldest is None and (self._entries or cp):
            extra["note_oldest"] = "the oldest entry carries no numeric ts; its age cannot be stated"
        if note:
            extra["note"] = str(note)[:2000]
        return self.record("retention:attest", status="ok", actor=actor, kind="retention", extra=extra)

    def archive(self, keep_days: float | None = None, before_ts: float | None = None, actor: str | None = None,
                now: float | None = None) -> dict:
        """Move the entries older than the cutoff into an archive file beside the ledger and start the live
        file with a signed checkpoint that names the archive, its SHA-256, the archived range and the archived
        tail hash. Nothing is deleted and the chain is unbroken: the next entry's prev is the archived tail,
        `verify()` follows the checkpoint into the archive, and `verify_file` on the live file alone reports
        the archived range as not verified rather than passing over it.

        The cut is moved earlier when a kept entry refers to an entry it would archive (an oversight event
        on an old action, an incident's evidence; a `meta` field is not a reference), and it never archives
        an incident that has not been reported, so the Art. 73 clock stays in the live file. Returns what
        was archived; with nothing old enough, returns archived=0 and writes nothing.

        LIMITS, the same as verify()'s. The checkpoint is signed only when this handle holds the signing
        key, and a signed ledger refuses to rotate without it. The archive file is not signed as a whole:
        its entries are, and the checkpoint carries its SHA-256. Whoever holds the key can rewrite the
        archive, the checkpoint and the live tail consistently, and a live tail cut after the checkpoint
        reads as complete; both are the witness's job, not this file's."""
        now = time.time() if now is None else now
        if before_ts is None and keep_days is None:
            raise ValueError("archive needs keep_days or before_ts")
        self._refresh_if_changed()
        cutoff = float(before_ts) if before_ts is not None else now - float(keep_days) * 86400.0
        if self._sk is None and (any("sig" in e for e in self._entries) or (self._checkpoint and "sig" in self._checkpoint)):
            raise ValueError("this ledger is signed; open it with its signing key to rotate it, or the checkpoint "
                             "would be the one unsigned link in a signed chain")
        for e in self._entries:
            t = e.get("ts")
            if not isinstance(t, (int, float)) or isinstance(t, bool):
                raise ValueError(f"seq {e.get('seq')} carries no numeric ts; refusing to rotate a ledger whose "
                                 f"entries cannot be placed in time")
        n = 0
        while n < len(self._entries) and self._entries[n]["ts"] < cutoff:
            n += 1
        base = self.base_seq
        # an open incident stays live
        for i in range(n):
            e = self._entries[i]
            if e.get("kind") == "incident" and e.get("event") != "reported" and not self._reported_ts(e):
                n = i
                break
        # a kept entry may not point into the archive
        changed = True
        while changed and n > 0:
            changed = False
            for e in self._entries[n:]:
                refs = [e.get("refers_to")] if isinstance(e.get("refers_to"), dict) else list(e.get("evidence") or [])
                for r in refs:
                    j = (r or {}).get("seq")
                    if isinstance(j, int) and base <= j < base + n:
                        n = j - base
                        changed = True
        if n <= 0:
            return {"archived": 0, "cutoff_ts": cutoff, "live_entries": len(self._entries), "archive_file": None}
        old, keep = self._entries[:n], self._entries[n:]
        prior = list((self._checkpoint or {}).get("archive_chain", []))
        index = len(prior) + 1
        name = f"{self.path.name}.archive.{index:04d}.json"
        arc = self.path.with_name(name)
        body = ([self._checkpoint] if self._checkpoint else []) + old
        raw = json.dumps(body, indent=1, ensure_ascii=False).encode("utf-8")
        if arc.exists():
            # a rotation that wrote its archive and then failed to save the live file leaves this file
            # behind; the same bytes mean the same rotation, so it is reused rather than refused forever
            if arc.read_bytes() != raw:
                raise FileExistsError(f"{arc} already exists with different content; refusing to overwrite an archive")
        else:
            tmp = arc.with_name(arc.name + ".tmp.%d" % os.getpid())
            tmp.write_bytes(raw)
            os.replace(tmp, arc)
        cp: dict = {
            "v": LEDGER_VERSION,
            "kind": "checkpoint",
            "event": "archive",
            "ts": now,
            "actor": actor if actor is not None else self.actor,
            "cutoff_ts": cutoff,
            "archive_file": name,
            "archive_sha256": _sha256_hex(raw),
            "archive_index": index,
            "archive_chain": prior + [name],
            "archived_from": old[0]["seq"],
            "archived_through": old[-1]["seq"],
            "archived_count": (int(self._checkpoint["archived_through"]) + 1 if self._checkpoint else 0) + n,
            "archived_first_ts": (self._checkpoint["archived_first_ts"] if self._checkpoint else old[0].get("ts")),
            "archived_tail_hash": old[-1]["hash"],
            "live_from": (keep[0]["seq"] if keep else old[-1]["seq"] + 1),
        }
        cp["hash"] = _entry_hash(cp)
        if self._sk:
            cp["sig"], cp["pubkey"] = _sign(self._sk, cp["hash"])
        self._checkpoint = cp
        self._entries = keep
        self._save()
        return {"archived": n, "cutoff_ts": cutoff, "archive_file": name, "archive_sha256": cp["archive_sha256"],
                "archived_through": cp["archived_through"], "live_entries": len(keep),
                "signed": "sig" in cp}

    # ----------------------------------------------------------------- verification
    def verify(self, expected_pubkey: str | None = None, require_signatures: bool | None = None,
               bind_to_store: bool = True) -> tuple[bool, list[str]]:
        """Recompute every hash and link, check every signature, and bind the chain to the store.

        `bind_to_store` checks that each entry's `memory_state.last_receipt` is the tail of the store's
        receipt chain at the count the entry recorded, and that entries never point earlier in the chain
        than their predecessor. A rewritten memory history changes those hashes, so the action ledger
        reports it even when the memory chain was re-signed consistently. Naming `expected_pubkey`
        requires a signature on every entry.

        LIMITS, stated because a verifier that hides them claims more than it checks. An operator who
        holds the receipt key can rewrite both chains consistently, and a ledger whose last entries are
        deleted and the file re-signed reads as complete: neither is detectable from these two files.
        That is the witness's job (`anchor()` co-signed by an independent party, `detect_split_view`),
        not this function's. And the memory binding needs the store present; the offline check of the
        ledger file alone (`verify_file`) covers hashes, links and signatures only. Returns
        (ok, problems); problems is empty when ok."""
        problems, _tail, _archived = _verify_chain(self._checkpoint, self._entries, self.path.parent,
                                                   expected_pubkey=expected_pubkey,
                                                   require_signatures=require_signatures)
        if bind_to_store and self.store is not None:
            chain = [r.get("hash") for r in (getattr(self.store, "_receipts", None) or [])]
            pos = {h: i for i, h in enumerate(chain)}
            last_n = -1
            unbound = 0
            for e in self._entries:
                ms = e.get("memory_state") or {}
                lr, n = ms.get("last_receipt"), ms.get("receipts")
                if not lr:
                    # An empty chain at recording time (receipts: 0 on a receipted store) is a state, not
                    # a gap. No store, or a store with receipts off, is a gap: nothing binds the entry.
                    if n is None or n > 0 or not getattr(self.store, "receipts_enabled", False):
                        unbound += 1
                    continue
                if lr not in pos:
                    problems.append(f"seq {e.get('seq')}: memory_state.last_receipt {lr[:12]} is not in the "
                                    f"store's receipt chain (memory history rewritten or wrong store)")
                    continue
                # POSITION, not membership: the receipt named must be the chain's tail at that count,
                # and counts must not go backwards. Any hash from the chain used to satisfy this check.
                if isinstance(n, int) and pos[lr] != n - 1:
                    problems.append(f"seq {e.get('seq')}: memory_state names receipt {lr[:12]} as the tail of "
                                    f"{n} receipts, but it sits at position {pos[lr] + 1}")
                if pos[lr] < last_n:
                    problems.append(f"seq {e.get('seq')}: memory_state points earlier in the receipt chain "
                                    f"than the previous entry did")
                last_n = max(last_n, pos[lr])
            if unbound:
                problems.append(f"{unbound} entr{'y' if unbound == 1 else 'ies'} carry no memory binding "
                                f"(the store had receipts off, or the ledger has no store); the memory side "
                                f"of those entries is unverifiable")
        return (not problems), problems


def verify_entries(entries: Iterable[dict], expected_pubkey: str | None = None,
                   require_signatures: bool | None = None, start_prev: str = GENESIS, base_seq: int = 0,
                   archived: dict | None = None) -> list[str]:
    """Pure check of a list of entries. `require_signatures=None` requires a signature on every
    entry when any entry carries one. `start_prev` and `base_seq` are the archived tail and the seq
    the list starts at when the entries follow a checkpoint; `archived` maps archived seq to hash so
    a reference into the archive can be resolved."""
    entries = list(entries)
    problems: list[str] = []
    if require_signatures is None:
        # A caller who names the key expects a signed chain. An unsigned chain used to pass with
        # expected_pubkey set, because the key was only consulted inside the signature branch.
        require_signatures = bool(expected_pubkey) or any("sig" in e for e in entries)
    prev = start_prev
    pubkeys = set()
    known = dict(archived or {})

    def _resolves(ref, i):
        j = (ref or {}).get("seq")
        if not isinstance(j, int) or j < 0 or j >= base_seq + i:
            return False
        if j >= base_seq:
            return entries[j - base_seq].get("hash") == ref.get("hash")
        if j in known:
            return known[j] == ref.get("hash")
        problems.append(f"seq {base_seq + i}: refers to archived entry {j} and the archive is not present")
        return True

    for i, e in enumerate(entries):
        if e.get("seq") != base_seq + i:
            problems.append(f"seq {base_seq + i}: entry carries seq {e.get('seq')}")
        if e.get("prev") != prev:
            problems.append(f"seq {base_seq + i}: prev does not match the previous hash")
        h = _entry_hash(e)
        if e.get("hash") != h:
            problems.append(f"seq {i}: hash does not match the entry's content")
        if "sig" in e or require_signatures:
            pub = e.get("pubkey")
            if not pub or not e.get("sig"):
                problems.append(f"seq {i}: no signature")
            else:
                pubkeys.add(pub)
                if expected_pubkey and pub != expected_pubkey:
                    problems.append(f"seq {i}: signed by an unexpected key {pub[:12]}")
                if not _verify_sig(pub, e["sig"], e.get("hash") or ""):
                    problems.append(f"seq {i}: signature does not verify"
                                    + ("" if _HAVE_ED else " (cryptography not installed)"))
        prev = e.get("hash") or ""
        ref = e.get("refers_to")
        if e.get("kind") == "oversight" and isinstance(ref, dict):
            if not _resolves(ref, i):
                problems.append(f"seq {base_seq + i}: oversight refers_to does not resolve to an earlier entry")
        if e.get("kind") == "oversight" and not e.get("actor"):
            problems.append(f"seq {base_seq + i}: oversight event with no actor")
        if e.get("kind") == "incident":
            for ref in e.get("evidence") or []:
                if not _resolves(ref, i):
                    problems.append(f"seq {base_seq + i}: incident evidence does not resolve to an earlier entry")
            if not e.get("actor"):
                problems.append(f"seq {base_seq + i}: incident with no actor")
    if len(pubkeys) > 1:
        problems.append(f"chain signed by {len(pubkeys)} different keys")
    return problems


def _split(data: list) -> tuple[dict | None, list]:
    if data and isinstance(data[0], dict) and data[0].get("kind") == "checkpoint":
        return data[0], data[1:]
    return None, data


def _verify_chain(checkpoint: dict | None, entries: list, archive_dir, expected_pubkey: str | None = None,
                  require_signatures: bool | None = None, _depth: int = 0) -> tuple[list[str], str, dict]:
    """Verify a live ledger and, through its checkpoint, every archive it descends from. Returns
    (problems, tail_hash, {seq: hash} for every entry seen, archives included).

    A checkpoint is the signed header a rotated ledger starts with. It names the archive file, the
    file's SHA-256, the archived range and the archived tail hash. The live entries chain from that
    tail, so the chain is unbroken across files. With the archive present it is read and verified
    the same way (an archive may itself start with a checkpoint); without it the live part verifies
    from the checkpoint and the archived entries are reported as not verified, never as fine."""
    problems: list[str] = []
    known: dict = {}
    start_prev, base = GENESIS, 0
    # A signed chain requires a signed checkpoint. This is decided HERE, before the checkpoint is read,
    # because verify_entries() infers "any entry signed" only over the entries and a keyless attacker
    # could strip the checkpoint's signature, rewrite archived_first_ts and pass verify() with no key
    # named (red team, mutation 3a, 2026-09-16).
    if require_signatures is None:
        require_signatures = bool(expected_pubkey) or any("sig" in e for e in entries) \
            or bool(checkpoint and "sig" in checkpoint)
    cp_pubkey = None
    if checkpoint is not None:
        if _depth > 64:
            return ["more than 64 chained archives; refusing to follow further"], "", {}
        cp = checkpoint
        if cp.get("hash") != _entry_hash(cp):
            problems.append("checkpoint: hash does not match its content")
        if "sig" in cp or require_signatures:
            if not cp.get("sig") or not cp.get("pubkey"):
                problems.append("checkpoint: no signature")
            elif not _verify_sig(cp["pubkey"], cp["sig"], cp.get("hash") or ""):
                problems.append("checkpoint: signature does not verify")
            elif expected_pubkey and cp["pubkey"] != expected_pubkey:
                problems.append(f"checkpoint: signed by an unexpected key {cp['pubkey'][:12]}")
            else:
                cp_pubkey = cp.get("pubkey")
        start_prev = cp.get("archived_tail_hash") or ""
        at = cp.get("archived_through")
        if not isinstance(at, int) or isinstance(at, bool) or at < 0:
            return problems + [f"checkpoint: archived_through is {at!r}, not a non-negative integer"], "", {}
        base = at + 1
        name = cp.get("archive_file")
        chain = cp.get("archive_chain")
        if not isinstance(name, str) or not name or Path(name).name != name:
            problems.append(f"checkpoint: archive_file {name!r} is not a bare file name beside the ledger")
            name = None
        if isinstance(cp.get("archived_from"), int) and cp["archived_from"] > at:
            problems.append("checkpoint: archived_from is after archived_through")
        if not isinstance(chain, list) or (name and (not chain or chain[-1] != name)):
            problems.append("checkpoint: archive_chain does not end with archive_file")
        if isinstance(chain, list) and cp.get("archive_index") != len(chain):
            problems.append("checkpoint: archive_index does not match the length of archive_chain")
        if cp.get("live_from") != base:
            problems.append(f"checkpoint: live_from {cp.get('live_from')!r} is not archived_through + 1")
        arc = Path(archive_dir) / name if name else None
        if arc is not None and arc.exists():
            raw = arc.read_bytes()
            if _sha256_hex(raw) != cp.get("archive_sha256"):
                problems.append(f"checkpoint: {arc.name} does not hash to the checkpoint's archive_sha256")
            else:
                try:
                    data = json.loads(raw.decode("utf-8"))
                except ValueError as e:
                    data = None
                    problems.append(f"checkpoint: {arc.name} is not JSON: {e}")
                if isinstance(data, list):
                    inner_cp, inner = _split(data)
                    p2, tail2, known2 = _verify_chain(inner_cp, inner, archive_dir, expected_pubkey,
                                                      require_signatures, _depth + 1)
                    problems += [f"{arc.name}: {x}" for x in p2]
                    known.update(known2)
                    if tail2 != start_prev:
                        problems.append(f"checkpoint: {arc.name} ends at {tail2[:12]}, the checkpoint names "
                                        f"{start_prev[:12]} as the archived tail")
                    if inner and inner[-1].get("seq") != base - 1:
                        problems.append(f"checkpoint: {arc.name} ends at seq {inner[-1].get('seq')}, the checkpoint "
                                        f"says archived_through {base - 1}")
                    if inner and inner[0].get("seq") != cp.get("archived_from"):
                        problems.append(f"checkpoint: {arc.name} starts at seq {inner[0].get('seq')}, the checkpoint "
                                        f"says archived_from {cp.get('archived_from')}")
                    inner_first = (inner_cp or {}).get("archived_first_ts") if inner_cp else (inner[0].get("ts") if inner else None)
                    if inner_first != cp.get("archived_first_ts"):
                        problems.append(f"checkpoint: archived_first_ts {cp.get('archived_first_ts')!r} is not the "
                                        f"oldest ts the archive accounts for ({inner_first!r})")
                    inner_count = (int(inner_cp["archived_through"]) + 1 if inner_cp and isinstance(inner_cp.get("archived_through"), int) else 0) + len(inner)
                    if cp.get("archived_count") != inner_count:
                        problems.append(f"checkpoint: archived_count {cp.get('archived_count')!r} is not the "
                                        f"{inner_count} entries the archive accounts for")
                    if inner_cp and isinstance(inner_cp.get("archive_chain"), list) and isinstance(chain, list) \
                            and chain[:-1] != inner_cp["archive_chain"]:
                        problems.append(f"checkpoint: archive_chain does not extend {arc.name}'s chain")
        else:
            problems.append(f"chain starts at a checkpoint after seq {base - 1}; archive "
                            f"{cp.get('archive_file')} is not present, so {base} archived entries were not verified")
    problems += verify_entries(entries, expected_pubkey=expected_pubkey, require_signatures=require_signatures,
                               start_prev=start_prev, base_seq=base, archived=known)
    if cp_pubkey and any(e.get("pubkey") and e["pubkey"] != cp_pubkey for e in entries):
        problems.append("checkpoint: signed by a different key than the entries")
    for e in entries:
        if isinstance(e.get("seq"), int):
            known[e["seq"]] = e.get("hash")
    tail = entries[-1].get("hash") if entries else start_prev
    return problems, tail or "", known


def verify_file(path: str | os.PathLike, expected_pubkey: str | None = None) -> tuple[bool, list[str]]:
    """Verify a ledger file offline, with no store and no key. A rotated ledger is followed into its
    archives when they sit beside it; a missing archive is reported, not skipped."""
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return False, [f"cannot read {p}: {type(e).__name__}: {e}"]
    if not isinstance(data, list):
        return False, [f"{p} is not a ledger (expected a JSON array)"]
    cp, entries = _split(data)
    problems, _tail, _known = _verify_chain(cp, entries, p.parent, expected_pubkey=expected_pubkey)
    return (not problems), problems
