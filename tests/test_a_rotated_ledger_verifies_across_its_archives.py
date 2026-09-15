"""Rotating the action ledger (Art. 19 / Art. 26(6) retention) without breaking the chain.

`archive()` moves old entries to an archive file and starts the live file with a signed checkpoint
that names the archive, its hash and the archived tail. Controls: the chain verifies across the
files; the live file alone reports the archived range as not verified rather than passing; a
rewritten archive, a rewritten checkpoint, a checkpoint signed by another key and a cut that would
strand a reference all fail or are refused; an open incident is never archived; seq numbers keep
counting; two rotations chain; the retention attestation says floor_observed only when it is.
"""
import json
import time

import pytest

from inspeximus import Inspeximus, new_receipt_keypair
from inspeximus.actions import ActionLedger, verify_file, _entry_hash, _sign

pytest.importorskip("cryptography")
DAY = 86400.0


def _ledger(tmp_path, n=6, spacing_days=30, signed=True):
    """n actions, spaced `spacing_days` apart, backdated so the first is n*spacing days old."""
    sk, pk = new_receipt_keypair()
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=sk if signed else None)
    m.remember("the limit is 50", key="limit")
    led = ActionLedger(m, actor="agent")
    now = time.time()
    for i in range(n):
        led.record(f"tool:{i}")
    # backdate in place and re-sign, the way a long-running ledger would have been written
    data = json.loads(led.path.read_text(encoding="utf-8"))
    prev = "0" * 64
    for i, e in enumerate(data):
        e["ts"] = now - (n - i) * spacing_days * DAY
        e["started"] = e["ts"]
        e["prev"] = prev
        e.pop("hash", None); e.pop("sig", None); e.pop("pubkey", None)
        e["hash"] = _entry_hash(e)
        if signed:
            e["sig"], e["pubkey"] = _sign(sk, e["hash"])
        prev = e["hash"]
    led.path.write_text(json.dumps(data), encoding="utf-8")
    led.reload()
    assert led.verify(expected_pubkey=pk if signed else None)[0]
    return m, led, sk, pk, now


def test_the_chain_verifies_across_the_live_file_and_the_archive(tmp_path):
    m, led, sk, pk, now = _ledger(tmp_path)
    res = led.archive(keep_days=100, actor="ops", now=now)         # entries 0..2 are 180, 150, 120 days old
    assert res["archived"] == 3 and res["archived_through"] == 2 and res["live_entries"] == 3
    assert led.base_seq == 3 and led.archived["archive_chain"] == [res["archive_file"]]
    assert (tmp_path / res["archive_file"]).exists()
    assert led.verify(expected_pubkey=pk) == (True, [])
    assert verify_file(led.path, expected_pubkey=pk) == (True, [])
    # a new entry continues the numbering and the chain from the archived tail
    e = led.record("tool:after")
    assert e["seq"] == 6 and led.verify(expected_pubkey=pk) == (True, [])
    # a fresh handle reads the same state
    led2 = ActionLedger(m)
    assert led2.base_seq == 3 and len(led2) == 4 and led2.verify(expected_pubkey=pk) == (True, [])
    assert led2.oldest_ts() == pytest.approx(now - 6 * 30 * DAY)


def test_the_live_file_alone_reports_the_archived_range_as_not_verified(tmp_path):
    m, led, sk, pk, now = _ledger(tmp_path)
    res = led.archive(keep_days=100, actor="ops", now=now)
    (tmp_path / res["archive_file"]).unlink()
    ok, problems = verify_file(led.path, expected_pubkey=pk)
    assert not ok and any("not present" in p and "3 archived entries" in p for p in problems)
    ok, problems = led.verify(expected_pubkey=pk)
    assert not ok and any("not present" in p for p in problems)


def test_a_rewritten_archive_and_a_rewritten_checkpoint_both_fail(tmp_path):
    m, led, sk, pk, now = _ledger(tmp_path)
    res = led.archive(keep_days=100, actor="ops", now=now)
    arc = tmp_path / res["archive_file"]
    original = arc.read_bytes()
    data = json.loads(original)
    data[1]["action"] = "tool:forged"
    arc.write_bytes(json.dumps(data, indent=1).encode())
    ok, problems = verify_file(led.path, expected_pubkey=pk)
    assert not ok and any("archive_sha256" in p for p in problems)
    arc.write_bytes(original)
    assert verify_file(led.path, expected_pubkey=pk)[0]              # CONTROL: restored, it passes
    # the checkpoint itself: change the range it claims, keep its signature
    live = json.loads(led.path.read_text(encoding="utf-8"))
    live[0]["archived_through"] = 1
    led.path.write_text(json.dumps(live), encoding="utf-8")
    ok, problems = verify_file(led.path, expected_pubkey=pk)
    assert not ok and any("checkpoint: hash does not match" in p for p in problems)
    # re-hash and re-sign the checkpoint with a DIFFERENT key
    sk2, pk2 = new_receipt_keypair()
    live[0].pop("hash"); live[0].pop("sig"); live[0].pop("pubkey")
    live[0]["hash"] = _entry_hash(live[0])
    live[0]["sig"], live[0]["pubkey"] = _sign(sk2, live[0]["hash"])
    led.path.write_text(json.dumps(live), encoding="utf-8")
    ok, problems = verify_file(led.path, expected_pubkey=pk)
    assert not ok and any("unexpected key" in p for p in problems)


