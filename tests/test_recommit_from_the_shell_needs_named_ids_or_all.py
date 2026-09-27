"""`recommit` from a surface: the records are named, or the caller says all of them.

`verify_writes()` and `context_unbound()` name `recommit(ids=[...])` as the remedy for UNSCOPED
records, and since 3.15.0 an UNSCOPED record fails verification by default. Until this file, the
remedy existed in Python only: the MCP server and the CLI did not expose it, so a user who saw the
line through either surface could not act on it. Measured on a copy of our own MCP store on
2026-09-27: 4,035 records would be reported UNSCOPED.

A recommit binds each record's state AS IT IS NOW, so a record edited out of band verifies clean
afterwards. `Inspeximus.recommit()` sweeps every active record when `ids` is None. The surfaces do
not inherit that default: they take ids or an explicit all, and neither or both writes nothing
(`inspeximus/_surface.py: recommit_named`). The MCP half is in
test_the_mcp_server_exposes_recommit.py, which needs the MCP SDK.
"""
import json
import os
import subprocess
import sys

import pytest

from inspeximus import Inspeximus
from inspeximus._surface import recommit_named

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _old_receipts(monkeypatch):
    """Write receipts as a store before 3.11.0 did: no context or partition commitment."""
    real = Inspeximus._write_commit

    def old(rec, retires=()):
        c = real(rec, retires)
        c.pop("context_sha256", None)
        c.pop("partition_sha256", None)
        return c
    monkeypatch.setattr(Inspeximus, "_write_commit", staticmethod(old))
    return real


def _unscoped_store(tmp_path, monkeypatch, n=3, key=None, **kw):
    """A store whose `n` records are UNSCOPED; returns (path, ids). `key` makes it a signed chain."""
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    path = str(tmp_path / "store.json")
    real = _old_receipts(monkeypatch)
    s = Inspeximus(path, receipts=True, receipt_key=key)
    ids = []
    for i in range(n):
        rid = s.remember(f"fact number {i} is value-{i}", key=f"fact{i}", **kw)
        ids.append(rid if isinstance(rid, str) else rid["id"])
    s.flush()
    monkeypatch.setattr(Inspeximus, "_write_commit", staticmethod(real))              # upgrade
    return path, ids


def _open(path, key=None):
    return Inspeximus(path, receipts=True, receipt_key=key)


def _n_receipts(path):
    return len(_open(path)._receipts)


def test_CONTROL_the_fixture_is_unscoped_and_fails_verification(tmp_path, monkeypatch):
    path, ids = _unscoped_store(tmp_path, monkeypatch)
    s = _open(path)
    assert s.context_unbound()["ids"] == sorted(ids)
    ok, problems = s.verify_writes()
    assert not ok and any("UNSCOPED" in p for p in problems), problems


def test_named_ids_are_bound_and_the_rest_stay_unscoped(tmp_path, monkeypatch):
    path, ids = _unscoped_store(tmp_path, monkeypatch)
    res = recommit_named(_open(path), ids=ids[:2])
    assert res == {"recommitted": ids[:2], "skipped": [], "problems": []}
    s = _open(path)
    assert s.context_unbound()["ids"] == [ids[2]]
    assert recommit_named(s, ids=[ids[2]])["recommitted"] == [ids[2]]
    assert _open(path).verify_writes() == (True, [])
    assert recommit_named(_open(path), ids=ids[:1]) == {"recommitted": [], "skipped": ids[:1], "problems": []}


def test_neither_ids_nor_all_is_refused_and_writes_nothing(tmp_path, monkeypatch):
    path, ids = _unscoped_store(tmp_path, monkeypatch)
    before = _n_receipts(path)
    for empty in (None, [], ["", "  "]):
        res = recommit_named(_open(path), ids=empty)
        assert res["recommitted"] == [] and len(res["problems"]) == 1, res
        assert "name the records" in res["problems"][0] and "nothing was recommitted" in res["problems"][0]
    assert _n_receipts(path) == before
    assert _open(path).context_unbound()["unbound"] == len(ids)


def test_ids_and_all_together_are_refused_and_write_nothing(tmp_path, monkeypatch):
    path, ids = _unscoped_store(tmp_path, monkeypatch)
    before = _n_receipts(path)
    res = recommit_named(_open(path), ids=ids[:1], all_records=True)
    assert res["recommitted"] == [] and "not both" in res["problems"][0], res
    assert _n_receipts(path) == before


def test_all_binds_every_active_record(tmp_path, monkeypatch):
    path, ids = _unscoped_store(tmp_path, monkeypatch)
    res = recommit_named(_open(path), all_records=True)
    assert sorted(res["recommitted"]) == sorted(ids) and res["problems"] == [], res
    assert _open(path).verify_writes() == (True, [])


def test_a_named_id_that_is_no_active_record_is_reported(tmp_path, monkeypatch):
    path, ids = _unscoped_store(tmp_path, monkeypatch)
    res = recommit_named(_open(path), ids=[ids[0], "no-such-id"])
    assert res["recommitted"] == [ids[0]], res
    assert len(res["problems"]) == 1 and "no-such-id" in res["problems"][0], res


