"""3.16.4 edges of AUDIT-A's round 2: where the key home may live (F-13), what counts as an embedding (F-17), and a
directory given as the decision store (F-16)."""
from __future__ import annotations

import io
import json
import math
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import _http, _keyhome  # noqa: E402
from inspeximus import claude_code as cc  # noqa: E402
import inspeximus.core as core  # noqa: E402


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(_keyhome, "_NOTICE", [])
    monkeypatch.setattr(_keyhome, "_CACHE", {})


# ---- F-13: the key home --------------------------------------------------------------------------------------------

def test_a_key_home_inside_a_git_work_tree_is_refused(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(repo / "keys"))
    assert _keyhome.key_home() == str(tmp_path / "appdata")
    assert cc.user_config_path().startswith(str(tmp_path / "appdata")), "the user config cannot move into a repository"
    err = capsys.readouterr().err
    assert err.count("INSPEXIMUS_KEY_HOME") == 1 and "git work tree" in err, err


def test_a_key_home_inside_the_stores_project_is_refused_without_git(tmp_path, monkeypatch):
    store = tmp_path / "proj" / ".inspeximus" / "coding_memory.json"
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "proj" / ".claude" / "keys"))
    assert _keyhome.key_home(str(store)) == str(tmp_path / "appdata")
    assert core._guard_key_file(str(store)).startswith(str(tmp_path / "appdata"))
    assert core._head_path(str(store)).startswith(str(tmp_path / "appdata"))


def test_control_a_key_home_outside_any_repository_is_used(tmp_path, monkeypatch, capsys):
    store = tmp_path / "proj" / ".inspeximus" / "coding_memory.json"
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "my-keys"))
    assert _keyhome.key_home(str(store)) == str(tmp_path / "my-keys")
    assert core._receipt_key_file(str(store)).startswith(str(tmp_path / "my-keys"))
    assert capsys.readouterr().err == ""


# ---- F-17: what counts as an embedding -----------------------------------------------------------------------------

@pytest.mark.parametrize("vec", [[], [math.nan], [math.inf], [True, 0.1], ["0.1"], [None],
                                 [0.1] * (_http.MAX_EMBEDDING_LEN + 1)])
def test_an_embedding_that_is_not_a_short_list_of_finite_numbers_is_refused(vec):
    with pytest.raises(ValueError):
        _http.embedding_from({"data": [{"embedding": vec}]})


@pytest.mark.parametrize("answer", [{}, {"data": []}, {"data": [{}]}, [], "x"])
def test_an_answer_without_an_embedding_is_refused(answer):
    with pytest.raises(ValueError):
        _http.embedding_from(answer)


def test_control_a_normal_embedding_is_returned():
    assert _http.embedding_from({"data": [{"embedding": [0.1, -2, 3.5]}]}) == [0.1, -2, 3.5]
    assert len(_http.embedding_from({"data": [{"embedding": [0.0] * _http.MAX_EMBEDDING_LEN}]})) == \
        _http.MAX_EMBEDDING_LEN


# ---- F-16: a directory given as the decision store -----------------------------------------------------------------

def test_a_directory_as_the_decision_store_costs_the_prompt_hook_no_wait(tmp_path, monkeypatch, capsys):
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    d = tmp_path / "decisions.json"
    (d / "sub").mkdir(parents=True)
    monkeypatch.setenv("INSPEXIMUS_DECISION_STORE", str(d))
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keys"))
    t = time.time()
    cc.recall({"hook_event_name": "UserPromptSubmit", "prompt": "what did we decide", "cwd": str(proj),
               "session_id": "s"})
    assert time.time() - t < 2.0


# ---- F-15: the commands `--install` and the Codex plugin write ------------------------------------------------------

def test_every_hook_command_the_install_flag_writes_is_isolated():
    groups = [cc._HOOK] + list(cc._EVENT_HOOK.values())
    cmds = [h["command"] for g in groups for h in g["hooks"]]
    assert len(cmds) == 4 and all(" -I -m inspeximus.claude_code" in c for c in cmds), cmds


def test_the_codex_plugin_command_is_isolated(tmp_path):
    root = tmp_path / "market"
    cc.install_codex(str(root))
    found = []
    for dirpath, _d, files in os.walk(root):
        for f in files:
            if f.endswith(".json"):
                text = open(os.path.join(dirpath, f), encoding="utf-8").read()
                if "inspeximus.claude_code" in text:
                    found.append(text)
    assert found and all("-I -m inspeximus.claude_code" in t for t in found), found


# ---- teeth for two mutants that the round-2 tests did not reach --------------------------------------------------

def test_post_json_reads_at_most_the_limit_plus_one_byte(monkeypatch):
    """An answer is never read whole: the read is bounded before any size check runs."""
    asked = []

    class _R:
        def read(self, n=-1):
            asked.append(n)
            return b'{"data": [{"embedding": [0.5]}]}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(_http, "opener_for", lambda url: type("O", (), {"open": staticmethod(lambda req, timeout=None: _R())})())
    _http.post_json("http://127.0.0.1:9/v1/embeddings", {"input": "x"}, None, 5)
    assert asked == [_http.MAX_ANSWER_BYTES + 1], asked


def test_an_answer_over_the_limit_is_refused_even_when_read_in_one_piece(monkeypatch):
    big = b'{"data": [{"embedding": [' + b"0.5," * (_http.MAX_ANSWER_BYTES // 4) + b'0.5]}]}'

    class _R:
        def read(self, n=-1):
            return big if n is None or n < 0 else big[:n]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(_http, "opener_for", lambda url: type("O", (), {"open": staticmethod(lambda req, timeout=None: _R())})())
    with pytest.raises(_http.AnswerTooLarge):
        _http.post_json("http://127.0.0.1:9/v1/embeddings", {"input": "x"}, None, 5)


def test_a_process_that_has_exited_is_not_alive_and_this_one_is():
    """The exit code is read, not just whether the pid can be opened: Popen keeps the handle of a finished child
    open, so OpenProcess succeeds and only the exit code tells that it is gone."""
    import subprocess
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    assert cc._pid_alive(os.getpid()) is True
    assert cc._pid_alive(p.pid) is False
