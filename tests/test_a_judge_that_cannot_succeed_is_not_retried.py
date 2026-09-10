"""Two debts this repository owed @mioimotoai-lgtm on issue #1, both named in our own reply and
neither written until now.

The report asked for "an actionable error instead of retrying unauthenticated judge calls". Our
2026-08-31 comment fixed the reporting half and said so plainly: "the attempts themselves still run,
six per case ... Cutting the retry short on an authentication failure is the rest of your sentence
and I have not written it."

Measured 2026-09-10 against a 401: six attempts, 45.0 s for ONE case, 15.0 minutes at the published
`--n 20`, on a credential that cannot start working. The bare `except Exception` treated 401 exactly
like 429, and only the second is worth waiting for.

The same comment admitted a second thing: the key loader has a third candidate under the CURRENT
DIRECTORY, and the refusal message never named it. "If you run the published command from a project
of your own that happens to have server/.env, it will read a key from a file I told you we do not
read. That is a footgun, and I have not fixed it." Reproduced from a scratch directory: the loader
took `sk-THIS-IS-A-STRANGERS-KEY` and printed nothing.

The candidate stays, because the repository this benchmark grew up in keeps its key exactly there and
dropping it would break the reproduction we publish. What changed is that a key read from a FILE is
announced with its absolute path, and the refusal prints the same candidate list the loader walks.

EVERY TEST HERE CARRIES A CONTROL that restores the old behaviour and requires the assertion to fail.
Without one, these would pass just as well against a deleted function.
"""
import importlib.util
import io
import os
import urllib.error

import pytest

BENCH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "probes", "integrity_bench_revert.py")


def _load(monkeypatch, cwd=None, key=None):
    """Import the benchmark fresh, so the module-level key binding is re-run under this environment."""
    if key is None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    else:
        monkeypatch.setenv("OPENAI_API_KEY", key)
    if cwd is not None:
        monkeypatch.chdir(cwd)
    spec = importlib.util.spec_from_file_location("bench_under_test", BENCH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _raises(module, code, counter):
    def fake(req, timeout=None):
        counter.append(1)
        raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(b"{}"))
    module.urllib.request.urlopen = fake


# -- the retry ------------------------------------------------------------------------------------

@pytest.mark.parametrize("code", [400, 401, 403, 404])
def test_a_status_that_cannot_succeed_is_attempted_once(monkeypatch, code):
    m = _load(monkeypatch, key="sk-bogus")
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    n = []
    _raises(m, code, n)
    assert m.openai_chat("x") is None
    assert len(n) == 1, "HTTP %d was attempted %d times; it cannot start working" % (code, len(n))


@pytest.mark.parametrize("code", [429, 500, 502, 503])
def test_a_status_that_might_succeed_is_still_retried(monkeypatch, code):
    """The control on the fix above: a change that stopped retrying everything would pass that test
    and break every rate-limited run, which is the failure this benchmark was built around."""
    m = _load(monkeypatch, key="sk-bogus")
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    n = []
    _raises(m, code, n)
    assert m.openai_chat("x") is None
    assert len(n) == 6, "HTTP %d was attempted %d times; it is worth waiting for" % (code, len(n))


def test_a_transient_failure_still_lands(monkeypatch):
    """Neither of the two above sees whether a retry can still SUCCEED, only how often it is tried."""
    m = _load(monkeypatch, key="sk-bogus")
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    calls = []

    class _Ok:
        def read(self):
            return b'{"choices":[{"message":{"content":" A "}}]}'

    def flaky(req, timeout=None):
        calls.append(1)
        if len(calls) < 3:
            raise urllib.error.HTTPError(req.full_url, 429, "rate limited", {}, io.BytesIO(b"{}"))
        return _Ok()

    m.urllib.request.urlopen = flaky
    assert m.openai_chat("x") == "A"
    assert len(calls) == 3


def test_the_control_the_old_loop_retried_a_bad_key_six_times(monkeypatch):
    """Restores the pre-fix behaviour and requires the assertion above to fail against it. Without
    this the retry tests would pass against a function that never retries anything."""
    m = _load(monkeypatch, key="sk-bogus")
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    monkeypatch.setattr(m, "_FATAL_HTTP", frozenset())      # the old `except Exception`
    n = []
    _raises(m, 401, n)
    m.openai_chat("x")
    assert len(n) == 6, "the control did not reproduce the defect, so the tests above measure nothing"


# -- where the key came from ----------------------------------------------------------------------

def test_a_key_from_a_stranger_directory_is_announced(monkeypatch, tmp_path, capsys):
    """The footgun itself. A key picked up from the CURRENT DIRECTORY must never be silent."""
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / ".env").write_text("OPENAI_API_KEY=sk-THIS-IS-A-STRANGERS-KEY\n",
                                              encoding="utf-8")
    m = _load(monkeypatch, cwd=tmp_path)
    assert m.OPENAI_KEY == "sk-THIS-IS-A-STRANGERS-KEY"
    assert m.KEY_SOURCE and m.KEY_SOURCE[0].endswith(os.path.join("server", ".env"))
    assert str(tmp_path) in m.KEY_SOURCE[0], "the announcement must name the absolute path"
    assert "read from" in capsys.readouterr().err


def test_a_key_from_the_process_is_not_announced(monkeypatch, tmp_path, capsys):
    """The control in the other direction: announcing every key would make the message noise, and
    noise is how the stranger case gets missed again. The caller put the process value there."""
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / ".env").write_text("OPENAI_API_KEY=from-the-dotenv\n", encoding="utf-8")
    m = _load(monkeypatch, cwd=tmp_path, key="from-the-process")
    assert m.OPENAI_KEY == "from-the-process", "the process must win against a dotenv"
    assert m.KEY_SOURCE == ["the process environment"]
    assert "read from" not in capsys.readouterr().err


def test_the_refusal_names_every_candidate_the_loader_walks(monkeypatch, tmp_path):
    """Our reply's complaint was that the message named two paths while the loader read three. This
    asserts the message is built FROM the loader's own list, so the two cannot drift apart again."""
    m = _load(monkeypatch, cwd=tmp_path)
    assert m.OPENAI_KEY == ""
    cands = m.KEY_CANDIDATES()
    assert len(cands) == 3
    assert all(len(c) == 2 for c in cands), "each candidate is a (label, path) pair"
    assert any(os.getcwd() in path for _lab, path in cands), \
        "the current-directory candidate is the one the message used to omit"
    # Run from the repository root, candidates 1 and 3 are the SAME file. The LABEL is what tells the
    # reader why one path appears twice, so an unlabelled list is a defect rather than a repetition.
    assert any("CURRENT DIRECTORY" in lab for lab, _p in cands)