def test_a_project_scope_recommits_what_it_reads_and_nothing_else(tmp_path, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    path = str(tmp_path / "store.json")
    real = _old_receipts(monkeypatch)
    s = Inspeximus(path, receipts=True)
    alpha = s.remember("alpha's deploy target is eu-1", key="alpha_deploy", project="alpha")
    beta = s.remember("beta's deploy target is us-2", key="beta_deploy", project="beta")
    shared = s.remember("the on-call rota is weekly", key="rota")
    alpha, beta, shared = (r if isinstance(r, str) else r["id"] for r in (alpha, beta, shared))
    s.flush()
    monkeypatch.setattr(Inspeximus, "_write_commit", staticmethod(real))
    res = recommit_named(_open(path), all_records=True, project="alpha")
    assert sorted(res["recommitted"]) == sorted([alpha, shared]), res
    assert _open(path).context_unbound()["ids"] == [beta]
    res = recommit_named(_open(path), ids=[beta], project="alpha")
    assert res["recommitted"] == [] and beta in res["problems"][0] and "'alpha'" in res["problems"][0], res
    assert _open(path).context_unbound()["ids"] == [beta]


def test_a_signed_chain_is_not_recommitted_by_a_handle_without_its_key(tmp_path, monkeypatch):
    pytest.importorskip("cryptography")
    from inspeximus import new_receipt_keypair
    sk, _pk = new_receipt_keypair()
    path, ids = _unscoped_store(tmp_path, monkeypatch, key=sk)
    before = _n_receipts(path)
    res = recommit_named(_open(path), ids=ids)
    assert res["recommitted"] == [] and "UNSIGNED" in res["problems"][0], res
    assert _n_receipts(path) == before
    assert recommit_named(_open(path, key=sk), ids=ids)["recommitted"] == ids
    assert _open(path, key=sk).verify_writes() == (True, [])


def test_an_unsigned_chain_is_not_recommitted_by_a_handle_that_signs(tmp_path, monkeypatch):
    pytest.importorskip("cryptography")
    from inspeximus import new_receipt_keypair
    sk, _pk = new_receipt_keypair()
    path, ids = _unscoped_store(tmp_path, monkeypatch)
    before = _n_receipts(path)
    res = recommit_named(_open(path, key=sk), ids=ids)
    assert res["recommitted"] == [] and "SIGNED" in res["problems"][0], res
    assert _n_receipts(path) == before


def test_a_store_without_receipts_gets_the_librarys_one_line(tmp_path):
    path = str(tmp_path / "plain.json")
    s = Inspeximus(path)
    rid = s.remember("a fact", key="k")
    rid = rid if isinstance(rid, str) else rid["id"]
    res = recommit_named(s, ids=[rid])
    assert res["recommitted"] == [] and len(res["problems"]) == 1, res
    assert "receipts are disabled" in res["problems"][0]


def _cli(path, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    env.update({"PYTHONPATH": ROOT, "INSPEXIMUS_HEADS": "0", "INSPEXIMUS_STORE_FORMAT": "json",
                "INSPEXIMUS_NO_UPDATE_CHECK": "1", "PYTHONIOENCODING": "utf-8"})
    return subprocess.run([sys.executable, "-m", "inspeximus.cli", "--path", path, *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT, env=env)


def test_the_cli_takes_named_ids_or_all_and_refuses_a_bare_call(tmp_path, monkeypatch):
    path, ids = _unscoped_store(tmp_path, monkeypatch)
    bare = _cli(path, "recommit")
    assert bare.returncode == 1 and "name the records" in bare.stdout, bare.stdout + bare.stderr
    both = _cli(path, "recommit", ids[0], "--all")
    assert both.returncode == 1 and "not both" in both.stdout, both.stdout + both.stderr
    assert _open(path).context_unbound()["unbound"] == len(ids), "a refused call wrote nothing"
    one = _cli(path, "recommit", ids[0])
    assert one.returncode == 0 and "recommitted 1 record(s)" in one.stdout, one.stdout + one.stderr
    rest = _cli(path, "recommit", "--all", "--json")
    assert rest.returncode == 0, rest.stdout + rest.stderr
    out = json.loads(rest.stdout)
    assert set(out) == {"recommitted", "skipped", "problems"}
    assert sorted(out["recommitted"]) == sorted(ids[1:]) and out["skipped"] == [ids[0]]
    assert _open(path).verify_writes() == (True, [])


def test_the_cli_signs_a_signed_store_only_with_its_key_file(tmp_path, monkeypatch):
    pytest.importorskip("cryptography")
    from inspeximus import new_receipt_keypair
    sk, _pk = new_receipt_keypair()
    path, ids = _unscoped_store(tmp_path, monkeypatch, key=sk)
    keyless = _cli(path, "recommit", *ids)
    assert keyless.returncode == 1 and "UNSIGNED" in keyless.stdout, keyless.stdout + keyless.stderr
    kf = tmp_path / "receipt.key"
    kf.write_text(sk, encoding="utf-8")
    keyed = _cli(path, "--receipt-key-file", str(kf), "recommit", *ids)
    assert keyed.returncode == 0, keyed.stdout + keyed.stderr
    assert _open(path, key=sk).verify_writes() == (True, [])
