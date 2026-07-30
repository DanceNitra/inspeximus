"""`check-code` is a BUILD GATE, so it must never go green on a store it could not read.

The guard already failed closed on a source file it cannot open (OSError -> exit 2). The store side
was the opposite: a mistyped --path yields an empty store, an empty store declares no deprecations,
and the scan then returns nothing for every file. Measured before the fix: the SAME violating file
exits 1 against the real store and 0, with no output, against a --path that does not exist.

A green build that checked nothing is the worst result a gate can produce, because it is
indistinguishable from a real pass.

Two cases that look alike and are deliberately treated differently:
  store missing      -> exit 2. There is no honest verdict available.
  zero deprecations  -> exit 0, because a project that has declared none is a correct state -- but the
                        output must SAY it had nothing to check against.

Driven as a subprocess: exit codes and stream separation are the whole subject.
"""
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIOLATING = "from app import Session\ns = Session()\ns.close_all()\n"


def cli(*args):
    p = subprocess.run([sys.executable, "-m", "inspeximus.cli", *args], cwd=REPO,
                       capture_output=True, text=True, timeout=180,
                       encoding="utf-8", errors="replace")
    return p.returncode, p.stdout or "", p.stderr or ""


@pytest.fixture()
def violating_file(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text(VIOLATING, encoding="utf-8")
    return f


@pytest.fixture()
def store_with_deprecation(tmp_path):
    from inspeximus.code_guard import deprecate_symbol
    from inspeximus.core import Inspeximus
    p = tmp_path / "s.json"
    m = Inspeximus(str(p))
    deprecate_symbol(m, "close_all", "aclose", "renamed in 2.0")
    m.flush()
    return p


def test_the_gate_catches_a_real_violation(store_with_deprecation, violating_file):
    """The control. Without it every other assertion here would pass on a gate that never fires."""
    code, _, err = cli("--path", str(store_with_deprecation), "check-code", str(violating_file))
    assert code == 1, f"a resurrected symbol must fail the build, got {code}: {err}"
    assert "close_all" in err or "resurrected" in err.lower()


def test_a_missing_store_refuses_instead_of_reporting_clean(tmp_path, violating_file):
    """The defect: this used to exit 0 with no output at all."""
    missing = tmp_path / "nope" / "typo.json"
    code, out, err = cli("--path", str(missing), "check-code", str(violating_file))
    assert code == 2, (
        f"a gate pointed at a store that does not exist must refuse, got exit {code}. "
        f"Reporting clean here means a mistyped --path produces a green build that checked nothing.")
    assert "refusing to report clean" in err.lower()
    assert "clean" not in out.lower()


def test_zero_deprecations_still_passes_but_says_it_checked_against_nothing(tmp_path, violating_file):
    """A project with no deprecations declared is a legitimate state, so this must NOT fail the build.

    But 'clean' has to disclose that there was nothing to compare against, or it reads exactly like a
    gate that verified something.
    """
    from inspeximus.core import Inspeximus
    p = tmp_path / "real_but_empty.json"
    m = Inspeximus(str(p))
    m.remember("an unrelated note")
    m.flush()

    code, _, err = cli("--path", str(p), "check-code", str(violating_file))
    assert code == 0, "an existing store with no deprecations is a real state, not an error"
    assert "0 deprecations declared" in err, f"'clean' must disclose it had nothing to check: {err!r}"
    assert "nothing to check against" in err


def test_an_unreadable_source_file_still_fails_closed(store_with_deprecation, tmp_path):
    """Pre-existing correct behaviour, pinned because it is the standard the store side now matches."""
    code, _, _ = cli("--path", str(store_with_deprecation), "check-code", str(tmp_path / "missing.py"))
    assert code == 2


def test_gate_diagnostics_are_ascii(tmp_path, violating_file):
    """cp1250 console: a non-ASCII diagnostic can raise UnicodeEncodeError and kill the command."""
    _, _, err = cli("--path", str(tmp_path / "nope" / "typo.json"), "check-code", str(violating_file))
    bad = [c for c in err if ord(c) > 127]
    assert not bad, f"diagnostics must be ASCII, found {bad[:5]!r}"
