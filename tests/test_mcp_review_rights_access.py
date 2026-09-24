"""MCP tool review, family: SUBJECT RIGHTS, ACCESS GRANTS, EVENTS AND THE CODE GUARD.

Part of audits/2026-09-24/mcp-tools-review.md. Tools: export_subject, record_objection, resolve_objection,
objections, rectify_subject, grant, revoke, grants, grant_log, can_read, recall_as, get_as,
poll_memory_events, subscribe_memory_event, deprecate_symbol, symbol_status, check_code.

Each test holds one tool to a sentence of its OWN description (the docstring an MCP client is shown) or
to the server's documented configuration, and fails today. They are strict xfails: the day the tool is
fixed, the test XPASSes and the marker has to come off. Preconditions go through `pytest.fail`, so a
setup that did not do what the test needs fails loudly instead of passing as an expected failure.
Every test runs on a throwaway store in tmp_path (see tests/_mcp_review.py).
"""
import importlib.util

import pytest

pytest.importorskip("mcp")

from _mcp_review import ToolResult, call, load_server  # noqa: E402

XFAIL = dict(strict=True, raises=AssertionError)


@pytest.fixture
def server(monkeypatch, tmp_path):
    """A fresh server on tmp_path/store.json; call it again with other env to reopen the same file."""
    def _load(**env):
        return load_server(monkeypatch, tmp_path, **env)
    return _load


def _peer_server(mod):
    """A SECOND server on the same store file: a separate module instance with its own `_MEM` handle,
    which is what a second MCP server process (or the Claude Code hook) holds. Read from the same
    environment load_server() set, so it opens the same path."""
    spec = importlib.util.spec_from_file_location("inspeximus._review_peer_server", mod.__file__)
    peer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(peer)
    if peer._MEM is mod._MEM or str(peer._PATH) != str(mod._PATH):
        pytest.fail(f"precondition: the peer must be a separate handle on the same file ({peer._PATH})")
    return peer


def _record(mod, rid):
    return next((r for r in mod._MEM.items if r.get("id") == rid), None)


def _ids(result: ToolResult) -> list:
    if result.is_error:
        pytest.fail(f"precondition: the read must answer, got {result.text}")
    return [h.get("id") for h in result.data]


# ── rectify_subject ─────────────────────────────────────────────────────────────────────────────────
def test_rectify_subject_reports_a_rectification_the_objectless_guard_retired(server):
    """rectify_subject: "GDPR Art. 16 rectification: supersede the value under `key` with `text` through the
    ordinary keyed write (every write guard applies), and record who asked and why as a rights:rectify
    entry on the action ledger bound to the memory receipt."

    The tool takes no `object`, so on a key whose value was written with one (as `remember` asks callers
    to do) the objectless guard retires every rectification on arrival. The guard applying is described;
    a result that then reads exactly like a landed correction is not: `remember` returns `blocked: true`
    and `policy` for the same write.
    """
    mod = server()
    call(mod, "remember", text="alice's phone is +100", key="alice::phone", object="+100", source="crm/alice")
    res = call(mod, "rectify_subject", key="alice::phone", text="alice's phone is +200", actor="dpo",
               reason="DSAR-17", subject="crm/alice")
    if res.is_error:
        pytest.fail(f"precondition: rectify_subject must answer, got {res.text}")
    new = _record(mod, res.data.get("new_id"))
    if new is None or new.get("status") != "superseded" \
            or (new.get("meta") or {}).get("superseded_by_policy") != "objectless_guard":
        pytest.fail(f"precondition: the objectless guard must have retired the correction, record={new}")
    said = res.data
    assert said.get("blocked") is True or "error" in said, \
        f"rectify_subject answered {said} while the correction it wrote was retired on arrival and " \
        f"recall still serves the old value"


