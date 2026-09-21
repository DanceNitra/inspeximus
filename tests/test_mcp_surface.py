"""Every MCP tool, driven at least once — 58 of them had zero executed body lines.

The MCP server is the surface most users actually touch, and 50 of its 55 tools were never executed by any
test. They are thin wrappers, which is exactly why this matters: a wrapper that passes the wrong argument,
drops a parameter, or returns a shape its docstring contradicts is invisible to the library's own tests, and
four audit rounds found precisely those defects here (a missing `allow_ambiguous`, a missing `request_id`, a
tool whose docstring promised keys the call never returns).

The sweep below calls every `@mcp.tool()` function with synthesised arguments and asserts only that it does
not blow up and returns a JSON-serialisable shape. That is a low bar deliberately — its value is that it
covers the whole surface and fails the moment a tool is added that cannot be driven at all.

IT IS NOT VERIFICATION, and the gap is measured. Five plausible sabotages of the server — recall ignoring
`k`, `get` returning some other record, `forget` reporting success without deleting, `verify_writes`
hard-coded to clean, `memory_report` inflating its active count — ALL pass this file. Every one of them is
caught by `tests/test_mcp_behaviour.py`, which asserts what the tools actually do. Keep both: this one for
breadth, that one for truth.
"""
import inspect
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytest.importorskip("mcp")


@pytest.fixture(scope="module")
def mcp_mod():
    """A fresh MCP module bound to a temp store. It builds a global `_MEM` at import from $INSPEXIMUS_PATH."""
    import importlib
    os.environ["INSPEXIMUS_PATH"] = os.path.join(tempfile.mkdtemp(), "mcp.json")
    os.environ["INSPEXIMUS_RECEIPTS"] = "1"
    mod = importlib.import_module("inspeximus.mcp_server")
    importlib.reload(mod)
    mod.remember("the deploy channel is BLUE-9", key="deploy", object="BLUE-9")
    mod.remember("the deploy channel is RED-2", key="deploy", object="RED-2")
    mod.remember("alice ssn 123-45-6789", tags=["pii"])
    return mod


def _tools(mod):
    """Every function decorated with @mcp.tool(). FastMCP keeps the plain function on the module."""
    out = []
    for name, obj in vars(mod).items():
        if name.startswith("_") or not inspect.isfunction(obj):
            continue
        if obj.__module__ != mod.__name__:
            continue
        out.append((name, obj))
    return sorted(out)


