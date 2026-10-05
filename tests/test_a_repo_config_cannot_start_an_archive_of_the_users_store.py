"""3.16.3, AUDIT-A F-9: a repository's `.inspeximus/config.json` cannot switch on the auto archive.

`maybe_archive_in_background` archives `coding_store_path(cwd)`, which is the user's shared store for every
project once `install --all` recorded one. On c246c24c it read the policy from the repository the user opened, so a
cloned repository with {"archive": {"auto": true, "trigger_mb": 0, "older_than_days": 0, "min_interval_s": 0}}
made two prompts start two detached runs, and one run moved 29 of 30 rows of the shared store out of recall
(AUDIT-A). The policy is now read only from the user's config (`user_config_path()`, in the key home) and the
environment. A repository config that carries `archive` is ignored with one stderr line. Floors apply to every
policy: at most one run a minute, nothing younger than a day.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.core as core  # noqa: E402
from inspeximus import claude_code as cc  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

DAY, T0 = 86400.0, 1790000000.0
HOSTILE = {"archive": {"auto": True, "trigger_mb": 0, "older_than_days": 0, "min_interval_s": 0,
                       "classes": ["cmd", "file"]}}


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keyhome"))
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(cc, "_REPO_ARCHIVE_NOTICE", [], raising=False)


def _users_store(tmp_path, monkeypatch, n=30):
    user_dir = tmp_path / "user_store"
    user_dir.mkdir()
    monkeypatch.setenv("INSPEXIMUS_CODING_STORE", str(user_dir))     # stands for the shared store
    m = Inspeximus(str(user_dir / "coding_memory.json"))
    with pytest.MonkeyPatch.context() as mp:
        for i in range(n):
            mp.setattr(core.time, "time", lambda i=i: T0 - 40 * DAY + i)
            m.remember(f"ran: command {i}", key=f"cmd:c{i}", tags=["bash"], mtype="episodic")
    m.flush()
    return str(user_dir / "coding_memory.json")


def _repo(tmp_path, config):
    repo = tmp_path / "cloned_repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".inspeximus").mkdir()
    if config is not None:
        (repo / ".inspeximus" / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return str(repo)


def _user_config(config):
    path = cc.user_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(config, fh)


def _no_spawn(monkeypatch):
    spawned = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: spawned.append(argv) or type("P", (), {"pid": 1})())
    return spawned


def test_f9_a_repo_config_does_not_start_an_archive_of_the_users_store(tmp_path, monkeypatch):
    """AUDIT-A's test, as sent: fails on c246c24c with 2 runs started."""
    _users_store(tmp_path, monkeypatch)
    repo = _repo(tmp_path, HOSTILE)
    spawned = _no_spawn(monkeypatch)
    cc.maybe_archive_in_background(repo)
    cc.maybe_archive_in_background(repo)
    assert not spawned, f"a repository's config started {len(spawned)} archive run(s) against the user's store"


def test_control_the_same_policy_in_the_users_config_starts_one_run_under_the_floors(tmp_path, monkeypatch):
    """The control: the feature works from the user's config, so the refusal above is not a dead feature."""
    _users_store(tmp_path, monkeypatch)
    repo = _repo(tmp_path, None)
    _user_config(HOSTILE)
    spawned = _no_spawn(monkeypatch)
    assert cc.maybe_archive_in_background(repo) == "started"
    assert cc.maybe_archive_in_background(repo) == "recent", "min_interval_s 0 is raised to the 60 s floor"
    assert len(spawned) == 1
    argv = spawned[0]
    assert argv[argv.index("--older-than") + 1] == "1.0", "older_than_days 0 is raised to the 1 day floor"


def test_the_floors_apply_to_every_policy(tmp_path, monkeypatch):
    _user_config({"archive": {"auto": True, "min_interval_s": 0, "older_than_days": 0}})
    pol = cc.archive_policy(str(tmp_path))
    assert pol["min_interval_s"] == 60.0 and pol["older_than_days"] == 1.0, pol
    _user_config({"archive": {"auto": True, "min_interval_s": 7200, "older_than_days": 30}})
    pol = cc.archive_policy(str(tmp_path))
    assert pol["min_interval_s"] == 7200.0 and pol["older_than_days"] == 30.0, "a value over the floor is kept"


def test_a_repo_archive_key_is_ignored_with_one_stderr_line_naming_the_file(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, HOSTILE)
    for _ in range(3):
        assert cc.archive_policy(repo) == cc.archive_policy(str(tmp_path / "nowhere"))
    out = capsys.readouterr()
    assert out.out == "", "stdout joins the prompt; the notice goes to stderr"
    lines = [x for x in out.err.splitlines() if "ignored" in x]
    assert len(lines) == 1, out.err
    assert os.path.join("cloned_repo", ".inspeximus", "config.json") in lines[0]
    assert cc.user_config_path() in lines[0]


def test_a_repo_config_without_an_archive_key_says_nothing(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path, {"embed": {"url": "http://127.0.0.1:9/v1/embeddings"}})
    cc.archive_policy(repo)
    assert capsys.readouterr().err == ""


def test_the_repo_policy_is_ignored_even_for_a_store_inside_the_repo(tmp_path, monkeypatch):
    """EM chose the primary fix: no repository policy at all, not one honoured for an in-repo store."""
    repo = _repo(tmp_path, HOSTILE)
    m = Inspeximus(os.path.join(repo, ".inspeximus", "coding_memory.json"))
    m.remember("ran: one", key="cmd:one", tags=["bash"], mtype="episodic")
    m.flush()
    spawned = _no_spawn(monkeypatch)
    assert cc.maybe_archive_in_background(repo) == "off"
    assert not spawned
