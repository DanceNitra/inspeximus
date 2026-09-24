"""MCP tool review, family: THE ACTION LEDGER AND ITS REGULATORY REGISTERS.

Part of audits/2026-09-24/mcp-tools-review.md. Tools: actions_verify, record_oversight, record_disclosure,
oversight_report, record_incident, incident_report, incident_reported, record_risk, risk_register,
post_market_report, record_corrective_action, corrective_action_report, record_authority_request,
decision_explanation, record_breach, breach_notified, breach_report, record_literacy, literacy_register,
record_attestation, attestation_register, record_responsibilities, responsibilities_register,
record_declaration, declaration_document, attest_documentation_retention, record_notice, notice_register,
record_processing_role, processing_roles, record_qms, qms_register, archive_actions, record_lifecycle,
export_audit_trail, action_timeline, timestamp_actions, attest_retention, actions_match, what_it_knew,
technical_documentation, deployer_report, registration_export, and the tool-boundary recording
(`_FreshFastMCP.tool` / `_action_ledger()` in inspeximus/mcp_server.py).

Each test holds a tool to a sentence of its OWN description (the docstring an MCP client is shown) or to
the server's documented configuration, and fails today. They are strict xfails: the day the tool is fixed
the test XPASSes and the marker has to come off. Preconditions go through `pytest.fail`, so a setup that
did not do what the test needs fails loudly instead of passing as an expected failure. Every test runs on
a throwaway store in tmp_path (see tests/_mcp_review.py).
"""
import json
import time as _time

import pytest

pytest.importorskip("mcp")
pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import serialization as _ser  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from _mcp_review import call, load_server  # noqa: E402

from inspeximus import Inspeximus  # noqa: E402

XFAIL = dict(strict=True, raises=AssertionError)
DAY = 86400.0


def _keypair():
    sk = Ed25519PrivateKey.generate()
    raw = sk.private_bytes(_ser.Encoding.Raw, _ser.PrivateFormat.Raw, _ser.NoEncryption()).hex()
    pub = sk.public_key().public_bytes(_ser.Encoding.Raw, _ser.PublicFormat.Raw).hex()
    return raw, pub


def _ledger_path(tmp_path):
    return tmp_path / "store.json.actions.json"