def test_the_cut_moves_earlier_so_no_kept_entry_points_into_the_archive(tmp_path):
    m, led, sk, pk, now = _ledger(tmp_path)
    led.oversight("review", "dpo", refers_to=1)                      # a recent event about an old action
    res = led.archive(keep_days=100, actor="ops", now=now)
    assert res["archived"] == 1 and led.base_seq == 1                # only seq 0 could go
    assert led.verify(expected_pubkey=pk) == (True, [])
    # CONTROL: a reference into the archive that a tamperer writes in fails to resolve without it
    live = json.loads(led.path.read_text(encoding="utf-8"))
    live[-1]["refers_to"] = {"seq": 0, "hash": "00" * 32}
    led.path.write_text(json.dumps(live), encoding="utf-8")
    ok, problems = verify_file(led.path)
    assert not ok


def test_an_open_incident_is_never_archived_and_a_reported_one_is(tmp_path):
    sk, pk = new_receipt_keypair()
    m = Inspeximus(str(tmp_path / "mem.json"), receipts=True, receipt_key=sk)
    led = ActionLedger(m, actor="agent")
    led.record("tool:a")
    led.incident("old and open", "serious", "dpo")
    led.record("tool:b")
    far = time.time() + 400 * DAY                                     # everything is old from here
    res = led.archive(keep_days=100, actor="ops", now=far)
    assert res["archived"] == 1 and led.base_seq == 1                # stopped at the open incident
    assert led.oversight_report()["incidents"] == 1
    # CONTROL: report it, and the next rotation takes it (the report entry is recent, so it stays)
    assert led.incident_report(1)["clock"]["reported"] is False
    led2 = ActionLedger(m)
    r = led2.incident_reported(1, actor="dpo", reported_to="market surveillance authority", reported_ts=far)
    assert r["evidence"][0]["seq"] == 1 and led2.incident_report(1)["clock"]["reported"] is True
    assert led2.oversight_report()["incidents"] == 1 and led2.oversight_report()["incidents_overdue"] == []
    res2 = led2.archive(before_ts=far + 1, actor="ops", now=far + 2)  # incident, tool:b and the report entry
    assert res2["archived"] == 3 and led2.base_seq == 4 and led2.verify(expected_pubkey=pk) == (True, [])
    assert led2.archived["archive_chain"] == [res["archive_file"], res2["archive_file"]]


def test_two_rotations_chain_and_a_missing_middle_archive_is_reported(tmp_path):
    m, led, sk, pk, now = _ledger(tmp_path, n=8, spacing_days=30)
    r1 = led.archive(keep_days=200, actor="ops", now=now)            # 240, 210 days old
    r2 = led.archive(keep_days=100, actor="ops", now=now)            # 180 .. 120 days old
    assert r1["archived"] == 2 and r2["archived"] == 3 and r2["archived_through"] == 4
    assert led.archived["archive_chain"] == [r1["archive_file"], r2["archive_file"]]
    assert led.verify(expected_pubkey=pk) == (True, [])
    assert verify_file(led.path, expected_pubkey=pk) == (True, [])
    (tmp_path / r1["archive_file"]).unlink()
    ok, problems = verify_file(led.path, expected_pubkey=pk)
    assert not ok and any(r1["archive_file"] in p and "not present" in p for p in problems)


def test_nothing_old_enough_writes_nothing(tmp_path):
    m, led, sk, pk, now = _ledger(tmp_path)
    before = led.path.read_bytes()
    res = led.archive(keep_days=400, actor="ops", now=now)
    assert res["archived"] == 0 and res["archive_file"] is None
    assert led.path.read_bytes() == before and led.archived is None
    with pytest.raises(ValueError):
        led.archive()