def test_rectify_subject_stamps_the_servers_project_scope(server):
    """rectify_subject: "supersede the value under `key` with `text` through the ordinary keyed write".
    Server config: "INSPEXIMUS_PROJECT ... Writes are stamped with it and recalls are filtered to it".

    The ordinary keyed write on this server (`remember`) stamps the project. The rectification does not,
    so a subject's corrected value, whose original was confined to project alpha, becomes a GLOBAL record
    that a server scoped to any other project recalls.
    """
    mod = server(INSPEXIMUS_PROJECT="alpha")
    first = call(mod, "remember", text="alice's phone is +100", key="alice::phone", source="crm/alice").data
    if (_record(mod, first["id"]).get("meta") or {}).get("project") != "alpha":
        pytest.fail("precondition: remember must stamp the project on this server")
    res = call(mod, "rectify_subject", key="alice::phone", text="alice's phone is +200", actor="dpo",
               reason="DSAR-17", subject="crm/alice")
    new = _record(mod, (res.data or {}).get("new_id"))
    if res.is_error or new is None or new.get("status") != "active":
        pytest.fail(f"precondition: the rectification must land, got {res.data} / {new}")
    assert (new.get("meta") or {}).get("project") == "alpha", \
        f"the rectified record carries project={(new.get('meta') or {}).get('project')!r} on a server " \
        f"scoped to 'alpha'; it is now visible from every project"


# ── record_objection ────────────────────────────────────────────────────────────────────────────────
def test_record_objection_is_honoured_by_every_server_on_the_store(server):
    """record_objection: "GDPR Art. 21: record the subject's objection and stop serving their records. From
    this call on, recall withholds every record whose source resolves to `subject`, including later
    writes, until the objection is resolved."

    The objection lives in `<store>.objections.json`, read once when a handle opens. A second server on
    the same file (a second MCP process, the Claude Code hook) never re-reads it: its per-call refresh
    only looks at the store file, which recording an objection does not touch.
    """
    mod = server()
    rid = call(mod, "remember", text="alice prefers the vegetarian menu at the offsite", source="crm/alice").data["id"]
    peer = _peer_server(mod)
    if rid not in _ids(call(peer, "recall", query="vegetarian menu offsite")):
        pytest.fail("precondition: the peer must see the record before the objection")
    res = call(peer, "record_objection", subject="crm/alice", actor="dpo", ground="own_situation")
    if res.is_error or (res.data or {}).get("status") != "standing":
        pytest.fail(f"precondition: the objection must be recorded, got {res.data or res.text}")
    if rid in _ids(call(peer, "recall", query="vegetarian menu offsite")):
        pytest.fail("precondition: the server that recorded the objection must withhold the record")
    assert rid not in _ids(call(mod, "recall", query="vegetarian menu offsite")), \
        "a standing Art. 21 objection on this store, and this server's recall still serves the subject's record"


@pytest.mark.parametrize("tool", ["memory_index", "verify_claim"])
def test_record_objection_stops_serving_the_subject_outside_recall(server, tool):
    """record_objection: "GDPR Art. 21: record the subject's objection and stop serving their records. From
    this call on, recall withholds every record whose source resolves to `subject` ..."

    Every recall variant withholds. `memory_index`, described as "THE ALWAYS-LOADED INDEX", still carries
    the record's line, and `verify_claim` answers `supported` and quotes the record. The library filters
    only recall's pool (core.py:12727-12733).
    """
    import json

    mod = server()
    rid = call(mod, "remember", text="alice takes insulin daily and is allergic to shellfish",
               source="crm/alice").data["id"]
    call(mod, "remember", text="the offsite menu has no shellfish", source="events")
    obj = call(mod, "record_objection", subject="crm/alice", actor="dpo", ground="own_situation")
    if obj.is_error or (obj.data or {}).get("status") != "standing":
        pytest.fail(f"precondition: the objection must be recorded, got {obj.data or obj.text}")
    if rid in _ids(call(mod, "recall", query="alice insulin shellfish")):
        pytest.fail("precondition: recall must withhold the record")
    if tool == "memory_index":
        out = call(mod, "memory_index").data
    else:
        out = call(mod, "verify_claim", text="alice takes insulin daily").data
    assert "insulin" not in json.dumps(out), f"{tool} still serves the withheld record: {out}"


