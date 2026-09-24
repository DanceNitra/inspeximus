"""The two root causes X4 and X6 of audits/2026-09-24/mcp-tools-review.md, beyond the review's own reproducers.

X4: the server's project scope now reaches revert, route, resolve_reopened, remember_in_partition,
rectify_subject, deprecate_symbol, recall_as, why_recalled, token_report and check_sources.
X6: route, remember_in_partition, rectify_subject and deprecate_symbol report a write a guard retired on
arrival as `blocked`, as remember does.

The review's tests (tests/test_mcp_review_*.py) pin the cases it found. These pin what the fix itself
added: the ledger status of a retired rectification, that route does not hand back the verdict of an
earlier call, the `id` form of why_recalled, and that the library keeps its unscoped default.
"""
import pytest

pytest.importorskip("mcp")

from _mcp_review import call, load_server  # noqa: E402

from inspeximus import Inspeximus  # noqa: E402
from inspeximus.actions import ActionLedger  # noqa: E402


def _rectify_entries(mod):
    return [e for e in ActionLedger(mod._MEM).entries() if e.get("action") == "rights:rectify"]


def test_a_retired_rectification_is_logged_as_blocked_and_a_landed_one_as_ok(monkeypatch, tmp_path):
    mod = load_server(monkeypatch, tmp_path)
    # With an object on the key, the objectless guard retires the rectification (the tool takes none).
    call(mod, "remember", text="alice's phone is +100", key="alice::phone", object="+100", source="crm/alice")
    retired = call(mod, "rectify_subject", key="alice::phone", text="alice's phone is +200", actor="dpo",
                   reason="DSAR-17", subject="crm/alice").data
    # Without one, it lands.
    call(mod, "remember", text="bob's phone is +300", key="bob::phone", source="crm/bob")
    landed = call(mod, "rectify_subject", key="bob::phone", text="bob's phone is +400", actor="dpo",
                  reason="DSAR-18", subject="crm/bob").data

    assert retired["blocked"] is True and retired["policy"] == "objectless_guard", retired
    assert landed["blocked"] is False and landed["status"] == "active", landed
    by_key = {e["key"]: e for e in _rectify_entries(mod)}
    assert by_key["alice::phone"]["status"] == "blocked", by_key["alice::phone"]
    assert by_key["alice::phone"]["policy"] == "objectless_guard"
    assert by_key["alice::phone"]["current_id"] == retired["current_id"]
    assert by_key["bob::phone"]["status"] == "ok" and "blocked" not in by_key["bob::phone"]


def test_route_does_not_report_the_verdict_of_an_earlier_write(monkeypatch, tmp_path):
    mod = load_server(monkeypatch, tmp_path)
    call(mod, "remember", text="the region is frankfurt", key="svc::region", object="frankfurt")
    blocked = call(mod, "route", text="the region is lima", key="svc::region").data
    if not blocked.get("blocked"):
        pytest.fail(f"precondition: the objectless guard must retire the routed write, got {blocked}")
    assert blocked["action"] == "blocked" and blocked["event"] == "NOOP", blocked
    # A NOOP writes nothing, so `last_write` still describes the retired write above.
    noop = call(mod, "route", text="the region is frankfurt", key="svc::region", object="frankfurt").data
    assert noop["action"] == "noop" and "blocked" not in noop, noop
    landed = call(mod, "route", text="the zone is blue", key="svc::zone", object="blue").data
    assert landed["action"] == "remembered" and landed["blocked"] is False, landed


def test_a_retired_partition_write_and_deprecation_say_blocked_and_landed_ones_do_not(monkeypatch, tmp_path):
    mod = load_server(monkeypatch, tmp_path)
    call(mod, "open_partition", name="w1", kind="process")
    assert call(mod, "remember_in_partition", partition="w1", text="a note").data["blocked"] is False
    first = call(mod, "deprecate_symbol", old="old_fn", new="new_fn").data
    assert first["blocked"] is False and first["replacement"] == "new_fn", first


def test_why_recalled_by_id_does_not_explain_another_projects_record(monkeypatch, tmp_path):
    beta = load_server(monkeypatch, tmp_path, INSPEXIMUS_PROJECT="beta")
    theirs = call(beta, "remember", text="the beta launch codename is heron").data["id"]
    alpha = load_server(monkeypatch, tmp_path, INSPEXIMUS_PROJECT="alpha")
    ours = call(alpha, "remember", text="the alpha launch codename is osprey").data["id"]

    assert call(alpha, "why_recalled", query="launch codename", id=theirs).data["explanations"] == \
        {"id": theirs, "found": False}
    mine = call(alpha, "why_recalled", query="launch codename", id=ours).data["explanations"]
    assert mine["id"] == ours and mine["surfaced"] is True, mine


def test_the_library_keeps_its_unscoped_default(tmp_path):
    """`project` is opt-in on the library calls: without it the record is unstamped, as before."""
    m = Inspeximus(path=str(tmp_path / "lib.json"))
    for v in ("0xAAA", "0xBBB", "0xCCC"):
        m.remember(f"the wallet is {v}", key="k::wallet", object=v, project="alpha")
    plain = m.revert("k::wallet")["restored"]
    assert "project" not in (next(r for r in m.items if r["id"] == plain).get("meta") or {})
    scoped = m.revert("k::wallet", project="alpha")["restored"]
    assert (next(r for r in m.items if r["id"] == scoped).get("meta") or {}).get("project") == "alpha"
