"""3.16.2: an erased record's journal rows keep no tenant or agent id in the clear.

AUDIT-A measured on 3.15.8 that `for_tenant("jane-tenant-77").forget_subject(...)` left the tenant id in
the store file twice, in the record's `record.added` and `record.removed` rows of `memory_events`. 3.16.1
documented it as a limit. Here the erasure replaces the `agent` and `tenant` columns of every journal row
of a removed record by `pseud:` plus an HMAC under a salt kept in the key home, and `poll_events` accepts
both forms, so a tenant handle and an agent filter still find those rows.

Each test that asserts an absence carries a control: the id is in the file before the erasure, so a
fixture that stopped writing it would fail instead of passing.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus  # noqa: E402
from inspeximus import sqlite_store as rows  # noqa: E402

TENANT = "jane-tenant-77"
AGENT = "jane-assistant-agent"


def _erase_one(path, tenant=TENANT, agent=None):
    view = Inspeximus(str(path)).for_tenant(tenant)
    if agent:
        view = view.as_agent(agent)
    view.remember("Jane prefers invoices to jane@example.test", key="invoice-email",
                  source={"doc": "jane.example"})
    before = path.read_bytes()
    assert view.forget_subject("jane.example")["erased"] == 1
    return before, path.read_bytes()


def test_the_tenant_id_leaves_the_file_with_the_record(tmp_path):
    path = tmp_path / "memory.json"
    before, after = _erase_one(path)
    assert before.count(TENANT.encode()) >= 2, "control: the id is in the journal before the erasure"
    assert after.count(TENANT.encode()) == 0, after.count(TENANT.encode())


def test_the_agent_id_leaves_the_file_with_the_record(tmp_path):
    path = tmp_path / "memory.json"
    before, after = _erase_one(path, agent=AGENT)
    assert before.count(AGENT.encode()) >= 1, "control: the agent id is in the file before the erasure"
    assert after.count(AGENT.encode()) == 0, after.count(AGENT.encode())


def test_the_tenant_handle_still_finds_its_events_and_another_tenant_does_not(tmp_path):
    path = tmp_path / "memory.json"
    _erase_one(path)
    mine = Inspeximus(str(path)).for_tenant(TENANT).poll_events()
    kinds = sorted(e["type"] for e in mine if e.get("memory_id"))
    assert kinds == ["record.added", "record.removed"], kinds
    assert all(str(e["tenant"]).startswith(rows.PSEUDONYM_PREFIX) for e in mine if e.get("memory_id"))
    assert Inspeximus(str(path)).for_tenant("someone-else").poll_events() == []


def test_the_agent_filter_still_finds_the_removed_records_events(tmp_path):
    path = tmp_path / "memory.json"
    _erase_one(path, agent=AGENT)
    got = [e for e in Inspeximus(str(path)).poll_events(agent_id=AGENT) if e.get("memory_id")]
    assert sorted(e["type"] for e in got) == ["record.added", "record.removed"], got
    assert Inspeximus(str(path)).poll_events(agent_id="another-agent") == []


def test_a_kept_record_of_the_same_tenant_keeps_its_id(tmp_path):
    """Scope: only the removed record's rows change. A record that stays is the tenant's live data."""
    path = tmp_path / "memory.json"
    view = Inspeximus(str(path)).for_tenant(TENANT)
    keep = view.remember("the office opens at nine", key="hours")
    view.remember("Jane prefers invoices to jane@example.test", key="invoice-email",
                  source={"doc": "jane.example"})
    view.forget_subject("jane.example")
    kept = [e for e in Inspeximus(str(path)).poll_events() if e.get("memory_id") == keep]
    assert kept and all(e["tenant"] == TENANT for e in kept), kept


def test_an_id_that_is_already_pseudonymous_is_replaced_the_same_way(tmp_path):
    """The library cannot tell an id that names a person from one that does not, so it treats them alike;
    a caller who already uses pseudonymous ids loses nothing, the filter still works."""
    path = tmp_path / "memory.json"
    before, after = _erase_one(path, tenant="t-0001")
    assert before.count(b"t-0001") >= 2 and after.count(b"t-0001") == 0
    assert len([e for e in Inspeximus(str(path)).for_tenant("t-0001").poll_events() if e.get("memory_id")]) == 2


def test_the_salt_lives_in_the_key_home_and_the_pseudonym_is_stable(tmp_path):
    path = tmp_path / "memory.json"
    assert rows.pseudonym(path, TENANT) is None, "no salt before the first erasure"
    _erase_one(path)
    salt = rows._event_salt_path(path)
    assert os.path.exists(salt)
    assert os.path.commonpath([os.path.realpath(salt), os.path.realpath(tmp_path)]) != os.path.realpath(tmp_path)
    first = rows.pseudonym(path, TENANT)
    _erase_one(path)
    tenants = {e["tenant"] for e in Inspeximus(str(path)).poll_events() if e.get("memory_id")}
    assert tenants == {first}, tenants
    other = tmp_path / "other.json"
    _erase_one(other)
    assert rows.pseudonym(other, TENANT) not in (None, first), "a second store gets its own salt"


def test_without_a_salt_home_the_columns_are_cleared(tmp_path, monkeypatch):
    """A key home inside the store's directory would put the salt next to the data it protects, so no
    salt is minted there and the ids are cleared: the id still goes, only the tenant filter is lost."""
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "inside"))
    path = tmp_path / "memory.json"
    before, after = _erase_one(path)
    assert before.count(TENANT.encode()) >= 2 and after.count(TENANT.encode()) == 0
    ev = [e for e in Inspeximus(str(path)).poll_events() if e.get("memory_id")]
    assert ev and all(e["tenant"] is None for e in ev), ev
    assert [t.get("tenant") for t in Inspeximus(str(path))._tombstones] == [None]
    mine, withheld = Inspeximus(str(path)).for_tenant(TENANT)._visible_tombstones()
    assert (len(mine), withheld) == (0, 1), "an erasure the handle cannot attribute is counted, not hidden"


# ── the class: every file beside the store and the certificate, for every field of the erased record ──
#
# AUDIT-A on b49d6775 (F-1): the journal was clean and the tenant id still sat in `<store>.tombstones.json`
# and in `erasure_certificate()`, because the test above read one file. These scan the whole directory.

FIELDS = {"tenant": b"zq-tenant-7741", "agent": b"zq-assistant-agent", "key": b"zq-invoice-email",
          "text": b"zq@example.test", "source": b"zq.example"}


def _beside(path):
    d = os.path.dirname(str(path))
    return {n: open(os.path.join(d, n), "rb").read() for n in os.listdir(d) if os.path.isfile(os.path.join(d, n))}


def _held(path):
    files = _beside(path)
    return {f: sorted(n for n, b in files.items() if needle in b) for f, needle in FIELDS.items()
            if any(needle in b for b in files.values())}


def test_f1_the_tenant_id_leaves_the_tombstone_sidecar_and_the_certificate(tmp_path, monkeypatch):
    """AUDIT-A's test as handed over: the whole directory and the certificate, after one erasure."""
    import json
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keyhome"))
    p = str(tmp_path / "memory.json")
    t = "zq-tenant-7741"
    view = Inspeximus(p).for_tenant(t)
    view.remember("Zq prefers invoices to zq@example.test", key="invoice-email", source={"doc": "zq.example"})
    assert sum(b.count(t.encode()) for b in _beside(p).values()) >= 2, "control: the id is in the store before"
    assert view.forget_subject("zq.example")["erased"] == 1
    held = {n: b.count(t.encode()) for n, b in _beside(p).items() if t.encode() in b}
    assert not held, f"the tenant id of an erased record is still in: {held}"
    assert t not in json.dumps(Inspeximus(p).erasure_certificate())


