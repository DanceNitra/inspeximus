"""AUDIT-A A-11: a mistyped store path is a new, empty store, and nothing says so.

`open_store` on a path whose directory does not exist opens an empty store and creates the
directory on the first write. A typo in INSPEXIMUS_PATH, or a Git Bash path on Windows
(`/c/Users/...`, which Python reads as `C:\\c\\Users\\...`), therefore gives the agent an empty memory:
`recall` returns nothing with `isError: false`, and the only trace is one stderr line from the MCP
server, which hosts discard. Session 1 hit this on 2026-09-27.

Measured the same day on origin/main 2b2151fb: an MCP `recall` against INSPEXIMUS_PATH in a missing
directory CREATED that directory. A read changed the filesystem.

Fixed in 3.15.3 (session 1 chose refuse): opening a store in a directory that does not exist raises
StoreLocationError at the surface, and no read creates a directory. These tests are AUDIT-A's reproducer
(026791eb), unchanged except that the strict xfail markers are gone.
"""
import os
import sys
import warnings

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import _surface

REASON = "A-11: a store path in a missing directory opens a silent empty store"


@pytest.fixture
def clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("INSPEXIMUS_"):
            monkeypatch.delenv(k, raising=False)


def _open_and_read(path):
    """Returns (raised, warned). Opening may raise; that is one acceptable answer."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        try:
            s = _surface.open_store(path)
            s.recall("anything at all", k=5)
            s.flush()
        except (FileNotFoundError, ValueError, OSError):
            return True, bool(w)
    return False, bool(w)


def test_opening_and_reading_a_store_in_a_missing_directory_creates_nothing(tmp_path, clean_env):
    real = tmp_path / "real"
    real.mkdir()
    s = _surface.open_store(str(real / "memory.json"))
    s.remember("the deploy key rotates on Fridays", key="deploy::rotation", object="friday")
    s.flush()
    if not s.recall("deploy key rotates", k=5):
        pytest.fail("control: the correctly spelled store does not recall its own record")
    typo = tmp_path / "reall" / "memory.json"
    _open_and_read(str(typo))
    assert not typo.parent.exists(), "opening and reading a mistyped path created its directory"


def test_a_store_in_a_missing_directory_is_refused_or_announced(tmp_path, clean_env):
    raised, warned = _open_and_read(str(tmp_path / "no_such_dir" / "memory.json"))
    assert raised or warned, "a store in a directory that does not exist opened silently and empty"


@pytest.mark.skipif(sys.platform != "win32", reason="Git Bash paths are a Windows spelling")
def test_a_git_bash_path_does_not_open_a_different_empty_store(tmp_path, clean_env):
    real = tmp_path / "real"
    real.mkdir()
    s = _surface.open_store(str(real / "memory.json"))
    s.remember("a fact", key="k", object="v")
    s.flush()
    win = str(real / "memory.json")
    gitbash = "/" + win[0].lower() + win[2:].replace("\\", "/")
    # The defect writes OUTSIDE tmp_path (under <drive>:\c\...), so remove what this test created
    # there: empty directories only, bottom-up, from the highest ancestor that did not exist before.
    mirror = os.path.abspath("/" + win[0].lower() + str(tmp_path)[2:].replace("\\", "/"))
    top = mirror
    while not os.path.exists(os.path.dirname(top)) and os.path.dirname(top) != top:
        top = os.path.dirname(top)
    existed = os.path.exists(top)
    try:
        raised, warned = _open_and_read(gitbash)
        if raised or warned:
            return
        assert len(_surface.open_store(gitbash).items) == 1, \
            f"{gitbash} opened a different, empty store instead of {win}"
    finally:
        if not existed and os.path.isdir(top):
            for d, _, _ in sorted(os.walk(top), key=lambda t: -len(t[0])):
                try:
                    os.rmdir(d)
                except OSError:
                    pass
