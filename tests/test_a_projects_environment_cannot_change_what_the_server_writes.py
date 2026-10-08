"""A project's settings reach the MCP server's environment, so the variables that change what the server writes to the
user's store are read from the user's own config (3.17.0, AUDIT-A's inventory of 2026-10-08).

Measured by AUDIT-A on the head before this change: one write from a project with INSPEXIMUS_RECEIPTS_TAIL=1 converted the
user's receipt sidecar to a format that servers before 3.17 cannot extend. The receipt tail is new in 3.17.0, so it is
read from `<key home>/inspeximus/config.json` (`receipts.tail`) from the start, in every scope.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from conftest import tail_config  # noqa: E402
from inspeximus import _userconfig  # noqa: E402
from inspeximus import claude_code as cc  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402


@pytest.fixture
def env(monkeypatch, tmp_path):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    home = tmp_path / "keyhome"
    home.mkdir()
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(home))
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")
    _userconfig._SAID.clear()
    return tmp_path


def _sidecar_is_array(p):
    with open(p + ".receipts.json", encoding="utf-8") as fh:
        return isinstance(json.load(fh), list)


def _write(p, n=2):
    m = Inspeximus(p, receipts=True)
    for i in range(n):
        m.remember("fact %d" % i, key="k%d" % i)
    m.flush()
    return m


def test_the_environment_alone_does_not_convert_the_receipt_sidecar(env, monkeypatch, capfd):
    p = str(env / "s.json")
    _write(p)
    assert _sidecar_is_array(p)
    monkeypatch.setenv("INSPEXIMUS_RECEIPTS_TAIL", "1")
    _write(p)
    assert _sidecar_is_array(p), "a project's environment converted the user's receipt sidecar"
    assert not os.path.exists(p + ".receipts.tail.jsonl")
    err = capfd.readouterr().err
    assert "INSPEXIMUS_RECEIPTS_TAIL" in err and "receipts.tail" in err, err
    assert os.path.join(os.environ["INSPEXIMUS_KEY_HOME"], "inspeximus", "config.json") in err, "the line must name the full path"


def test_the_users_config_converts_it(env):
    p = str(env / "s.json")
    _write(p)
    tail_config(True)
    _write(p)
    assert os.path.exists(p + ".receipts.tail.jsonl") and not _sidecar_is_array(p)


def test_the_line_is_said_once_per_process(env, monkeypatch, capfd):
    monkeypatch.setenv("INSPEXIMUS_RECEIPTS_TAIL", "1")
    p = str(env / "s.json")
    _write(p, 4)
    assert capfd.readouterr().err.count("INSPEXIMUS_RECEIPTS_TAIL") == 1


def test_a_repositorys_config_cannot_switch_it_on(env):
    """The merged `claude_code._cfg(cwd)` includes the repository's file; the switch never reads it."""
    repo = env / "repo"
    (repo / ".inspeximus").mkdir(parents=True)
    (repo / ".inspeximus" / "config.json").write_text(json.dumps({"receipts": {"tail": True}}))
    p = str(env / "s.json")
    cwd = os.getcwd()
    os.chdir(repo)
    try:
        _write(p)
    finally:
        os.chdir(cwd)
    assert _sidecar_is_array(p) and not os.path.exists(p + ".receipts.tail.jsonl")


def test_the_hook_embedder_does_not_take_its_model_from_the_repository(env, monkeypatch, capfd):
    repo = env / "repo"
    (repo / ".inspeximus").mkdir(parents=True)
    (repo / ".inspeximus" / "config.json").write_text(json.dumps({"embed": {"model": "repo-chosen-model"}}))
    os.makedirs(os.path.dirname(_userconfig.path()), exist_ok=True)
    with open(_userconfig.path(), "w", encoding="utf-8") as fh:
        json.dump({"embed": {"hooks": True, "url": "http://127.0.0.1:9/v1/embeddings", "model": "users-model"}}, fh)
    doc, query, embed_id = cc._make_embedder(str(repo))
    assert doc is not None and "users-model" in embed_id and "repo-chosen-model" not in embed_id, embed_id
    assert "model" in capfd.readouterr().err, "the repository's model is ignored with a line, like its key"


def test_the_hooks_embed_only_when_the_user_switches_them_on(env, monkeypatch):
    """The server's persistence default does not reach the hooks: with a loopback URL configured and no switch, the hook
    embedder is absent; the user's `embed.hooks` turns it on."""
    os.makedirs(os.path.dirname(_userconfig.path()), exist_ok=True)
    monkeypatch.setenv("INSPEXIMUS_EMBED_URL", "http://127.0.0.1:9/v1/embeddings")
    assert cc._make_embedder(str(env)) == (None, None, None), "the hooks embed without being switched on"
    with open(_userconfig.path(), "w", encoding="utf-8") as fh:
        json.dump({"embed": {"hooks": True}}, fh)
    assert cc._make_embedder(str(env))[0] is not None, "control: embed.hooks does turn them on"


