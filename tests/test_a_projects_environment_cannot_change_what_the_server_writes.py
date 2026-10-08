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


def test_without_a_user_model_the_default_is_used_not_the_repositorys(env):
    repo = env / "repo"
    (repo / ".inspeximus").mkdir(parents=True)
    (repo / ".inspeximus" / "config.json").write_text(json.dumps({"embed": {"model": "repo-chosen-model"}}))
    os.makedirs(os.path.dirname(_userconfig.path()), exist_ok=True)
    with open(_userconfig.path(), "w", encoding="utf-8") as fh:
        json.dump({"embed": {"hooks": True, "url": "http://127.0.0.1:9/v1/embeddings"}}, fh)
    _doc, _query, embed_id = cc._make_embedder(str(repo))
    assert "repo-chosen-model" not in embed_id and "nomic-embed-text" in embed_id, embed_id