def _ledger(tmp_path) -> list[dict]:
    p = _ledger_path(tmp_path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def _entry(tmp_path, seq):
    return next((e for e in _ledger(tmp_path) if e.get("seq") == seq and e.get("kind") != "checkpoint"), None)


def _ok(res, what):
    """The call succeeded and returned a dict without an `error` key, or the test's setup failed."""
    if res.is_error or not isinstance(res.data, dict) or "error" in res.data:
        pytest.fail(f"precondition: {what} failed: {res.text[:300] if res.is_error else res.data}")
    return res.data


class _ShiftedClock:
    """Stands in for the `time` module inside inspeximus.actions so ledger entries can be dated in the past
    without sleeping. Only the ledger's clock moves; the store and the MCP session keep the real one."""

    def __init__(self):
        self.offset = 0.0

    def time(self):
        return _time.time() + self.offset

    def __getattr__(self, name):
        return getattr(_time, name)


def _backdating_clock(monkeypatch):
    import inspeximus.actions as actions_mod
    clock = _ShiftedClock()
    monkeypatch.setattr(actions_mod, "time", clock)
    return clock


# ── every ledger-writing tool, under INSPEXIMUS_ACTIONS=1 with a signing key ──────────────────────────
_NOW = _time.time()
_DECLARATION = dict(actor="bob", system_name="Support agent", system_type="chat assistant",
                    system_reference="SA-1", provider_name="Acme GmbH", provider_address="Berlin",
                    conformity_procedure="annex_vi_internal_control", place="Berlin", signer_name="C. Doe",
                    signer_function="CEO", signed_for="Acme GmbH")
_DOCUMENTS = [{"kind": "technical_documentation", "ref": "docs/annex-iv.pdf"},
              {"kind": "eu_declaration_of_conformity", "ref": "docs/doc.pdf"},
              {"kind": "notified_body_changes", "present": False, "not_applicable_reason": "no notified body"},
              {"kind": "notified_body_decisions", "present": False, "not_applicable_reason": "no notified body"}]
# (tool, arguments, where the result names the seq of the entry the tool wrote). "BREACH" is replaced by
# the seq of a breach entry the fixture records through the server's own (signing) ledger handle.
_LEDGER_WRITERS = [
    ("record_oversight", dict(event="approve", actor="alice", refers_to=0), "seq"),
    ("record_disclosure", dict(session="s1", shown="You are talking to an AI system."), "seq"),
    ("record_incident", dict(title="wrong refund", severity="serious", actor="bob"), "seq"),
    ("record_risk", dict(risk_id="R1", hazard="stale price", harm="safety", source="intended_use", actor="bob"), "seq"),
    ("post_market_report", dict(since=0, actor="bob"), "ledger_seq"),
    ("record_corrective_action", dict(kind="conformity", actor="bob", non_conformity="stale price served"), "seq"),
    ("record_authority_request", dict(authority="MSA", reference="REF-1", actor="bob", scope="logs"), "seq"),
    ("decision_explanation", dict(seq=0, actor="dpo"), "ledger_entry"),
    ("record_breach", dict(title="export leaked", actor="bob", nature="misdirected email"), "seq"),
    ("breach_notified", dict(seq="BREACH", actor="bob", to="public"), "seq"),
    ("record_literacy", dict(actor="bob", measure="training", audience="staff", description="onboarding"), "seq"),
    ("record_attestation", dict(actor="bob", practice="a", statement="not_used"), "seq"),
    ("record_responsibilities", dict(actor="bob", agreement_ref="AGR-1",
                                     parties=[{"party": "Acme GmbH", "role": "provider"}]), "seq"),
    ("record_declaration", _DECLARATION, "seq"),
    ("attest_documentation_retention", dict(actor="bob", placed_on_market_ts=_NOW - 30 * DAY,
                                            documents=_DOCUMENTS), "seq"),
    ("record_notice", dict(actor="bob", subject="crm/alice", channel="ui", items=["controller_identity"]), "seq"),
    ("record_processing_role", dict(actor="bob", role="controller"), "seq"),
    ("record_qms", dict(actor="bob", procedure="change management", version="1", owner="QA",
                        review_due_ts=_NOW + 90 * DAY), "seq"),
]


@pytest.mark.xfail(reason="ledger writers: with INSPEXIMUS_ACTIONS=1 the ledger is 'one signed, hash-chained "
                          "entry per call' (module docstring); these tools append through a second, keyless "
                          "ActionLedger, so the entry is unsigned and actions_verify fails", **XFAIL)
@pytest.mark.parametrize("tool,args,seq_field", _LEDGER_WRITERS, ids=[t[0] for t in _LEDGER_WRITERS])
def test_a_ledger_writing_tool_keeps_a_signed_ledger_signed(monkeypatch, tmp_path, tool, args, seq_field):
    """Module docstring: "INSPEXIMUS_ACTIONS  1 to record every tool call in the ACTION LEDGER
    (<store>.actions.json): one signed, hash-chained entry per call". `_action_ledger()`: "Signed with the
    store's receipt key when it has one, else with the server's writer key". post_market_report: "With
    `actor` the report is signed into the ledger as a `monitoring` entry"; attest_documentation_retention:
    "Append a signed statement"; record_qms: "this is the signed record that it exists".

    Every record-style tool opens its own `ActionLedger(_MEM, actor=_ACTOR)` with no signing key (the MCP
    store never holds a receipt key), so the entry it writes is the one unsigned link in a chain the
    boundary signs, and from then on actions_verify answers ok=False, "seq N: no signature".
    """
    sk, _pub = _keypair()
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_ACTIONS="1", INSPEXIMUS_RECEIPTS="1",
                      INSPEXIMUS_WRITER_KEY=sk, INSPEXIMUS_ACTOR="support-agent")
    _ok(call(mod, "remember", text="the refund limit is 100 EUR"), "remember")          # seq 0: an action
    if tool == "breach_notified":
        breach = mod._action_ledger().breach("export leaked", "bob", "misdirected email")  # signed handle
        args = dict(args, seq=breach["seq"])
    before = _ok(call(mod, "actions_verify"), "actions_verify before the tool")
    if not before["ok"] or not all("sig" in e for e in _ledger(tmp_path)):
        pytest.fail(f"precondition: the ledger must be signed and verify before the tool runs: {before}")

    data = _ok(call(mod, tool, **args), tool)
    seq = data[seq_field]["seq"] if seq_field == "ledger_entry" else data[seq_field]
    entry = _entry(tmp_path, seq)
    if entry is None:
        pytest.fail(f"precondition: {tool} reported seq {seq}, which is not in the ledger file")

    after = call(mod, "actions_verify").data
    assert "sig" in entry and after["ok"], (
        f"{tool} wrote seq {seq} unsigned={'sig' not in entry} into a signed ledger; actions_verify now "
        f"reports ok={after['ok']} {after['problems']}")


