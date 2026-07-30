"""The installer edits a file the USER owns. It must never crash on it, and never lose it.

install.plan() is documented to work out what would change "without touching anything", reporting
trouble in res["error"]. Every shape a real config file can arrive in is exercised here, because the
blast radius is someone's working Claude/Cursor setup rather than a wrong return value.

Found by driving these shapes: a config that is valid JSON but NOT an object (a bare list) crashed
plan() with `AttributeError: 'list' object has no attribute 'setdefault'`. The guard for that existed
but sat one level too deep -- it refused a root_key whose value was not an object, after already
calling .setdefault() on the top level.
"""
import json
import pathlib
import tempfile

import pytest

from inspeximus import install as I

HOST = "claude"


@pytest.fixture()
def retarget():
    """Point the host's config path at a scratch file, restoring the real hook afterwards."""
    original = I.HOSTS[HOST]["paths"]
    made = []

    def _set(content=None):
        cfg = pathlib.Path(tempfile.mkdtemp()) / "config.json"
        if content is not None:
            cfg.write_text(content, encoding="utf-8")
        I.HOSTS[HOST]["paths"] = lambda project=None, _c=cfg: {"user": _c}
        made.append(cfg)
        return cfg

    yield _set
    I.HOSTS[HOST]["paths"] = original


@pytest.mark.parametrize("label,content", [
    ("bare list", "[1, 2, 3]"),
    ("bare string", '"just a string"'),
    ("bare number", "42"),
    ("bare null", "null"),
])
def test_valid_json_that_is_not_an_object_is_refused_not_crashed(retarget, label, content):
    cfg = retarget(content)
    p = I.plan(HOST)                      # must NOT raise
    assert p.get("error"), f"{label}: expected a reported error, got {p!r}"
    assert cfg.read_text(encoding="utf-8") == content, f"{label}: the user's file was modified"


def test_malformed_json_is_refused_and_the_file_is_untouched(retarget):
    content = '{"mcpServers": {"a": {'
    cfg = retarget(content)
    p = I.plan(HOST)
    assert p.get("error")
    assert cfg.read_text(encoding="utf-8") == content


def test_applying_is_idempotent_and_preserves_a_foreign_config(retarget):
    """The realistic case: the user already has another tool installed.

    Three runs, because 'add' then 'unchanged' is the property that matters -- a second run that
    re-added or duplicated the entry would corrupt a working setup.
    """
    foreign = {"mcpServers": {"someone-elses-tool": {"command": "othercmd", "args": ["--x"]}},
               "unrelatedTopLevelKey": {"a": 1}}
    cfg = retarget(json.dumps(foreign, indent=2))

    actions = []
    for _ in range(3):
        p = I.plan(HOST)
        assert not p.get("error"), p.get("error")
        I.apply(p)
        actions.append(p["action"])
        data = json.loads(cfg.read_text(encoding="utf-8"))
        assert data["mcpServers"]["someone-elses-tool"] == foreign["mcpServers"]["someone-elses-tool"], \
            "another tool's entry was modified"
        assert data.get("unrelatedTopLevelKey") == {"a": 1}, "an unrelated top-level key was dropped"
        assert "inspeximus" in data["mcpServers"]

    assert actions[0] == "add"
    assert actions[1:] == ["unchanged", "unchanged"], \
        f"install must be idempotent, got {actions} -- a rerun that re-writes can duplicate or clobber"


def test_an_empty_file_is_treated_as_a_new_config(retarget):
    cfg = retarget("")
    p = I.plan(HOST)
    assert not p.get("error"), p.get("error")
    I.apply(p)
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert "inspeximus" in data["mcpServers"]


def test_the_original_is_backed_up_before_a_write(retarget):
    """A user who dislikes the result must be able to get their file back."""
    foreign = {"mcpServers": {"keep": {"command": "keep"}}}
    cfg = retarget(json.dumps(foreign, indent=2))
    I.apply(I.plan(HOST))
    bak = cfg.parent / (cfg.name + ".bak")
    assert bak.exists(), "no .bak was written before modifying the user's config"
    assert json.loads(bak.read_text(encoding="utf-8")) == foreign, "the .bak is not the original"
