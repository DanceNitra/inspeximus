"""An input over the model's context is embedded by its longest prefix the model takes, and `reembed` names what failed (3.18).

Measured 2026-10-10 on the crew store: record 85378a0fb4 (6,123 characters of markdown and figures) got HTTP 413 "the
input length exceeds the context length" from Ollama bge-m3, so every `reembed` reported `failed 1` without saying
which record, and the record stayed lexical. The limit is in tokens, so it fits up to about 5,980 characters and a text
of plain words fits at 30,000. The embedders the MCP server, the CLI and the hooks build now send such an input again,
shortened to FIT_SHRINK of its length, at most FIT_TRIES times; every other error is raised as before.

The stub endpoint below takes at most LIMIT characters and answers a vector that names the length it embedded.
"""
from __future__ import annotations

import http.server
import json
import os
import sys
import threading
import urllib.error

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import _http  # noqa: E402
from inspeximus import claude_code as cc  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

LIMIT = 1000


class _Endpoint:
    """`mode`: "413" (a 413 over LIMIT), "400ctx" (a 400 that names the context), "400other" (a 400 for another
    reason, always), "500" (always)."""

    def __init__(self, mode="413"):
        self.inputs = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                text = json.loads(self.rfile.read(n))["input"]
                outer.inputs.append(text)
                if mode == "500":
                    return self._send(500, {"error": "down"})
                if mode == "400other":
                    return self._send(400, {"error": {"message": "model not found"}})
                if len(text) > LIMIT:
                    if mode == "400ctx":
                        return self._send(400, {"error": {"message": "This model's maximum context length is 8192"}})
                    return self._send(413, {"error": {"message": "the input length exceeds the context length"}})
                return self._send(200, {"data": [{"embedding": [float(len(text)), 1.0, 0.5]}]})

            def _send(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d/v1/embeddings" % self.srv.server_address[1]

    def close(self):
        self.srv.shutdown()


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_") or k.lower().endswith("_proxy")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keyhome"))
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")


@pytest.fixture
def endpoint(request):
    e = _Endpoint(getattr(request, "param", "413"))
    yield e
    e.close()


def _fit(n):
    """The length embed_fitting ends on for a text of n characters, by the rule it documents."""
    t = n
    for _ in range(_http.FIT_TRIES):
        if t <= LIMIT:
            return t
        t = int(t * _http.FIT_SHRINK)
    return t if t <= LIMIT else None


TEXT = "".join("word%d " % i for i in range(400))          # 2,690 characters, over LIMIT


def test_the_fixture_refuses_the_text_whole(endpoint):
    with pytest.raises(urllib.error.HTTPError) as e:
        _http.post_json(endpoint.url, {"model": "m", "input": TEXT}, None, 10)
    assert e.value.code == 413, "CONTROL: the endpoint refuses an input over its context"


@pytest.mark.parametrize("endpoint", ["413", "400ctx"], indirect=True)
def test_an_input_over_the_context_is_embedded_by_a_prefix(endpoint):
    v = _http.embed_fitting(endpoint.url, "m", TEXT, "", None, 10)
    want = _fit(len(TEXT))
    assert v[0] == float(want), "embedded %r characters, the rule gives %r" % (v[0], want)
    assert endpoint.inputs[-1] == TEXT[:want], "the input sent is not a prefix of the text"
    seq = [len(TEXT)]
    while seq[-1] > LIMIT:
        seq.append(int(seq[-1] * _http.FIT_SHRINK))
    assert [len(x) for x in endpoint.inputs] == seq, "the lengths tried are not the documented ones"
    assert _http.embed_fitting(endpoint.url, "m", TEXT, "", None, 10) == v, "the same text gave another vector"


def test_the_task_prefix_is_kept_whole_and_only_the_text_is_shortened(endpoint):
    p = "search_document: "
    _http.embed_fitting(endpoint.url, "m", TEXT, p, None, 10)
    assert all(x.startswith(p) for x in endpoint.inputs), "a shortened input lost the task prefix"
    assert endpoint.inputs[-1] == p + TEXT[:len(endpoint.inputs[-1]) - len(p)]


def test_an_input_inside_the_context_is_sent_once_and_whole(endpoint):
    short = TEXT[:LIMIT]
    v = _http.embed_fitting(endpoint.url, "m", short, "", None, 10)
    assert v[0] == float(LIMIT) and endpoint.inputs == [short]


@pytest.mark.parametrize("endpoint", ["400other", "500"], indirect=True)
def test_any_other_error_is_raised_after_one_request(endpoint):
    with pytest.raises(urllib.error.HTTPError):
        _http.embed_fitting(endpoint.url, "m", TEXT, "", None, 10)
    assert len(endpoint.inputs) == 1, "an error that is not about the context was retried"


def test_a_text_that_never_fits_is_raised_after_the_last_try(endpoint, monkeypatch):
    monkeypatch.setattr(_http, "FIT_TRIES", 2)
    with pytest.raises(urllib.error.HTTPError):
        _http.embed_fitting(endpoint.url, "m", TEXT * 10, "", None, 10)
    assert len(endpoint.inputs) == 3, "the tries are bounded by FIT_TRIES"


def test_the_shared_builder_and_the_hook_builder_both_fit(endpoint, tmp_path, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_EMBED_URL", endpoint.url)
    monkeypatch.setenv("INSPEXIMUS_EMBED_MODEL", "symmetric-model")
    from inspeximus._embedders import make_embedders
    doc, _q, _id = make_embedders()
    assert doc(TEXT)[0] == float(_fit(len(TEXT))), "the MCP server's and the CLI's embedder"
    path = cc.user_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"embed": {"hooks": True, "url": endpoint.url}}, fh)
    hook = cc._make_embedder(str(tmp_path))[0]
    assert hook(TEXT)[0] == float(_fit(len(TEXT))), "the hooks' embedder"


def test_reembed_names_the_records_it_could_not_embed(tmp_path):
    def embed(t):
        if "unembeddable" in t:
            raise urllib.error.HTTPError("http://x", 413, "Request Entity Too Large", None, None)
        return [1.0, 0.0, 0.5]
    m = Inspeximus(path=str(tmp_path / "s.json"))
    m.remember("an ordinary note")
    bad = m.remember("an unembeddable note")
    m.embed = embed
    out = m.reembed(only_missing=False)
    assert out["failed"] == 1 and out["reembedded"] == 1, out
    assert [f["id"] for f in out["failed_ids"]] == [bad], out
    assert "413" in out["failed_ids"][0]["error"]


def test_reembed_with_nothing_failed_has_no_failed_ids(tmp_path):
    m = Inspeximus(path=str(tmp_path / "s.json"))
    m.remember("an ordinary note")
    m.embed = lambda t: [1.0, 0.0, 0.5]
    out = m.reembed(only_missing=False)
    assert out["failed"] == 0 and "failed_ids" not in out, out