# ── technical_documentation / deployer_report / registration_export: the configured key pin ─────────
def _memory_chain_verdicts(doc):
    found = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "memory_chain_verified":
                    found.append(v)
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(doc)
    return found


_DOC_TOOLS = [("technical_documentation", {}), ("deployer_report", {}), ("registration_export", {"section": "C"})]


@pytest.mark.parametrize("tool,args", _DOC_TOOLS, ids=[t[0] for t in _DOC_TOOLS])
def test_the_documentation_tools_honour_the_configured_receipt_pubkey(monkeypatch, tmp_path, tool, args):
    """Module docstring: "INSPEXIMUS_RECEIPT_PUBKEY  hex Ed25519 PUBLIC key the write receipts are expected to
    be signed by. Set it whenever the store is signed: without it the tamper-evidence tools verify that
    receipts are signed by SOMEBODY, which a party who rewrites the store and re-signs it with a key of
    their own satisfies." technical_documentation: "the evidence sections filled from the store ... (chain
    verification ...)"; deployer_report and registration_export carry the same `memory_chain_verified`.

    governance_report reads the pin from the environment (`_pin()`), and reports this store's receipts as
    signed by an unexpected key. The three documentation tools hand their `expected_pubkey` argument
    (default None) straight to the library, so with the pin configured they state memory_chain_verified:
    true over a chain the configured key never signed.
    """
    signer, _ = _keypair()
    _, pinned_pub = _keypair()
    store = Inspeximus(path=str(tmp_path / "store.json"), receipts=True, receipt_key=signer)
    store.remember("the wire-transfer limit is 1000 EUR", key="payments::limit", object="1000")
    del store
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_RECEIPT_PUBKEY=pinned_pub)

    gov = _ok(call(mod, "governance_report"), "governance_report")
    if gov.get("proof", {}).get("verified") is not False:
        pytest.fail(f"precondition: the configured pin must make the chain fail where it is honoured: {gov.get('proof')}")
    explicit = _memory_chain_verdicts(_ok(call(mod, tool, expected_pubkey=pinned_pub, **args), tool))
    if not explicit or any(v is not False for v in explicit):
        pytest.fail(f"precondition: {tool} with the key passed explicitly must report the chain unverified: {explicit}")

    default = _memory_chain_verdicts(_ok(call(mod, tool, **args), tool))
    assert default and all(v is False for v in default), (
        f"{tool} with INSPEXIMUS_RECEIPT_PUBKEY configured reports memory_chain_verified={default} over "
        f"receipts signed by another key")


# ── operator_json through the MCP boundary ───────────────────────────────────────────────────────────
@pytest.mark.xfail(reason="technical_documentation/deployer_report/registration_export: '`operator_json` is a "
                          "JSON object string'; FastMCP pre-parses any JSON string for a `str | None` "
                          "parameter, so every JSON object string is refused by argument validation", **XFAIL)
