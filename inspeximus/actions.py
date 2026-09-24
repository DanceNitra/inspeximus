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
import re
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

__all__ = ["ActionLedger", "ActionContext", "LedgerUnreadable", "verify_file", "GENESIS", "LEDGER_VERSION",
           "OVERSIGHT_EVENTS", "DISCLOSURE_KINDS", "INCIDENT_SEVERITIES", "INCIDENT_DEADLINES_DAYS",
           "RISK_SOURCES", "RISK_HARMS", "RISK_MEASURES", "RISK_LEVELS", "CORRECTIVE_ACTIONS",
           "INFORMED_PARTIES", "AUTHORITY_REQUEST_SCOPES", "BREACH_NOTIFY_TARGETS", "BREACH_EXEMPTIONS",
           "BREACH_DEADLINE_HOURS", "LITERACY_MEASURES", "LITERACY_AUDIENCES", "LITERACY_CONSIDERATIONS",
           "PROHIBITED_PRACTICES", "ATTESTATION_STATEMENTS", "RESPONSIBILITY_ROLES", "PROVIDER_TRIGGERS",
           "COOPERATION_ITEMS", "CONFORMITY_PROCEDURES", "RETENTION_DOCUMENTS", "DOCUMENTATION_RETENTION_YEARS",
           "NOTICE_ITEMS", "NOTICE_ART14_ITEMS", "NOTICE_CHANNELS", "NOTICE_TIMINGS", "PROCESSING_ROLES"]

# Art. 73(2) to (4): a serious incident is reported immediately and no later than 15 days after the
# provider becomes aware of it; 2 days for a widespread infringement or a serious incident concerning
# critical infrastructure; 10 days for the death of a person. Days here are calendar days from `aware_ts`.
INCIDENT_SEVERITIES = ("serious", "widespread", "death", "other")
INCIDENT_DEADLINES_DAYS = {"serious": 15, "widespread": 2, "death": 10, "other": None}

OVERSIGHT_EVENTS = ("approve", "refuse", "override", "stop", "review")

#: Art. 9(2): where a risk was identified. (a) intended use, (b) reasonably foreseeable misuse,
#: (c) data from post-market monitoring (Art. 72).
RISK_SOURCES = ("intended_use", "foreseeable_misuse", "post_market")
#: Art. 9(2)(a): what the risk is to.
RISK_HARMS = ("health", "safety", "fundamental_rights")
#: Art. 9(5)(a) to (c): the kind of measure taken.
RISK_MEASURES = ("eliminate", "mitigate", "inform")
RISK_LEVELS = ("low", "medium", "high")

#: Art. 20(1): what a provider does with a non-conforming system.
CORRECTIVE_ACTIONS = ("conformity", "withdraw", "disable", "recall")
#: Art. 20(1) names the first four; 20(2) adds the authority and the notified body when the system
#: presents a risk within the meaning of Art. 79(1).
INFORMED_PARTIES = ("distributor", "deployer", "authorised_representative", "importer",
                    "market_surveillance_authority", "notified_body")
#: Art. 21(1) documentation, 21(2) the automatically generated logs.
AUTHORITY_REQUEST_SCOPES = ("documentation", "logs", "both")
#: GDPR Art. 33(1) the supervisory authority, 34(1) the data subjects, 34(3)(c) a public communication.
BREACH_NOTIFY_TARGETS = ("supervisory_authority", "data_subjects", "public")
#: GDPR Art. 34(3)(a) to (c): why the subjects were not told directly.
BREACH_EXEMPTIONS = ("protected", "mitigated", "disproportionate")
BREACH_DEADLINE_HOURS = 72
DISCLOSURE_KINDS = ("interaction", "generated_content", "emotion_recognition", "biometric_categorisation",
                    "deepfake", "public_interest_text")

#: Art. 4 (as amended by the Digital Omnibus, in force 27 July 2026): providers and deployers take measures
#: to support the development of AI literacy of their staff and other persons dealing with the operation
#: and use of AI systems on their behalf, taking into account their technical knowledge, experience,
#: education and training, the context of use, and the persons the systems are used on. The obligation
#: does not require a specific level for any individual, so the record is the measure taken, never a score.
LITERACY_MEASURES = ("training", "guidance", "documentation", "briefing", "assessment")
LITERACY_AUDIENCES = ("staff", "contractor", "operator_of_the_system", "other_person_on_behalf")
LITERACY_CONSIDERATIONS = ("technical_knowledge", "experience", "education", "training", "context_of_use",
                           "persons_affected")

#: Art. 5(1), the classes of prohibited practice a provider or deployer attests it does not use. (ba) and
#: (bb) were added by the amendment and apply from 2 December 2026; (g) and (h) are the biometric ones.
PROHIBITED_PRACTICES = {
    "a": "subliminal, manipulative or deceptive techniques that materially distort behaviour",
    "b": "exploiting vulnerabilities of age, disability or social or economic situation",
    "ba": "generating or manipulating intimate imagery of an identifiable person without consent",
    "bb": "generating or manipulating child sexual abuse material or performance",
    "c": "social scoring leading to detrimental or disproportionate treatment",
    "d": "risk assessment of a natural person committing a criminal offence based solely on profiling",
    "e": "creating or expanding facial recognition databases by untargeted scraping",
    "f": "inferring emotions in the workplace or in education, outside medical or safety reasons",
    "g": "biometric categorisation inferring race, political opinion, union membership, religion, sex life or orientation",
    "h": "real-time remote biometric identification in publicly accessible spaces for law enforcement",
}
#: What the attestation says about a class: the system is not used for it, or the class cannot arise in
#: this system at all (with the basis stated, because "not applicable" is the easier claim).
ATTESTATION_STATEMENTS = ("not_used", "not_applicable")

#: Art. 25: who is the provider along the value chain, and why a party became one (25(1)(a) to (c)).
RESPONSIBILITY_ROLES = ("provider", "initial_provider", "new_provider", "product_manufacturer", "distributor",
                        "importer", "deployer", "authorised_representative", "third_party_supplier")
PROVIDER_TRIGGERS = ("name_or_trademark", "substantial_modification", "changed_intended_purpose")
#: Art. 25(2) as amended: what the initial provider makes available to a new provider.
COOPERATION_ITEMS = ("technical_documentation", "known_limitations_and_failure_modes", "targeted_technical_access")

#: Art. 43(1): the conformity assessment procedure the provider followed. Annex VI is internal control;
#: Annex VII involves a notified body, whose identification then follows the CE marking (Art. 48(4)).
CONFORMITY_PROCEDURES = ("annex_vi_internal_control", "annex_vii_notified_body")

#: Art. 18(1)(a) to (e): what the provider keeps at the disposal of the authorities for ten years after
#: the system was placed on the market or put into service.
RETENTION_DOCUMENTS = ("technical_documentation", "quality_management_system", "notified_body_changes",
                       "notified_body_decisions", "eu_declaration_of_conformity")
DOCUMENTATION_RETENTION_YEARS = 10

#: GDPR Art. 13(1) and (2): what the controller tells the subject when data is collected from them.
NOTICE_ITEMS = ("controller_identity", "dpo_contact", "purposes_and_legal_basis", "legitimate_interests",
                "recipients", "third_country_transfer", "retention_period", "rights", "withdraw_consent",
                "complaint_to_authority", "provision_required", "automated_decision_making")
#: Art. 14(1)(d) and (2)(f): the two items that exist only when the data did not come from the subject.
NOTICE_ART14_ITEMS = ("data_categories", "data_source")
NOTICE_CHANNELS = ("ui", "email", "letter", "api", "voice", "document")
#: Art. 14(3): when a notice for data obtained elsewhere is due.
NOTICE_TIMINGS = ("at_collection", "within_one_month", "at_first_communication", "at_first_disclosure")

#: GDPR Art. 28: the role this store's operator plays for the personal data in it.
PROCESSING_ROLES = ("controller", "joint_controller", "processor", "sub_processor")

#: The aspects Art. 17(1) lists for a quality management system, (a) to (m). A qms entry names the
#: one its procedure covers, so the register can say which letters have a current procedure.
QMS_ASPECTS = {
    "a": "strategy for regulatory compliance, including conformity assessment and change management",
    "b": "techniques, procedures and systematic actions for design, design control and design verification",
    "c": "techniques, procedures and systematic actions for development, quality control and quality assurance",
    "d": "examination, test and validation procedures before, during and after development, and their frequency",
    "e": "technical specifications, including standards, and the means to meet the Chapter III, Section 2 requirements",
    "f": "systems and procedures for data management",
    "g": "the risk management system referred to in Article 9",
    "h": "the setting-up, implementation and maintenance of a post-market monitoring system (Article 72)",
    "i": "procedures related to the reporting of a serious incident (Article 73)",
    "j": "the handling of communication with authorities, notified bodies, other operators, customers or other interested parties",
    "k": "systems and procedures for record-keeping of all relevant documentation and information",
    "l": "resource management, including security-of-supply related measures",
    "m": "an accountability framework setting out the responsibilities of the management and other staff",
}

GENESIS = "0" * 64
LEDGER_VERSION = 1

#: Lifecycle events a ledger records about the system itself. `substantial_modification` is the Art. 3(23)
#: change that ends Art. 111(2) grandfathering and re-opens conformity; `decommission` is the entry an
#: ISO/IEC 42001 reviewer asks for, with what happened to the persistent memory.
LIFECYCLE_EVENTS = ("start", "stop", "pause", "resume", "configuration_change", "key_rotation",
                    "substantial_modification", "decommission")
DISPOSITIONS = ("erased", "archived", "transferred", "retained")


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


def _chain_state(ledger) -> dict:
    """The ledger's own verifier, run for the monitoring report: Art. 72(2) asks the provider to
    evaluate continuous compliance with Section 2, and a chain that no longer verifies is the first
    thing such an evaluation has to say. Never raises: a verifier that cannot run is reported as such."""
    try:
        ok, problems = ledger.verify()
        return {"verified": bool(ok), "problems": len(problems), "entries": len(ledger)}
    except Exception as ex:  # noqa: BLE001 - the report must still build
        return {"verified": None, "problems": None, "entries": len(ledger), "error": str(ex)[:200]}


def _count_by(entries: list, field: str) -> dict:
    out: dict = {}
    for e in entries:
        k = str(e.get(field))
        out[k] = out.get(k, 0) + 1
    return out


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


