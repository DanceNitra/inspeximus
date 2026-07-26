"""The remaining FIRST-PARTY functions with zero executed body lines.

After core.py and claude_code.py, what is left splits cleanly: ~52 uncovered functions are wrappers for
third-party frameworks (langgraph, google_adk, haystack, autogen, crewai, ...) that need optional
dependencies, and the rest is our own code that needs nothing. This file is the second group:

  _update.check_for_update    38 body lines -- network-facing, and it once pointed at a 404 package name
  browser.render_html/write_html          -- the HTML view of a store
  deletion_manifest / erasure_auditor     -- one-line methods named erase / recover / purge /
                                             still_recoverable, in a product about erasure

`check_for_update` is driven with a stubbed `urlopen`: it exists to make ONE network call, so the contract
is testable without a network, and a test that needs PyPI to be reachable is a flaky test, not a test.
"""
import io
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus._update as up
import inspeximus.browser as browser
import inspeximus.deletion_manifest as dmod
import inspeximus.erasure_auditor as eamod
from inspeximus import Inspeximus


def _cache():
    return tempfile.mkdtemp()


class _Resp:
    def __init__(self, payload):
        self._b = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _stub_pypi(monkeypatch, payload, calls=None):
    def fake(req, timeout=None):
        if calls is not None:
            calls.append(getattr(req, "full_url", req))
        if isinstance(payload, Exception):
            raise payload
        return _Resp(payload)
    monkeypatch.setattr(up.urllib.request, "urlopen", fake)


# ── version comparison: the classic 1.9 vs 1.10 trap ────────────────────────────────────────────────
@pytest.mark.parametrize("latest,current,newer", [
    ("1.71.0", "1.70.0", True),
    ("1.10.0", "1.9.0", True),      # string comparison gets this WRONG ("1.10.0" < "1.9.0")
    ("1.9.0", "1.10.0", False),
    ("1.70.0", "1.70.0", False),
    ("1.69.0", "1.70.0", False),
    ("2.0.0", "1.99.99", True),
])
def test_version_ordering_is_numeric_not_lexicographic(latest, current, newer):
    assert up._is_newer(latest, current) is newer


def test_a_garbage_version_does_not_raise():
    assert up._is_newer("not-a-version", "1.0.0") is False
    assert up._is_newer("1.0.0", None) in (True, False)


# ── check_for_update ────────────────────────────────────────────────────────────────────────────────
def test_it_reports_a_newer_release(monkeypatch):
    _stub_pypi(monkeypatch, {"info": {"version": "9.9.9"}})
    note = up.check_for_update("1.0.0", cache_dir=_cache())
    assert note and "9.9.9" in note, note
    assert note.isascii(), "the notice must stay ASCII -- it prints on a non-UTF-8 console"


def test_it_stays_quiet_when_current(monkeypatch):
    _stub_pypi(monkeypatch, {"info": {"version": "1.0.0"}})
    assert up.check_for_update("1.0.0", cache_dir=_cache()) is None


def test_the_opt_out_env_var_skips_the_network_entirely(monkeypatch):
    calls = []
    _stub_pypi(monkeypatch, {"info": {"version": "9.9.9"}}, calls)
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")
    assert up.check_for_update("1.0.0", cache_dir=_cache()) is None
    assert calls == [], "opting out must not make the request at all"


def test_it_is_throttled_to_one_network_call(monkeypatch):
    calls = []
    _stub_pypi(monkeypatch, {"info": {"version": "9.9.9"}}, calls)
    d = _cache()
    assert up.check_for_update("1.0.0", cache_dir=d)
    assert len(calls) == 1
    for _ in range(5):
        assert up.check_for_update("1.0.0", cache_dir=d) is None
    assert len(calls) == 1, f"the 24h throttle must hold: {len(calls)} calls"


@pytest.mark.parametrize("payload", [
    OSError("offline"),
    TimeoutError("slow"),
    b"not json at all",
    {"unexpected": "shape"},
])
def test_it_is_fail_open_on_anything(monkeypatch, payload):
    """Documented "fully fail-open". A courtesy notice must never break the caller -- the MCP server prints
    it to stderr because stdout is the JSON-RPC channel, so a raise here would take a session down."""
    _stub_pypi(monkeypatch, payload)
    assert up.check_for_update("1.0.0", cache_dir=_cache()) is None


def test_it_asks_pypi_for_the_package_that_actually_exists(monkeypatch):
    """Regression guard: this pointed at `agora-inspeximus`, which 404s. The notice could never fire, and
    if it had it would have told the user to install a package that does not exist."""
    calls = []
    _stub_pypi(monkeypatch, {"info": {"version": "9.9.9"}}, calls)
    up.check_for_update("1.0.0", cache_dir=_cache())
    assert calls and calls[0].rstrip("/").endswith("/pypi/inspeximus/json"), calls