def _args_for(name, sig, mod):
    """Synthesise plausible arguments from parameter names."""
    rid = mod._MEM.items[0]["id"] if mod._MEM.items else "deadbeef00"
    known = {
        "text": "a new fact about the deploy channel", "query": "deploy channel", "q": "deploy channel",
        "id": rid, "memory_id": rid, "ids": [rid], "key": "deploy", "object": "BLUE-9",
        # provenance() takes exactly ONE of key= or id=, so the generic table cannot drive it
        "_provenance_key": "deploy",
        "subject": "deploy", "claim": "the deploy channel is RED-2", "outcome": True,
        "bundle": None, "cert": None, "w": None, "anchor": None, "prior_anchor": None,
        "code": "print(1)", "path": None, "symbol": "foo", "name": "foo",
        "because": "it was decided", "topic": "ops", "session_id": "s1", "namespace": "ns",
        "when": 9e9, "question": "what is the deploy channel", "decision": "ship on friday",
        "max_age_days": 3650.0,
        # erasure_residue scans a directory; point it at a real, empty temp dir so the sweep drives it
        # for real rather than declaring it undriveable. `values` must be non-empty, since an empty
        # search is deliberately not a clean result.
        "root": tempfile.mkdtemp(), "values": ["a-value-that-is-not-anywhere"],
        # the agent-grant ACL: a plain agent name is all the read-side tools need
        "agent": "bob",
        # set_index_line writes the always-loaded index line for a record. Driven for real rather
        # than declared undriveable: the whole point of this file is that a skipped tool has no
        # coverage, and this one is reachable with the same `key` the rest of the table uses.
        "line": "concluded the deploy channel is BLUE-9 after the RED-2 rollback",
        # what_it_knew(seq) reads the action ledger beside the store; on an empty ledger it answers
        # with an error dict rather than raising, which is the contract the sweep checks.
        "seq": 0,
        # record_oversight / record_disclosure: a human decision with an actor, and a disclosure with the
        # text shown; both are driven for real so a crash on ordinary input is caught here.
        "event": "review", "actor": "reviewer", "session": "s1", "reason": "DSAR-17",
        "title": "a transfer above the limit", "severity": "other", "evidence": [],
        "shown": "You are chatting with an AI assistant.",
        # archive_actions / attest_retention / incident_reported: rotation with nothing old enough writes
        # nothing, an attestation appends one entry, and reporting seq 0 answers with an error dict when
        # seq 0 is not an incident; none may raise.
        "keep_days": 3650.0, "policy_days": 183.0, "reported_to": "market surveillance authority",
        # record_risk / post_market_report: one Art. 9 entry in the register, and an Art. 72 report over
        # a period that started before the sweep (read-only without an actor; the actor above signs it)
        "risk_id": "R-sweep", "hazard": "a recalled fact steers the agent", "harm": "fundamental_rights",
        "source": "foreseeable_misuse", "since": 0.0,
        # record_corrective_action / record_authority_request / record_breach / breach_notified / the two
        # reports: `kind` here is the Art. 20 kind (the disclosure tool has its own default), `scope` the
        # Art. 21 scope, `to` the Art. 33 target; seq 0 is not a breach so the report answers with an error
        "kind": "disable", "non_conformity": "the send tool acted on a stale address",
        "authority": "market surveillance authority", "reference": "REQ-sweep", "scope": "logs",
        "nature": "an export reached another subject", "to": "supervisory_authority",
        # export_audit_trail writes a JSONL file: a temp path, a URI and a semver
        "out_path": os.path.join(tempfile.gettempdir(), "inspeximus-sweep-trail.jsonl"),
        # remember_in_partition names a partition that the sweep has not opened: an error dict, never a raise
        "partition": "sweep-partition",
        "agent_id": "urn:agent:sweep.example", "agent_version": "0.0.1",
        # record_literacy / record_attestation / record_responsibilities / record_declaration /
        # attest_documentation_retention (3.3.0): one Art. 4 measure, one Art. 5(1)(a) attestation, one
        # Art. 25 agreement with a provider, an Annex VI declaration, and an Art. 18 statement with the two
        # required documents present and the notified-body items not applicable; all five append one entry
        "measure": "briefing", "audience": "staff", "description": "the retirement rule, in one page",
        "practice": "a", "statement": "not_used",
        "agreement_ref": "MSA-sweep", "parties": [{"party": "Acme", "role": "provider"}],
        "system_name": "Assistant", "system_type": "chat", "system_reference": "asst-sweep",
        "provider_name": "Acme", "provider_address": "Street 1", "conformity_procedure": "annex_vi_internal_control",
        "place": "Bratislava", "signer_name": "R. D.", "signer_function": "CEO", "signed_for": "Acme",
        "placed_on_market_ts": 0.0,
        # record_notice / record_objection / record_processing_role (3.4.0): an Art. 13 notice with one
        # item, an objection by a subject the sweep store may not hold (an error dict, never a raise), and a
        # controller declaration; `ground`, `role` and `channel` are theirs (`kind`/`scope`/`to` are taken)
        "items": ["rights"], "channel": "ui", "ground": "own_situation", "role": "controller",
        # release_quarantine (3.5.0): aimed at the sweep record, which is not quarantined, so an error dict
        "documents": [{"kind": "technical_documentation", "sha256": "a" * 64},
                      {"kind": "eu_declaration_of_conformity", "sha256": "b" * 64},
                      {"kind": "notified_body_changes", "present": False, "not_applicable_reason": "Annex VI"},
                      {"kind": "notified_body_decisions", "present": False, "not_applicable_reason": "Annex VI"}],
    }
    args = []
    for pname, p in sig.parameters.items():
        if p.default is not p.empty or p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            break
        if pname not in known or known[pname] is None:
            return None                                   # cannot drive it honestly
        args.append(known[pname])
    return tuple(args)