def test_without_a_user_model_the_default_is_used_not_the_repositorys(env):
    repo = env / "repo"
    (repo / ".inspeximus").mkdir(parents=True)
    (repo / ".inspeximus" / "config.json").write_text(json.dumps({"embed": {"model": "repo-chosen-model"}}))
    os.makedirs(os.path.dirname(_userconfig.path()), exist_ok=True)
    with open(_userconfig.path(), "w", encoding="utf-8") as fh:
        json.dump({"embed": {"hooks": True, "url": "http://127.0.0.1:9/v1/embeddings"}}, fh)
    _doc, _query, embed_id = cc._make_embedder(str(repo))
    assert "repo-chosen-model" not in embed_id and "nomic-embed-text" in embed_id, embed_id


# ── INSPEXIMUS_STORE_FORMAT=json: pins a JSON or new store, converts no row store ───────────────────────────────────────
def _is_rows(p):
    from inspeximus import sqlite_store
    return sqlite_store.looks_like_sqlite(p)


def test_the_environment_does_not_turn_a_row_store_into_json(env, monkeypatch, capfd):
    p = str(env / "s.json")
    m = Inspeximus(p)
    m.remember("one", key="a")
    m.flush()
    assert _is_rows(p), "CONTROL: a new store is rows"
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    m2 = Inspeximus(p)
    m2.remember("two", key="b")
    m2.flush()
    assert _is_rows(p), "a project's environment converted the user's row store to JSON"
    err = capfd.readouterr().err
    assert "INSPEXIMUS_STORE_FORMAT" in err and "store.format" in err, err
    assert os.path.join(os.environ["INSPEXIMUS_KEY_HOME"], "inspeximus", "config.json") in err


def test_the_documented_use_still_works_for_a_new_store_and_for_a_json_store(env, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    p = str(env / "tooling.json")
    m = Inspeximus(p)
    m.remember("read by other tooling", key="a")
    m.flush()
    assert not _is_rows(p), "a new store with the variable set is not JSON"
    m2 = Inspeximus(p)
    m2.remember("again", key="b")
    m2.flush()
    assert not _is_rows(p) and len(json.load(open(p, encoding="utf-8"))) == 2


def test_the_users_config_can_pin_json_for_a_row_store(env):
    p = str(env / "s.json")
    m = Inspeximus(p)
    m.remember("one", key="a")
    m.flush()
    assert _is_rows(p)
    os.makedirs(os.path.dirname(_userconfig.path()), exist_ok=True)
    with open(_userconfig.path(), "w", encoding="utf-8") as fh:
        json.dump({"store": {"format": "json"}}, fh)
    m2 = Inspeximus(p)
    m2.remember("two", key="b")
    m2.flush()
    assert not _is_rows(p), "store.format in the user's config did not pin JSON"


# ── the stat cache of the user's config (AUDIT-A P-6) ───────────────────────────────────────────────────────────────────
def test_a_same_size_replace_with_the_mtime_put_back_is_seen(env):
    """The cache keyed on size and mtime only. A rewrite of the same size whose mtime was restored read as unchanged."""
    path = _userconfig.path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"receipts": {"tail": False}}, fh)
    assert _userconfig.get("receipts", "tail") is False
    st = os.stat(path)
    tmp = path + ".new"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"receipts": {"tail": True}}, fh)                  # "false" and "true" differ in length: pad to the same size
    data = open(tmp, encoding="utf-8").read()
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(data.replace("true", "true ", 1) if len(data) < st.st_size else data)
    assert os.stat(tmp).st_size == st.st_size, (os.stat(tmp).st_size, st.st_size)
    os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
    os.replace(tmp, path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert os.stat(path).st_mtime_ns == st.st_mtime_ns and os.stat(path).st_size == st.st_size
    assert _userconfig.get("receipts", "tail") is True, "a same-size replace with the mtime restored was not seen"


def test_an_unchanged_file_is_not_parsed_again(env, monkeypatch):
    path = _userconfig.path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"a": 1}, fh)
    _userconfig.read()
    calls = []
    real = json.load
    monkeypatch.setattr(json, "load", lambda fh: calls.append(1) or real(fh))
    for _ in range(5):
        _userconfig.read()
    assert calls == [], "the cache re-parsed an unchanged file"
