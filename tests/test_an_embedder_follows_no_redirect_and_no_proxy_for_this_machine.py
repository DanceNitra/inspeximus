"""3.16.4 (AUDIT-A's residual of F-10): the text an embedder or a distiller sends goes to the configured URL only.

`urllib.request.urlopen` follows a 301/302/303 to whatever host the answer names, keeping the Authorization header,
and sends an http URL through $HTTP_PROXY. So a loopback embedder (the only kind a repository's config may name)
that answers a redirect, or a proxy variable in the environment, carried the request to another host. Every
embedder and the default distiller now POST through `inspeximus._http.post_json`: no redirect is followed, and a
loopback URL uses no proxy. Each test has a control showing that plain `urlopen` does reach the second listener,
so the fixture still reproduces what the fix closes.
"""
from __future__ import annotations

import http.server
import json
import os
import sys
import threading
import urllib.error
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import _http  # noqa: E402
from inspeximus import claude_code as cc  # noqa: E402


class _Listener:
    """A local HTTP server. `mode` "embed" answers an embedding, "redirect" answers 302 to `location`."""

    def __init__(self, mode="embed", location=None):
        self.requests = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def _any(self):
                n = int(self.headers.get("Content-Length") or 0)
                outer.requests.append({"path": self.path, "body": self.rfile.read(n).decode("utf-8", "replace"),
                                       "auth": self.headers.get("Authorization")})
                if mode == "redirect":
                    self.send_response(302)
                    self.send_header("Location", location)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = json.dumps({"data": [{"embedding": [0.1] * 8}],
                                   "choices": [{"message": {"content": "distilled"}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_POST = _any
            do_GET = _any

            def log_message(self, *a):
                pass

        self.srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d/v1/embeddings" % self.srv.server_address[1]

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def pair():
    """`second` stands for another host; `first` is the loopback embedder that redirects to it."""
    second = _Listener("embed")
    first = _Listener("redirect", location=second.url)
    yield first, second
    first.close()
    second.close()


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_") or k.lower().endswith("_proxy")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keyhome"))
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(cc, "_REPO_EMBED_NOTICE", [], raising=False)
    monkeypatch.setattr(cc, "_REPO_EMBED_KEYS_NOTICE", [], raising=False)


def _post_plain(url):
    req = urllib.request.Request(url, data=b'{"input": "SECRET-TEXT"}', headers={
        "Content-Type": "application/json", "Authorization": "Bearer SECRET-KEY"})
    # A FRESH default opener, which is what urlopen builds: urlopen itself caches one opener per process, built
    # with the proxies of whatever environment the first call saw.
    with urllib.request.build_opener().open(req, timeout=10) as r:
        return r.read()


# ---- redirects ---------------------------------------------------------------------------------------------------

def test_control_plain_urlopen_follows_the_redirect_and_carries_the_key(pair):
    first, second = pair
    _post_plain(first.url)
    assert len(second.requests) == 1, "the fixture no longer reproduces the redirect"
    assert second.requests[0]["auth"] == "Bearer SECRET-KEY", "urlopen keeps the Authorization header"


def test_post_json_refuses_a_redirect_and_the_second_host_gets_nothing(pair):
    first, second = pair
    with pytest.raises(_http.RedirectRefused):
        _http.post_json(first.url, {"input": "SECRET-TEXT"}, {"Authorization": "Bearer SECRET-KEY"}, 10)
    assert len(first.requests) == 1
    assert second.requests == [], second.requests


def test_the_hook_embedder_gets_no_request_through_at_the_second_host(pair, tmp_path, monkeypatch):
    first, second = pair
    path = cc.user_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"embed": {"hooks": True, "url": first.url, "key": "SECRET-KEY"}}, fh)
    emb = cc._make_embedder(str(tmp_path))[0]
    with pytest.raises(urllib.error.HTTPError):
        emb("SECRET-TEXT")
    assert second.requests == []


