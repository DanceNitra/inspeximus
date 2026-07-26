"""The release scripts had no tests, which is why a blind reader shipped.

Both MCP-registry checks read page ONE of an API that caps a page at 30 entries and hands back a
`nextCursor`. Once we had published more than 30 versions the verifier could not see the version it had
just released — it failed every release — and the state check would have answered "not already listed" and
driven publish into the duplicate-version 400 it exists to prevent.

Nothing in CI or the suite covered these scripts, so the only detector was a red release. These tests are
offline: `urlopen` is stubbed with a two-page registry.
"""
import json
import os
import sys
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "packages"))
import _registry_verify as rv  # noqa: E402

NAME = "io.github.DanceNitra/inspeximus"


def _entry(v, latest=False):
    e = {"server": {"name": NAME, "version": v}}
    if latest:
        e["_meta"] = {"io.modelcontextprotocol.registry/official": {"isLatest": True}}
    return e


class _Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _two_pages(monkeypatch, page_size=30, total=43):
    """A registry whose newest version is only reachable on page two."""
    versions = [f"1.{n}.0" for n in range(28, 28 + total)]
    pages, calls = [], []
    for i in range(0, total, page_size):
        chunk = versions[i:i + page_size]
        last = i + page_size >= total
        pages.append({"servers": [_entry(v, latest=(v == versions[-1])) for v in chunk],
                      "metadata": {"nextCursor": None if last else f"{NAME}:{chunk[-1]}"}})

    def fake(url, timeout=None):
        calls.append(url)
        return _Resp(pages[0] if "cursor=" not in url else pages[1])

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return versions, calls


def test_a_version_only_on_page_two_is_found(monkeypatch):
    """THE bug: 1.68.0 was listed and the check said it was not."""
    versions, calls = _two_pages(monkeypatch)
    assert rv.main(["x", versions[-1]]) == 0
    assert len(calls) == 2, "the second page must actually be fetched"


def test_reading_only_the_first_page_would_have_missed_it(monkeypatch):
    """The control: without the cursor loop this is what the check saw."""
    versions, _ = _two_pages(monkeypatch)
    first_page_only = {"servers": [_entry(v) for v in versions[:30]]}
    have = {e["server"]["version"] for e in first_page_only["servers"]}
    assert versions[-1] not in have


def test_a_genuinely_absent_version_is_still_reported_absent(monkeypatch):
    """A reader that finds everything is as useless as one that finds nothing."""
    _two_pages(monkeypatch)
    assert rv.main(["x", "9.9.9"]) == 1


def test_another_server_with_the_same_version_is_not_counted(monkeypatch):
    def fake(url, timeout=None):
        return _Resp({"servers": [{"server": {"name": "io.github.someone/other", "version": "2.0.0"}}],
                      "metadata": {"nextCursor": None}})
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert rv.main(["x", "2.0.0"]) == 1


def test_a_repeating_cursor_terminates(monkeypatch):
    """A registry that keeps handing back the same cursor must not spin forever."""
    def fake(url, timeout=None):
        return _Resp({"servers": [_entry("1.0.0")], "metadata": {"nextCursor": "same"}})
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert rv.main(["x", "1.0.0"]) == 0


def test_hitting_the_page_cap_raises_instead_of_answering(monkeypatch):
    """A partial view must not be able to say "not listed" — that is the original bug in another costume."""
    n = {"i": 0}

    def fake(url, timeout=None):
        n["i"] += 1
        return _Resp({"servers": [_entry(f"1.{n['i']}.0")],
                      "metadata": {"nextCursor": f"c{n['i']}"}})
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    monkeypatch.setattr(rv, "MAX_PAGES", 5)
    with pytest.raises(RuntimeError, match="partial view"):
        rv.fetch_all()


def test_the_state_check_pages_too(monkeypatch):
    """`_registry_state` decides whether to publish at all; the same blindness there means a duplicate 400."""
    import _registry_state as rs
    versions, _ = _two_pages(monkeypatch)
    assert versions[-1] in rs.listed_versions({"servers": rs.fetch_all_pages()}, NAME)