def test_no_field_of_an_erased_record_is_left_in_any_file_or_the_certificate(tmp_path, monkeypatch):
    """Receipts on, so the receipt sidecar exists beside the tombstones; a tenant, an agent, a personal key.
    The key home is a SIBLING of the store's directory: inside it, no salt is minted and the ids are cleared,
    which passes an absence check without exercising the pseudonym at all."""
    import json
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keyhome"))
    (tmp_path / "store").mkdir()
    p = tmp_path / "store" / "memory.json"
    view = Inspeximus(str(p), receipts=True).for_tenant("zq-tenant-7741").as_agent("zq-assistant-agent")
    view.remember("Zq prefers invoices to zq@example.test", key="zq-invoice-email", source={"doc": "zq.example"})
    # A record under ANOTHER tenant and agent keeps every sidecar non-empty after the erasure; one under the
    # same tenant would carry its ids in the clear as live data and hide a leak of the erased record's.
    Inspeximus(str(p), receipts=True).for_tenant("other-tenant").as_agent("other-agent").remember(
        "the office opens at nine", key="hours")
    before = _held(p)
    assert set(before) == set(FIELDS), f"control: every field is in the store before the erasure: {before}"
    assert view.forget_subject("zq.example")["erased"] == 1
    after = _held(p)
    assert not after, f"a field of the erased record is still in a file: {after}"
    stamps = [t.get("tenant") for t in Inspeximus(str(p))._tombstones]
    assert stamps == [rows.pseudonym(p, "zq-tenant-7741")] and stamps[0].startswith(rows.PSEUDONYM_PREFIX), stamps
    assert sorted(os.listdir(p.parent)) == ["memory.json", "memory.json.receipts.json", "memory.json.tombstones.json"]
    mine, withheld = Inspeximus(str(p)).for_tenant("zq-tenant-7741")._visible_tombstones()
    assert len(mine) == 1 and withheld == 0, "the tenant handle still finds its own erasure"
    cert = json.dumps(Inspeximus(str(p)).erasure_certificate())
    leaked = [f for f, needle in FIELDS.items() if needle.decode() in cert]
    assert not leaked, f"the certificate carries {leaked}"