def test_a_hook_write_falls_back_and_still_lands_when_the_embedder_redirects(pair, tmp_path, monkeypatch):
    first, second = pair
    path = cc.user_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"embed": {"hooks": True, "url": first.url}}, fh)
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    s = cc._store(str(repo))
    rid = s.remember("ran: REDIRECT-UNIQUE", key="cmd:r", tags=["bash"], mtype="episodic")
    s.flush()
    assert rid and any(r["id"] == rid for r in cc._store(str(repo))._items), "the capture is not dropped"
    assert second.requests == []


def test_the_cli_embedder_and_the_default_distiller_refuse_the_redirect(pair, monkeypatch):
    first, second = pair
    from inspeximus import cli
    from inspeximus.core import default_distiller
    monkeypatch.setenv("INSPEXIMUS_EMBED_URL", first.url)
    with pytest.raises(urllib.error.HTTPError):
        cli._embedder()("SECRET-TEXT")
    with pytest.raises(urllib.error.HTTPError):
        default_distiller(url=first.url)("prompt", "SECRET-TEXT")
    assert second.requests == []


def test_the_mcp_server_embedder_refuses_the_redirect(pair, monkeypatch):
    pytest.importorskip("mcp.server.fastmcp")
    first, second = pair
    from inspeximus import mcp_server
    monkeypatch.setenv("INSPEXIMUS_EMBED_URL", first.url)
    with pytest.raises(urllib.error.HTTPError):
        mcp_server._make_embedders()[0]("SECRET-TEXT")
    assert second.requests == []


# ---- proxies -----------------------------------------------------------------------------------------------------

@pytest.fixture
def embed_and_proxy():
    embedder, proxy = _Listener("embed"), _Listener("embed")
    yield embedder, proxy
    embedder.close()
    proxy.close()


def _proxy_env(monkeypatch, proxy):
    origin = proxy.url.rsplit("/v1/", 1)[0]
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(k, origin)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)


def test_control_plain_urlopen_sends_a_loopback_url_through_the_proxy(embed_and_proxy, monkeypatch):
    embedder, proxy = embed_and_proxy
    _proxy_env(monkeypatch, proxy)
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: False)
    _post_plain(embedder.url)
    assert len(proxy.requests) == 1 and embedder.requests == [], "the fixture no longer reproduces the proxy use"


def test_a_loopback_url_uses_no_proxy(embed_and_proxy, monkeypatch):
    embedder, proxy = embed_and_proxy
    _proxy_env(monkeypatch, proxy)
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: False)
    out = _http.post_json(embedder.url, {"input": "SECRET-TEXT"}, {}, 10)
    assert out["data"][0]["embedding"]
    assert len(embedder.requests) == 1 and proxy.requests == [], (embedder.requests, proxy.requests)


def test_a_url_to_another_host_keeps_the_environments_proxy(embed_and_proxy, monkeypatch):
    """A network can need its proxy for a remote embedder; only this machine is taken out of it."""
    embedder, proxy = embed_and_proxy
    _proxy_env(monkeypatch, proxy)
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: False)
    _http.post_json("http://embedder.example.test/v1/embeddings", {"input": "x"}, {}, 10)
    assert len(proxy.requests) == 1 and proxy.requests[0]["path"].startswith("http://embedder.example.test/")


@pytest.mark.parametrize("url,loop", [("http://localhost:1/x", True), ("http://127.0.0.1:1/x", True),
                                      ("http://[::1]:1/x", True), ("http://10.0.0.5/x", False),
                                      ("http://127.0.0.1:%40evil.example/", False)])
def test_the_opener_has_no_proxy_handler_exactly_for_this_machine(url, loop, monkeypatch):
    # With a proxy in the environment, a loopback opener carries no proxying handler; another host keeps one.
    # (`build_opener` drops a ProxyHandler({}) from its list, having no scheme methods, and adds no default.)
    for k in ("HTTP_PROXY", "http_proxy"):
        monkeypatch.setenv(k, "http://127.0.0.1:9")
    handlers = _http.opener_for(url).handlers
    proxying = any(isinstance(h, urllib.request.ProxyHandler) and h.proxies for h in handlers)
    assert proxying is not loop
    assert any(isinstance(h, _http._NoRedirect) for h in handlers)
    assert not any(type(h) is urllib.request.HTTPRedirectHandler for h in handlers)
