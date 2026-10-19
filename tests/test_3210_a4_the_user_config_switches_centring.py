"""`store.center_embeddings` in the user's own config switches centring for every surface (3.21, AUDIT-A A-4).

The 3.21 Upgrade note offered `center_embeddings = False` to anyone relying on the old order, and that is a plain
attribute: the MCP server, the command line and the hooks, which are the paths without numpy, had no way to set it.
The switch is now a key in `<key home>/inspeximus/config.json`, beside `store.format`, and never an environment
variable, because a project's settings can set those. A repository's own `.inspeximus/config.json` does not count.
"""
import json
import os

import pytest

from inspeximus import _userconfig
from inspeximus.core import Inspeximus


@pytest.fixture
def home(monkeypatch, tmp_path):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    kh = tmp_path / "keyhome"
    (kh / "inspeximus").mkdir(parents=True)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(kh))
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")
    (tmp_path / "proj").mkdir()
    monkeypatch.chdir(tmp_path / "proj")    # outside the key home: a key home inside the cwd is refused (F-13)
    _userconfig._CACHE.clear()
    return kh


def _config(kh, value):
    (kh / "inspeximus" / "config.json").write_text(json.dumps({"store": {"center_embeddings": value}}))
    _userconfig._CACHE.clear()


def test_centring_is_on_by_default(home):
    assert Inspeximus().center_embeddings is True


def test_the_users_config_turns_it_off_for_every_handle(home):
    _config(home, False)
    assert Inspeximus().center_embeddings is False
    assert Inspeximus(str(home.parent / "s.json")).center_embeddings is False


def test_only_a_boolean_counts(home):
    for value in ("false", 0, None, "off"):
        _config(home, value)
        assert Inspeximus().center_embeddings is True, value


def test_a_repositorys_config_cannot_switch_it(home, tmp_path):
    (tmp_path / "proj" / ".inspeximus").mkdir()
    (tmp_path / "proj" / ".inspeximus" / "config.json").write_text(json.dumps({"store": {"center_embeddings": False}}))
    assert Inspeximus().center_embeddings is True


def test_an_environment_variable_does_not_switch_it(home, monkeypatch):
    monkeypatch.setenv("INSPEXIMUS_CENTER_EMBEDDINGS", "0")
    assert Inspeximus().center_embeddings is True


def test_code_still_wins(home):
    _config(home, False)
    h = Inspeximus()
    h.center_embeddings = True
    assert h.center_embeddings is True


def test_switched_off_the_pure_path_scores_raw_cosines(home, monkeypatch):
    """The switch reaches the ranking, not only the attribute: off, the pure path builds no centring pack."""
    from inspeximus import core
    vecs = {"a": [1.0, 0.2, 0.0], "b": [0.1, 1.0, 0.3], "c": [0.0, 0.1, 1.0]}
    _config(home, False)
    h = Inspeximus(embed=lambda t: vecs.get(t, vecs["a"]))
    for t in vecs:
        h.remember(t)
    built = []
    real = Inspeximus._center_pack
    monkeypatch.setattr(Inspeximus, "_center_pack", lambda self: built.append(1) or real(self))
    monkeypatch.setattr(core, "_np", None)
    assert h.recall("a", k=3, mode="semantic")
    assert not built, "the pure path centred although the user's config turned centring off"