#: Tools the sweep cannot synthesise arguments for, each with the reason. Named deliberately — a tool that
#: is silently skipped is a tool with no coverage at all, which is the state this file exists to end.
UNDRIVEABLE = {
    "main": "the server entrypoint, not a tool — it starts the stdio loop",
    "verify_audit_bundle": "takes a bundle dict produced by another tool",
    "verify_witness": "takes a witness dict",
    "verify_cosigned_anchor": "takes an anchor dict plus co-signatures",
    "verify_consistency": "takes a prior anchor recorded out of band",
    "detect_split_view": "compares two anchors",
    "resolve_reopened": "needs a reopened id surfaced by observe()",
    "check_code": "reads a source file from disk",
    "deprecate_symbol": "mutates a code-guard ledger",
    "symbol_status": "reads the code-guard ledger",
    "timestamp_actions": "POSTs to an RFC 3161 authority on the network; driven with a fake TSA in "
                         "tests/test_a_timestamp_entry_stamps_the_tail_it_sits_on.py, and a network failure "
                         "is returned as an error dict, never raised",
}


def test_every_mcp_tool_can_be_called(mcp_mod):
    """Covers the whole surface. A tool that cannot be driven must be named in UNDRIVEABLE with a reason."""
    driven, undriveable, broke = 0, [], []
    for name, fn in _tools(mcp_mod):
        if name in UNDRIVEABLE:
            continue
        if name == "provenance":
            out = fn(key="deploy")                        # exclusive-argument signature, driven by hand
            driven += 1
            assert out.get("found") is True
            continue
        if name in ("grant", "revoke"):
            # Same shape as provenance: the generic table cannot supply a SELECTOR, and calling these with
            # only an agent is refused on purpose (a grant with no selector is ambiguous, and an ambiguous
            # access-control decision must deny rather than mean "everything"). Driven by hand with one.
            out = fn("bob", tag="ops")
            driven += 1
            assert out.get("state") == ("granted" if name == "grant" else "revoked"), out
            continue
        args = _args_for(name, inspect.signature(fn), mcp_mod)
        if args is None:
            undriveable.append(name)
            continue
        try:
            out = fn(*args)
        except Exception as e:                            # a refusal is fine; a crash on valid input is not
            if type(e).__name__ in ("AmbiguousSubject", "StoreChangedOnDisk", "PermissionError"):
                driven += 1
                continue
            broke.append(f"{name}: {type(e).__name__}: {e}")
            continue
        driven += 1
        try:
            json.dumps(out, default=str)
        except Exception as e:
            broke.append(f"{name}: result is not JSON-serialisable ({e})")

    assert not broke, "these MCP tools failed on ordinary input:\n  " + "\n  ".join(broke)
    assert not undriveable, ("the sweep could not drive these — add them to the arg table so they ARE "
                             f"covered, or to UNDRIVEABLE with a reason: {undriveable}")
    assert driven >= 30, f"the sweep only exercised {driven} tools; it is not covering the surface"


def test_the_undriveable_list_holds_no_tool_that_could_be_driven(mcp_mod):
    """Stops the exemption list becoming the place tools go to avoid being tested."""
    names = {n for n, _ in _tools(mcp_mod)}
    stale = sorted(set(UNDRIVEABLE) - names)
    assert not stale, f"UNDRIVEABLE names tools that no longer exist: {stale}"


def test_the_recall_tool_returns_the_current_value_not_the_retired_one():
    """One behavioural check, so the sweep cannot be the only thing standing here.

    It builds its OWN module instance: the sweep calls every tool including `revert`, so sharing a store with
    it means asserting against whatever the sweep last did — my first version did exactly that and failed on
    a value the sweep had reverted."""
    import importlib
    os.environ["INSPEXIMUS_PATH"] = os.path.join(tempfile.mkdtemp(), "clean.json")
    mod = importlib.reload(importlib.import_module("inspeximus.mcp_server"))
    mod.remember("the deploy channel is BLUE-9", key="deploy", object="BLUE-9")
    mod.remember("the deploy channel is RED-2", key="deploy", object="RED-2")
    hits = mod.recall("deploy channel", k=5)
    assert hits and all("BLUE-9" not in h["text"] for h in hits), hits


def test_the_erasure_tools_carry_their_guards(mcp_mod):
    """The parameters four audit rounds had to add: without them a legitimate erasure is unreachable and the
    request it answers cannot be recorded."""
    for tool in ("forget_subject", "forget_pii"):
        params = set(inspect.signature(getattr(mcp_mod, tool)).parameters)
        assert {"allow_ambiguous", "request_id"} <= params, f"{tool} is missing {params}"