# ── export_subject (and every rights tool: record_objection, resolve_objection, rectify_subject) ────
def test_export_subject_rights_entry_keeps_the_signed_action_ledger_verifiable(server):
    """export_subject: "Writes one rights:export entry to the action ledger carrying the export's manifest
    hash." Server config: "INSPEXIMUS_ACTIONS 1 to record every tool call in the ACTION LEDGER: one signed,
    hash-chained entry per call"; INSPEXIMUS_WRITER_KEY: "this server signs its own writes".

    The tool-boundary ledger signs with the writer key; the rights tools build a second ActionLedger with
    no key, so their entry lands unsigned in a signed chain, and `actions_verify` (which requires every
    entry signed once any is) reports the ledger broken after one legitimate access request.
    """
    from inspeximus.core import new_source_keypair

    sk, _pk = new_source_keypair()
    mod = server(INSPEXIMUS_WRITER_KEY=sk, INSPEXIMUS_ACTIONS="1", INSPEXIMUS_RECEIPTS="1")
    call(mod, "remember", text="alice's phone is +100", key="alice::phone", source="crm/alice")
    before = call(mod, "actions_verify")
    if before.is_error or not before.data.get("ok"):
        pytest.fail(f"precondition: the signed ledger must verify before the export, got {before.data or before.text}")
    exp = call(mod, "export_subject", subject="crm/alice")
    if exp.is_error or "error" in (exp.data or {}) or not (exp.data or {}).get("ledger_entry"):
        pytest.fail(f"precondition: the export must write its rights entry, got {exp.data or exp.text}")
    after = call(mod, "actions_verify").data
    assert after.get("ok") is True, \
        f"one export_subject later actions_verify reports {after.get('problems')}"


# ── recall_as ───────────────────────────────────────────────────────────────────────────────────────
def test_recall_as_honours_the_servers_project_scope(server):
    """recall_as: "Recall AS a named agent: the same ranking as `recall`, hard-filtered to what that agent
    owns or has an active grant for." Server config: "INSPEXIMUS_PROJECT ... recalls are filtered to it".

    `recall`, `recall_iterative`, `recall_followup` and `neighbors` pass the server's project to the store;
    `recall_as` calls `as_agent(agent).recall(query, k=k)` without it, so the SCOPED read is the one read
    on this server that crosses projects.
    """
    mod = server(INSPEXIMUS_PROJECT="beta")
    beta = call(mod, "remember", text="the beta roadmap ships in march", tags=["roadmap"]).data["id"]
    mod = server(INSPEXIMUS_PROJECT="alpha")
    alpha = call(mod, "remember", text="the alpha roadmap ships in june", tags=["roadmap"]).data["id"]
    call(mod, "grant", agent="bob", tag="roadmap")
    if _ids(call(mod, "recall", query="roadmap ships")) != [alpha]:
        pytest.fail("precondition: the operator recall on project alpha must see only alpha's record")
    got = _ids(call(mod, "recall_as", agent="bob", query="roadmap ships"))
    if alpha not in got:
        pytest.fail(f"precondition: bob's grant must reach the alpha record, got {got}")
    assert beta not in got, "recall_as on a server scoped to 'alpha' returned project beta's record"


# ── subscribe_memory_event / poll_memory_events ─────────────────────────────────────────────────────
def test_subscribe_memory_event_default_cursor_receives_the_later_events(server):
    """subscribe_memory_event: "Start a tail: returns the cursor to poll from ({event_type, since_seq}) ...
    call `poll_memory_events` with this `since_seq` (and `event_type`) to receive everything published
    after this moment."

    The default subscription returns event_type "*", and poll_memory_events hands "*" to the event table
    as a literal `type = '*'` filter.
    """
    mod = server()
    sub = call(mod, "subscribe_memory_event").data
    call(mod, "remember", text="the deploy runs on friday")
    call(mod, "remember", text="the freeze starts on thursday")
    everything = call(mod, "poll_memory_events", since_seq=sub["since_seq"]).data["events"]
    if len(everything) < 2:
        pytest.fail(f"precondition: the two writes must publish events, got {everything}")
    got = call(mod, "poll_memory_events", since_seq=sub["since_seq"], event_type=sub["event_type"]).data
    assert [e["seq"] for e in got["events"]] == [e["seq"] for e in everything], \
        f"polling the cursor subscribe_memory_event returned ({sub}) gave {got}"


