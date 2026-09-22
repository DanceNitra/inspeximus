"""The mirror may only contain what the source already publishes, and the sweep must be able to see a leak.

Two lines of defence, tested separately because each has to work when the other does not:

1. the source is fetched over HTTP, so a file that was never published cannot be in the mirror;
2. a denylist and a byte-level sweep, in case the publisher leaked it at the source.

The sweep's own control matters most: a scanner that finds nothing because it looks at nothing
reports exactly the same "clean" as a tree that is genuinely clean.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
sys.path.insert(0, TOOLS)

import mirror_public_site as mirror                                      # noqa: E402


def test_a_private_path_is_refused_before_it_is_fetched():
    for url in ("https://x/agora_output/strategy/plan.md", "https://x/.env",
                "https://x/drafts/letter.md", "https://x/id_ed25519"):
        assert mirror.denied(url), url


def test_a_public_path_is_not_refused():
    """The control for the line above: a denylist that refuses everything protects nothing."""
    for url in ("https://x/public/posts/a.html", "https://x/index.html",
                "https://x/public/crucible/index.html"):
        assert mirror.denied(url) is None, url


def test_the_sweep_finds_a_private_path(tmp_path):
    os.makedirs(tmp_path / "agora_output" / "strategy")
    (tmp_path / "agora_output" / "strategy" / "plan.md").write_text("internal", encoding="utf-8")
    out = mirror.sweep(str(tmp_path))
    assert out["clean"] is False
    assert out["private_by_path"][0]["path"].endswith("plan.md")


def test_the_sweep_finds_a_key_inside_a_public_looking_page(tmp_path):
    """Clean by path and dirty by content is the leak a path check cannot see."""
    (tmp_path / "post.html").write_text("<html>-----BEGIN PRIVATE KEY-----</html>", encoding="utf-8")
    out = mirror.sweep(str(tmp_path))
    assert out["clean"] is False and out["private_by_content"]


def test_a_genuinely_public_tree_is_clean(tmp_path):
    """The other half of the control: the sweep must not cry wolf over ordinary pages."""
    (tmp_path / "index.html").write_text("<html>a post about memory integrity</html>", encoding="utf-8")
    os.makedirs(tmp_path / "public" / "posts")
    (tmp_path / "public" / "posts" / "a.html").write_text("<html>text</html>", encoding="utf-8")
    out = mirror.sweep(str(tmp_path))
    assert out["clean"] is True and out["files"] == 2


def test_a_directory_url_becomes_an_index_page():
    base = "https://example.test/site/"
    assert mirror.local_path("/out", base, base).endswith(os.path.join("out", "index.html"))
    assert mirror.local_path("/out", base, base + "public/").endswith(os.path.join("public", "index.html"))
    assert mirror.local_path("/out", base, base + "a/b.html").endswith(os.path.join("a", "b.html"))


def test_a_transient_failure_is_retried_rather_than_recorded_as_missing(monkeypatch):
    """Measured on the first full run: one page timed out mid-read and curl then returned it twice."""
    calls = {"n": 0}

    class _Resp:
        status = 200
        headers = {"Content-Type": "text/html"}

        def read(self):
            return b"<html></html>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise TimeoutError("the read timed out")
        return _Resp()

    monkeypatch.setattr(mirror.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(mirror.time, "sleep", lambda *_a: None)
    status, _ctype, body = mirror.fetch("https://example.test/x")
    assert status == 200 and body and calls["n"] == 3


def test_a_404_is_not_retried(monkeypatch):
    """A 404 is an answer. Retrying it three times is a slower way to learn the same thing."""
    calls = {"n": 0}

    def gone(*a, **k):
        calls["n"] += 1
        raise mirror.urllib.error.HTTPError("https://example.test/x", 404, "Not Found", {}, None)

    monkeypatch.setattr(mirror.urllib.request, "urlopen", gone)
    with pytest.raises(mirror.urllib.error.HTTPError):
        mirror.fetch("https://example.test/x")
    assert calls["n"] == 1
