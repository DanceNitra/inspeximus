"""The MCP `recommit` tool: the remedy the UNSCOPED line names, reachable by the client that reads it.

`verify_writes` over MCP reports UNSCOPED records and names `recommit(ids=[...])`, and until this file
no MCP tool had that name. The rule the tool follows is the CLI's, from one place
(`_surface.recommit_named`), and is tested without the SDK in
test_recommit_from_the_shell_needs_named_ids_or_all.py. What is tested here is what only the server
adds: the call goes through a real MCP client session, `all` reaches the rule, the server's project
scope reaches it, and a recommit refused for a concurrent write reloads once and lands, like every
other write tool on this server.
"""
import pytest

pytest.importorskip("mcp")

from _mcp_review import call, load_server  # noqa: E402

from inspeximus import Inspeximus  # noqa: E402
from inspeximus.core import StoreChangedOnDisk  # noqa: E402


def _unscoped(tmp_path, monkeypatch, rows):
    """Write `rows` [(text, key, project)] with pre-3.11.0 receipts into the store the server will open."""
    monkeypatch.delenv("INSPEXIMUS_STORE_FORMAT", raising=False)
    real = Inspeximus._write_commit

    def old(rec, retires=()):
        c = real(rec, retires)
        c.pop("context_sha256", None)
        c.pop("partition_sha256", None)
        return c
    monkeypatch.setattr(Inspeximus, "_write_commit", staticmethod(old))
    s = Inspeximus(str(tmp_path / "store.json"), receipts=True)
    ids = []
    for text, key, project in rows:
        rid = s.remember(text, key=key, project=project)
        ids.append(rid if isinstance(rid, str) else rid["id"])
    s.flush()
    monkeypatch.setattr(Inspeximus, "_write_commit", staticmethod(real))              # upgrade
    return ids


def test_the_tool_binds_the_ids_the_unscoped_line_names_and_refuses_a_bare_call(monkeypatch, tmp_path):
    ids = _unscoped(tmp_path, monkeypatch, [("the payout IBAN is SK11", "payout", None),
                                            ("the staging db is db-7", "staging", None)])
    mod = load_server(monkeypatch, tmp_path)
    v = call(mod, "verify_writes").data
    assert v["ok"] is False and v["context_unbound"] == 2, v

    bare = call(mod, "recommit")
    assert not bare.is_error and bare.data["recommitted"] == [], bare
    assert "name the records" in bare.data["problems"][0]
    assert call(mod, "verify_writes").data["context_unbound"] == 2, "a refused call wrote nothing"

    res = call(mod, "recommit", ids=ids)
    assert res.data == {"recommitted": ids, "skipped": [], "problems": []}, res
    v = call(mod, "verify_writes").data
    assert v["ok"] is True and v["context_unbound"] == 0, v


def test_all_reaches_the_rule_and_stays_inside_the_servers_project(monkeypatch, tmp_path):
    alpha, beta, shared = _unscoped(tmp_path, monkeypatch, [("alpha deploys to eu-1", "alpha_deploy", "alpha"),
                                                            ("beta deploys to us-2", "beta_deploy", "beta"),
                                                            ("the rota is weekly", "rota", None)])
    mod = load_server(monkeypatch, tmp_path, INSPEXIMUS_PROJECT="alpha")
    res = call(mod, "recommit", all=True)
    assert sorted(res.data["recommitted"]) == sorted([alpha, shared]), res
    assert Inspeximus(str(tmp_path / "store.json"), receipts=True).context_unbound()["ids"] == [beta]


def test_a_recommit_refused_for_a_concurrent_write_reloads_once_and_lands(monkeypatch, tmp_path):
    (rid,) = _unscoped(tmp_path, monkeypatch, [("the region is osaka", "region", None)])
    mod = load_server(monkeypatch, tmp_path)
    calls = {"n": 0}
    real = Inspeximus.recommit

    def refused_once(self, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise StoreChangedOnDisk("another process wrote first")
        return real(self, *a, **k)
    monkeypatch.setattr(Inspeximus, "recommit", refused_once)
    store = Inspeximus(str(tmp_path / "store.json"), receipts=True)
    mod._recover_from_concurrent_writes(store)
    from inspeximus._surface import recommit_named
    assert recommit_named(store, ids=[rid])["recommitted"] == [rid]
    assert calls["n"] == 2
