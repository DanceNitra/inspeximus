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
    monkeypatch.setattr(_keyhome, "_CWD_PROJECT", {})


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
    from inspeximus import _launch
    expected = "python " + _launch.module_command("inspeximus.claude_code")     # a bare python: the shim (F-22)
    assert len(cmds) == 4 and all(c == expected for c in cmds), cmds


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
    from inspeximus import _launch
    form = _launch.module_command("inspeximus.claude_code", sys.version_info)
    assert found and all(form.replace(chr(34), chr(92) + chr(34)) in t or form in t for t in found), found


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


# ---- 3.16.4 second round: which, the output encoding under -E, the uvx index, the working-directory project ----

def test_which_never_answers_from_the_working_directory(tmp_path, monkeypatch):
    """Windows Python 3.9 to 3.11 search the working directory before PATH (measured: uvx.BAT, relative)."""
    from inspeximus import _launch
    import shutil
    work, good = tmp_path / "repo", tmp_path / "bin"
    work.mkdir()
    good.mkdir()
    ext = ".bat" if os.name == "nt" else ""
    for d in (work, good):
        p = d / ("uvx" + ext)
        p.write_text("@echo off\n" if os.name == "nt" else "#!/bin/sh\n")
        p.chmod(0o755)
    monkeypatch.chdir(work)
    monkeypatch.setenv("PATH", str(good))
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: str(work / ("uvx" + ext)))   # what 3.10 answers
    found = _launch.which("uvx")
    assert found and os.path.dirname(os.path.abspath(found)) == str(good), found


def test_control_which_keeps_an_answer_from_path(tmp_path, monkeypatch):
    from inspeximus import _launch
    import shutil
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: "/usr/local/bin/uvx")
    assert _launch.which("uvx") == "/usr/local/bin/uvx"


def test_every_generated_uvx_command_names_its_package_index():
    """AUDIT-A F-23: UV_INDEX_URL from a project's settings would choose where the hook's code comes from."""
    import inspeximus.install as ins
    from inspeximus import _launch
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert "--default-index " + _launch.DEFAULT_INDEX in ins.hook_command("uvx", "uvx")
    assert ins._server_launch("uvx", "uvx")[1][:2] == ["--default-index", _launch.DEFAULT_INDEX]
    assert ins.default_server_block()["args"][:2] == ["--default-index", _launch.DEFAULT_INDEX]
    hooks = json.load(open(os.path.join(root, "hooks", "hooks.json"), encoding="utf-8"))["hooks"]
    cmds = [h["command"] for g in hooks.values() for e in g for h in e["hooks"]]
    assert cmds and all(c.startswith("uvx --default-index " + _launch.DEFAULT_INDEX + " ") for c in cmds), cmds
    mcp = json.load(open(os.path.join(root, ".mcp.json"), encoding="utf-8"))["mcpServers"]["inspeximus"]["args"]
    assert mcp[:2] == ["--default-index", _launch.DEFAULT_INDEX], mcp


def test_the_hook_keeps_the_users_utf8_choice_under_E(tmp_path):
    """-E makes the interpreter ignore PYTHONUTF8; main() applies it to its own stdout (measured: c4 be vs be).
    The hook's JSON is ASCII-escaped, so the probe runs main() with a command that prints raw text."""
    import subprocess
    env = {k: v for k, v in os.environ.items() if not k.startswith(("INSPEXIMUS_", "PYTHON"))}
    env.update(INSPEXIMUS_KEY_HOME=str(tmp_path / "keys"), INSPEXIMUS_NO_UPDATE_CHECK="1")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = ("import sys;sys.path.insert(0, %r);import inspeximus.claude_code as c;"
            "c.install=lambda: print(chr(318)+'adov'+chr(225));sys.argv=['x','--install'];c.main()") % root
    for name, extra in (("PYTHONUTF8", {"PYTHONUTF8": "1"}), ("PYTHONIOENCODING", {"PYTHONIOENCODING": "utf-8"})):
        out = subprocess.run([sys.executable, "-E", "-c", code], env=dict(env, **extra), cwd=str(tmp_path),
                             capture_output=True, timeout=120)
        assert out.returncode == 0, out.stderr[-300:]
        assert "ľadová".encode("utf-8") in out.stdout, (name, out.stdout)


def test_the_working_directory_project_rule_ignores_the_home_directory(tmp_path, monkeypatch):
    """A terminal starts in the home directory, and ~/.claude is the user's own config, not a project."""
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude").mkdir()
    monkeypatch.chdir(tmp_path)
    assert _keyhome._cwd_project() is None
    sub = tmp_path / "work" / "repo"
    (sub / ".claude").mkdir(parents=True)
    monkeypatch.chdir(sub)
    assert os.path.normcase(_keyhome._cwd_project()) == os.path.normcase(os.path.realpath(str(sub)))