@pytest.mark.parametrize("tool,args", _DOC_TOOLS, ids=[t[0] for t in _DOC_TOOLS])
def test_the_documentation_tools_accept_operator_json_through_mcp(monkeypatch, tmp_path, tool, args):
    """technical_documentation: "`operator_json` is a JSON object string with the provider's own fields."
    deployer_report: "`operator_json` is a JSON object string with the deployer's own fields".

    FastMCP's `pre_parse_json` json-decodes a string argument whenever the parameter's annotation is not
    exactly `str`; `operator_json: str | None` qualifies, so '{"system_name": ...}' arrives as a dict and
    fails the `str | None` validation. The Python function accepts the same string; no MCP client can.
    """
    mod = load_server(monkeypatch, tmp_path)
    operator_json = json.dumps({"system_name": "Support agent", "provider": "Acme GmbH"})
    direct = getattr(mod, tool)(operator_json=operator_json, **args)
    if not isinstance(direct, dict) or "error" in direct:
        pytest.fail(f"precondition: the plain function must accept the object string: {str(direct)[:300]}")

    res = call(mod, tool, operator_json=operator_json, **args)
    assert not res.is_error and "error" not in (res.data or {}), \
        f"{tool} refused a JSON object string for operator_json: {res.text[:300]}"


# ── seq bounds after archive_actions ─────────────────────────────────────────────────────────────────
_SEQ_TOOLS = ["what_it_knew", "actions_match", "incident_report"]


@pytest.mark.xfail(reason="what_it_knew/actions_match/incident_report: an entry in the live ledger is refused "
                          "as 'no entry #N' once archive_actions has rotated the ledger (bounds checked "
                          "against len(led), not against the live seq range)", **XFAIL)
@pytest.mark.parametrize("tool", _SEQ_TOOLS)
def test_a_live_entry_is_found_after_the_ledger_was_rotated(monkeypatch, tmp_path, tool):
    """what_it_knew: "What the agent KNEW when it performed action number `seq` in the action ledger".
    actions_match: "Check a retained transcript against action number `seq`". incident_report: "The Art. 73
    report skeleton for incident `seq`". archive_actions: "start the live file with a checkpoint ... the
    chain verifies across the files".

    After a rotation the live file starts at seq archived_through+1, but the three tools refuse any
    seq >= len(ledger) before asking the ledger, so the entry right after the checkpoint (and every
    incident recorded later) is answered "no entry #N; the ledger has M entries".
    """
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_ACTIONS="1", INSPEXIMUS_RECEIPTS="1")
    for i in range(3):
        _ok(call(mod, "remember", text=f"fact number {i}"), "remember")
    arc = _ok(call(mod, "archive_actions", keep_days=0), "archive_actions")
    if arc.get("archived", 0) < 3:
        pytest.fail(f"precondition: the rotation must archive the three remember actions: {arc}")
    inc = _ok(call(mod, "record_incident", title="wrong refund", severity="serious", actor="bob"),
              "record_incident")["seq"]
    live = [e["seq"] for e in _ledger(tmp_path) if e.get("kind") != "checkpoint"]
    action_seq = arc["archived_through"] + 1           # the mcp:archive_actions entry, first in the live file
    if action_seq not in live or inc not in live:
        pytest.fail(f"precondition: seqs {action_seq} and {inc} must be in the live file: {live}")

    args = {"what_it_knew": dict(seq=action_seq),
            "actions_match": dict(seq=action_seq, inputs={"args": [], "kwargs": {"keep_days": 0, "actor": None}}),
            "incident_report": dict(seq=inc)}[tool]
    res = call(mod, tool, **args)
    assert not res.is_error and "error" not in (res.data or {}), \
        f"{tool}({args['seq']}) on an entry in the live ledger answered {res.text[:200] if res.is_error else res.data}"


# ── post_market_report after incident_reported ───────────────────────────────────────────────────────
@pytest.mark.xfail(reason="post_market_report: 'incidents and their clocks'; an incident closed by "
                          "incident_reported is still listed overdue, and the report entry is counted as a "
                          "second incident opened", **XFAIL)
