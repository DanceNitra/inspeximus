"""MCP tool review, family: ERASURE, RETENTION AND PARTITIONS.

Part of audits/2026-09-24/mcp-tools-review.md. Tools: forget, forget_subject, forget_pii, pii_report,
retention, erasure_report, erasure_certificate, erasure_audit, erasure_residue,
declare_out_of_band_deletion, open_partition, remember_in_partition, sweep_partitions, close_partition,
partitions_report.

Each test holds one tool to a sentence of its OWN description (the docstring an MCP client is shown), or
to a config promise in the server's module docstring, or to a library guard the same operation keeps
outside the server, and fails today. Strict xfail, so a fix turns it into an XPASS that has to be
acknowledged. Preconditions go through `pytest.fail`. Every test runs on a throwaway store in tmp_path
(see tests/_mcp_review.py).
"""
import os

import pytest

pytest.importorskip("mcp")

from _mcp_review import SERVER_ENV, call, load_server  # noqa: E402

XFAIL = dict(strict=True, raises=AssertionError)


@pytest.fixture
def server(monkeypatch, tmp_path):
    """A fresh server on tmp_path/store.json with write receipts on."""
    return load_server(monkeypatch, tmp_path, INSPEXIMUS_RECEIPTS="1")


@pytest.fixture
def signed_store(monkeypatch, tmp_path):
    """A store at tmp_path/store.json written and signed by the LIBRARY with a receipt key, which is the
    configuration the module docstring's INSPEXIMUS_RECEIPT_PUBKEY exists for ("Set it whenever the store
    is signed"). Returns (secret_hex, public_hex, ids of the two records written)."""
    pytest.importorskip("cryptography")
    from inspeximus import Inspeximus
    from inspeximus.core import new_receipt_keypair

    for k in SERVER_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "key_home"))
    sk, pk = new_receipt_keypair()
    lib = Inspeximus(path=str(tmp_path / "store.json"), receipts=True, receipt_key=sk)
    a = lib.remember("alice lives at 5 elm st", source={"doc": "crm/alice"})
    b = lib.remember("bob lives at 9 oak st", source={"doc": "crm/bob"})
    lib.forget_subject("crm/alice", request_id="DSAR-1", basis="gdpr_art17")
    del lib
    return sk, pk, (a, b)


def _tagged(mod, partition: str) -> list[dict]:
    return [r for r in mod._MEM.items if f"partition:{partition}" in (r.get("tags") or [])]


# ── remember_in_partition ───────────────────────────────────────────────────────────────────────────
@pytest.mark.xfail(reason="remember_in_partition: a keyed write the objectless/echo guard retired on "
                          "arrival comes back as {id, partition}, indistinguishable from a landed write",
                   **XFAIL)
def test_remember_in_partition_reports_a_write_the_guard_retired(server):
    """remember_in_partition: "Remember into a partition: the record is tagged partition:<name>, counted
    against its cap ... and erased by its expiry or at close."

    Guard parity (audit brief, check 3): a write blocked by a guard must be REPORTED as blocked, as
    remember/remember_decision do with `blocked`/`policy`/`status`. The tool has no `object` parameter, so
    a partition write to a key that carries objects is always retired by the objectless guard; the
    library says so in `last_write` and the tool drops it.
    """
    call(server, "remember", text="the region is frankfurt", key="svc::region", object="frankfurt")
    if call(server, "open_partition", name="ctx1", kind="context").data.get("error"):
        pytest.fail("precondition: the partition must open")
    r = call(server, "remember_in_partition", partition="ctx1", text="the region is lima", key="svc::region")
    lw = server._MEM.last_write or {}
    if r.is_error or not lw.get("blocked") or lw.get("id") != r.data.get("id"):
        pytest.fail(f"precondition: the library must have retired this write on arrival: {r.data} / {lw}")
    assert r.data.get("blocked") is True, (
        f"the objectless guard retired the write (policy={lw.get('policy')!r}, status={lw.get('status')!r}) "
        f"and the tool result says nothing about it: {r.data}")


