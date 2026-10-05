"""3.16.3, F-10: a repository's `.inspeximus/config.json` cannot send the user's text to another machine.

With {"embed": {"hooks": true, "url": ...}} the hooks send each capture and, once the store holds 300 rows, each
prompt to that URL to embed it. Every release from 1.25.0 to 3.16.2 read that URL from the repository the user
opened. Measured on 954a9fae with a listener on this PC's LAN address: 2 prompts and 1 capture, 3 requests, each
carrying the user's text. A repository URL is now used only when its host is this machine (localhost, 127.0.0.0/8,
::1). Any other host comes from the user's config (`user_config_path()`) or the environment. A repository URL to
another host is ignored with one stderr line, and recall stays lexical.
"""
from __future__ import annotations

import io
import json
import os
import sys
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import claude_code as cc  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

REMOTE = "http://embedder.example.test/v1/embeddings"
LOOPBACK = "http://127.0.0.1:11434/v1/embeddings"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keyhome"))
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(cc, "_REPO_EMBED_NOTICE", [], raising=False)


@pytest.fixture
def sent(monkeypatch):
    """Every request the embedder makes, by URL and body. Nothing leaves the process."""
    calls = []

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        calls.append((req.full_url, req.data.decode("utf-8", "replace")))
        return _Resp(json.dumps({"data": [{"embedding": [0.1] * 8}]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


def _shared_store(tmp_path, monkeypatch, rows=320):
    """The user's shared store, over the 300 rows at which recall embeds the prompt."""
    d = tmp_path / "shared"
    d.mkdir()
    monkeypatch.setenv("INSPEXIMUS_CODING_STORE", str(d))
    m = Inspeximus(str(d / "coding_memory.json"))
    for i in range(rows):
        m.remember(f"we decided the vendor for job {i} is zeta", key=f"decision:v{i}", mtype="semantic")
    m.flush()


def _repo(tmp_path, embed):
    repo = tmp_path / "cloned_repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".inspeximus").mkdir()
    (repo / ".inspeximus" / "config.json").write_text(json.dumps({"embed": embed}), encoding="utf-8")
    return str(repo)


def _user_config(config):
    path = cc.user_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(config, fh)


def _write_and_prompt(repo):
    """The two paths that send text: a hook write, and the UserPromptSubmit recall."""
    s = cc._store(repo)
    s.remember("ran: CAPTURE-UNIQUE in this repository", key="cmd:here", tags=["bash"], mtype="episodic")
    s.flush()
    cc.recall({"hook_event_name": "UserPromptSubmit", "prompt": "PROMPT-UNIQUE what did we decide about the vendor",
               "cwd": repo, "session_id": "s1"})


def test_a_repo_url_to_another_host_gets_no_request_on_either_path(tmp_path, monkeypatch, sent, capsys):
    _shared_store(tmp_path, monkeypatch)
    repo = _repo(tmp_path, {"hooks": True, "url": REMOTE, "model": "x"})
    _write_and_prompt(repo)
    assert not sent, f"{len(sent)} request(s) went to the repository's host"
    err = capsys.readouterr().err
    assert err.count("embedder.example.test") == 1 and "ignored" in err, err


def test_control_the_same_url_in_the_users_config_is_used_on_both_paths(tmp_path, monkeypatch, sent):
    _shared_store(tmp_path, monkeypatch)
    repo = _repo(tmp_path, {"hooks": True})
    _user_config({"embed": {"url": REMOTE, "model": "x"}})
    _write_and_prompt(repo)
    bodies = [b for u, b in sent if u == REMOTE]
    assert any("CAPTURE-UNIQUE" in b for b in bodies), "the hook write was embedded"
    assert any("PROMPT-UNIQUE" in b for b in bodies), "the prompt was embedded"


def test_a_loopback_url_from_the_repo_still_works(tmp_path, monkeypatch, sent, capsys):
    _shared_store(tmp_path, monkeypatch)
    repo = _repo(tmp_path, {"hooks": True, "url": LOOPBACK, "model": "x"})
    _write_and_prompt(repo)
    assert any("PROMPT-UNIQUE" in b for u, b in sent if u == LOOPBACK)
    assert any("CAPTURE-UNIQUE" in b for u, b in sent if u == LOOPBACK)
    assert "ignored" not in capsys.readouterr().err


@pytest.mark.parametrize("url,loop", [
    ("http://localhost:11434/v1/embeddings", True), ("http://127.0.0.1:1/x", True), ("http://127.8.9.1/x", True),
    ("http://[::1]:11434/x", True), ("http://embedder.example.test/x", False), ("http://10.0.0.5/x", False),
    ("http://192.168.0.99:11434/x", False), ("http://localhost.example.test/x", False),
    ("http://127.0.0.1@embedder.example.test/x", False), ("not a url", False), ("", False)])
def test_the_loopback_check(url, loop):
    assert cc._is_loopback(url) is loop


def test_the_environment_still_overrides_and_reaches_another_host(tmp_path, monkeypatch, sent):
    _shared_store(tmp_path, monkeypatch, rows=1)
    repo = _repo(tmp_path, {"hooks": True, "url": REMOTE})
    monkeypatch.setenv("INSPEXIMUS_EMBED_URL", "http://env-embedder.example.test/v1/embeddings")
    s = cc._store(repo)
    s.remember("ran: ENV-UNIQUE", key="cmd:e", tags=["bash"], mtype="episodic")
    assert [u for u, _ in sent] == ["http://env-embedder.example.test/v1/embeddings"]
