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

__all__ = ["ActionLedger", "ActionContext", "verify_file", "GENESIS", "LEDGER_VERSION"]

GENESIS = "0" * 64
LEDGER_VERSION = 1
_SIGNED_FIELDS_EXCLUDED = ("hash", "sig", "pubkey")


def _canon(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _content_hash(obj: Any) -> str:
    """Digest of any JSON-serialisable value. Non-serialisable values are digested by their repr,
    and the entry records that fact so an auditor knows the digest is over a repr, not the value."""
    try:
        return _sha256_hex(_canon(obj))
    except (TypeError, ValueError):
        return _sha256_hex(repr(obj).encode("utf-8"))


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
        self._load()

    # ----------------------------------------------------------------- persistence
    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = []
            self._entries = list(data) if isinstance(data, list) else []
        else:
            self._entries = []

    def _save(self) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp.%d" % os.getpid())
        tmp.write_text(json.dumps(self._entries, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def reload(self) -> None:
        """Re-read the ledger file. A long-lived handle answers from what it loaded at open; call this
        before `verify()` or `entries()` when another process may have appended."""
        self._load()

    # ----------------------------------------------------------------- memory binding
    def memory_state(self) -> dict:
        """The store's state at this moment: digest, record count, tail of the receipt chain, and the
        ids the last recall returned. Empty fields when the ledger has no store."""
        st = self.store
        if st is None:
            return {"digest": None, "records": None, "last_receipt": None, "receipts": 0,
                    "recalled": [], "recalled_at": None}
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
        return {"digest": digest, "records": n,
                "last_receipt": receipts[-1].get("hash") if receipts else None,
                "receipts": len(receipts),
                "recalled": recalled[:64], "recalled_at": recalled_at}

    # ----------------------------------------------------------------- recording
    def record(self, action: str, inputs: Any = None, output: Any = None, status: str = "ok",
               error: str | None = None, meta: dict | None = None, started: float | None = None,
               actor: str | None = None) -> dict:
        """Append one action. Returns the entry as stored (with hash, and sig when a key is set)."""
        if not isinstance(action, str) or not action:
            raise ValueError("action must be a non-empty string, for example 'tool:search'")
        now = time.time()
        inp = self.redact(inputs) if (self.redact and inputs is not None) else inputs
        out = self.redact(output) if (self.redact and output is not None) else output
        entry: dict = {
            "v": LEDGER_VERSION,
            "seq": len(self._entries),
            "prev": self._entries[-1]["hash"] if self._entries else GENESIS,
            "ts": now,
            "started": started if started is not None else now,
            "actor": actor if actor is not None else self.actor,
            "action": action,
            "status": status,
            "inputs_sha256": _content_hash(inp) if inp is not None else None,
            "output_sha256": _content_hash(out) if out is not None else None,
            "memory_state": self.memory_state(),
        }
        if error:
            entry["error"] = str(error)[:2000]
        if meta:
            entry["meta"] = meta
        if self.keep_content:
            entry["inputs"] = inp
            entry["output"] = out
        entry["hash"] = _entry_hash(entry)
        if self._sk:
            entry["sig"], entry["pubkey"] = _sign(self._sk, entry["hash"])
        self._entries.append(entry)
        self._save()
        return entry

    @contextlib.contextmanager
    def action(self, action: str, inputs: Any = None, meta: dict | None = None, actor: str | None = None):
        """Record an action around a block of code. The block's exception, if any, is recorded as the
        action's error and re-raised."""
        ctx = ActionContext(self, action, inputs, meta)
        try:
            yield ctx
        except BaseException as e:  # noqa: BLE001 - recorded, then re-raised
            ctx.fail(e)
            ctx.entry = self.record(action, inputs, None, status="error", error=ctx._error,
                                    meta=ctx.meta or None, started=ctx.started, actor=actor)
            raise
        status = "error" if ctx._error else "ok"
        ctx.entry = self.record(action, inputs, ctx._output if ctx._has_output else None,
                                status=status, error=ctx._error, meta=ctx.meta or None,
                                started=ctx.started, actor=actor)

    def wrap(self, name: str | None = None, actor: str | None = None):
        """Decorator: every call of the function becomes one action; positional and keyword arguments
        are the inputs and the return value is the output."""
        def deco(fn):
            action = name or f"call:{getattr(fn, '__qualname__', getattr(fn, '__name__', 'fn'))}"

            @functools.wraps(fn)
            def inner(*a, **k):
                with self.action(action, inputs={"args": list(a), "kwargs": k}, actor=actor) as ctx:
                    return ctx.output(fn(*a, **k))
            return inner
        return deco

    # ----------------------------------------------------------------- reading
    def entries(self) -> list[dict]:
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def what_it_knew(self, seq: int) -> dict:
        """The memory state recorded at action `seq`, plus, when the store is present, the current
        provenance of each id that recall had returned. Answers "what did the agent know when it did
        this" from the chain rather than from memory."""
        e = self._entries[seq]
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

    # ----------------------------------------------------------------- verification
    def verify(self, expected_pubkey: str | None = None, require_signatures: bool | None = None,
               bind_to_store: bool = True) -> tuple[bool, list[str]]:
        """Recompute every hash and link, check every signature, and bind the chain to the store.

        `bind_to_store` checks that each entry's `memory_state.last_receipt` is a hash that still
        exists in the store's receipt chain. A rewritten memory history changes those hashes, so the
        action ledger reports it even when the memory chain was re-signed consistently. Returns
        (ok, problems); problems is empty when ok."""
        problems = verify_entries(self._entries, expected_pubkey=expected_pubkey,
                                  require_signatures=require_signatures)
        if bind_to_store and self.store is not None:
            chain = {r.get("hash") for r in (getattr(self.store, "_receipts", None) or [])}
            for e in self._entries:
                lr = (e.get("memory_state") or {}).get("last_receipt")
                if lr and lr not in chain:
                    problems.append(f"seq {e.get('seq')}: memory_state.last_receipt {lr[:12]} is not in the "
                                    f"store's receipt chain (memory history rewritten or wrong store)")
        return (not problems), problems


def verify_entries(entries: Iterable[dict], expected_pubkey: str | None = None,
                   require_signatures: bool | None = None) -> list[str]:
    """Pure check of a list of entries. `require_signatures=None` requires a signature on every
    entry when any entry carries one."""
    entries = list(entries)
    problems: list[str] = []
    if require_signatures is None:
        require_signatures = any("sig" in e for e in entries)
    prev = GENESIS
    pubkeys = set()
    for i, e in enumerate(entries):
        if e.get("seq") != i:
            problems.append(f"seq {i}: entry carries seq {e.get('seq')}")
        if e.get("prev") != prev:
            problems.append(f"seq {i}: prev does not match the previous hash")
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
    if len(pubkeys) > 1:
        problems.append(f"chain signed by {len(pubkeys)} different keys")
    return problems


def verify_file(path: str | os.PathLike, expected_pubkey: str | None = None) -> tuple[bool, list[str]]:
    """Verify a ledger file offline, with no store and no key."""
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return False, [f"cannot read {p}: {type(e).__name__}: {e}"]
    if not isinstance(data, list):
        return False, [f"{p} is not a ledger (expected a JSON array)"]
    problems = verify_entries(data, expected_pubkey=expected_pubkey)
    return (not problems), problems