@pytest.mark.xfail(reason="remember_in_partition: module docstring 'INSPEXIMUS_PROJECT ... Writes are stamped "
                          "with it and recalls are filtered to it'; a partition write is unstamped and "
                          "surfaces in every other project", **XFAIL)
def test_remember_in_partition_stamps_the_server_project(monkeypatch, tmp_path):
    """Module docstring, INSPEXIMUS_PROJECT: "Writes are stamped with it and recalls are filtered to it".

    remember_in_partition calls the store's remember() without `project=`, so the record carries no stamp
    and an unstamped record is shared by every project.
    """
    alpha = load_server(monkeypatch, tmp_path, INSPEXIMUS_PROJECT="alpha")
    call(alpha, "open_partition", name="w1", kind="process")
    in_part = call(alpha, "remember_in_partition", partition="w1", text="alpha roadmap zebra partition").data
    plain = call(alpha, "remember", text="alpha roadmap zebra plain").data
    beta = load_server(monkeypatch, tmp_path, INSPEXIMUS_PROJECT="beta")
    hits = {h["id"] for h in call(beta, "recall", query="alpha roadmap zebra", k=10).data}
    if plain["id"] in hits or "id" not in in_part:
        pytest.fail(f"precondition: project filtering must hold for a plain remember: {hits} / {in_part}")
    assert in_part["id"] not in hits, "a record written in project alpha is recalled from project beta"


# ── close_partition / sweep_partitions ──────────────────────────────────────────────────────────────
@pytest.mark.xfail(reason="close_partition: 'A context partition erases its records'; a record of the "
                          "partition that a later keyed write superseded survives the close", **XFAIL)
def test_close_partition_erases_every_record_of_a_context_partition(server):
    """close_partition: "Close a partition when its process ends. A context partition erases its records
    (disposition erased)". open_partition: "a context partition erases its records at close".

    Partitions._records() counts ACTIVE records only, so a superseded record the partition wrote is
    neither erased nor counted, and stays readable through history().
    """
    call(server, "open_partition", name="c1", kind="context")
    first = call(server, "remember_in_partition", partition="c1", text="customer card ends 4471",
                 key="ctx::card").data["id"]
    call(server, "remember_in_partition", partition="c1", text="customer card ends 9920", key="ctx::card")
    before = {r["id"]: r.get("status") for r in _tagged(server, "c1")}
    if before.get(first) != "superseded" or len(before) != 2:
        pytest.fail(f"precondition: two partition records, the first superseded: {before}")
    closed = call(server, "close_partition", name="c1", actor="triage-bot").data
    if closed.get("disposition") != "erased":
        pytest.fail(f"precondition: a context partition closes with disposition erased: {closed}")
    left = [(r["id"], r.get("status"), r.get("text")) for r in _tagged(server, "c1")]
    assert not left, f"records of the closed context partition are still in the store: {left}"


@pytest.mark.xfail(reason="sweep_partitions: 'records past max_age_days ... are hard-deleted'; a superseded "
                          "partition record past its expiry is left in place", **XFAIL)
def test_sweep_partitions_erases_every_expired_partition_record(server):
    """sweep_partitions: "Apply every open partition's expiry and cap now: records past max_age_days and
    beyond max_records are hard-deleted with a tombstone whose basis names the partition and the rule."

    Same cause as the close: the sweep walks ACTIVE records only.
    """
    call(server, "open_partition", name="a1", kind="agent", max_age_days=0)
    first = call(server, "remember_in_partition", partition="a1", text="plan v1 ship friday",
                 key="agent::plan").data["id"]
    call(server, "remember_in_partition", partition="a1", text="plan v2 ship monday", key="agent::plan")
    swept = call(server, "sweep_partitions").data
    if (swept.get("partitions", {}).get("a1") or {}).get("expired", 0) < 1:
        pytest.fail(f"precondition: max_age_days=0 must expire the partition's records: {swept}")
    left = [(r["id"], r.get("status"), r.get("text")) for r in _tagged(server, "a1")]
    assert not left, f"records past the partition's expiry survived the sweep (first={first}): {left}"