def test_post_market_report_counts_a_reported_incident_once_and_not_overdue(monkeypatch, tmp_path):
    """post_market_report: "The Art. 72 post-market monitoring report for one period, from the ledgers: ...
    incidents and their clocks". incident_reported: "A later entry that names the incident".

    incident_report and oversight_report follow the later entry (`_reported_ts`) and call the incident
    reported; post_market_report counts every kind="incident" entry as one opened, the incident_reported
    follow-up included, and keeps the incident in `overdue` because it reads `reported_ts` off the incident
    entry itself, which the MCP record_incident never sets.
    """
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_ACTIONS="1", INSPEXIMUS_RECEIPTS="1")
    inc = _ok(call(mod, "record_incident", title="wrong refund", severity="serious", actor="bob",
                   aware_ts=_time.time() - 30 * DAY), "record_incident")["seq"]
    _ok(call(mod, "incident_reported", seq=inc, actor="bob", reported_to="market surveillance authority"),
        "incident_reported")
    clock = _ok(call(mod, "incident_report", seq=inc), "incident_report")["clock"]
    if not clock["reported"] or clock["overdue"]:
        pytest.fail(f"precondition: incident_report must see the incident as reported: {clock}")

    incidents = _ok(call(mod, "post_market_report", since=0), "post_market_report")["incidents"]
    assert incidents["opened"] == 1 and inc not in incidents["overdue"], \
        f"one incident, reported: post_market_report says {incidents}"


# ── risk entries and the "who refers to this entry" scans ────────────────────────────────────────────
def _incident_corrective_and_action(mod):
    _ok(call(mod, "remember", text="the refund limit is 100 EUR"), "remember")                  # seq 0
    inc = _ok(call(mod, "record_incident", title="wrong refund", severity="other", actor="bob",
                   evidence=[0]), "record_incident")["seq"]
    car = _ok(call(mod, "record_corrective_action", kind="conformity", actor="bob",
                   non_conformity="stale limit", refers_to=[inc]), "record_corrective_action")["seq"]
    return {"incident_report": inc, "corrective_action_report": car, "decision_explanation": 0}


def _referring_seqs(tool, doc):
    rows = {"incident_report": doc.get("updates"), "corrective_action_report": doc.get("later_entries"),
            "decision_explanation": doc.get("referring_entries")}[tool]
    return [r["seq"] for r in rows or []]


_REF_TOOLS = ["incident_report", "corrective_action_report", "decision_explanation"]


@pytest.mark.xfail(reason="incident_report/corrective_action_report/decision_explanation: 'later entries that "
                          "refer to it' / 'the ... risks ... that refer to it'; a risk's list-valued refers_to "
                          "is never read, so a risk that refers to the entry is left out", **XFAIL)
@pytest.mark.parametrize("tool", _REF_TOOLS)
def test_a_risk_that_refers_to_an_entry_is_listed_as_referring_to_it(monkeypatch, tmp_path, tool):
    """incident_report: "later entries that refer to the incident". corrective_action_report: "the evidence
    entries and later entries that refer to it". decision_explanation: "the incidents, risks and corrective
    actions that refer to it". record_risk: "`refers_to` lists ledger seqs and each must exist".

    The scans read `refers_to` only when it is a single dict and otherwise read `evidence` as the list of
    references. A risk stores `refers_to` as a LIST of references (and `evidence` as free text), so a risk
    that refers to the incident, the corrective action or the action is never found.
    """
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_ACTIONS="1", INSPEXIMUS_RECEIPTS="1")
    target = _incident_corrective_and_action(mod)[tool]
    risk = _ok(call(mod, "record_risk", risk_id="R1", hazard="stale limit", harm="fundamental_rights",
                    source="post_market", actor="bob", refers_to=[target]), "record_risk")["seq"]
    if [r.get("seq") for r in (_entry(tmp_path, risk) or {}).get("refers_to") or []] != [target]:
        pytest.fail(f"precondition: the risk entry must refer to seq {target}: {_entry(tmp_path, risk)}")

    doc = _ok(call(mod, tool, seq=target), tool)
    assert risk in _referring_seqs(tool, doc), \
        f"{tool}({target}) lists {_referring_seqs(tool, doc)} as referring to it; the risk at seq {risk} refers to it"


