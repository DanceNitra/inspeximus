"""INSPEXIMUS_PATH="~/store.json" must resolve to the home directory, on every surface.

THE DEFECT. `resolve_path` returned the environment variable unexpanded, and `Store.__init__`
expanded it on its own. So the file opened in the right place while everything that REPORTS the
path described a different one: `where_am_i` answered `<cwd>/~/store.json`, the startup line on
stderr said the same, and `recall`'s "no store yet" branch tested `os.path.exists` on the literal
tilde and therefore fired over a store that existed and held memories. The one tool whose whole
job is "which store am I talking to" named a path that cannot exist.

The link vetting in `_vet_path_link` ran on that same literal path, so a `~`-prefixed store was
never actually vetted: `os.path.join(cwd, "~/store.json")` resolves to nothing, no link chain is
found, and the path is returned as the user's own.
"""
import os

from inspeximus._surface import resolve_path


def test_resolve_path_expands_a_leading_tilde(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("INSPEXIMUS_PATH", "~/store.json")

    got = resolve_path()

    assert "~" not in got, "the tilde survived resolution: %r" % got
    assert os.path.isabs(got), "a home-relative path must resolve to an absolute one: %r" % got
    assert os.path.normcase(got) == os.path.normcase(str(home / "store.json")), got


def test_an_expanded_path_is_the_one_a_reporting_surface_would_test(monkeypatch, tmp_path):
    """The reporting surfaces all call os.path.exists on what resolve_path returned.

    This is the assertion that fails on the old code for the reason the user felt: the store is
    there, and the path the surfaces hold says it is not.
    """
    home = tmp_path / "home"
    home.mkdir()
    (home / "store.json").write_text("[]", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("INSPEXIMUS_PATH", "~/store.json")

    assert os.path.exists(resolve_path()), "a surface would report this existing store as missing"


def test_an_ordinary_path_is_untouched(monkeypatch, tmp_path):
    """CONTROL. The fix must change nothing for a path that carries no tilde.

    Without this, a resolve_path that returned the home directory unconditionally would pass the
    two tests above.
    """
    plain = tmp_path / "plain.json"
    monkeypatch.setenv("INSPEXIMUS_PATH", str(plain))

    assert os.path.normcase(resolve_path()) == os.path.normcase(str(plain))


def test_the_tilde_case_is_reachable_at_all(monkeypatch, tmp_path):
    """CONTROL. If `~` ever stopped meaning the home directory on this platform, the tests above
    would pass for a reason that has nothing to do with the fix."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    assert os.path.expanduser("~/store.json") != "~/store.json", "expanduser is a no-op here"