def test_poll_memory_events_on_a_json_store_does_not_read_as_no_changes(server):
    """poll_memory_events: "What changed in the store since `since_seq`, from the `memory_events` table the
    row writer appends to ... Another process's write is visible on the next call."

    A JSON-format store (INSPEXIMUS_STORE_FORMAT=json, an encrypted store, or a JSON store whose conversion
    to rows failed on open) has no event table. The library's publish_event raises there; poll_events
    returns [] and events_tip 0, so the tool reports an empty feed as if nothing had changed.
    """
    mod = server(INSPEXIMUS_STORE_FORMAT="json")
    if mod._MEM._rows_available():
        pytest.fail("precondition: the store must be JSON-format")
    call(mod, "remember", text="the deploy runs on friday")
    call(mod, "remember", text="the freeze starts on thursday")
    if len(mod._MEM.items) != 2:
        pytest.fail("precondition: both writes must be in the store")
    res = call(mod, "poll_memory_events", since_seq=0)
    said = res.data if isinstance(res.data, dict) else {}
    assert res.is_error or "error" in said or said.get("events"), \
        f"two writes landed and poll_memory_events answered {said}"


# ── deprecate_symbol ────────────────────────────────────────────────────────────────────────────────
def test_deprecate_symbol_reports_a_deprecation_the_echo_guard_retired(server):
    """deprecate_symbol: "A later deprecate_symbol of the same `old` supersedes the replacement. Then call
    check_code(generated) before emitting code. Returns the recorded deprecation."

    old_fn -> new_fn, then old_fn -> newer_fn, then old_fn -> new_fn again. The third write restates a
    retired object under the same key, so the echo guard retires it on arrival; the tool returns
    {replacement: new_fn} while symbol_status and check_code keep answering newer_fn.
    """
    mod = server()
    call(mod, "deprecate_symbol", old="old_fn", new="new_fn", reason="rename")
    call(mod, "deprecate_symbol", old="old_fn", new="newer_fn", reason="rename again")
    res = call(mod, "deprecate_symbol", old="old_fn", new="new_fn", reason="back to new_fn")
    if res.is_error:
        pytest.fail(f"precondition: deprecate_symbol must answer, got {res.text}")
    status = call(mod, "symbol_status", name="old_fn").data
    lw = getattr(mod._MEM, "last_write", None) or {}
    if not (lw.get("blocked") and lw.get("policy") == "echo_guard"):
        pytest.fail(f"precondition: the echo guard must have retired the third write, last_write={lw}")
    said = res.data
    assert said.get("blocked") is True or said.get("replacement") == status.get("replacement"), \
        f"deprecate_symbol returned {said} as the recorded deprecation; symbol_status says {status}"


def test_deprecate_symbol_stamps_the_servers_project_scope(server):
    """deprecate_symbol: "CODING-AGENT REFACTOR RECORD (write, deterministic, no LLM)". Server config:
    "INSPEXIMUS_PROJECT project/workspace scope for ONE store shared across several repos. Writes are
    stamped with it and recalls are filtered to it".

    code_guard.deprecate_symbol calls store.remember without a project, so a rename recorded in repo
    alpha is an unscoped record that every other project's recall serves.
    """
    mod = server(INSPEXIMUS_PROJECT="alpha")
    res = call(mod, "deprecate_symbol", old="connect", new="open_session", reason="api v2")
    recs = [r for r in mod._MEM.items if r.get("key") == "code::symbol::connect"]
    if res.is_error or len(recs) != 1 or recs[0].get("status") != "active":
        pytest.fail(f"precondition: the deprecation must be recorded, got {res.data or res.text} / {recs}")
    assert (recs[0].get("meta") or {}).get("project") == "alpha", \
        f"the deprecation carries project={(recs[0].get('meta') or {}).get('project')!r} on a server scoped to 'alpha'"
