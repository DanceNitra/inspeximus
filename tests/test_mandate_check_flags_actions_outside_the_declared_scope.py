"""A declared mandate is checked at write time, signed into the entry, and reported.

The case it exists for: an agent asked to read one set of sites acts on another. The ledger already
recorded what the agent knew and did; nothing told anyone that the action was outside what it was asked
to do until someone read the log. These tests hold the check to three properties:

1. the verdict is computed when the action is recorded and is covered by the entry hash, so editing it
   later breaks verification;
2. an action outside the mandate reaches `on_mandate_breach` at once, and a failing alert does not lose
   the entry;
3. a control: with no mandate declared, nothing is checked and nothing is flagged, so a pass is not
   the check being absent.
"""
import json

import pytest

from inspeximus.actions import ActionLedger


def _ledger(tmp_path, **kw):
    return ActionLedger(path=tmp_path / "actions.json", actor="agent-1", **kw)


def test_no_mandate_means_no_check_and_no_breach(tmp_path):
    led = _ledger(tmp_path)
    e = led.record("http:GET", target="https://portal.example.gov/records")
    assert "mandate_check" not in e
    rep = led.mandate_breaches()
    assert rep == {"breaches": [], "outside": 0, "checked": 0, "unchecked": 1, "first_breach_ts": None,
                   "mandates": []}


def test_action_inside_and_outside_the_mandate(tmp_path):
    led = _ledger(tmp_path)
    m = led.mandate(["http:GET"], actor="operator", targets=["https://data.example.gov/*"])
    ok = led.record("http:GET", target="https://data.example.gov/tables/1")
    wrong_target = led.record("http:GET", target="https://portal.example.gov/login")
    wrong_action = led.record("http:POST", target="https://data.example.gov/tables/1")
    no_target = led.record("http:GET")
    assert ok["mandate_check"] == {"mandate_seq": m["seq"], "within": True, "reasons": []}
    assert wrong_target["mandate_check"]["within"] is False
    assert "portal.example.gov" in wrong_target["mandate_check"]["reasons"][0]
    assert wrong_action["mandate_check"]["within"] is False
    assert no_target["mandate_check"]["reasons"] == ["no target recorded while the mandate restricts targets"]
    rep = led.mandate_breaches()
    assert [r["seq"] for r in rep["breaches"]] == [wrong_target["seq"], wrong_action["seq"], no_target["seq"]]
    assert rep["checked"] == 4 and rep["unchecked"] == 0 and rep["outside"] == 3
    assert rep["first_breach_ts"] == wrong_target["ts"]


def test_the_verdict_is_signed_into_the_chain(tmp_path):
    led = _ledger(tmp_path)
    led.mandate(["http:GET"], actor="operator", targets=["https://data.example.gov/*"])
    led.record("http:GET", target="https://portal.example.gov/login")
    assert led.verify()[0] is True
    raw = json.loads((tmp_path / "actions.json").read_text(encoding="utf-8"))
    raw[-1]["mandate_check"]["within"] = True          # someone hides the breach after the fact
    raw[-1]["mandate_check"]["reasons"] = []
    (tmp_path / "actions.json").write_text(json.dumps(raw), encoding="utf-8")
    ok, problems = ActionLedger(path=tmp_path / "actions.json").verify()
    assert ok is False and problems


def test_breach_alert_fires_once_per_breach_and_a_failing_alert_keeps_the_entry(tmp_path):
    seen = []
    led = _ledger(tmp_path, on_mandate_breach=seen.append)
    led.mandate(["tool:*"], actor="operator")
    led.record("tool:search")
    led.record("shell:rm")
    assert [e["action"] for e in seen] == ["shell:rm"]

    def boom(_e):
        raise RuntimeError("pager down")

    (tmp_path / "b").mkdir()
    led2 = _ledger(tmp_path / "b", on_mandate_breach=boom)
    led2.mandate(["tool:*"], actor="operator")
    e = led2.record("shell:rm")
    assert e["mandate_check"]["within"] is False
    assert led2.mandate_breaches()["outside"] == 1


def test_a_mandate_for_one_actor_does_not_judge_another(tmp_path):
    led = _ledger(tmp_path)
    led.mandate(["tool:search"], actor="operator", for_actor="agent-1")
    a = led.record("shell:rm", actor="agent-1")
    b = led.record("shell:rm", actor="agent-2")
    assert a["mandate_check"]["within"] is False
    assert "mandate_check" not in b


def test_a_later_mandate_does_not_rejudge_earlier_actions(tmp_path):
    led = _ledger(tmp_path)
    early = led.record("shell:rm")
    led.mandate(["tool:*"], actor="operator")
    assert "mandate_check" not in led._entries[early["seq"] - led.base_seq]
    assert led.mandate_breaches()["unchecked"] == 1


