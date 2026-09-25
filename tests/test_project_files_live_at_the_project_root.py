"""Every file the Claude Code hooks keep for a project lives at the project root, whichever subdirectory
Claude Code was launched from.

3.9.6 moved the store to <git root>/.inspeximus and left three neighbours keyed by the launch directory.
Measured on 3.9.6, launched from <root>/sub/dir:
  * SessionStart wrote `.update_check.json` into <root>/sub/dir/.inspeximus, a second directory beside
    the store (reported by session 1's clean-sandbox re-test);
  * the star-nudge counter wrote to <root>/sub/dir/.inspeximus/nudge.json, which does not exist, so it
    failed silently and never counted;
  * `config.json` was read from <root>/sub/dir/.inspeximus, so a config at the root was ignored.
"""
import json
import os
import urllib.request

import pytest

from inspeximus import claude_code as cc


@pytest.fixture
def repo(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("INSPEXIMUS_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("INSPEXIMUS_NO_NUDGE", "1")

    def offline(*a, **k):
        raise OSError("offline in tests")
    monkeypatch.setattr(urllib.request, "urlopen", offline)
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    sub = root / "sub" / "dir"
    sub.mkdir(parents=True)
    return root, sub


def test_session_start_from_a_subdirectory_writes_nothing_there(repo, capsys):
    root, sub = repo
    cc.session_start({"cwd": str(sub), "session_id": "s1"})
    capsys.readouterr()
    assert (root / ".inspeximus" / "coding_memory.json").exists(), "control: the store is at the root"
    assert (root / ".inspeximus" / ".update_check.json").exists()
    assert not (sub / ".inspeximus").exists()


def test_the_nudge_counter_counts_from_a_subdirectory(repo):
    root, sub = repo
    (root / ".inspeximus").mkdir()
    cc._bump_writes(str(sub))
    cc._bump_writes(str(sub))
    st = json.loads((root / ".inspeximus" / "nudge.json").read_text(encoding="utf-8"))
    assert st["writes"] == 2
    assert not (sub / ".inspeximus").exists()


def test_a_config_at_the_root_is_read_from_a_subdirectory(repo):
    root, sub = repo
    (root / ".inspeximus").mkdir()
    (root / ".inspeximus" / "config.json").write_text('{"orphan_notice": false}', encoding="utf-8")
    assert cc._cfg(str(sub)) == {"orphan_notice": False}


def test_a_config_in_the_launch_directory_still_applies_when_the_root_has_none(repo):
    """Compatibility: a project that put its config beside where it launches keeps it."""
    root, sub = repo
    (sub / ".inspeximus").mkdir()
    (sub / ".inspeximus" / "config.json").write_text('{"orphan_notice": false}', encoding="utf-8")
    assert cc._cfg(str(sub)) == {"orphan_notice": False}