def test_the_retention_attestation_says_floor_observed_only_when_it_is(tmp_path):
    m, led, sk, pk, now = _ledger(tmp_path, n=3, spacing_days=10)   # oldest is 30 days old
    a = led.attest_retention(183, actor="dpo", now=now)
    assert a["kind"] == "retention" and a["floor_observed"] is False and 29.9 < a["oldest_age_days"] < 30.1
    b = led.attest_retention(183, actor="dpo", now=now + 160 * DAY)
    assert b["floor_observed"] is True and b["live_entries"] == 4
    led.archive(keep_days=15, actor="ops", now=now)                  # archives seq 0 and 1
    c = led.attest_retention(183, actor="dpo", now=now + 160 * DAY)
    assert c["archived_entries"] == 2 and c["oldest_age_days"] == b["oldest_age_days"]   # archives still count
    assert led.verify(expected_pubkey=pk) == (True, [])
    assert led.oversight_report()["actions"] == 1                     # retention entries are not actions
    from inspeximus.technical_documentation import instructions_for_use
    assert instructions_for_use(m, led)["retention"]["archives"] == led.archived["archive_chain"]
    with pytest.raises(ValueError):
        led.attest_retention(183, actor="")


# ---- the second red team (2026-09-16) on the rotation itself: each of these passed or crashed before

def test_a_keyless_rewrite_of_the_checkpoint_fails_with_no_key_named(tmp_path):
    """The checkpoint's signature was only required when a key was named; a keyless attacker could strip
    it, rewrite archived_first_ts and make attest_retention report a 400-day-old log on 180-day data."""
    m, led, sk, pk, now = _ledger(tmp_path)
    led.archive(keep_days=100, actor="ops", now=now)
    live = json.loads(led.path.read_text(encoding="utf-8"))
    live[0]["archived_first_ts"] = now - 400 * DAY
    live[0].pop("sig"); live[0].pop("pubkey"); live[0].pop("hash")
    live[0]["hash"] = _entry_hash(live[0])
    led.path.write_text(json.dumps(live), encoding="utf-8")
    ok, problems = verify_file(led.path)                              # no expected_pubkey
    assert not ok and any("checkpoint: no signature" in p for p in problems)
    led.reload()
    assert not led.verify()[0]
    # the CLI path the compliance overlay uses
    from inspeximus.compliance import _ledger_counts
    assert _ledger_counts(m)["ledger_verified"] is False


def test_a_checkpoint_signed_by_another_key_than_the_entries_fails(tmp_path):
    m, led, sk, pk, now = _ledger(tmp_path)
    led.archive(keep_days=100, actor="ops", now=now)
    live = json.loads(led.path.read_text(encoding="utf-8"))
    sk2, pk2 = new_receipt_keypair()
    live[0].pop("sig"); live[0].pop("pubkey"); live[0].pop("hash")
    live[0]["hash"] = _entry_hash(live[0])
    live[0]["sig"], live[0]["pubkey"] = _sign(sk2, live[0]["hash"])
    led.path.write_text(json.dumps(live), encoding="utf-8")
    ok, problems = verify_file(led.path)                              # still no key named
    assert not ok and any("different key" in p for p in problems)


def test_a_signed_ledger_refuses_to_rotate_without_its_key(tmp_path):
    m, led, sk, pk, now = _ledger(tmp_path)
    keyless = ActionLedger(path=led.path)
    with pytest.raises(ValueError, match="signed"):
        keyless.archive(keep_days=100, actor="ops", now=now)
    assert led.archived is None
    # CONTROL: an unsigned ledger rotates through a keyless handle
    (tmp_path / "u").mkdir()
    m2, led2, _, _, now2 = _ledger(tmp_path / "u", signed=False)
    res = ActionLedger(path=led2.path).archive(keep_days=100, actor="ops", now=now2)
    assert res["archived"] == 3 and res["signed"] is False


def test_a_stale_handle_neither_unrotates_nor_loses_a_write(tmp_path):
    """Handle B loaded the ledger before handle A rotated it. B's next record() used to write the
    un-rotated chain back over the checkpoint, and A's next write then dropped B's entry."""
    m, led, sk, pk, now = _ledger(tmp_path)
    b = ActionLedger(m, actor="b")
    led.archive(keep_days=100, actor="ops", now=now)
    eb = b.record("tool:from-b")
    assert b.archived is not None and eb["seq"] == 6                  # B re-read the rotated file first
    ea = led.record("tool:from-a")
    assert ea["seq"] == 7 and ea["prev"] == eb["hash"]                # A re-read B's entry
    fresh = ActionLedger(m)
    assert [e["action"] for e in fresh.entries()[-2:]] == ["tool:from-b", "tool:from-a"]
    assert fresh.verify(expected_pubkey=pk) == (True, [])