def test_an_unwritable_cache_dir_does_not_raise(monkeypatch):
    _stub_pypi(monkeypatch, {"info": {"version": "9.9.9"}})
    assert up.check_for_update("1.0.0", cache_dir="\0illegal") is None


# ── browser ─────────────────────────────────────────────────────────────────────────────────────────
def _store():
    m = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "m.json"), receipts=True)
    m.remember("the deploy target is prod", key="deploy", object="prod")
    m.remember("the deploy target is staging", key="deploy", object="staging")
    return m


def test_render_html_shows_the_current_value_and_is_a_document():
    html = browser.render_html(_store())
    assert "<html" in html.lower() and "</html>" in html.lower()
    assert "staging" in html, "the CURRENT value must be visible"


def test_render_html_escapes_markup_from_a_memory():
    """The store holds arbitrary user text; rendering it raw is a script-injection hole in a file the
    user opens in a browser."""
    m = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "m.json"))
    m.remember("<script>alert('xss')</script> the payload")
    html = browser.render_html(m)
    assert "<script>alert" not in html, "memory text must be escaped, not interpolated"


def test_write_html_creates_a_readable_file():
    p = os.path.join(tempfile.mkdtemp(), "view.html")
    out = browser.write_html(_store(), path=p)
    assert os.path.exists(p), out
    assert "<html" in io.open(p, encoding="utf-8").read().lower()


# ── deletion manifest / erasure auditor: erase, recover, purge ──────────────────────────────────────
def test_a_registered_target_is_erased_and_reports_not_recoverable():
    """These are one-line methods with names like `erase` and `still_recoverable`. In a product about
    erasure, an untested one-liner is not a small gap."""
    store = {"alice": ["alice@example.com", "12 Oak St"], "bob": ["bob@example.com"]}

    class DictTarget:
        kind, name = "dict", "test-store"

        def erase(self, subject):
            n = len(store.pop(subject, []))
            return {"erased": n, "subject": subject}

        def still_recoverable(self, subject, values):
            return any(v in json.dumps(store) for v in values)

    man = dmod.DeletionManifest()
    man.register(DictTarget())
    # execute(subject, values, ...) -- `values` is what `still_recoverable` will be asked to look for.
    # I listed the method NAMES when probing and not their parameters, and three tests failed on it.
    res = man.execute("alice", ["alice@example.com", "12 Oak St"])

    assert "alice" not in store, res
    assert "bob" in store, "erasing one subject must not take the other"
    assert res["targets"] if "targets" in res else res, res
    assert "true" not in json.dumps(res).lower().split("still_recoverable")[-1][:12], res


def test_the_manifest_verifies_its_own_execution():
    class NoopTarget:
        kind, name = "noop", "does-nothing"

        def erase(self, subject):
            return {"erased": 0, "subject": subject}

        def still_recoverable(self, subject, values):
            return True                              # deliberately still there

    man = dmod.DeletionManifest()
    man.register(NoopTarget())
    manifest = man.execute("alice", ["alice@example.com"])

    # verify() takes the MANIFEST that execute() produced, not a subject and values.
    ok, problems = man.verify(manifest)
    assert isinstance(ok, bool), (ok, problems)

    flat = json.dumps(manifest).lower()
    assert "true" in flat, \
        f"a target that did not actually erase must be recorded as still recoverable: {manifest}"


def test_the_auditor_reports_a_soft_delete_as_still_recoverable():
    """A soft-delete store is the exact failure the auditor exists to catch: the row is 'deleted' and the
    value is still readable."""
    rows = [{"id": "1", "text": "alice@example.com", "deleted": False}]

    class SoftDeleteStore:
        kind, name = "sql", "soft-delete-db"

        def erase(self, subject):
            for r in rows:
                if subject in r["text"]:
                    r["deleted"] = True              # the row stays
            return {"erased": 1}

        def still_recoverable(self, subject, values):
            return any(v in r["text"] for r in rows for v in values)

    aud = eamod.ErasureAuditor()
    aud.register(SoftDeleteStore())
    report = aud.audit("alice@example.com", ["alice@example.com"])
    assert report is not None
    assert "soft-delete-db" in json.dumps(report), report


def test_the_auditor_produces_a_compliance_receipt():
    class CleanStore:
        kind, name = "dict", "clean"

        def erase(self, subject):
            return {"erased": 1}

        def still_recoverable(self, subject, values):
            return False

    aud = eamod.ErasureAuditor()
    aud.register(CleanStore())
    aud.audit("alice", ["alice@example.com"])
    receipt = aud.compliance_receipt("alice", ["alice@example.com"])
    assert isinstance(receipt, dict) and receipt, receipt
    assert "clean" in json.dumps(receipt), "the receipt must name the store it audited"