class LedgerUnreadable(RuntimeError):
    """The ledger file exists and cannot be read as a ledger, so nothing may be appended to it.

    `_load` used to read such a file as an empty chain: `verify()` then passed it (0 entries, no
    problems) and the next `record()` numbered its entry 0 from GENESIS and replaced the file, so the
    entries on disk were gone and the call reported an ordinary success. Measured 2026-09-24 (MCP tool
    review, L3 and L4): a ledger holding two incidents, truncated to half its bytes, then one
    `record_risk`. A torn copy or a disk-full restore is exactly the file this meets, and a ledger
    that starts over is worse than one that stops: the loss reads as a fresh chain. Restore the file,
    or move it aside deliberately to start a new one."""


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
        self._load_error: str | None = None        # why the file on disk is not a ledger; see LedgerUnreadable
        self._load()

    @property
    def salt_path(self):
        return self.path.with_name(self.path.name + ".salt")

    def _salt_bytes(self, create: bool = True) -> bytes:
        """The per-ledger digest salt, minted on first use and kept beside the ledger in its own file.
        Not part of the ledger, so a copy of the ledger alone cannot be dictionary-attacked.

        `create=False` is for the READ side. `matches()` used to mint a fresh salt when the file was
        missing, so a ledger handed over without its salt answered `false` for every transcript,
        including the true one, and the verdict was indistinguishable from an edit (red team,
        2026-09-17). A comparison that cannot be made is refused, not answered."""
        if self._salt is None:
            p = self.salt_path
            if p.exists():
                self._salt = bytes.fromhex(p.read_text(encoding="utf-8").strip())
            elif not create:
                raise FileNotFoundError(
                    f"the ledger's salt file {p} is missing, so no transcript can be checked against "
                    f"this ledger: a digest without its salt answers false for every input, the true "
                    f"one included. Restore the salt file beside the ledger.")
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
        self._load_error = None
        if self.path.exists():
            # A file that is there and is not a ledger is NOT an empty ledger. The wording matches
            # verify_file, the offline check, so both verifiers name the same failure.
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                data = []
                self._load_error = f"cannot read {self.path}: {type(e).__name__}: {e}"
            if not isinstance(data, list):
                data = []
                self._load_error = f"{self.path} is not a ledger (expected a JSON array)"
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

    def require_readable(self) -> None:
        """Raise LedgerUnreadable when the ledger file on disk cannot be read, after re-reading it if
        another handle changed it. Every write calls this before it changes anything; a caller that
        changes something ELSE before it writes here (a memory rectification, an objection) calls it
        first, so a refused ledger write cannot leave the other half done."""
        self._refresh_if_changed()
        if self._load_error:
            raise LedgerUnreadable(
                f"{self._load_error}. Refusing to append: a new entry would start a new chain over the "
                f"entries that file holds. Restore it from a copy, or move it aside to start a new "
                f"ledger deliberately.")

    def _save(self) -> None:
        if self._load_error:                       # the backstop; every writer checks before it mutates
            raise LedgerUnreadable(f"{self._load_error}. Refusing to overwrite it.")
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
        # The timestamp fallback compares against when the previous action STARTED, not when its entry
        # was written: an action that performs the recall itself starts before it and is written after
        # it, and comparing against the write time called every such window stale (measured on our own
        # MCP server with INSPEXIMUS_OBSERVE_RECALL=1, 2026-09-16).
        prev = self._entries[-1] if self._entries else None
        prev_ts = (prev.get("started") or prev.get("ts")) if prev else None
        # strictly earlier: on a coarse clock (Windows, ~1 ms) a recall made inside the previous action
        # can share its start tick, and the identity check above already covers a consumed window
        if not stale and recalled and recalled_at and prev_ts and recalled_at < prev_ts:
            stale = True
        return {"digest": digest, "records": n,
                "last_receipt": receipts[-1].get("hash") if receipts else None,
                "receipts": len(receipts),
                "recalled": [] if stale else recalled[:64], "recalled_at": recalled_at,
                "recall_scope": "this handle", "recall_before_previous_entry": stale,
                # the identity of the window this state REPORTS, so record() marks that one consumed
                # and not whichever window exists when the entry is written (see record)
                "_window_id": id(cur) if cur is not None else None}

    # ----------------------------------------------------------------- recording
    def record(self, action: str, inputs: Any = None, output: Any = None, status: str = "ok",
               error: str | None = None, meta: dict | None = None, started: float | None = None,
               actor: str | None = None, kind: str = "action", extra: dict | None = None,
               memory_state: dict | None = None, model: str | None = None,
               principal: str | None = None, session: str | None = None) -> dict:
        """Append one entry. Returns it as stored (with hash, and sig when a key is set). `kind` is
        "action" for what the agent did; `oversight()` and `disclosure()` set the other two.

        `model` names the model version behind a model call and `principal` the person or account on
        whose behalf the agent acted. Both are what an ISO/IEC 42001 or SOC 2 reviewer asks for on every
        action (identity-tied attribution, model version per call) and neither is inferred: absent when
        the caller did not say."""
        if not isinstance(action, str) or not action:
            raise ValueError("action must be a non-empty string, for example 'tool:search'")
        if kind not in ("action", "oversight", "disclosure", "rights", "incident", "retention", "timestamp",
                        "lifecycle", "risk", "monitoring", "corrective", "authority", "breach",
                        "literacy", "attestation", "responsibilities", "declaration", "documentation",
                        "notice", "processing_role", "qms"):
            raise ValueError("kind must be action, oversight, disclosure, rights, incident, retention, timestamp, "
                             "lifecycle, risk, monitoring, corrective, authority, breach, literacy, attestation, "
                             "responsibilities, declaration, documentation, notice, processing_role or qms")
        self.require_readable()
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
            "memory_state": dict(memory_state) if memory_state is not None else self.memory_state(),
        }
        # The window this entry consumed is the one its memory_state reports. It used to be whatever
        # window existed at write time, which for an action that itself performed the recall was the
        # NEW window, so the next action reported recall_before_previous_entry and an empty list and
        # the fact the agent acted on was never attributed to it. Measured on our own MCP server the
        # first hour the ledger was on (2026-09-16): mcp:recall then mcp:actions_verify, recalled [].
        consumed = entry["memory_state"].pop("_window_id", None)
        if error:
            entry["error"] = str(error)[:2000]
        if model:
            entry["model"] = str(model)[:200]
        if principal:
            entry["principal"] = str(principal)[:200]
        if session and "session" not in (extra or {}):
            entry["session"] = str(session)[:200]
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
        self._seen_recall = consumed
        return entry

    @contextlib.contextmanager
    def action(self, action: str, inputs: Any = None, meta: dict | None = None, actor: str | None = None,
               model: str | None = None, principal: str | None = None, session: str | None = None):
        """Record an action around a block of code. The block's exception, if any, is recorded as the
        action's error and re-raised. `model`, `principal` and `session` are recorded as on record().

        An unreadable ledger raises LedgerUnreadable HERE, before the block runs: refusing only at the
        end would let the action happen and then leave it unrecorded."""
        self.require_readable()
        ctx = ActionContext(self, action, inputs, meta)
        before = self.memory_state()          # what the agent knew BEFORE it acted, not after
        try:
            yield ctx
        except BaseException as e:  # noqa: BLE001 - recorded, then re-raised
            ctx.fail(e)
            ctx.entry = self.record(action, inputs, None, status="error", error=ctx._error,
                                    meta=ctx.meta or None, started=ctx.started, actor=actor,
                                    memory_state=before, model=model, principal=principal, session=session)
            raise
        status = "error" if ctx._error else "ok"
        ctx.entry = self.record(action, inputs, ctx._output if ctx._has_output else None,
                                status=status, error=ctx._error, meta=ctx.meta or None,
                                started=ctx.started, actor=actor, memory_state=before,
                                model=model, principal=principal, session=session)

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

    def lifecycle(self, event: str, actor: str, note: str | None = None, disposition: str | None = None,
                  refers_to: int | str | None = None, meta: dict | None = None) -> dict:
        """Record a lifecycle event of the system on the same chain: start, stop, pause, resume,
        configuration_change, key_rotation, substantial_modification or decommission. `actor` is the
        person or role who did it. `substantial_modification` is the Art. 3(23) change that ends the
        Art. 111(2) grandfathering of a system placed on the market before its date and re-opens
        conformity; record it when the design changes, with `note` saying what. `decommission` needs a
        `disposition` for the persistent memory: erased, archived, transferred or retained, the entry an
        ISO/IEC 42001 reviewer asks for. Annex IV point 6 and the deployer report list these entries."""
        if event not in LIFECYCLE_EVENTS:
            raise ValueError(f"event must be one of {LIFECYCLE_EVENTS}")
        if not actor:
            raise ValueError("a lifecycle event needs an actor: the person or role who did it")
        if event == "decommission" and disposition not in DISPOSITIONS:
            raise ValueError(f"decommission needs a disposition of the persistent memory: one of {DISPOSITIONS}")
        if disposition is not None and disposition not in DISPOSITIONS:
            raise ValueError(f"disposition must be one of {DISPOSITIONS}")
        extra: dict = {"event": event}
        if note:
            extra["note"] = str(note)[:2000]
        if disposition:
            extra["disposition"] = disposition
        if refers_to is not None:
            extra["refers_to"] = self._resolve_ref(refers_to)
        if event == "substantial_modification":
            extra["basis"] = "Art. 3(23); a modified system placed on the market before its date is covered from this change (Art. 111(2))"
        return self.record(f"lifecycle:{event}", status="ok", actor=actor, meta=meta, kind="lifecycle", extra=extra)

    def lifecycle_events(self) -> list[dict]:
        return [{"seq": e["seq"], "ts": e.get("ts"), "event": e.get("event"), "actor": e.get("actor"),
                 "disposition": e.get("disposition"), "note": e.get("note")}
                for e in self._entries if e.get("kind") == "lifecycle"]

    # ------------------------------------------------------------------ Art. 9: risk register
    def risk(self, risk_id: str, hazard: str, harm: str, source: str, actor: str,
             likelihood: str = "medium", severity: str = "medium",
             measure: str | None = None, measure_kind: str | None = None,
             residual: str | None = None, residual_acceptable: bool | None = None,
             evidence: list | None = None, refers_to: list | None = None,
             tests: list | None = None, affects_vulnerable_groups: bool = False,
             status: str = "open", meta: dict | None = None) -> dict:
        """One entry in the risk register (EU AI Act Art. 9), appended, never rewritten.

        Art. 9(2) asks for a continuous, iterative process with regular systematic review, so a risk
        is a `risk_id` with a history: every review, re-estimation or new measure is a new entry under
        the same id, and `risk_register()` shows the latest state and the age of the last review.
        `source` is where the risk was found: the intended use (9(2)(a)), reasonably foreseeable
        misuse (9(2)(b)), or data from post-market monitoring (9(2)(c)). `harm` is what it threatens.
        `measure` and `measure_kind` are the 9(2)(d) measure and whether it eliminates, mitigates or
        informs (9(5)(a) to (c)); `residual` and `residual_acceptable` are the 9(5) judgement.
        `evidence` is a list of free references to what supports the estimate (a probe path, a
        receipt hash, a test name); `refers_to` is a list of ledger entries (an incident, a
        monitoring report) and each must resolve. `tests` is the 9(6) to 9(8) record: each item names
        the `metric`, the prior defined `threshold`, the `observed` value and whether it `passed`, so
        the register can say which risks were tested against a threshold set before the test and
        which were not. `affects_vulnerable_groups` is the 9(9) flag. `actor` is the person or role
        making the entry."""
        if source not in RISK_SOURCES:
            raise ValueError(f"source must be one of {RISK_SOURCES}")
        if harm not in RISK_HARMS:
            raise ValueError(f"harm must be one of {RISK_HARMS}")
        if likelihood not in RISK_LEVELS or severity not in RISK_LEVELS:
            raise ValueError(f"likelihood and severity must be one of {RISK_LEVELS}")
        if residual is not None and residual not in RISK_LEVELS:
            raise ValueError(f"residual must be one of {RISK_LEVELS}")
        if measure_kind is not None and measure_kind not in RISK_MEASURES:
            raise ValueError(f"measure_kind must be one of {RISK_MEASURES}")
        if status not in ("open", "closed"):
            raise ValueError("status must be 'open' or 'closed'")
        if not risk_id or not hazard or not actor:
            raise ValueError("a risk entry needs a risk_id, a hazard and an actor")
        if residual_acceptable and residual is None:
            raise ValueError("residual_acceptable=True needs the residual level it judges")
        checked = []
        for t in (tests or []):
            if not isinstance(t, dict) or not t.get("metric") or "threshold" not in t:
                raise ValueError("each test needs a metric and the threshold defined before the test (Art. 9(8))")
            checked.append({"metric": str(t["metric"]), "threshold": t["threshold"],
                            "observed": t.get("observed"), "passed": t.get("passed"),
                            "probe": t.get("probe")})
        refs = [self._resolve_ref(r) for r in (refers_to or [])]
        extra = {"risk_id": risk_id, "hazard": hazard, "harm": harm, "source": source,
                 "likelihood": likelihood, "severity": severity, "measure": measure,
                 "measure_kind": measure_kind, "residual": residual,
                 "residual_acceptable": residual_acceptable, "evidence": list(evidence or []),
                 "refers_to": refs, "tests": checked,
                 "affects_vulnerable_groups": bool(affects_vulnerable_groups), "risk_status": status}
        return self.record(f"risk:{source}", inputs={"risk_id": risk_id, "hazard": hazard}, status="ok",
                           actor=actor, meta=meta, kind="risk", extra=extra)

    def risk_register(self, now: float | None = None) -> dict:
        """The register an assessor reads: the latest entry per risk_id, its history length, the days
        since its last review, and the counts Art. 9 asks about (by source, unmitigated, residual not
        judged acceptable, vulnerable groups). Read-only."""
        now = time.time() if now is None else now
        by_id: dict = {}
        for e in self._entries:
            if e.get("kind") != "risk":
                continue
            by_id.setdefault(e["risk_id"], []).append(e)
        rows = []
        for rid, hist in by_id.items():
            last = hist[-1]
            rows.append({"risk_id": rid, "hazard": last.get("hazard"), "harm": last.get("harm"),
                         "source": last.get("source"), "likelihood": last.get("likelihood"),
                         "severity": last.get("severity"), "measure": last.get("measure"),
                         "measure_kind": last.get("measure_kind"), "residual": last.get("residual"),
                         "residual_acceptable": last.get("residual_acceptable"),
                         "status": last.get("risk_status"), "entries": len(hist),
                         "first_seq": hist[0]["seq"], "last_seq": last["seq"],
                         "last_review_ts": last.get("ts"),
                         "days_since_review": round((now - float(last.get("ts") or now)) / 86400, 1),
                         "evidence": last.get("evidence") or [], "refers_to": last.get("refers_to") or [],
                         "tests": last.get("tests") or [],
                         "affects_vulnerable_groups": bool(last.get("affects_vulnerable_groups"))})
        open_rows = [r for r in rows if r["status"] == "open"]
        return {"kind": "inspeximus.risk_register/1", "ts": now, "risks": rows,
                "counts": {"total": len(rows), "open": len(open_rows),
                           "by_source": {s: sum(1 for r in rows if r["source"] == s) for s in RISK_SOURCES},
                           "by_harm": {h: sum(1 for r in rows if r["harm"] == h) for h in RISK_HARMS},
                           "without_measure": sum(1 for r in open_rows if not r["measure"]),
                           "residual_not_judged": sum(1 for r in open_rows if r["residual_acceptable"] is None),
                           "residual_not_acceptable": sum(1 for r in open_rows if r["residual_acceptable"] is False),
                           "without_evidence": sum(1 for r in open_rows if not r["evidence"]),
                           "without_test": sum(1 for r in open_rows if not r["tests"]),
                           "test_failed": sum(1 for r in open_rows if any(t.get("passed") is False for t in r["tests"])),
                           "vulnerable_groups": sum(1 for r in rows if r["affects_vulnerable_groups"])},
                "scope": ("The register as recorded in this ledger: each entry is signed and chained, and a risk "
                          "is a history, not a row. Whether the risks are the right ones and the residual "
                          "judgement sound is the provider's assessment; this is the evidence of it.")}

    # ------------------------------------------------------------------ Art. 72: post-market monitoring
    def post_market_report(self, since: float, until: float | None = None, actor: str | None = None,
                           plan: dict | None = None, note: str | None = None) -> dict:
        """The periodic report of a post-market monitoring system (EU AI Act Art. 72(2)): what the
        ledgers show about the agent's performance over one period, gathered and analysed here rather
        than asserted. It counts actions and error actions, oversight events by type (refusals and
        overrides are the operator's own signal that outputs needed correction), incidents opened and
        their reporting clocks, erasures and rights requests, retention attestations, lifecycle
        events, and risks added from post-market data (Art. 9(2)(c)). `plan` is the operator's
        monitoring plan (Art. 72(3): part of the Annex IV documentation; the Commission's template is
        pending) and is carried by reference: its name, version and content hash, never its text.

        With `actor`, the report is ALSO appended to the ledger as a signed `monitoring` entry, so
        the fact that monitoring happened is itself evidence; without an actor it is read-only."""
        until = time.time() if until is None else float(until)
        since = float(since)
        if until <= since:
            raise ValueError("until must be after since")
        inside = [e for e in self._entries if since <= float(e.get("ts") or 0) < until]

        def kind(k):
            return [e for e in inside if e.get("kind", "action") == k]
        actions = kind("action")
        overs = kind("oversight")
        incidents = kind("incident")
        risks = kind("risk")
        rights = kind("rights")
        report = {
            "kind": "inspeximus.post_market_report/1", "period": {"since": since, "until": until},
            "actions": {"total": len(actions),
                        "error": sum(1 for e in actions if e.get("status") not in (None, "ok")),
                        "by_action": _count_by(actions, "action")},
            "oversight": {"total": len(overs), "by_event": _count_by(overs, "event"),
                          "refusal_or_override_rate": (round(sum(1 for e in overs if e.get("event") in ("refuse", "override"))
                                                             / len(actions), 4) if actions else None)},
            "incidents": {"opened": len(incidents), "by_severity": _count_by(incidents, "severity"),
                          "overdue": [e["seq"] for e in incidents
                                      if e.get("report_deadline_ts") and not e.get("reported_ts")
                                      and float(e["report_deadline_ts"]) < until]},
            "rights_requests": _count_by(rights, "action"),
            "risks": {"recorded": len(risks), "from_post_market": sum(1 for e in risks if e.get("source") == "post_market"),
                      "residual_not_acceptable": sum(1 for e in risks if e.get("residual_acceptable") is False)},
            "retention_attestations": len(kind("retention")),
            "lifecycle": _count_by(kind("lifecycle"), "event"),
            "disclosures": len(kind("disclosure")),
            "corrective_actions": _count_by(kind("corrective"), "corrective_kind"),
            "authority_requests": len(kind("authority")),
            "breaches": {"opened": sum(1 for e in kind("breach") if not e.get("event")),
                         "notified_late": sum(1 for e in kind("breach") if e.get("late"))},
            "literacy_measures": len(kind("literacy")),
            "attestations": {"entries": len(kind("attestation")),
                             "classes_attested": len({e.get("practice") for e in kind("attestation")})},
            "responsibilities_agreements": len(kind("responsibilities")),
            "declarations": len(kind("declaration")),
            "documentation_attestations": len(kind("documentation")),
            "notices": len(kind("notice")),
            "processing_roles": len(kind("processing_role")),
            "qms_procedures": len({e.get("procedure") for e in kind("qms")}),
            "objections": {"recorded": sum(1 for e in kind("rights") if e.get("event") == "objection"),
                           "resolved": sum(1 for e in kind("rights") if e.get("event") == "objection_resolved")},
            "chain": _chain_state(self),
            "requirements": {"Art. 9": "risks", "Art. 12": "actions, chain", "Art. 14": "oversight",
                             "Art. 15": "chain, incidents", "Art. 13": "disclosures", "Art. 73": "incidents",
                             "Art. 20": "corrective_actions", "Art. 21": "authority_requests",
                             "GDPR Art. 33": "breaches", "Art. 4": "literacy_measures", "Art. 5": "attestations",
                             "Art. 25": "responsibilities_agreements", "Art. 47": "declarations",
                             "Art. 18": "documentation_attestations", "GDPR Art. 13, 14": "notices",
                             "GDPR Art. 21": "objections", "GDPR Art. 28": "processing_roles",
                             "Art. 17": "qms_procedures"},
            "store": {"records": len(list(self.store.items)) if self.store is not None else None,
                      "tombstones": len(getattr(self.store, "_tombstones", None) or []) if self.store is not None else None},
            "plan": ({"name": plan.get("name"), "version": plan.get("version"),
                      "sha256": _content_hash(plan)} if plan else None),
            "note": note,
            "scope": ("Counts from this ledger and store for the period, read through their verifiers. Not a "
                      "judgement of continuous compliance; the material for one. Interaction with other AI "
                      "systems is visible only where those systems wrote into this ledger."),
        }
        if actor:
            extra = {"since": since, "until": until, "summary_sha256": _content_hash(report),
                     "plan": report["plan"], "note": note,
                     "counts": {"actions": report["actions"]["total"], "incidents": report["incidents"]["opened"],
                                "oversight": report["oversight"]["total"], "risks": report["risks"]["recorded"]}}
            entry = self.record("monitoring:post_market", inputs={"since": since, "until": until}, status="ok",
                                actor=actor, kind="monitoring", extra=extra)
            report["ledger_seq"] = entry["seq"]
        return report

    # ------------------------------------------------------------------ Art. 20: corrective actions
    def corrective_action(self, kind: str, actor: str, non_conformity: str, refers_to: list | None = None,
                          informed: list | None = None, causes: str | None = None,
                          presents_risk: bool = False, meta: dict | None = None) -> dict:
        """Record a corrective action (EU AI Act Art. 20): what was done with a system the provider has
        reason to consider non-conforming (bring into conformity, withdraw, disable, recall: 20(1)), the
        non-conformity itself, and who was informed and when (distributors, deployers, the authorised
        representative, importers: 20(1); the market surveillance authority and the notified body when
        the system presents a risk under Art. 79(1): 20(2)). `causes` is the 20(2) investigation.
        `refers_to` lists the ledger entries that are the evidence (an incident, a risk, a monitoring
        report) and each must resolve. `informed` is a list of {party, ts, how}; a party outside the
        Art. 20 list is refused. Nothing here judges whether the right parties were informed: the
        report names which of them were and which were not."""
        if kind not in CORRECTIVE_ACTIONS:
            raise ValueError(f"kind must be one of {CORRECTIVE_ACTIONS}")
        if not actor or not non_conformity:
            raise ValueError("a corrective action needs an actor and the non-conformity it corrects")
        told = []
        for p in (informed or []):
            if not isinstance(p, dict) or p.get("party") not in INFORMED_PARTIES:
                raise ValueError(f"each informed party needs a party in {INFORMED_PARTIES}")
            told.append({"party": p["party"], "ts": float(p.get("ts") or time.time()), "how": p.get("how")})
        refs = [self._resolve_ref(r) for r in (refers_to or [])]
        extra = {"corrective_kind": kind, "non_conformity": str(non_conformity)[:2000], "causes": causes,
                 "informed": told, "presents_risk": bool(presents_risk), "evidence": refs}
        return self.record(f"corrective:{kind}", inputs={"non_conformity": str(non_conformity)[:200]},
                           status="ok", actor=actor, meta=meta, kind="corrective", extra=extra)

    def corrective_action_report(self, seq: int) -> dict:
        """The Art. 20 record for corrective action `seq`: the non-conformity, the action, the causes,
        the parties informed with their dates, the Art. 20 parties not informed, whether the authority
        was informed when the system presented a risk, the evidence entries, and later entries that
        refer to this one. Read-only."""
        e = self._at(seq)
        if e.get("kind") != "corrective":
            raise ValueError(f"entry {seq} is a {e.get('kind', 'action')}, not a corrective action")
        told = {p["party"]: p for p in (e.get("informed") or [])}
        evidence = []
        for ref in e.get("evidence") or []:
            x = self._at(ref["seq"])
            evidence.append({"seq": x["seq"], "kind": x.get("kind", "action"), "action": x.get("action"),
                             "ts": x.get("ts"), "hash": x.get("hash")})
        later = [{"seq": u["seq"], "kind": u.get("kind", "action"), "action": u.get("action"), "ts": u.get("ts")}
                 for u in self._entries if u.get("seq", -1) > seq
                 and any((r or {}).get("seq") == seq for r in
                         ([u.get("refers_to")] if isinstance(u.get("refers_to"), dict) else list(u.get("evidence") or [])))]
        return {"kind": "inspeximus.corrective_action_report/1", "seq": seq, "ts": e.get("ts"),
                "actor": e.get("actor"), "action": e.get("corrective_kind"),
                "non_conformity": e.get("non_conformity"), "causes": e.get("causes"),
                "presents_risk": bool(e.get("presents_risk")),
                "informed": list(told.values()),
                "not_informed": [p for p in INFORMED_PARTIES if p not in told],
                "authority_informed": ("market_surveillance_authority" in told) if e.get("presents_risk") else None,
                "evidence": evidence, "later_entries": later, "hash": e.get("hash"), "signed": "sig" in e,
                "scope": ("What this ledger records about the action. Which parties had to be informed is "
                          "'as applicable' under Art. 20(1); the list of those not informed is for the "
                          "provider to answer, not a finding.")}

    # ------------------------------------------------------------------ Art. 21: cooperation with authorities
    def authority_request(self, authority: str, reference: str, actor: str, scope: str,
                          received_ts: float | None = None, provided: list | None = None,
                          provided_ts: float | None = None, language: str | None = None,
                          note: str | None = None, meta: dict | None = None) -> dict:
        """Record a reasoned request from a competent authority (EU AI Act Art. 21) and what was handed
        over: `scope` is documentation (21(1)), logs (21(2)) or both; `provided` lists {item, sha256}
        references to what was given (an audit bundle, an export trail, a technical documentation
        file), never the content, because 21(3) puts what the authority receives under Art. 78
        confidentiality and the ledger is not the place for it. `language` is the 21(1) language."""
        if scope not in AUTHORITY_REQUEST_SCOPES:
            raise ValueError(f"scope must be one of {AUTHORITY_REQUEST_SCOPES}")
        if not authority or not reference or not actor:
            raise ValueError("an authority request needs the authority, its reference and an actor")
        items = []
        for it in (provided or []):
            if not isinstance(it, dict) or not it.get("item"):
                raise ValueError("each provided item needs an item name, and a sha256 where it is a file")
            items.append({"item": str(it["item"]), "sha256": it.get("sha256")})
        extra = {"authority": authority, "reference": reference, "scope": scope,
                 "received_ts": float(received_ts) if received_ts is not None else time.time(),
                 "provided": items, "provided_ts": float(provided_ts) if provided_ts is not None else None,
                 "language": language, "note": note}
        return self.record(f"authority:{scope}", inputs={"authority": authority, "reference": reference},
                           status="ok", actor=actor, meta=meta, kind="authority", extra=extra)

    def authority_requests(self) -> list[dict]:
        """Every Art. 21 request recorded, with what was provided and when. Read-only."""
        return [{"seq": e["seq"], "ts": e.get("ts"), "authority": e.get("authority"),
                 "reference": e.get("reference"), "scope": e.get("scope"), "received_ts": e.get("received_ts"),
                 "provided": e.get("provided") or [], "provided_ts": e.get("provided_ts"),
                 "language": e.get("language"), "actor": e.get("actor"), "signed": "sig" in e}
                for e in self._entries if e.get("kind") == "authority"]

    # ------------------------------------------------------------------ Art. 86: explanation of a decision
    def decision_explanation(self, seq: int, actor: str | None = None, subject: str | None = None,
                             request_id: str | None = None) -> dict:
        """The material for an Art. 86 explanation of the decision recorded at action `seq`: the role of
        the AI system (the action, its model and principal, the memory state it acted on and what recall
        returned, with each returned record's provenance as it stands now), the oversight events on
        that action, the disclosures in its session, and the incidents, risks and corrective actions
        that refer to it. In one document, from the chain, so the deployer's explanation to the person
        rests on records rather than recollection. With `actor` the fact that an explanation was
        produced is appended as a `rights:explanation` entry carrying the document's hash, the
        subject reference and the request id; without it, read-only."""
        e = self._at(seq)
        if e.get("kind", "action") != "action":
            raise ValueError(f"entry {seq} is a {e.get('kind')}, not an action")
        knew = self.what_it_knew(seq)

        def refers(u):
            refs = [u.get("refers_to")] if isinstance(u.get("refers_to"), dict) else list(u.get("evidence") or [])
            return any((r or {}).get("seq") == seq for r in refs)
        oversight = [{"seq": u["seq"], "event": u.get("event"), "actor": u.get("actor"), "ts": u.get("ts"),
                      "reason": u.get("reason")} for u in self._entries if u.get("kind") == "oversight" and refers(u)]
        referring = [{"seq": u["seq"], "kind": u.get("kind"), "action": u.get("action"), "ts": u.get("ts")}
                     for u in self._entries if u.get("kind") in ("incident", "risk", "corrective") and refers(u)]
        session = e.get("session")
        disclosures = [{"seq": u["seq"], "ts": u.get("ts"), "disclosure_kind": u.get("disclosure_kind"),
                        "channel": u.get("channel")} for u in self._entries
                       if u.get("kind") == "disclosure" and session and u.get("session") == session]
        doc = {"kind": "inspeximus.decision_explanation/1", "seq": seq,
               "decision": {"action": e.get("action"), "ts": e.get("ts"), "actor": e.get("actor"),
                            "model": e.get("model"), "principal": e.get("principal"), "session": session,
                            "status": e.get("status"), "error": e.get("error"),
                            "inputs_sha256": e.get("inputs_sha256"), "output_sha256": e.get("output_sha256"),
                            "hash": e.get("hash"), "signed": "sig" in e},
               "role_of_the_system": {"memory_state": knew.get("memory_state"),
                                      "recalled_now": knew.get("recalled_now")},
               "oversight": oversight, "disclosures_in_session": disclosures, "referring_entries": referring,
               "fields_the_deployer_adds": ["the decision as communicated to the person",
                                            "the main elements of the decision (Art. 86(1))",
                                            "the human steps between the output and the decision"],
               "scope": ("What the chain records about the action and what the system knew when it ran. "
                         "The explanation itself, in clear and meaningful terms, is the deployer's; this "
                         "is the evidence it is written from.")}
        if actor:
            extra = {"event": "explanation", "subject": subject, "request_id": request_id,
                     "evidence": [self._resolve_ref(seq)], "manifest_sha256": _content_hash(doc)}
            entry = self.record("rights:explanation", inputs={"seq": seq, "request_id": request_id},
                                status="ok", actor=actor, kind="rights", extra=extra)
            doc["ledger_entry"] = {"seq": entry["seq"], "hash": entry["hash"]}
        return doc

    # ------------------------------------------------------------------ GDPR Art. 33 and 34: breaches
    def breach(self, title: str, actor: str, nature: str, aware_ts: float | None = None,
               subjects_approx: int | None = None, records_approx: int | None = None,
               categories: list | None = None, consequences: str | None = None, measures: str | None = None,
               high_risk: bool | None = None, contact: str | None = None, refers_to: list | None = None,
               meta: dict | None = None) -> dict:
        """Record a personal data breach (GDPR Art. 33) with its 72-hour clock from `aware_ts`, and the
        Art. 33(3) content as far as it is known: the nature of the breach, the categories and
        approximate numbers of subjects and records, the contact point, the likely consequences and
        the measures taken or proposed. Information may come in phases (33(4)): a later `breach_notified`
        or a new entry referring to this one is the phase. `high_risk` is the Art. 34(1) judgement that
        decides whether the subjects must be told. `refers_to` lists the ledger entries that are the
        evidence (an incident, an action) and each must resolve."""
        if not title or not actor or not nature:
            raise ValueError("a breach needs a title, an actor and the nature of the breach")
        refs = [self._resolve_ref(r) for r in (refers_to or [])]
        aware = float(aware_ts) if aware_ts is not None else time.time()
        extra = {"title": title, "nature": str(nature)[:2000], "aware_ts": aware,
                 "notify_deadline_ts": aware + BREACH_DEADLINE_HOURS * 3600,
                 "subjects_approx": subjects_approx, "records_approx": records_approx,
                 "categories": list(categories or []), "consequences": consequences, "measures": measures,
                 "high_risk": high_risk, "contact": contact, "evidence": refs}
        return self.record("breach:opened", inputs={"title": title}, status="ok", actor=actor, meta=meta,
                           kind="breach", extra=extra)

    def breach_notified(self, seq: int, actor: str, to: str, ts: float | None = None,
                        reasons_for_delay: str | None = None, exemption: str | None = None,
                        note: str | None = None) -> dict:
        """Record that breach `seq` was notified: to the supervisory authority (Art. 33(1)), to the data
        subjects (Art. 34(1)), or by a public communication (Art. 34(3)(c)). A notification to the
        authority after the 72 hours must carry `reasons_for_delay` (33(1)). With `exemption` the entry
        records instead why the subjects were not told directly (34(3): protected, mitigated, or
        disproportionate), and `to` must be data_subjects."""
        e = self._at(seq)
        if e.get("kind") != "breach" or e.get("event"):
            raise ValueError(f"entry {seq} is not a breach record")
        if to not in BREACH_NOTIFY_TARGETS:
            raise ValueError(f"to must be one of {BREACH_NOTIFY_TARGETS}")
        if not actor:
            raise ValueError("breach_notified needs an actor")
        when = float(ts) if ts is not None else time.time()
        if exemption is not None:
            if exemption not in BREACH_EXEMPTIONS or to != "data_subjects":
                raise ValueError(f"an exemption is one of {BREACH_EXEMPTIONS} and applies to data_subjects only")
            event = "subjects_exempt"
        else:
            event = "notified"
            if to == "supervisory_authority" and when > float(e["notify_deadline_ts"]) and not reasons_for_delay:
                raise ValueError("a notification to the supervisory authority after 72 hours needs reasons_for_delay (Art. 33(1))")
        extra = {"title": e.get("title"), "event": event, "to": to, "notified_ts": when,
                 "late": bool(to == "supervisory_authority" and when > float(e["notify_deadline_ts"])),
                 "reasons_for_delay": reasons_for_delay, "exemption": exemption,
                 "evidence": [self._resolve_ref(seq)], "aware_ts": e.get("aware_ts"),
                 "notify_deadline_ts": e.get("notify_deadline_ts")}
        if note:
            extra["note"] = str(note)[:2000]
        return self.record(f"breach:{event}", inputs={"seq": seq, "to": to}, status="ok", actor=actor,
                           kind="breach", extra=extra)

    def breach_report(self, seq: int, now: float | None = None) -> dict:
        """The Art. 33 and 34 record for breach `seq`: the 33(3) content, the 72-hour clock and whether
        the authority was notified in time (with the reasons given if not), the subject communication
        or the 34(3) exemption, the documentation 33(5) asks for (facts, effects, remedial action), the
        evidence entries, and the fields the controller must add. Read-only."""
        e = self._at(seq)
        if e.get("kind") != "breach" or e.get("event"):
            raise ValueError(f"entry {seq} is not a breach record")
        now = time.time() if now is None else now
        updates = [u for u in self._entries if u.get("kind") == "breach" and u.get("event")
                   and any((r or {}).get("seq") == seq for r in (u.get("evidence") or []))]
        authority = next((u for u in updates if u.get("to") == "supervisory_authority"), None)
        subjects = next((u for u in updates if u.get("to") in ("data_subjects", "public")), None)
        deadline = float(e["notify_deadline_ts"])
        evidence = []
        for ref in e.get("evidence") or []:
            x = self._at(ref["seq"])
            evidence.append({"seq": x["seq"], "kind": x.get("kind", "action"), "action": x.get("action"),
                             "ts": x.get("ts"), "hash": x.get("hash")})
        return {"kind": "inspeximus.breach_report/1", "seq": seq, "title": e.get("title"), "actor": e.get("actor"),
                "aware_ts": e.get("aware_ts"), "notify_deadline_ts": deadline,
                "article_33_3": {"nature": e.get("nature"), "categories": e.get("categories") or [],
                                 "subjects_approx": e.get("subjects_approx"), "records_approx": e.get("records_approx"),
                                 "contact": e.get("contact"), "consequences": e.get("consequences"),
                                 "measures": e.get("measures")},
                "authority": ({"notified_ts": authority.get("notified_ts"), "late": authority.get("late"),
                               "reasons_for_delay": authority.get("reasons_for_delay"), "seq": authority["seq"]}
                              if authority else {"notified_ts": None, "overdue": now > deadline,
                                                 "hours_left": round((deadline - now) / 3600, 1)}),
                "subjects": ({"event": subjects.get("event"), "to": subjects.get("to"), "ts": subjects.get("notified_ts"),
                              "exemption": subjects.get("exemption"), "seq": subjects["seq"]}
                             if subjects else {"event": None, "high_risk": e.get("high_risk"),
                                               "required": bool(e.get("high_risk"))}),
                "documentation_33_5": {"facts": e.get("nature"), "effects": e.get("consequences"),
                                       "remedial_action": e.get("measures"), "updates": len(updates)},
                "evidence": evidence, "hash": e.get("hash"), "signed": "sig" in e,
                "fields_the_controller_adds": ["the supervisory authority competent under Art. 55",
                                               "the assessment of risk to rights and freedoms (33(1), 34(1))",
                                               "the wording of the communication to the subjects (34(2))"],
                "scope": ("What this ledger records about the breach and its clock. Whether the breach was "
                          "unlikely to result in a risk, and whether the risk to the subjects is high, are "
                          "the controller's assessments; this is the evidence of when they were made.")}

    # ------------------------------------------------------------------ Art. 4: AI literacy
    def record_literacy(self, actor: str, measure: str, audience: str, description: str,
                        ts: float | None = None, system: str | None = None, context: str | None = None,
                        considered: list | None = None, persons_affected: list | None = None,
                        refers_to: list | None = None, meta: dict | None = None) -> dict:
        """Record one AI-literacy measure (EU AI Act Art. 4, as amended): what was done (`measure`), for
        whom (`audience`: staff, or other persons operating or using the system on the operator's behalf),
        when, for which system and context of use, and which of the Art. 4 considerations it took into
        account (technical knowledge, experience, education, training, the context, the persons the system
        is used on). The article requires measures, not a level of literacy for any individual, so the
        record names the measure and never scores a person. `refers_to` links the material (a documentation
        entry, an instructions-for-use export) and each reference must resolve."""
        if measure not in LITERACY_MEASURES:
            raise ValueError(f"measure must be one of {LITERACY_MEASURES}")
        if audience not in LITERACY_AUDIENCES:
            raise ValueError(f"audience must be one of {LITERACY_AUDIENCES}")
        if not actor or not description:
            raise ValueError("a literacy record needs an actor and a description of the measure")
        bad = [c for c in (considered or []) if c not in LITERACY_CONSIDERATIONS]
        if bad:
            raise ValueError(f"considered must be among {LITERACY_CONSIDERATIONS}, got {bad}")
        refs = [self._resolve_ref(r) for r in (refers_to or [])]
        extra = {"literacy_measure": measure, "audience": audience, "description": str(description)[:2000],
                 "delivered_ts": float(ts) if ts is not None else time.time(), "system": system,
                 "context": context, "considered": list(considered or []),
                 "persons_affected": [str(x) for x in (persons_affected or [])], "material": refs}
        return self.record(f"literacy:{measure}", inputs={"audience": audience}, status="ok", actor=actor,
                           meta=meta, kind="literacy", extra=extra)

    def literacy_register(self) -> dict:
        """Every Art. 4 measure recorded, with counts by audience and by measure. Read-only."""
        ev = [e for e in self._entries if e.get("kind") == "literacy"]
        return {"kind": "inspeximus.literacy_register/1", "measures": len(ev),
                "by_audience": _count_by(ev, "audience"), "by_measure": _count_by(ev, "literacy_measure"),
                "entries": [{"seq": e["seq"], "ts": e.get("ts"), "delivered_ts": e.get("delivered_ts"),
                             "measure": e.get("literacy_measure"), "audience": e.get("audience"),
                             "system": e.get("system"), "considered": e.get("considered") or [],
                             "actor": e.get("actor"), "signed": "sig" in e} for e in ev],
                "scope": ("The measures this ledger records. Art. 4 asks for measures taken, not for a level "
                          "of literacy reached; whether the measures were sufficient is the operator's judgement.")}

    # ------------------------------------------------------------------ Art. 5: prohibited practices
    def record_attestation(self, actor: str, practice: str, statement: str, basis: str | None = None,
                           ts: float | None = None, system: str | None = None, meta: dict | None = None) -> dict:
        """Attest, for one Art. 5(1) class, that the system is not used for that practice (`not_used`) or
        that the class cannot arise in it (`not_applicable`, which needs a `basis`: what about the system
        rules it out). One entry per class per attestation date; the register shows the latest for each of
        the ten classes and names the classes with none. The attestation is the operator's statement,
        signed when the ledger signs; nothing here inspects the system."""
        if practice not in PROHIBITED_PRACTICES:
            raise ValueError(f"practice must be one of {sorted(PROHIBITED_PRACTICES)}")
        if statement not in ATTESTATION_STATEMENTS:
            raise ValueError(f"statement must be one of {ATTESTATION_STATEMENTS}")
        if not actor:
            raise ValueError("an attestation needs an actor")
        if statement == "not_applicable" and not basis:
            raise ValueError("a not_applicable attestation needs its basis: what about the system rules the class out")
        extra = {"practice": practice, "practice_text": PROHIBITED_PRACTICES[practice], "statement": statement,
                 "basis": (str(basis)[:2000] if basis else None),
                 "attested_ts": float(ts) if ts is not None else time.time(), "system": system}
        return self.record(f"attestation:{practice}", inputs={"statement": statement}, status="ok", actor=actor,
                           meta=meta, kind="attestation", extra=extra)

    def attestation_register(self) -> dict:
        """The latest Art. 5 attestation per prohibited-practice class, and the classes with none. Read-only."""
        latest: dict = {}
        for e in self._entries:
            if e.get("kind") == "attestation":
                latest[e["practice"]] = e
        rows = []
        for code, text in PROHIBITED_PRACTICES.items():
            e = latest.get(code)
            rows.append({"practice": code, "text": text,
                         "statement": e.get("statement") if e else None, "basis": e.get("basis") if e else None,
                         "attested_ts": e.get("attested_ts") if e else None, "seq": e["seq"] if e else None,
                         "actor": e.get("actor") if e else None, "signed": ("sig" in e) if e else False})
        return {"kind": "inspeximus.attestation_register/1", "attested": len(latest),
                "missing": [c for c in PROHIBITED_PRACTICES if c not in latest], "rows": rows,
                "scope": ("The operator's own attestations, dated and signed. Whether a practice is in fact "
                          "absent is not something a ledger can see; the register shows what was attested and "
                          "when, and which classes were never attested.")}

    # ------------------------------------------------------------------ Art. 25: responsibilities along the value chain
    def record_responsibilities(self, actor: str, agreement_ref: str, parties: list, ts: float | None = None,
                                agreement_sha256: str | None = None, trigger: str | None = None,
                                cooperation: dict | None = None, not_to_be_changed_into_high_risk: bool = False,
                                system: str | None = None, meta: dict | None = None) -> dict:
        """Record who carries which obligations along the value chain (EU AI Act Art. 25). `parties` is a
        list of {party, role, obligations}; exactly the roles of Art. 25 are allowed and at least one party
        must carry the provider's obligations (provider, new_provider or product_manufacturer). `trigger`
        is why a party became the provider (25(1)(a) name or trademark, (b) substantial modification,
        (c) changed intended purpose). `cooperation` records the 25(2) items the initial provider made
        available (technical_documentation, known_limitations_and_failure_modes, targeted_technical_access)
        as {item: reference}; `not_to_be_changed_into_high_risk` is the 25(2) opt-out, which excludes the
        cooperation duty, so the two are refused together. `agreement_ref` names the 25(4) written
        agreement and `agreement_sha256` pins its bytes."""
        if not actor or not agreement_ref:
            raise ValueError("a responsibilities record needs an actor and the written agreement it records")
        if not parties:
            raise ValueError("at least one party is needed")
        rows = []
        for p in parties:
            if not isinstance(p, dict) or not p.get("party") or p.get("role") not in RESPONSIBILITY_ROLES:
                raise ValueError(f"each party needs a name and a role in {RESPONSIBILITY_ROLES}")
            rows.append({"party": str(p["party"]), "role": p["role"],
                         "obligations": [str(o) for o in (p.get("obligations") or [])]})
        if not any(r["role"] in ("provider", "new_provider", "product_manufacturer") for r in rows):
            raise ValueError("no party carries the provider's obligations; Art. 25 exists to say who does")
        if trigger is not None and trigger not in PROVIDER_TRIGGERS:
            raise ValueError(f"trigger must be one of {PROVIDER_TRIGGERS}")
        coop = {}
        for k, v in (cooperation or {}).items():
            if k not in COOPERATION_ITEMS:
                raise ValueError(f"cooperation items are {COOPERATION_ITEMS}")
            coop[k] = str(v)
        if coop and not_to_be_changed_into_high_risk:
            raise ValueError("the 25(2) opt-out and cooperation items exclude each other")
        extra = {"agreement_ref": str(agreement_ref), "agreement_sha256": agreement_sha256, "parties": rows,
                 "trigger": trigger, "cooperation": coop,
                 "not_to_be_changed_into_high_risk": bool(not_to_be_changed_into_high_risk),
                 "agreed_ts": float(ts) if ts is not None else time.time(), "system": system}
        return self.record("responsibilities:agreement", inputs={"agreement": str(agreement_ref)}, status="ok",
                           actor=actor, meta=meta, kind="responsibilities", extra=extra)

    def responsibilities_register(self) -> dict:
        """Every Art. 25 record: the agreement, the parties and roles, the trigger, the 25(2) items. Read-only."""
        ev = [e for e in self._entries if e.get("kind") == "responsibilities"]
        return {"kind": "inspeximus.responsibilities_register/1", "agreements": len(ev),
                "entries": [{"seq": e["seq"], "ts": e.get("ts"), "agreement_ref": e.get("agreement_ref"),
                             "agreement_sha256": e.get("agreement_sha256"), "parties": e.get("parties") or [],
                             "trigger": e.get("trigger"), "cooperation": e.get("cooperation") or {},
                             "not_to_be_changed_into_high_risk": bool(e.get("not_to_be_changed_into_high_risk")),
                             "actor": e.get("actor"), "signed": "sig" in e} for e in ev],
                "scope": ("Who the parties said carries what. Whether a modification was substantial, and so "
                          "whether the trigger applies, is a finding under Art. 3(23), not a ledger field.")}

    # ------------------------------------------------------------------ Art. 43, 47, 48: declaration of conformity
    def record_declaration(self, actor: str, system_name: str, system_type: str, system_reference: str,
                           provider_name: str, provider_address: str, conformity_procedure: str, place: str,
                           signer_name: str, signer_function: str, signed_for: str,
                           issue_ts: float | None = None, annex_iv_sha256: str | None = None,
                           personal_data: bool = False, harmonised_standards: list | None = None,
                           common_specifications: list | None = None, notified_body: dict | None = None,
                           other_union_law: list | None = None, ce_marking: dict | None = None,
                           authorised_representative: dict | None = None, meta: dict | None = None) -> dict:
        """Record an EU declaration of conformity (Art. 47) with every Annex V item: (1) the system's name,
        type and unambiguous reference; (2) the provider's name and address, or the authorised
        representative's; (3) that it is issued under the provider's sole responsibility; (4) that the system
        conforms to the Regulation and, where applicable, other Union law (47(3)); (5) where personal data is
        processed, that it complies with the GDPR, Regulation 2018/1725 and Directive 2016/680; (6) the
        harmonised standards or common specifications used; (7) where applicable, the notified body, the
        procedure and the certificate; (8) the place and date of issue and who signed, in what function, for
        whom. `conformity_procedure` is the Art. 43 choice; Annex VII requires a notified body with an
        identification number, which Art. 48(4) then puts after the CE marking. `annex_iv_sha256` pins the
        technical documentation the declaration rests on. The assessment itself is the provider's; this is
        the declaration as drawn up, machine readable (47(1))."""
        req = {"actor": actor, "system_name": system_name, "system_type": system_type,
               "system_reference": system_reference, "provider_name": provider_name,
               "provider_address": provider_address, "place": place, "signer_name": signer_name,
               "signer_function": signer_function, "signed_for": signed_for}
        missing = [k for k, v in req.items() if not v]
        if missing:
            raise ValueError(f"a declaration needs {missing} (Annex V items 1, 2 and 8)")
        if conformity_procedure not in CONFORMITY_PROCEDURES:
            raise ValueError(f"conformity_procedure must be one of {CONFORMITY_PROCEDURES}")
        nb = None
        if conformity_procedure == "annex_vii_notified_body":
            if not isinstance(notified_body, dict) or not notified_body.get("name") or not notified_body.get("id"):
                raise ValueError("an Annex VII assessment needs the notified body's name and identification number (Annex V item 7, Art. 48(4))")
            nb = {"name": str(notified_body["name"]), "id": str(notified_body["id"]),
                  "procedure": str(notified_body.get("procedure") or "Annex VII"),
                  "certificate": notified_body.get("certificate")}
        elif notified_body:
            raise ValueError("a notified body is recorded only for an Annex VII assessment")
        if annex_iv_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", str(annex_iv_sha256)):
            raise ValueError("annex_iv_sha256 must be a 64-hex sha256 of the technical documentation")
        ce = None
        if ce_marking is not None:
            if not isinstance(ce_marking, dict):
                raise ValueError("ce_marking is a dict: {digital_access, affixed_to, notified_body_id}")
            ce = {"digital_access": ce_marking.get("digital_access"), "affixed_to": ce_marking.get("affixed_to"),
                  "notified_body_id": ce_marking.get("notified_body_id")}
            if nb and ce.get("notified_body_id") and ce["notified_body_id"] != nb["id"]:
                raise ValueError("the notified body id after the CE marking (Art. 48(4)) must be the assessing body's")
        extra = {"system_name": str(system_name), "system_type": str(system_type),
                 "system_reference": str(system_reference), "provider_name": str(provider_name),
                 "provider_address": str(provider_address),
                 "authorised_representative": (dict(authorised_representative) if authorised_representative else None),
                 "sole_responsibility": True, "conforms": True,
                 "other_union_law": [str(x) for x in (other_union_law or [])],
                 "personal_data": bool(personal_data),
                 "data_protection_statement": bool(personal_data),
                 "harmonised_standards": [str(x) for x in (harmonised_standards or [])],
                 "common_specifications": [str(x) for x in (common_specifications or [])],
                 "conformity_procedure": conformity_procedure, "notified_body": nb, "ce_marking": ce,
                 "place": str(place), "issue_ts": float(issue_ts) if issue_ts is not None else time.time(),
                 "signer": {"name": str(signer_name), "function": str(signer_function), "for": str(signed_for)},
                 "annex_iv_sha256": annex_iv_sha256}
        return self.record("declaration:eu", inputs={"system": str(system_reference)}, status="ok", actor=actor,
                           meta=meta, kind="declaration", extra=extra)

    def declaration_document(self, seq: int) -> dict:
        """The declaration at `seq` as one machine-readable document with the Annex V items in order,
        the Art. 43 procedure, the Art. 48 marking, and the ledger hash that binds it. Read-only."""
        e = self._at(seq)
        if e.get("kind") != "declaration":
            raise ValueError(f"entry {seq} is a {e.get('kind', 'action')}, not a declaration")
        items = {
            "1_system": {"name": e.get("system_name"), "type": e.get("system_type"), "reference": e.get("system_reference")},
            "2_provider": {"name": e.get("provider_name"), "address": e.get("provider_address"),
                           "authorised_representative": e.get("authorised_representative")},
            "3_sole_responsibility": "This EU declaration of conformity is issued under the sole responsibility of the provider.",
            "4_conformity": ("The AI system identified above is in conformity with Regulation (EU) 2024/1689"
                             + (" and with " + ", ".join(e.get("other_union_law") or []) if e.get("other_union_law") else "") + "."),
            "5_personal_data": ("The AI system complies with Regulations (EU) 2016/679 and (EU) 2018/1725 and Directive (EU) 2016/680."
                                if e.get("personal_data") else "not applicable: the system does not process personal data, as declared"),
            "6_standards": {"harmonised_standards": e.get("harmonised_standards") or [],
                            "common_specifications": e.get("common_specifications") or []},
            "7_notified_body": e.get("notified_body") or "not applicable: Annex VI internal control",
            "8_signature": {"place": e.get("place"), "issue_ts": e.get("issue_ts"), **(e.get("signer") or {})},
        }
        return {"kind": "inspeximus.eu_declaration_of_conformity/1", "seq": seq, "ts": e.get("ts"),
                "annex_v": items, "conformity_procedure": e.get("conformity_procedure"),
                "ce_marking": e.get("ce_marking"), "technical_documentation_sha256": e.get("annex_iv_sha256"),
                "hash": e.get("hash"), "signed": "sig" in e,
                "scope": ("The declaration as the provider drew it up, in the Annex V order. The assessment behind "
                          "it (Art. 43) is the provider's or the notified body's; this document is what Art. 47(1) "
                          "asks to be kept and handed over on request.")}

    # ------------------------------------------------------------------ GDPR Art. 13, 14: information to the subject
    def record_notice(self, actor: str, subject: str, channel: str, items: list, article: int = 13,
                      ts: float | None = None, text_sha256: str | None = None, source: str | None = None,
                      timing: str | None = None, request_id: str | None = None, meta: dict | None = None) -> dict:
        """Record that `subject` was given the Art. 13 (data collected from them) or Art. 14 (data obtained
        elsewhere) information: on which `channel`, when, and which of the article's items the notice
        carried; the entry lists the items it did NOT carry as `missing`, so a partial notice is visible
        rather than judged. `text_sha256` pins the notice text without storing it. Art. 14 additionally
        needs `source` (14(2)(f)) and `timing` (14(3)). The shape is the Art. 50 disclosure receipt."""
        if article not in (13, 14):
            raise ValueError("article must be 13 (collected from the subject) or 14 (obtained elsewhere)")
        if channel not in NOTICE_CHANNELS:
            raise ValueError(f"channel must be one of {NOTICE_CHANNELS}")
        if not actor or not subject:
            raise ValueError("a notice record needs the actor and the subject reference")
        allowed = set(NOTICE_ITEMS) | (set(NOTICE_ART14_ITEMS) if article == 14 else set())
        bad = [i for i in (items or []) if i not in allowed]
        if bad:
            raise ValueError(f"items must be among {sorted(allowed)}, got {bad}")
        if text_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", str(text_sha256)):
            raise ValueError("text_sha256 must be a 64-hex sha256 of the notice text")
        if article == 14:
            if not source:
                raise ValueError("an Art. 14 notice names the source the data came from (14(2)(f))")
            if timing not in NOTICE_TIMINGS:
                raise ValueError(f"an Art. 14 notice needs its timing (14(3)): one of {NOTICE_TIMINGS}")
        elif timing is not None and timing != "at_collection":
            raise ValueError("an Art. 13 notice is given at collection")
        given = sorted(set(items or []))
        extra = {"article": article, "subject": subject, "channel": channel, "items": given,
                 "missing": [i for i in allowed if i not in given],
                 "given_ts": float(ts) if ts is not None else time.time(), "text_sha256": text_sha256,
                 "source": source, "timing": timing or ("at_collection" if article == 13 else None),
                 "request_id": request_id}
        return self.record(f"notice:art{article}", inputs={"subject": subject, "channel": channel}, status="ok",
                           actor=actor, meta=meta, kind="notice", extra=extra)

    def notice_register(self) -> dict:
        """The latest Art. 13 or 14 notice per subject, and the items every notice left out. Read-only."""
        latest: dict = {}
        for e in self._entries:
            if e.get("kind") == "notice":
                latest[e.get("subject")] = e
        rows = [{"subject": s, "seq": e["seq"], "article": e.get("article"), "channel": e.get("channel"),
                 "given_ts": e.get("given_ts"), "items": e.get("items") or [], "missing": e.get("missing") or [],
                 "actor": e.get("actor"), "signed": "sig" in e} for s, e in latest.items()]
        return {"kind": "inspeximus.notice_register/1", "subjects": len(rows),
                "incomplete": sorted(r["subject"] for r in rows if r["missing"]), "rows": rows,
                "scope": ("What this ledger records was told, to whom and when. Whether the text was clear and "
                          "in plain language (Art. 12(1)) is the controller's judgement; the hash pins which text.")}

    # ------------------------------------------------------------------ GDPR Art. 28: processing role
    def record_processing_role(self, actor: str, role: str, controller: str | None = None,
                               instructions_ref: str | None = None, instructions_sha256: str | None = None,
                               sub_processors: list | None = None, purposes: list | None = None,
                               categories: list | None = None, store_ref: str | None = None,
                               ts: float | None = None, meta: dict | None = None) -> dict:
        """Record who this store's operator is for the personal data in it (GDPR Art. 28): a `controller`
        or `joint_controller`, or a `processor` or `sub_processor` acting for `controller` under the written
        `instructions_ref` (the 28(3) contract, pinned by `instructions_sha256`). `sub_processors` lists
        {name, authorised_by, authorised_ts} (28(2): no sub-processor without the controller's written
        authorisation, so each row needs one). `purposes` and `categories` describe the processing the
        way an Art. 30(2) processor record does."""
        if role not in PROCESSING_ROLES:
            raise ValueError(f"role must be one of {PROCESSING_ROLES}")
        if not actor:
            raise ValueError("a processing-role record needs an actor")
        if role in ("processor", "sub_processor"):
            if not controller:
                raise ValueError(f"a {role} names the controller it acts for")
            if not instructions_ref:
                raise ValueError(f"a {role} names the written instructions it acts under (Art. 28(3))")
        if instructions_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", str(instructions_sha256)):
            raise ValueError("instructions_sha256 must be a 64-hex sha256")
        subs = []
        for sp in sub_processors or []:
            if not isinstance(sp, dict) or not sp.get("name") or not sp.get("authorised_by"):
                raise ValueError("each sub-processor needs a name and who authorised it (Art. 28(2))")
            subs.append({"name": str(sp["name"]), "authorised_by": str(sp["authorised_by"]),
                         "authorised_ts": sp.get("authorised_ts")})
        extra = {"role": role, "controller": controller, "instructions_ref": instructions_ref,
                 "instructions_sha256": instructions_sha256, "sub_processors": subs,
                 "purposes": [str(x) for x in (purposes or [])], "categories": [str(x) for x in (categories or [])],
                 "store_ref": store_ref, "declared_ts": float(ts) if ts is not None else time.time()}
        return self.record(f"processing_role:{role}", inputs={"role": role, "controller": controller},
                           status="ok", actor=actor, meta=meta, kind="processing_role", extra=extra)

    def processing_roles(self) -> dict:
        """Every Art. 28 role declaration, latest first, with the current one named. Read-only."""
        ev = [e for e in self._entries if e.get("kind") == "processing_role"]
        rows = [{"seq": e["seq"], "ts": e.get("ts"), "role": e.get("role"), "controller": e.get("controller"),
                 "instructions_ref": e.get("instructions_ref"), "sub_processors": e.get("sub_processors") or [],
                 "purposes": e.get("purposes") or [], "actor": e.get("actor"), "signed": "sig" in e} for e in ev]
        return {"kind": "inspeximus.processing_roles/1", "declarations": len(rows),
                "current": rows[-1] if rows else None, "rows": list(reversed(rows)),
                "scope": ("The operator's own declaration of its role. Whether a party is in fact a controller "
                          "or a processor follows from the facts of the processing (Art. 4(7) and (8)), not "
                          "from what it recorded here.")}

    # ------------------------------------------------------------------ Art. 17: quality management system
    def record_qms(self, actor: str, procedure: str, version: str, owner: str, review_due_ts: float,
                   aspect: str | None = None, ref: str | None = None, sha256: str | None = None,
                   ts: float | None = None, meta: dict | None = None) -> dict:
        """Record one procedure of the provider's quality management system (AI Act Art. 17): its name,
        version, owner, the date its next review is due, the Art. 17(1) aspect it covers (a letter from
        QMS_ASPECTS) and, where the document is a file, a reference and its sha256 so the ledger pins
        which text the entry describes. A later entry for the same procedure is the current one.

        The QMS itself is the provider's; this is the signed record that it exists, who owns it and
        when it was last confirmed current, which is what an Art. 17 review asks for first."""
        if not actor:
            raise ValueError("a qms record needs an actor")
        if not procedure or not str(procedure).strip():
            raise ValueError("a qms record names the procedure it describes")
        if not version or not str(version).strip():
            raise ValueError("a qms record carries the procedure's version")
        if not owner or not str(owner).strip():
            raise ValueError("a qms record names the procedure's owner")
        try:
            due = float(review_due_ts)
        except (TypeError, ValueError):
            raise ValueError("review_due_ts is the epoch time the next review is due")
        if aspect is not None and aspect not in QMS_ASPECTS:
            raise ValueError(f"aspect must be one of the Art. 17(1) letters {sorted(QMS_ASPECTS)}")
        if sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", str(sha256)):
            raise ValueError("sha256 must be a 64-hex sha256")
        extra = {"procedure": str(procedure).strip(), "version": str(version).strip(), "owner": str(owner).strip(),
                 "review_due_ts": due, "aspect": aspect, "ref": ref, "sha256": sha256,
                 "declared_ts": float(ts) if ts is not None else time.time()}
        return self.record(f"qms:{extra['procedure']}", inputs={"procedure": extra["procedure"], "version": extra["version"]},
                           status="ok", actor=actor, meta=meta, kind="qms", extra=extra)

    def qms_register(self, now: float | None = None) -> dict:
        """The current procedure per name (latest entry wins), which ones are overdue for review at
        `now`, and which Art. 17(1) aspects have a current procedure. Read-only."""
        now = time.time() if now is None else float(now)
        ev = [e for e in self._entries if e.get("kind") == "qms"]
        latest: dict = {}
        for e in ev:
            latest[e.get("procedure")] = e
        rows = []
        for name, e in sorted(latest.items()):
            due = e.get("review_due_ts")
            rows.append({"seq": e["seq"], "procedure": name, "version": e.get("version"), "owner": e.get("owner"),
                         "aspect": e.get("aspect"), "review_due_ts": due,
                         "overdue": bool(due is not None and float(due) < now),
                         "ref": e.get("ref"), "sha256": e.get("sha256"), "actor": e.get("actor"),
                         "signed": "sig" in e})
        covered = sorted({r["aspect"] for r in rows if r.get("aspect")})
        return {"kind": "inspeximus.qms_register/1", "procedures": len(rows), "entries": len(ev),
                "overdue": [r["procedure"] for r in rows if r["overdue"]],
                "aspects_covered": covered,
                "aspects_uncovered": sorted(set(QMS_ASPECTS) - set(covered)),
                "rows": rows,
                "scope": ("The provider's own record of its quality management system: which procedures exist, "
                          "who owns them and when their review is due. Whether the system meets Art. 17 is "
                          "assessed against the procedures themselves, not against this register.")}

    # ------------------------------------------------------------------ Art. 18: documentation keeping
    def attest_documentation_retention(self, actor: str, placed_on_market_ts: float, documents: list,
                                       now: float | None = None, declaration_seq: int | None = None,
                                       note: str | None = None, meta: dict | None = None) -> dict:
        """Append a signed statement of which Art. 18(1) documents are at the disposal of the authorities:
        (a) the technical documentation, (b) the quality management system documentation, (c) changes
        approved by notified bodies and (d) their decisions, where applicable, (e) the EU declaration of
        conformity. `documents` is a list of {kind, sha256 or ref, present, not_applicable_reason}. (a) and
        (e) are required present; (c) and (d) may be not applicable with a reason; (b) absent is recorded
        as a gap, not refused. The period ends ten years after `placed_on_market_ts`, and the statement
        carries the end date and whether the attestation falls inside it. `declaration_seq` links the
        declaration entry, like `attest_retention` over logs (Art. 19)."""
        if not actor:
            raise ValueError("attest_documentation_retention needs an actor")
        now = time.time() if now is None else float(now)
        placed = float(placed_on_market_ts)
        seen: dict = {}
        for d in documents or []:
            if not isinstance(d, dict) or d.get("kind") not in RETENTION_DOCUMENTS:
                raise ValueError(f"each document needs a kind in {RETENTION_DOCUMENTS}")
            present = bool(d.get("present", True))
            na = d.get("not_applicable_reason")
            if present and not (d.get("sha256") or d.get("ref")):
                raise ValueError(f"a present {d['kind']} needs a sha256 or a ref an assessor can follow")
            if d.get("sha256") is not None and not re.fullmatch(r"[0-9a-f]{64}", str(d["sha256"])):
                raise ValueError(f"{d['kind']}: sha256 must be 64 hex characters")
            seen[d["kind"]] = {"present": present, "sha256": d.get("sha256"), "ref": d.get("ref"),
                               "not_applicable_reason": (str(na)[:500] if na else None)}
        for k in ("technical_documentation", "eu_declaration_of_conformity"):
            if not seen.get(k, {}).get("present"):
                raise ValueError(f"{k} must be present: Art. 18(1) has no case where it is not applicable")
        for k in ("notified_body_changes", "notified_body_decisions"):
            row = seen.get(k)
            if row is None or (not row["present"] and not row["not_applicable_reason"]):
                raise ValueError(f"{k} is present, or not applicable with the reason (no notified body)")
        decl = self._resolve_ref(declaration_seq) if declaration_seq is not None else None
        if decl is not None and self._at(decl["seq"]).get("kind") != "declaration":
            raise ValueError("declaration_seq must point at a declaration entry")
        end = placed + DOCUMENTATION_RETENTION_YEARS * 365.25 * 86400.0
        extra = {"event": "attest", "placed_on_market_ts": placed, "retention_years": DOCUMENTATION_RETENTION_YEARS,
                 "retention_end_ts": end, "within_period": bool(placed <= now <= end),
                 "years_elapsed": round((now - placed) / (365.25 * 86400.0), 3),
                 "documents": {k: seen.get(k, {"present": False, "sha256": None, "ref": None, "not_applicable_reason": None})
                               for k in RETENTION_DOCUMENTS},
                 "gaps": [k for k in RETENTION_DOCUMENTS
                          if not seen.get(k, {}).get("present") and not seen.get(k, {}).get("not_applicable_reason")],
                 "declaration": decl, "note": (str(note)[:2000] if note else None), "attested_ts": now}
        return self.record("documentation:attest", inputs={"placed": placed}, status="ok", actor=actor,
                           meta=meta, kind="documentation", extra=extra)

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
        self.require_readable()     # else a reference into an unreadable file reads as "not in the ledger"
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
            "lifecycle_events": sum(1 for e in self._entries if e.get("kind") == "lifecycle"),
            "risk_entries": sum(1 for e in self._entries if e.get("kind") == "risk"),
            "monitoring_reports": sum(1 for e in self._entries if e.get("kind") == "monitoring"),
            "corrective_actions": sum(1 for e in self._entries if e.get("kind") == "corrective"),
            "authority_requests": sum(1 for e in self._entries if e.get("kind") == "authority"),
            "breaches": sum(1 for e in self._entries if e.get("kind") == "breach" and not e.get("event")),
            "breaches_overdue": [e["seq"] for e in self._entries if e.get("kind") == "breach" and not e.get("event")
                                 and now > float(e.get("notify_deadline_ts") or now)
                                 and not any(u.get("kind") == "breach" and u.get("to") == "supervisory_authority"
                                             and any((r or {}).get("seq") == e["seq"] for r in (u.get("evidence") or []))
                                             for u in self._entries)],
            "incidents": len([i for i in incidents if i.get("event") != "reported"]),
            "incidents_overdue": [i["seq"] for i in incidents if i.get("event") != "reported"
                                  and i.get("report_deadline_ts") and now > i["report_deadline_ts"]
                                  and not self._reported_ts(i)],
        }

    # ----------------------------------------------------------------- reading
    def entries(self) -> list[dict]:
        return list(self._entries)

    def all_entries(self) -> list[dict]:
        """Every entry the ledger accounts for, archives first, in seq order: the checkpoint chain is
        followed file by file. A missing archive raises FileNotFoundError naming it, because a trail
        exported with a hole is not the trail. Use entries() for the live file alone."""
        chain: list[dict] = []
        cp = self._checkpoint
        seen = 0
        stack = []
        while cp:
            name = cp.get("archive_file")
            arc = self.path.with_name(str(name)) if name else None
            if arc is None or not arc.exists():
                raise FileNotFoundError(f"archive {name} named by the checkpoint is not beside the ledger")
            data = json.loads(arc.read_bytes().decode("utf-8"))
            inner_cp, inner = _split(data)
            stack.append(inner)
            cp = inner_cp
            seen += 1
            if seen > 64:
                raise ValueError("more than 64 chained archives")
        for inner in reversed(stack):
            chain.extend(inner)
        chain.extend(self._entries)
        return chain

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

    def matches(self, seq: int, inputs: Any = None, output: Any = None) -> dict:
        """Check a retained transcript against entry `seq`: recompute the salted digest of `inputs`
        and `output` exactly as record() did and compare with `inputs_sha256` and `output_sha256`.
        Answers "is this what the model was given, and is this what came back" for an action whose
        content the ledger does not keep. The operator keeps the transcript (a prompt log, a trace,
        `keep_content=True`) and the ledger binds it: a transcript edited by one character no longer
        matches. Needs the ledger's salt file, so only the salt holder can run this; without it the
        digests are useless, which is the point of the salt.

        Returns {"seq", "inputs": True | False | None, "output": True | False | None} where None means
        the caller passed nothing for that side, or the entry has no digest for it. For a LangChain
        chat-model entry pass the messages in the shape the callback digested, see
        `inspeximus.integrations.langchain.context_messages`."""
        e = self._at(seq)
        salt = self._salt_bytes(create=False)

        def side(val, key):
            if val is None or e.get(key) is None:
                return None
            v = self.redact(val) if self.redact else val
            return _content_hash(v, salt) == e[key]
        return {"seq": seq, "action": e.get("action"),
                "inputs": side(inputs, "inputs_sha256"), "output": side(output, "output_sha256")}

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

    def timestamp_tail(self, url: str, actor: str | None = None, timeout: float = 20.0, stamp_fn=None) -> dict:
        """Ask an RFC 3161 Time-Stamping Authority to stamp the ledger's current tail hash and append the
        token as a chained entry. Everything else in the ledger proves ORDER; every clock in it is the
        operator's. A TSA token says a third party saw this tail at that moment, and under eIDAS Art. 41
        a qualified one carries a presumption of the time it shows.

        The entry carries the stamped hash (the previous entry's, or the checkpoint's archived tail on an
        empty live file), the TSA url and the token verbatim, base64. The token is not parsed here beyond
        its PKIStatus (a rejection is refused, never stored as proof); an auditor verifies it with
        `openssl ts -verify` through `inspeximus.timestamp.verify_with_openssl`. `stamp_fn` replaces the
        network call in tests; it must return {token: bytes, status: {...}}."""
        import base64
        from . import timestamp as _ts
        self._refresh_if_changed()
        tail = (self._entries[-1]["hash"] if self._entries
                else (self._checkpoint["archived_tail_hash"] if self._checkpoint else GENESIS))
        digest = bytes.fromhex(tail)
        res = (stamp_fn or (lambda u, d: _ts.stamp(u, d, timeout=timeout)))(url, digest)
        token = res["token"]
        st = _ts.read_status(token)
        if not st.get("has_token"):
            raise _ts.TimestampError("the authority did not grant a timestamp: " + "; ".join(st.get("problems") or []))
        extra = {"event": "rfc3161", "stamped_hash": tail, "tsa_url": url,
                 "token_b64": base64.b64encode(token).decode("ascii"),
                 "token_sha256": _sha256_hex(token), "pki_status": st.get("status_text"),
                 "verify_with": "inspeximus.timestamp.verify_with_openssl(token, bytes.fromhex(stamped_hash))"}
        return self.record("timestamp:rfc3161", status="ok", actor=actor, kind="timestamp", extra=extra)

    def timeline(self, session: str | None = None, principal: str | None = None) -> list[dict]:
        """One workflow, reconstructed from the chain: the entries in order, content-free, filtered to a
        session or a principal when given. The CNIL's July 2026 note on agentic AI asks for exactly this
        traceability: which personal data was used, which agents acted, which third-party services were
        called, in what order. Each row carries the memory digest the agent held, so a reader can tell
        which facts were current at each step. Rows are the ledger's own fields; nothing is inferred."""
        rows = []
        for e in self._entries:
            if session is not None and e.get("session") != session:
                continue
            if principal is not None and e.get("principal") != principal:
                continue
            ms = e.get("memory_state") or {}
            row = {"seq": e["seq"], "ts": e.get("ts"), "kind": e.get("kind", "action"), "action": e.get("action"),
                   "status": e.get("status"), "actor": e.get("actor"), "model": e.get("model"),
                   "principal": e.get("principal"), "session": e.get("session"),
                   "memory_digest": ms.get("digest"), "recalled": len(ms.get("recalled") or []),
                   "refers_to": (e.get("refers_to") or {}).get("seq") if isinstance(e.get("refers_to"), dict) else None}
            if e.get("kind") == "oversight":
                row["event"] = e.get("event")
            if e.get("kind") == "incident":
                row["severity"] = e.get("severity")
            rows.append(row)
        return rows

    def timestamps(self) -> list[dict]:
        """The timestamp entries, each with the hash it stamped and whether that hash is the entry's own
        prev (a token over any other hash is a token over something else)."""
        out = []
        for e in self._entries:
            if e.get("kind") == "timestamp":
                out.append({"seq": e["seq"], "ts": e["ts"], "tsa_url": e.get("tsa_url"),
                            "stamped_hash": e.get("stamped_hash"), "binds_previous_entry": e.get("stamped_hash") == e.get("prev"),
                            "pki_status": e.get("pki_status")})
        return out

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
        self.require_readable()
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
        (ok, problems); problems is empty when ok.

        A ledger file that cannot be read fails, as it does in `verify_file`. It used to load as an
        empty chain, and an empty chain verifies (see LedgerUnreadable)."""
        if self._load_error:
            return False, [self._load_error]
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
        if e.get("kind") == "lifecycle":
            if not e.get("actor"):
                problems.append(f"seq {base_seq + i}: lifecycle event with no actor")
            if e.get("event") not in LIFECYCLE_EVENTS:
                problems.append(f"seq {base_seq + i}: lifecycle event {e.get('event')!r} is not one this ledger records")
            if e.get("event") == "decommission" and e.get("disposition") not in DISPOSITIONS:
                problems.append(f"seq {base_seq + i}: decommission with no disposition of the memory")
            if isinstance(ref, dict) and not _resolves(ref, i):
                problems.append(f"seq {base_seq + i}: lifecycle refers_to does not resolve to an earlier entry")
        if e.get("kind") == "timestamp":
            # the token is over the entry's own prev, so it dates everything before it; a token over any
            # other hash dates something else. The token's PKIStatus is re-read from the bytes stored,
            # never from the pki_status field beside them.
            if e.get("stamped_hash") != e.get("prev"):
                problems.append(f"seq {base_seq + i}: timestamp stamps {str(e.get('stamped_hash'))[:12]}, not the "
                                f"previous entry {str(e.get('prev'))[:12]}")
            try:
                import base64
                from . import timestamp as _ts
                tok = base64.b64decode(e.get("token_b64") or "")
                st = _ts.read_status(tok)
                if not st.get("has_token"):
                    problems.append(f"seq {base_seq + i}: the stored timestamp token was not granted "
                                    f"({'; '.join(st.get('problems') or [])})")
                elif _sha256_hex(tok) != e.get("token_sha256"):
                    problems.append(f"seq {base_seq + i}: token_sha256 does not match the stored token")
            except Exception as ex:  # noqa: BLE001
                problems.append(f"seq {base_seq + i}: the stored timestamp token cannot be read ({type(ex).__name__})")
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