# ── open_partition ──────────────────────────────────────────────────────────────────────────────────
@pytest.mark.xfail(reason="open_partition: re-opening an existing name with other rules echoes the requested "
                          "kind/cap/expiry as if applied, while the partition keeps its original rules",
                   **XFAIL)
def test_open_partition_reports_the_rules_actually_in_force(server):
    """open_partition: "Open a memory partition: a named scope ... with a size cap and an expiry ... `kind`
    is context, process or agent" and "a context partition erases its records at close".

    The library re-opens a handle to an existing partition and ignores the new arguments; the tool then
    returns the ARGUMENTS, so the caller is told it has a context partition capped at 1 while it has an
    uncapped process partition that keeps its records at close.
    """
    call(server, "open_partition", name="w1", kind="process")
    r = call(server, "open_partition", name="w1", kind="context", max_records=1,
                                             max_age_days=1)
    row = next((p for p in call(server, "partitions_report").data["partitions"] if p["name"] == "w1"), None)
    if row is None:
        pytest.fail("precondition: partitions_report must list the partition")
    if r.is_error or r.data.get("error"):
        return  # refusing the conflicting re-open is an honest answer
    reported = (r.data.get("kind"), r.data.get("max_records"), r.data.get("max_age_days"))
    in_force = (row["kind"], row["max_records"], row["max_age_days"])
    assert reported == in_force, f"open_partition reported {reported}, the partition's rules are {in_force}"


# ── forget_subject ──────────────────────────────────────────────────────────────────────────────────
@pytest.mark.xfail(reason="forget_subject: 'Returns a receipt (forgotten count, ids, scrubbed_links)'; the "
                          "result has no scrubbed_links (the count of links scrubbed is dropped)", **XFAIL)
def test_forget_subject_returns_scrubbed_links(server):
    """forget_subject: "delete every memory about `subject` AND scrub its id from survivors' links/
    supersession pointers ... Returns a receipt (forgotten count, ids, scrubbed_links) you can keep as
    evidence."
    """
    a = call(server, "remember", text="alice lives at 5 elm st", source="crm/alice").data["id"]
    b = call(server, "remember", text="the office is on elm street").data["id"]
    mem = server._MEM
    rec = next(r for r in mem._items if r["id"] == b)
    rec["links"] = [a]
    mem._touch(rec)
    mem._save(force=True)
    r = call(server, "forget_subject", subject="crm/alice", request_id="DSAR-9")
    survivor = next((x for x in mem.items if x["id"] == b), {})
    if r.is_error or r.data.get("erased") != 1 or a in (survivor.get("links") or []):
        pytest.fail(f"precondition: the subject's record is erased and the survivor's link scrubbed: "
                    f"{r.data} / links={survivor.get('links')}")
    assert r.data.get("scrubbed_links") == 1, f"receipt without scrubbed_links: {sorted(r.data)}"


# ── pii_report ──────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.xfail(reason="pii_report: 'What PII the store currently holds'; PII held in a superseded "
                          "record is not counted, while forget_pii (its pair) erases it", **XFAIL)
def test_pii_report_counts_pii_held_in_superseded_records(monkeypatch, tmp_path):
    """pii_report: "What PII the store currently holds, by type (emails, phones, cards, …) — a
    data-minimization / audit view. Read-only; pair with forget_pii to act on it."

    A corrected contact keeps the old email in a superseded record. The report skips every non-active
    record, so it says 0; forget_pii then erases that record.
    """
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_RECEIPTS="1", INSPEXIMUS_PII_DETECT="1")
    old = call(mod, "remember", text="alice email is alice@example.com", key="contact::alice",
               object="alice@example.com").data["id"]
    call(mod, "remember", text="alice prefers postal mail", key="contact::alice", object="postal")
    rec = next((r for r in mod._MEM.items if r["id"] == old), {})
    if rec.get("status") != "superseded" or "email" not in (rec.get("pii") or []):
        pytest.fail(f"precondition: the old record is superseded and tagged email: {rec.get('status')} "
                    f"{rec.get('pii')}")
    report = call(mod, "pii_report").data
    assert report.get("records_with_pii", 0) >= 1 and old in (report.get("ids") or {}).get("email", []), (
        f"the store holds an email in record {old} and pii_report says: {report}")