@pytest.mark.xfail(reason="incident_report/corrective_action_report/decision_explanation: a later risk with "
                          "free-text `evidence` (as record_risk documents it) makes the report raise "
                          "AttributeError for every earlier entry", **XFAIL)
@pytest.mark.parametrize("tool", _REF_TOOLS)
def test_a_risk_with_free_text_evidence_does_not_break_the_reports(monkeypatch, tmp_path, tool):
    """incident_report: "The Art. 73 report skeleton for incident `seq`". corrective_action_report: "The
    Art. 20 record for corrective action `seq`". decision_explanation: "The material for an Art. 86
    explanation of the decision at action `seq`". record_risk (library): "`evidence` is a list of free
    references to what supports the estimate (a probe path, a receipt hash, a test name)".

    The same scan treats a risk's free-text `evidence` strings as reference dicts and calls `.get` on them,
    so once any risk with evidence is recorded, the report for every incident, corrective action or action
    before it raises: "'str' object has no attribute 'get'".
    """
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_ACTIONS="1", INSPEXIMUS_RECEIPTS="1")
    target = _incident_corrective_and_action(mod)[tool]
    _ok(call(mod, tool, seq=target), f"{tool} before the risk")
    _ok(call(mod, "record_risk", risk_id="R2", hazard="stale limit", harm="safety", source="intended_use",
             actor="bob", evidence=["probes/refund_limit.py"]), "record_risk")

    res = call(mod, tool, seq=target)
    assert not res.is_error and "error" not in (res.data or {}), \
        f"{tool}({target}) after a risk with evidence: {res.text[:200] if res.is_error else res.data}"


@pytest.mark.xfail(reason="archive_actions: 'anything a kept entry refers to stay live'; an entry a kept risk "
                          "refers to is archived (list-valued refers_to is not read)", **XFAIL)
def test_archive_actions_keeps_live_what_a_kept_risk_refers_to(monkeypatch, tmp_path):
    """archive_actions: "Nothing is deleted and the chain verifies across the files; an open incident and
    anything a kept entry refers to stay live."

    The cut is pulled back only for references read through the same dict-or-evidence scan, so the action
    a kept risk refers to is moved into the archive.
    """
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_ACTIONS="1", INSPEXIMUS_RECEIPTS="1")
    clock = _backdating_clock(monkeypatch)
    clock.offset = -10 * DAY
    _ok(call(mod, "remember", text="the refund limit is 100 EUR"), "remember")               # seq 0, 10 days old
    clock.offset = 0.0
    risk = _ok(call(mod, "record_risk", risk_id="R1", hazard="stale limit", harm="safety",
                    source="intended_use", actor="bob", refers_to=[0]), "record_risk")["seq"]
    arc = _ok(call(mod, "archive_actions", keep_days=1), "archive_actions")
    live = [e["seq"] for e in _ledger(tmp_path) if e.get("kind") != "checkpoint"]
    if risk not in live:
        pytest.fail(f"precondition: the risk (seq {risk}) is newer than the cutoff and must stay live: {live}, {arc}")

    assert 0 in live, f"the action the kept risk refers to was archived: live seqs {live}, rotation {arc}"


@pytest.mark.xfail(reason="archive_actions: a kept risk with free-text `evidence` makes the rotation raise "
                          "AttributeError instead of rotating", **XFAIL)