def test_action_context_manager_passes_the_target(tmp_path):
    led = _ledger(tmp_path)
    led.mandate(["http:GET"], actor="operator", targets=["https://data.example.gov/*"])
    with led.action("http:GET", target="https://portal.example.gov/login") as ctx:
        ctx.output("200")
    assert ctx.entry["target"] == "https://portal.example.gov/login"
    assert ctx.entry["mandate_check"]["within"] is False


def test_empty_mandate_is_refused(tmp_path):
    with pytest.raises(ValueError):
        _ledger(tmp_path).mandate([], actor="operator")


# ---------------------------------------------------------------- red-team findings, 2026-09-28

def test_a_mandate_keeps_a_store_backed_ledger_verifiable(tmp_path):
    import os
    pytest.importorskip("cryptography")
    from inspeximus import Inspeximus
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=os.urandom(32).hex())
    led = ActionLedger(m, actor="agent")
    led.record("tool:search")
    led.mandate(["tool:*"], actor="operator")
    led.record("tool:search")
    ok, problems = led.verify()
    assert ok, problems


def test_every_declaration_is_listed_and_a_self_declared_one_is_marked(tmp_path):
    led = ActionLedger(path=tmp_path / "actions.json", actor="agent-1")   # the agent's own handle
    led.mandate(["http:GET"], actor="operator", for_actor="agent-1")
    led.mandate(["*"], actor="agent-1", for_actor="agent-1")               # the agent widens its own scope
    ms = led.mandate_breaches()["mandates"]
    assert [x["actions"] for x in ms] == [["http:GET"], ["*"]]
    assert all(x["declared_by"] == "agent-1" for x in ms)
    assert [x["self_declared"] for x in ms] == [True, True]                # written by the handle it governs


def test_url_targets_match_host_by_label_and_path_normalised(tmp_path):
    led = _ledger(tmp_path)
    led.mandate(["http:GET"], actor="operator", targets=["https://*.example.gov/*"])
    within = {t: led.record("http:GET", target=t)["mandate_check"]["within"] for t in [
        "https://data.example.gov/x",
        "https://evil.com/.example.gov/x",
        "https://evil.com?.example.gov/x",
        "https://evil.com#.example.gov/x",
        "https://data.example.gov.evil.com/x",
        "https://user@data.example.gov/x",
        "https://data.example.gov:8443/x",
        "http://data.example.gov/x",
    ]}
    assert within == {"https://data.example.gov/x": True, "https://evil.com/.example.gov/x": False,
                      "https://evil.com?.example.gov/x": False, "https://evil.com#.example.gov/x": False,
                      "https://data.example.gov.evil.com/x": False, "https://user@data.example.gov/x": False,
                      "https://data.example.gov:8443/x": False, "http://data.example.gov/x": False}
    led2 = ActionLedger(path=tmp_path / "b.json", actor="agent-1")
    led2.mandate(["http:GET"], actor="operator", targets=["https://data.example.gov/public/*"])
    assert led2.record("http:GET", target="https://data.example.gov/public/../admin")["mandate_check"]["within"] is False
    assert led2.record("http:GET", target="https://DATA.example.gov./public/a")["mandate_check"]["within"] is True


def test_a_bare_string_is_refused(tmp_path):
    with pytest.raises(TypeError):
        _ledger(tmp_path).mandate("http:GET", actor="operator")
    with pytest.raises(TypeError):
        _ledger(tmp_path).mandate(["http:GET"], actor="operator", targets="https://a.example/*")


def test_an_actor_mandate_outranks_a_later_global_one(tmp_path):
    led = _ledger(tmp_path)
    led.mandate(["http:GET"], actor="operator", for_actor="agent-1")
    led.mandate(["*"], actor="operator")
    assert led.record("rm:rf", actor="agent-1")["mandate_check"]["within"] is False
    assert led.record("rm:rf", actor="agent-2")["mandate_check"]["within"] is True


def test_archive_keeps_the_mandate_in_force_live(tmp_path):
    led = _ledger(tmp_path)
    led.record("tool:old")
    led.mandate(["tool:*"], actor="operator")
    led.record("tool:search")
    led.archive(before_ts=led._entries[-1]["ts"] + 1)
    e = led.record("shell:rm")
    assert e["mandate_check"]["within"] is False
    assert led.verify()[0] is True


def test_the_alert_gets_a_copy_it_cannot_corrupt(tmp_path):
    def meddle(entry):
        entry["mandate_check"]["within"] = True
    led = _ledger(tmp_path, on_mandate_breach=meddle)
    led.mandate(["tool:*"], actor="operator")
    led.record("shell:rm")
    assert led.mandate_breaches()["outside"] == 1
    assert led.verify()[0] is True