# ── retention / forget on a signed store ────────────────────────────────────────────────────────────
def test_retention_apply_leaves_a_signed_tombstone(monkeypatch, tmp_path, signed_store):
    """retention: "find ACTIVE records older than `max_age_days` and, with apply=True, hard-delete them —
    each erasure leaving a signed tombstone, so the enforcement is itself auditable."

    No server configuration reached the store's receipt_key/receipt_signer (open_store was called without
    either), so every tombstone this server appended was unsigned, even on a store that is signed. FIXED:
    the server takes the store's key through INSPEXIMUS_RECEIPT_KEY (or _FILE, or the key home).
    """
    sk, pk, (_a, b) = signed_store
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_RECEIPT_PUBKEY=pk, INSPEXIMUS_RECEIPT_KEY=sk)
    before = call(mod, "verify_writes").data
    if not before.get("ok"):
        pytest.fail(f"precondition: the signed store verifies against its pinned key: {before}")
    res = call(mod, "retention", max_age_days=0, pii_only=False, apply=True, basis="policy", request_id="RET-1")
    if res.data.get("erased") != 1 or b not in res.data.get("ids", []):
        pytest.fail(f"precondition: retention must erase the remaining record: {res.data}")
    tomb = next(t for t in mod._MEM._tombstones if t.get("memory_id") == b)
    after = call(mod, "verify_writes").data
    assert "sig" in tomb and after.get("ok"), (
        f"retention tombstone signed={'sig' in tomb}; verify_writes after the sweep: {after.get('problems')}")


def test_forget_on_a_signed_store_keeps_the_chain_verifiable(monkeypatch, tmp_path, signed_store):
    """forget: "TRULY DELETE memories ... Use for an erasure / right-to-be-forgotten request".

    Guard parity (audit brief, check 3): every write extends the receipt chain and verify_writes stays
    ok. On a signed store the same forget() through the library keeps it ok; through this server the chain
    becomes "signed in places", which verify_writes reports as "something without the key appended to it".
    FIXED: the server is handed the store's key (INSPEXIMUS_RECEIPT_KEY) and signs the tombstone.
    """
    sk, pk, (_a, b) = signed_store
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_RECEIPT_PUBKEY=pk, INSPEXIMUS_RECEIPT_KEY=sk)
    if not call(mod, "verify_writes").data.get("ok"):
        pytest.fail("precondition: the signed store verifies against its pinned key")
    res = call(mod, "forget", ids=[b], basis="gdpr_art17", request_id="DSAR-2")
    if res.is_error or res.data.get("forgotten") != 1:
        pytest.fail(f"precondition: forget must erase the record: {res.data}")
    after = call(mod, "verify_writes").data
    assert after.get("ok"), f"a legitimate MCP erasure left the chain unverifiable: {after.get('problems')}"


# ── erasure_certificate ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.xfail(reason="erasure_certificate: module docstring 'INSPEXIMUS_RECEIPT_PUBKEY ... Set it whenever "
                          "the store is signed'; the certificate's self_check ignores it and verifies a chain "
                          "re-signed under a foreign key", **XFAIL)