def test_the_shim_form_does_not_run_a_repository_package(tmp_path):
    """Python 3.9 and 3.10 get `-E -c SHIM`; run that form here, in a directory holding inspeximus/."""
    import subprocess
    from inspeximus import _launch
    repo = tmp_path / "repo"
    (repo / "inspeximus").mkdir(parents=True)
    marker = tmp_path / "SHADOW-RAN"
    (repo / "inspeximus" / "__init__.py").write_text("open(%r, 'w').write('ran')" % str(marker))
    args = _launch.module_args("inspeximus.claude_code", (3, 10))
    assert args[:3] == ["-E", "-c", _launch.SHIM], args
    subprocess.run([sys.executable] + args, input=b"{}", cwd=str(repo), capture_output=True, timeout=120)
    assert not marker.exists(), "the shim form ran the repository's inspeximus/"


def test_the_installer_resolves_tools_without_the_working_directory(tmp_path, monkeypatch):
    import shutil
    import inspeximus.install as ins
    work, good = tmp_path / "repo", tmp_path / "bin"
    work.mkdir()
    good.mkdir()
    ext = ".bat" if os.name == "nt" else ""
    for d in (work, good):
        p = d / ("hermes" + ext)
        p.write_text("@echo off\n" if os.name == "nt" else "#!/bin/sh\n")
        p.chmod(0o755)
    monkeypatch.chdir(work)
    monkeypatch.setenv("PATH", str(good))
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: str(work / ("hermes" + ext)))
    found = ins._which_safe("hermes")
    assert found and os.path.dirname(os.path.abspath(found)) == str(good), found


def test_the_uvx_warm_up_names_the_same_index(tmp_path):
    import inspeximus.install_all as ia
    from inspeximus import _launch
    exe = tmp_path / ("uvx.exe" if os.name == "nt" else "uvx")
    exe.write_text("")
    seen = []
    ia.warm_uvx(str(exe), runner=lambda cmd, **k: seen.append(cmd) or type("R", (), {"returncode": 0})())
    assert seen and seen[0][1:3] == _launch.UVX_INDEX_ARGS, seen


# ---- 3.16.4, AUDIT-B: the pid write never erases a finished run's mark ----

def _archive_setup(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keyhome"))
    (tmp_path / "keyhome" / "inspeximus").mkdir(parents=True)
    (tmp_path / "keyhome" / "inspeximus" / "config.json").write_text(json.dumps(
        {"archive": {"auto": True, "trigger_mb": 0.0001}}))
    proj = tmp_path / "repo"
    (proj / ".git").mkdir(parents=True)
    m = cc._store(str(proj))
    for i in range(40):
        m.remember("ran: command number %d with some output" % i, key="cmd:%d" % i, tags=["bash"], mtype="episodic")
    m.flush()
    return proj, str(m.path)


def test_a_run_that_finishes_before_the_pid_write_keeps_its_done_mark(tmp_path, monkeypatch):
    """The run marks itself done before the parent writes the pid: the mark must survive, or the finished run looks
    dead and is started again after 60 s (AUDIT-B saw it as a 120 s test timeout)."""
    import subprocess as sp
    proj, path = _archive_setup(tmp_path, monkeypatch)

    def fast_run(argv, **kw):
        cc._mark_archive_run_done(path, True)               # the whole run, finished inside the start
        return type("P", (), {"pid": 424242})()
    monkeypatch.setattr(sp, "Popen", fast_run)
    monkeypatch.setattr(cc, "ARCHIVE_MARK_WAIT_S", 0.5)      # here the run waits inside the start call
    assert cc.maybe_archive_in_background(str(proj)) == "started"
    st = json.load(open(cc._archive_state_path(path), encoding="utf-8"))
    assert st.get("done") and st.get("result") == "ok" and st.get("pid") == 424242, st


def test_a_mark_lost_between_the_parents_read_and_write_is_written_again(tmp_path, monkeypatch):
    """The parent read the record before the mark and wrote the pid after it: the run sees the pid without its mark
    and writes the mark again."""
    import threading
    import time as _t
    proj, path = _archive_setup(tmp_path, monkeypatch)
    state = cc._archive_state_path(path)
    with open(state, "w", encoding="utf-8") as fh:
        json.dump({"last_attempt": _t.time(), "hot_bytes": 1}, fh)
    t = threading.Thread(target=cc._mark_archive_run_done, args=(path, True))
    t.start()
    _t.sleep(0.5)                                            # the run has marked itself and waits for the pid
    seen = json.load(open(state, encoding="utf-8"))
    assert seen.get("done"), "control: the run marked itself before the parent's write"
    with open(state, "w", encoding="utf-8") as fh:            # the parent's write, from a read before the mark
        json.dump({"last_attempt": seen["last_attempt"], "hot_bytes": 1, "pid": 7}, fh)
    t.join(15)
    st = json.load(open(state, encoding="utf-8"))
    assert not t.is_alive() and st.get("done") and st.get("pid") == 7, st


def test_control_a_stale_record_without_a_pid_does_not_make_the_run_wait(tmp_path, monkeypatch):
    import time as _t
    proj, path = _archive_setup(tmp_path, monkeypatch)
    with open(cc._archive_state_path(path), "w", encoding="utf-8") as fh:
        json.dump({"last_attempt": _t.time() - 3600, "hot_bytes": 1}, fh)
    t0 = _t.time()
    cc._mark_archive_run_done(path, True)
    assert _t.time() - t0 < 2 and json.load(open(cc._archive_state_path(path), encoding="utf-8")).get("done")
