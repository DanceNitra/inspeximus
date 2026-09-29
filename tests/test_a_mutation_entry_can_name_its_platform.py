"""A mutation entry can name the platform its line runs on (3.15.6).

The POSIX `flock` line in `_StoreLock` runs only where fcntl exists. Its mutant is killed on Linux and
survives on Windows, and a survivor fails the gate, so the entry could not be registered and nothing ran
it. With `platform` it is registered, runs where its line runs, and is named as not run elsewhere.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import mutation_check  # noqa: E402

E = {"name": "e", "file": "x.py", "old": "a", "new": "b", "tests": ["t.py"]}


def _names(ms):
    return [m["name"] for m in ms]


def test_an_entry_without_the_field_runs_everywhere():
    for plat, osname in (("win32", "nt"), ("linux", "posix"), ("darwin", "posix")):
        here, elsewhere = mutation_check.split_by_platform([E], plat, osname)
        assert _names(here) == ["e"] and elsewhere == []


@pytest.mark.parametrize("want,plat,osname,runs", [
    ("linux", "linux", "posix", True),
    ("linux", "win32", "nt", False),
    ("linux", "darwin", "posix", False),
    ("posix", "linux", "posix", True),
    ("posix", "darwin", "posix", True),
    ("posix", "win32", "nt", False),
    ("win32", "win32", "nt", True),
    ("win32", "linux", "posix", False),
])
def test_an_entry_runs_only_where_its_platform_matches(want, plat, osname, runs):
    here, elsewhere = mutation_check.split_by_platform([{**E, "platform": want}], plat, osname)
    assert (_names(here), _names(elsewhere)) == ((["e"], []) if runs else ([], ["e"]))


def test_an_unknown_platform_is_an_error_not_an_entry_that_never_runs():
    with pytest.raises(ValueError, match="posx"):
        mutation_check.split_by_platform([{**E, "platform": "posx"}], "linux", "posix")


def test_an_entry_for_another_platform_is_named_and_the_run_does_not_fail(tmp_path, capsys, monkeypatch):
    other = "win32" if not sys.platform.startswith("win") else "linux"
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps([{**E, "name": "only elsewhere", "platform": other}]), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["mutation_check.py", str(spec)])
    assert mutation_check.main() == 0
    out = capsys.readouterr().out
    assert f"NOT RUN ON {sys.platform}: only elsewhere (platform {other})" in out


def test_the_registry_carries_the_flock_entry_with_its_platform_and_a_live_target():
    """Control: the entry this field exists for is registered, marked, and still points at real code."""
    with open(os.path.join(ROOT, "tools", "mutations.json"), encoding="utf-8") as fh:
        reg = json.load(fh)
    flock = [m for m in reg if "flock" in m["name"] and m.get("platform")]
    assert flock, "the POSIX flock mutant is not registered with a platform"
    for m in flock:
        assert m["platform"] in mutation_check.PLATFORMS
        with open(os.path.join(ROOT, m["file"]), encoding="utf-8") as fh:
            assert fh.read().count(m["old"]) == 1, m["name"]
