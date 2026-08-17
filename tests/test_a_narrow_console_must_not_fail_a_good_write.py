"""A console that cannot render a key must not turn a SUCCESSFUL write into a reported failure.

MEASURED 2026-08-16 on this machine's default cp1250 console:

    inspeximus remember "a value" --key "sedácia"      # the accented key, NFD form
    UnicodeEncodeError: 'charmap' codec can't encode character '\\u0301'
    rc 1 ... and the record IS on disk

The write had already succeeded. The crash was in the line printing the confirmation. So an
operator sees a traceback and a non-zero exit, concludes the write failed, and writes it again --
producing exactly the duplicate that supersession exists to prevent. A successful write reported as
a failure is worse than a refusal, because a refusal is honest about what happened.

WHY IT SURVIVED THIS LONG: our own probes forced `PYTHONUTF8=1` into the child environment, which
is not test hygiene, it is the mitigation. The suite applied the fix and then measured with the fix
applied. Found only when a review ran the probe WITHOUT the forcing.

`ascii` rather than `cp1250` below: cp1250 is a Windows default and this class is not
platform-specific. ascii is narrow everywhere, so this test reproduces the defect on any runner.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unicodedata

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NFD = unicodedata.normalize("NFD", "sedácia-klúč")
NFC = unicodedata.normalize("NFC", "sedácia-klúč")


def _run(store, *argv, encoding="ascii"):
    env = dict(os.environ)
    env.pop("PYTHONUTF8", None)
    env["PYTHONIOENCODING"] = encoding
    return subprocess.run([sys.executable, "-m", "inspeximus.cli", "--path", store, *argv],
                          capture_output=True, cwd=REPO, env=env)


@pytest.fixture
def store():
    return os.path.join(tempfile.mkdtemp(), "s.json")


@pytest.mark.parametrize("key", [NFD, NFC], ids=["nfd", "nfc"])
def test_an_unrenderable_key_still_exits_zero_and_is_stored(store, key):
    """THE POINT. Both halves matter: rc 0 so no one retries, and the bytes on disk unharmed."""
    r = _run(store, "remember", "a value", "--key", key, "--object", "x")
    assert r.returncode == 0, (r.stderr or b"").decode("utf-8", "replace")[-400:]

    rows = json.load(open(store, encoding="utf-8"))
    assert len(rows) == 1
    assert rows[0]["key"] == key, "the console fix must not touch what reaches the store"


def test_the_key_is_shown_losslessly_not_replaced_by_question_marks(store):
    """`errors="replace"` would print `sed?cia` and destroy the identifier the operator needs to
    read -- silently, which is the same class of defect one layer over. backslashreplace is ugly
    and reversible."""
    _run(store, "remember", "a value", "--key", NFD, "--object", "x")
    out = _run(store, "list").stdout.decode("ascii", "replace")
    assert "?" not in out or "\\u" in out, out[:300]


def test_the_json_surface_survives_it_too(store):
    """`--json` dumps with ensure_ascii=False, so it carries the same characters and would crash on
    the same console. An agent parsing JSON output is the caller least able to recover."""
    _run(store, "remember", "a value", "--key", NFD, "--object", "x")
    r = _run(store, "--json", "recall", "a value")
    assert r.returncode == 0, (r.stderr or b"").decode("utf-8", "replace")[-400:]


def test_the_json_surface_stays_parseable_and_lossless(store):
    """NOT LUCK, and the reason `backslashreplace` is the right escape rather than merely a safe one:
    it emits `\\u0301`, which is a VALID JSON string escape. So on a narrow console the machine
    surface stays parseable AND decodes back to the original key -- no crash, and no mangling either.
    `replace` would satisfy the crash half of this test and fail the second assert."""
    _run(store, "remember", "a value", "--key", NFD, "--object", "x")
    r = _run(store, "--json", "list")
    assert r.returncode == 0

    raw = r.stdout.decode("ascii", "strict")          # pure ASCII: nothing unencodable escaped
    obj = json.loads(raw)                             # still valid JSON
    assert NFD in json.dumps(obj, ensure_ascii=False), "the key did not survive the escape"


def test_control_an_encodable_key_is_printed_unchanged(store):
    """The must-not-mangle control. If the fix altered ordinary output, every human-readable line in
    152 print() calls would quietly change shape."""
    r = _run(store, "remember", "a value", "--key", "plain-ascii-key", "--object", "y")
    assert r.returncode == 0
    assert "plain-ascii-key" in r.stdout.decode("ascii", "replace")
    assert "\\u" not in r.stdout.decode("ascii", "replace")


def test_control_the_defect_is_real_on_this_runner(store):
    """MUST-FAIL CONTROL. If a runner's stdout is wide enough that an unguarded print cannot fail,
    every test above passes for free. This asserts the environment can actually produce the crash,
    by doing the unguarded thing in a subprocess."""
    r = subprocess.run(
        [sys.executable, "-c", "import sys; print(sys.argv[1])", NFD],
        capture_output=True, cwd=REPO,
        env={**os.environ, "PYTHONIOENCODING": "ascii", "PYTHONUTF8": "0"})
    assert r.returncode != 0 and b"UnicodeEncodeError" in (r.stderr or b""), (
        "an unguarded print of this key did NOT fail on this runner, so the tests above are not "
        "measuring the defect they name")