def test_archive_actions_rotates_past_a_risk_with_free_text_evidence(monkeypatch, tmp_path):
    """archive_actions: "Rotate the action ledger: move entries older than `keep_days` into a signed archive
    file ... Returns what was archived".

    The kept-entry reference scan calls `.get` on each of a risk's free-text evidence strings.
    """
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_ACTIONS="1", INSPEXIMUS_RECEIPTS="1")
    clock = _backdating_clock(monkeypatch)
    clock.offset = -10 * DAY
    _ok(call(mod, "remember", text="the refund limit is 100 EUR"), "remember")               # seq 0, 10 days old
    clock.offset = 0.0
    _ok(call(mod, "record_risk", risk_id="R2", hazard="stale limit", harm="safety", source="intended_use",
             actor="bob", evidence=["probes/refund_limit.py"]), "record_risk")
    if not _ledger(tmp_path) or _ledger(tmp_path)[0].get("ts", _time.time()) > _time.time() - 5 * DAY:
        pytest.fail("precondition: the first ledger entry must be older than keep_days")

    res = call(mod, "archive_actions", keep_days=1)
    assert not res.is_error and (res.data or {}).get("archived", 0) >= 1, \
        f"archive_actions: {res.text[:200] if res.is_error else res.data}"


# ── an unreadable ledger file ────────────────────────────────────────────────────────────────────────
def _two_incidents_then_truncate(mod, tmp_path):
    for t in ("wrong refund", "wrong address"):
        _ok(call(mod, "record_incident", title=t, severity="serious", actor="bob"), "record_incident")
    v = _ok(call(mod, "actions_verify"), "actions_verify")
    if not v["ok"] or v["entries"] != 2:
        pytest.fail(f"precondition: two verified entries before the damage: {v}")
    p = _ledger_path(tmp_path)
    raw = p.read_text(encoding="utf-8")
    damaged = raw[: len(raw) // 2]                     # what a torn copy or a disk-full restore leaves behind
    p.write_text(damaged, encoding="utf-8")
    return damaged


@pytest.mark.xfail(reason="actions_verify: 'Recomputes every hash, link and signature'; a ledger file that is "
                          "not JSON is loaded as an empty chain and verified ok=True, entries=0", **XFAIL)
def test_actions_verify_does_not_pass_a_ledger_it_cannot_read(monkeypatch, tmp_path):
    """actions_verify: "Verify the ACTION LEDGER beside this store ... Recomputes every hash, link and
    signature".

    `ActionLedger._load` turns an unparseable file into `[]`, and the empty chain verifies. The offline
    verifier on the same file (`verify_file`) says "cannot read"; the tool says ok.
    """
    from inspeximus.actions import verify_file
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_RECEIPTS="1")
    _two_incidents_then_truncate(mod, tmp_path)
    if verify_file(_ledger_path(tmp_path))[0]:
        pytest.fail("precondition: the offline verifier must reject the damaged file")

    v = call(mod, "actions_verify").data
    assert v["ok"] is False, f"actions_verify over a ledger file that is not JSON: {v}"


@pytest.mark.xfail(reason="record_risk: 'Append one entry to the risk register'; on a ledger file it cannot "
                          "parse it starts a new chain at seq 0 and overwrites the file, destroying the earlier "
                          "entries", **XFAIL)
def test_a_ledger_write_does_not_overwrite_a_ledger_it_cannot_read(monkeypatch, tmp_path):
    """record_risk: "Append one entry to the risk register (EU AI Act Art. 9)."

    `_load` reads an unparseable file as an empty chain, `record()` numbers the new entry 0 from GENESIS,
    and `_save()` replaces the file: the two incident records that were on disk are gone, and the tool
    reports a normal success. With INSPEXIMUS_ACTIONS=1 the tool boundary does the same on ANY tool call.
    """
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_RECEIPTS="1")
    damaged = _two_incidents_then_truncate(mod, tmp_path)

    res = call(mod, "record_risk", risk_id="R1", hazard="stale limit", harm="safety", source="intended_use",
               actor="bob")
    survived = any(damaged in f.read_text(encoding="utf-8", errors="ignore")
                   for f in tmp_path.iterdir() if f.is_file())
    assert survived, (f"record_risk answered {res.text[:200] if res.is_error else res.data} and the ledger that "
                      f"held two incidents was replaced by a new chain")