def test_erasure_certificate_self_check_honours_the_configured_pubkey(monkeypatch, tmp_path, signed_store):
    """Module docstring, INSPEXIMUS_RECEIPT_PUBKEY: "Set it whenever the store is signed: without it the
    tamper-evidence tools verify that receipts are signed by SOMEBODY".

    erasure_certificate passes `expected_pubkey or None` straight through instead of `_pin()`, unlike
    verify_writes and governance_report. On a store signed by a key that is NOT the configured one,
    verify_writes says so and the certificate's self_check reports verified.
    """
    from inspeximus.core import new_receipt_keypair

    _sk, _pk, _ids = signed_store
    _other_sk, configured_pk = new_receipt_keypair()
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_RECEIPT_PUBKEY=configured_pk)
    vw = call(mod, "verify_writes").data
    if vw.get("ok") or not any("unexpected key" in p for p in vw.get("problems", [])):
        pytest.fail(f"precondition: verify_writes (pinned) must reject the foreign signatures: {vw}")
    cert = call(mod, "erasure_certificate", request_id="DSAR-1").data
    if cert.get("count") != 1:
        pytest.fail(f"precondition: the certificate covers the erasure: {cert.get('count')}")
    assert cert["self_check"]["verified"] is False, (
        f"certificate self_check {cert['self_check']} while verify_writes reports {vw['problems']}")


# ── erasure_residue ─────────────────────────────────────────────────────────────────────────────────
@pytest.mark.xfail(reason="erasure_residue: 'A file it could not read makes the verdict False'; a directory "
                          "it could not list is dropped silently and the verdict is ok=True", **XFAIL)
def test_erasure_residue_an_unreadable_directory_is_not_clean(server, tmp_path, monkeypatch):
    """erasure_residue: "A file it could not read makes the verdict False: "clean" must never mean "we did
    not look at that part"."

    os.walk() is called without onerror, so a subdirectory whose listing fails (permission denied, which
    is what chmod 000 gives a non-root scanner) vanishes from the scan. Simulated by failing os.scandir
    for that one directory, because this suite may run as root.
    """
    root = tmp_path / "scan"
    (root / "locked").mkdir(parents=True)
    (root / "readme.txt").write_text("nothing to see")
    (root / "locked" / "export.json").write_text('{"email": "alice@example.com"}')
    seen = call(server, "erasure_residue", root=str(root), values=["alice@example.com"]).data
    if seen.get("ok") is not False or not seen.get("findings"):
        pytest.fail(f"precondition: readable, the value is found: {seen}")
    real = os.scandir

    def scandir(path="."):
        if os.fspath(path).rstrip(os.sep).endswith("locked"):
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real(path)

    monkeypatch.setattr(os, "scandir", scandir)
    r = call(server, "erasure_residue", root=str(root), values=["alice@example.com"]).data
    monkeypatch.setattr(os, "scandir", real)
    assert r.get("ok") is False, f"a directory that could not be read produced a clean verdict: {r}"


@pytest.mark.xfail(reason="erasure_residue: 'clean must never mean we did not look at that part'; a "
                          "symlinked directory under root is not followed, not reported, and the verdict is "
                          "ok=True", **XFAIL)
def test_erasure_residue_a_symlinked_directory_is_not_clean(server, tmp_path):
    """erasure_residue: "A file it could not read makes the verdict False: "clean" must never mean "we did
    not look at that part"."

    scan_residue walks with followlinks=False (not exposed on this tool) and, unlike a pruned skip_dir or
    a broken symlink, a symlinked directory is not added to `skipped`.
    """
    root = tmp_path / "scan"
    root.mkdir()
    (root / "readme.txt").write_text("nothing to see")
    target = tmp_path / "volume"
    target.mkdir()
    (target / "export.json").write_text('{"email": "alice@example.com"}')
    try:
        os.symlink(target, root / "data", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks on this platform")
    r = call(server, "erasure_residue", root=str(root), values=["alice@example.com"]).data
    if r.get("checked_files") != 1:
        pytest.fail(f"precondition: the plain file is read: {r}")
    assert r.get("ok") is False, f"a directory the scan did not enter produced a clean verdict: {r}"
