"""3.16.4, F-12 (the owner's decision): a URL from the environment to another host, and a key from the environment,
are used only when the user's own config names the host in `embed.url` or `embed.allowed_hosts`. A project's
settings `env` block reaches the hooks and the MCP server, so it can set both. Loopback needs no entry. The hook,
the CLI, the MCP server and `default_distiller` resolve through `inspeximus._http.env_url` and `env_key`."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import _http  # noqa: E402
from inspeximus import claude_code as cc  # noqa: E402
from inspeximus import cli  # noqa: E402
import inspeximus.core as core  # noqa: E402

REMOTE = "http://192.168.0.99:18765/v1/embeddings"


@pytest.fixture
def home(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keyhome"))
    (tmp_path / "keyhome" / "inspeximus").mkdir(parents=True)
    monkeypatch.setattr(_http, "_ENV_NOTICE", set())

    def allow(embed):
        (tmp_path / "keyhome" / "inspeximus" / "config.json").write_text(json.dumps({"embed": embed}))
    return allow


def _mcp():
    try:
        from inspeximus import mcp_server
    except Exception as e:                                      # noqa: BLE001
        pytest.skip("the MCP SDK is not installed: %s" % e)
    return mcp_server


def test_the_cli_and_the_mcp_server_ignore_an_env_url_to_another_host(home, monkeypatch, capsys):
    monkeypatch.setenv("INSPEXIMUS_EMBED_URL", REMOTE)
    assert cli._embedder() is None
    err = capsys.readouterr().err
    assert "INSPEXIMUS_EMBED_URL" in err and "192.168.0.99" in err, err
    assert _mcp()._make_embedders() == (None, None, None)


def test_control_the_cli_and_the_mcp_server_use_an_allowed_host_and_loopback(home, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_EMBED_URL", REMOTE)
    home({"allowed_hosts": ["192.168.0.99"]})
    assert cli._embedder() is not None and _mcp()._make_embedders()[0] is not None
    home({})
    monkeypatch.setenv("INSPEXIMUS_EMBED_URL", "http://127.0.0.1:11434/v1/embeddings")
    assert cli._embedder() is not None and _mcp()._make_embedders()[0] is not None


def test_a_host_named_in_the_users_embed_url_is_allowed(home, monkeypatch):
    home({"url": "http://192.168.0.99:9999/other"})
    monkeypatch.setenv("INSPEXIMUS_EMBED_URL", REMOTE)
    assert _http.env_url("INSPEXIMUS_EMBED_URL") == REMOTE


def test_the_env_key_is_not_sent_to_a_host_the_user_did_not_allow(home, monkeypatch):
    sent = []
    monkeypatch.setattr(_http, "post_json", lambda url, payload, headers=None, timeout=20: sent.append(
        (url, dict(headers or {}))) or {"choices": [{"message": {"content": "x"}}]})
    monkeypatch.setenv("INSPEXIMUS_LLM_KEY", "repo-chosen-key")
    core.default_distiller(url="http://203.0.113.7/v1/chat/completions")("p", "t")
    assert "Authorization" not in sent[-1][1], sent
    home({"allowed_hosts": ["203.0.113.7"]})                   # control: the user allows the host
    core.default_distiller(url="http://203.0.113.7/v1/chat/completions")("p", "t")
    assert sent[-1][1].get("Authorization") == "Bearer repo-chosen-key", sent


def test_the_distiller_ignores_an_env_url_to_another_host(home, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_LLM_URL", "http://203.0.113.7/v1/chat/completions")
    with pytest.raises(RuntimeError):
        core.default_distiller()
    home({"allowed_hosts": ["http://203.0.113.7/"]})           # an entry can be a URL
    assert callable(core.default_distiller())


def test_a_url_whose_host_two_parsers_read_differently_is_not_allowed(home, monkeypatch):
    home({"allowed_hosts": ["192.168.0.99"]})
    for url in ("http://192.168.0.99@203.0.113.7/v1", "http://203.0.113.7\@192.168.0.99/v1"):
        monkeypatch.setenv("INSPEXIMUS_EMBED_URL", url)
        assert _http.env_url("INSPEXIMUS_EMBED_URL") == "", url
    # urlsplit reads the host "192.168.0.99%40203.0.113.7"; urllib.request unquotes it and connects to 203.0.113.7.
    url = "http://192.168.0.99%40203.0.113.7/v1"
    home({"allowed_hosts": ["192.168.0.99%40203.0.113.7"]})
    monkeypatch.setenv("INSPEXIMUS_EMBED_URL", url)
    assert _http.env_url("INSPEXIMUS_EMBED_URL") == "", url


def test_a_url_with_no_host_is_refused_without_an_exception(home, monkeypatch):
    for url in ("http:203.0.113.7/v1", "http://[::1]x/"):
        monkeypatch.setenv("INSPEXIMUS_EMBED_URL", url)
        assert _http.env_url("INSPEXIMUS_EMBED_URL") == "" and not _http.is_loopback(url), url


def test_the_hook_falls_back_to_the_users_url_when_the_env_url_is_refused(home, monkeypatch, tmp_path):
    home({"url": "http://10.1.2.3:8080/v1/embeddings", "hooks": True})
    monkeypatch.setenv("INSPEXIMUS_EMBED_URL", REMOTE)
    seen = []
    monkeypatch.setattr(_http, "post_json", lambda url, *a, **k: seen.append(url) or {"data": [{"embedding": [0.1]}]})
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    emb = cc._make_embedder(str(repo))
    emb[0]("text")
    assert seen == ["http://10.1.2.3:8080/v1/embeddings"], seen