def test_a_torn_rotation_is_resumed_not_blocked(tmp_path, monkeypatch):
    m, led, sk, pk, now = _ledger(tmp_path)
    calls = {"n": 0}
    real_save = led._save

    def failing_save():
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full")
        return real_save()
    monkeypatch.setattr(led, "_save", failing_save)
    with pytest.raises(OSError):
        led.archive(keep_days=100, actor="ops", now=now)
    assert (tmp_path / "mem.json.actions.json.archive.0001.json").exists()   # the orphan
    led.reload()
    assert led.archived is None
    res = led.archive(keep_days=100, actor="ops", now=now)            # same bytes: reused
    assert res["archived"] == 3 and led.verify(expected_pubkey=pk) == (True, [])
    # CONTROL: an orphan with DIFFERENT bytes is still refused
    led2 = ActionLedger(m)
    arc2 = tmp_path / "mem.json.actions.json.archive.0002.json"
    arc2.write_text("[]", encoding="utf-8")
    with pytest.raises(FileExistsError):
        led2.archive(before_ts=now + 1, actor="ops", now=now + 2)


def test_a_malformed_checkpoint_is_a_problem_not_a_crash(tmp_path):
    m, led, sk, pk, now = _ledger(tmp_path)
    led.archive(keep_days=100, actor="ops", now=now)
    for bad in ("abc", None, -5, 2.5):
        live = json.loads(led.path.read_text(encoding="utf-8"))
        live[0]["archived_through"] = bad
        led.path.write_text(json.dumps(live), encoding="utf-8")
        ok, problems = verify_file(led.path)
        assert not ok and any("archived_through" in p for p in problems), bad
    live = json.loads(led.path.read_text(encoding="utf-8"))
    live[0]["archived_through"] = 2
    live[0]["archive_file"] = str(tmp_path / "elsewhere.json")
    led.path.write_text(json.dumps(live), encoding="utf-8")
    ok, problems = verify_file(led.path)
    assert not ok and any("bare file name" in p for p in problems)


def test_a_key_holder_who_rewrites_checkpoint_counts_is_caught_by_the_archive(tmp_path):
    m, led, sk, pk, now = _ledger(tmp_path)
    led.archive(keep_days=100, actor="ops", now=now)
    live = json.loads(led.path.read_text(encoding="utf-8"))
    live[0]["archived_first_ts"] = now - 400 * DAY
    live[0]["archived_count"] = 99
    live[0].pop("hash"); live[0].pop("sig"); live[0].pop("pubkey")
    live[0]["hash"] = _entry_hash(live[0])
    live[0]["sig"], live[0]["pubkey"] = _sign(sk, live[0]["hash"])
    led.path.write_text(json.dumps(live), encoding="utf-8")
    ok, problems = verify_file(led.path, expected_pubkey=pk)
    assert not ok and any("archived_first_ts" in p for p in problems) and any("archived_count" in p for p in problems)


def test_a_non_numeric_ts_refuses_rotation_and_does_not_crash_attestation(tmp_path):
    m, led, sk, pk, now = _ledger(tmp_path)
    live = json.loads(led.path.read_text(encoding="utf-8"))
    live[1]["ts"] = "yesterday"
    led.path.write_text(json.dumps(live), encoding="utf-8")
    led.reload()
    with pytest.raises(ValueError, match="numeric ts"):
        led.archive(keep_days=100, actor="ops", now=now)
    live[0]["ts"] = "long ago"
    led.path.write_text(json.dumps(live), encoding="utf-8")
    led.reload()
    a = led.attest_retention(183, actor="dpo", now=now)
    assert a["oldest_ts"] is None and a["floor_observed"] is False and "note_oldest" in a


def test_six_months_is_a_calendar_period_not_183_days():
    from inspeximus.actions import six_months_before
    import datetime as dt
    def ts(y, mo, d):
        return dt.datetime(y, mo, d, tzinfo=dt.timezone.utc).timestamp()
    assert six_months_before(ts(2026, 9, 1)) == ts(2026, 3, 1)            # 184 days
    assert six_months_before(ts(2026, 3, 1)) == ts(2025, 9, 1)            # 181 days
    assert six_months_before(ts(2026, 8, 31)) == ts(2026, 2, 28)          # day clamped
    # a log that began 183 days before 1 September is NOT six months old yet
    began = ts(2026, 9, 1) - 183 * DAY
    assert began > six_months_before(ts(2026, 9, 1))
