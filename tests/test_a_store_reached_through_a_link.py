"""A store reached through a link (3.16.5, AUDIT-A design 2026-10-07).

A repository can ship a link at `.inspeximus` or at the store file. The rule (inspeximus/_storelink.py): a link, junction
included, is followed only when A the user's config names the target, B the target stays inside the project (or the main
checkout of a git worktree) and never inside `.git`, or C git does not track the link. Otherwise there is no store, with one
stderr line and the fix. INSPEXIMUS_CODING_STORE gets A and B.

The 13 scenarios are AUDIT-A's (7 a user builds on purpose, 5 a repository ships, 1 harmless). Links are real symlinks on POSIX
and junctions on Windows; a file link on Windows needs a privilege, so those scenarios skip there with the reason.
"""
import hashlib
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from inspeximus import _safewrite, _storelink, _surface  # noqa: E402

GIT = ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", "-c", "commit.gpgsign=false",
       "-c", "core.autocrlf=false", "-c", "core.symlinks=true"]


def _git(cwd, *args):
    r = subprocess.run(GIT + list(args), cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout


def _mklink(link, target, is_dir):
    """A real link: a symlink, or on Windows a junction for a directory."""
    os.makedirs(os.path.dirname(link), exist_ok=True)
    try:
        os.symlink(target, link, target_is_directory=is_dir)
    except (OSError, NotImplementedError):
        if os.name == "nt" and is_dir:
            r = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True, text=True, encoding="utf-8", errors="replace")
            assert r.returncode == 0, r.stderr
        else:
            pytest.skip("this platform cannot create a %s link without a privilege" % ("directory" if is_dir else "file"))
    assert _safewrite.is_link(link), "fixture error: the link is not a link"


def _store(path):
    """A real store file (a row store holding one record) so a follower has something to read."""
    from inspeximus import Inspeximus
    os.makedirs(os.path.dirname(path), exist_ok=True)
    m = Inspeximus(path)
    m.remember("the other project's private note about the release", key="n")
    m.flush()
    return path


@pytest.fixture()
def world(tmp_path, monkeypatch):
    home = tmp_path / "home"
    keyhome = tmp_path / "keyhome"
    home.mkdir()
    keyhome.mkdir()
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(keyhome))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")
    return tmp_path


def _repo(base, name="proj", tracked=()):
    """A git work tree with one commit that tracks `tracked` (paths relative to it, created by the caller beforehand)."""
    p = os.path.join(str(base), name)
    os.makedirs(p, exist_ok=True)
    _git(p, "init", "-q")
    open(os.path.join(p, "README.md"), "w").write("x\n")
    _git(p, "add", "README.md")
    for t in tracked:
        _git(p, "add", "--", t)
    _git(p, "commit", "-q", "-m", "init")
    return p


def _decide(proj, env=None):
    """(allowed, path_or_message): what the resolver the hook and the MCP server share decides."""
    try:
        return True, _surface.coding_store_path(proj, dict(env or {}))
    except _surface.StoreLinkRefused as exc:
        return False, str(exc)


def _same(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _track(proj, rel):
    """Commit `rel`, which a repository that ships a link would do."""
    _git(proj, "add", "-f", "--", rel)
    _git(proj, "commit", "-q", "-m", "ships " + rel)


def _config_names(target):
    _storelink.add_configured_link(target)


# ── the scenarios a user builds on purpose ────────────────────────────────────────────────────────────────────────────

def test_l1_a_link_to_a_dotfiles_directory_outside_the_project_is_followed_when_git_does_not_track_it(world):
    proj = _repo(world)
    target = os.path.dirname(_store(str(world / "dotfiles" / "store" / "coding_memory.json")))
    _mklink(os.path.join(proj, ".inspeximus"), target, True)
    ok, got = _decide(proj)
    assert ok, got
    assert _same(got, os.path.join(target, "coding_memory.json"))        # the REAL path comes back


def test_l2_a_store_file_link_to_a_file_outside_the_project_is_followed_when_git_does_not_track_it(world):
    proj = _repo(world)
    real = _store(str(world / "share" / "coding_memory.json"))
    os.makedirs(os.path.join(proj, ".inspeximus"))
    _mklink(os.path.join(proj, ".inspeximus", "coding_memory.json"), real, False)
    ok, got = _decide(proj)
    assert ok, got
    assert _same(got, real)


def test_l3_a_worktree_may_link_to_the_main_checkouts_store_even_when_the_link_is_tracked(world):
    main = _repo(world, "main")
    store = os.path.dirname(_store(os.path.join(main, ".inspeximus", "coding_memory.json")))
    wt = str(world / "wt")
    _git(main, "worktree", "add", "-q", wt)
    _mklink(os.path.join(wt, ".inspeximus"), store, True)
    _track(wt, ".inspeximus")                     # tracked: C cannot decide it, B (the main checkout) must
    ok, got = _decide(wt)
    assert ok, got
    assert _storelink.main_checkout(wt) is not None


def test_l4_a_link_to_a_directory_inside_the_project_is_followed(world):
    proj = _repo(world)
    target = os.path.dirname(_store(os.path.join(proj, "data", "mem", "coding_memory.json")))
    _mklink(os.path.join(proj, ".inspeximus"), target, True)
    ok, got = _decide(proj)
    assert ok, got


def test_l5_a_tracked_link_outside_the_project_is_followed_when_the_config_names_the_target(world):
    proj = _repo(world)
    target = os.path.dirname(_store(str(world / "dotfiles" / "store" / "coding_memory.json")))
    _mklink(os.path.join(proj, ".inspeximus"), target, True)
    _track(proj, ".inspeximus")
    ok, got = _decide(proj)
    assert not ok, "control: a tracked link outside the project is refused until the config names it"
    _config_names(target)
    ok, got = _decide(proj)
    assert ok, got


def test_l6_a_folder_that_is_not_a_git_work_tree_cannot_use_an_outside_link_without_the_config(world):
    proj = str(world / "plain")
    os.makedirs(proj)
    target = os.path.dirname(_store(str(world / "dotfiles" / "store" / "coding_memory.json")))
    _mklink(os.path.join(proj, ".inspeximus"), target, True)
    ok, got = _decide(proj)
    assert not ok
    assert "inspeximus link" in got
    _config_names(target)
    assert _decide(proj)[0]


def test_l7_no_link_changes_nothing(world):
    proj = _repo(world)
    ok, got = _decide(proj)
    assert ok
    assert got == os.path.join(proj, ".inspeximus", "coding_memory.json")      # the lexical path, untouched


# ── the links a repository ships ──────────────────────────────────────────────────────────────────────────────────────

def test_h1_a_shipped_link_to_another_projects_store_is_refused(world):
    victim = os.path.dirname(_store(str(world / "victim" / ".inspeximus" / "coding_memory.json")))
    proj = str(world / "clone")
    os.makedirs(proj)
    _mklink(os.path.join(proj, ".inspeximus"), victim, True)
    proj = _repo(world, "clone")
    _track(proj, ".inspeximus")
    ok, got = _decide(proj)
    assert not ok
    assert "inspeximus link" in got and victim.replace("\\", "/").split("/")[-1] in got.replace("\\", "/")


def test_h2_a_shipped_store_file_link_to_another_store_is_refused(world):
    victim = _store(str(world / "victim" / ".inspeximus" / "coding_memory.json"))
    proj = str(world / "clone")
    os.makedirs(os.path.join(proj, ".inspeximus"))
    _mklink(os.path.join(proj, ".inspeximus", "coding_memory.json"), victim, False)
    proj = _repo(world, "clone")
    _track(proj, ".inspeximus/coding_memory.json")
    assert not _decide(proj)[0]


def test_h3_a_shipped_link_to_a_config_directory_is_refused(world):
    cfgdir = str(world / "home" / ".config" / "inspeximus")
    os.makedirs(cfgdir)
    open(os.path.join(cfgdir, "config.json"), "w").write("{}")      # a junction to an empty folder tracks nothing
    proj = str(world / "clone")
    os.makedirs(proj)
    _mklink(os.path.join(proj, ".inspeximus"), cfgdir, True)
    proj = _repo(world, "clone")
    _track(proj, ".inspeximus")
    assert not _decide(proj)[0]


def test_h4_a_download_without_git_with_a_link_to_the_outside_is_refused(world):
    outside = str(world / "somewhere")
    os.makedirs(outside)
    proj = str(world / "zip")
    os.makedirs(proj)
    _mklink(os.path.join(proj, ".inspeximus"), outside, True)
    assert not _decide(proj)[0]
    assert os.listdir(outside) == [], "a refused link must not gain files"


def test_h5_a_shipped_link_from_the_store_directory_to_dot_git_is_refused(world):
    proj = str(world / "clone")
    os.makedirs(proj)
    proj = _repo(world, "clone")
    _mklink(os.path.join(proj, ".inspeximus"), os.path.join(proj, ".git"), True)
    _track(proj, ".inspeximus")
    ok, got = _decide(proj)
    assert not ok, "inside the project is not enough: .git is never a store"


def test_h6_a_shipped_link_to_a_directory_inside_the_project_is_harmless_and_followed(world):
    proj = str(world / "clone")
    os.makedirs(os.path.join(proj, "data"))
    open(os.path.join(proj, "data", "keep.txt"), "w").write("x")      # a junction to an empty folder tracks nothing
    _mklink(os.path.join(proj, ".inspeximus"), os.path.join(proj, "data"), True)
    proj = _repo(world, "clone")
    _track(proj, ".inspeximus")
    ok, got = _decide(proj)
    assert ok, got


# ── the same conditions for INSPEXIMUS_CODING_STORE ───────────────────────────────────────────────────────────────────

def test_the_environment_override_outside_the_project_needs_the_config(world):
    proj = _repo(world)
    other = os.path.dirname(_store(str(world / "victim" / ".inspeximus" / "coding_memory.json")))
    env = {"INSPEXIMUS_CODING_STORE": other}
    ok, got = _decide(proj, env)
    assert not ok and "INSPEXIMUS_CODING_STORE" in got and "inspeximus link" in got
    _config_names(other)
    assert _decide(proj, env)[0]


def test_the_environment_override_inside_the_project_is_followed(world):
    proj = _repo(world)
    env = {"INSPEXIMUS_CODING_STORE": os.path.join(proj, ".inspeximus")}
    ok, got = _decide(proj, env)
    assert ok and _same(got, os.path.join(proj, ".inspeximus", "coding_memory.json"))


def test_the_environment_override_cannot_reach_dot_git(world):
    proj = _repo(world)
    ok, _ = _decide(proj, {"INSPEXIMUS_CODING_STORE": os.path.join(proj, ".git")})
    assert not ok


# ── rules for the implementation ──────────────────────────────────────────────────────────────────────────────────────

def test_a_refusal_never_falls_back_to_the_default_path(world):
    """No store means no store: the resolver raises, it does not hand back `.inspeximus/coding_memory.json`."""
    outside = str(world / "somewhere")
    os.makedirs(outside)
    proj = str(world / "zip")
    os.makedirs(proj)
    _mklink(os.path.join(proj, ".inspeximus"), outside, True)
    with pytest.raises(_surface.StoreLinkRefused):
        _surface.coding_store_path(proj, {})
    with pytest.raises(_surface.StoreLinkRefused):
        _surface.coding_store_dir(proj, {})


def test_the_decision_uses_the_real_path_and_the_caller_opens_that_path(world):
    proj = _repo(world)
    target = os.path.dirname(_store(str(world / "dotfiles" / "store" / "coding_memory.json")))
    _mklink(os.path.join(proj, ".inspeximus"), target, True)
    got = _surface.coding_store_path(proj, {})
    assert got == os.path.realpath(os.path.join(target, "coding_memory.json")), got
    assert not _safewrite.is_link(os.path.dirname(got)) and not _safewrite.is_link(got)


def test_a_link_is_tested_with_is_link_and_not_islink():
    import inspect
    src = inspect.getsource(_storelink)
    assert "islink(" not in src.replace("os.path.islink` misses", ""), "os.path.islink misses a Windows junction"


def test_the_link_command_records_the_target_and_keeps_the_rest_of_the_config(world):
    cfg = _storelink.user_config_file()
    os.makedirs(os.path.dirname(cfg), exist_ok=True)
    with open(cfg, "w", encoding="utf-8") as fh:
        json.dump({"embed": {"hooks": True}}, fh)
    target = str(world / "dotfiles")
    os.makedirs(target)
    r = subprocess.run([sys.executable, "-m", "inspeximus.cli", "link", target], capture_output=True, text=True,
                       encoding="utf-8", env=dict(os.environ, PYTHONPATH=ROOT))
    assert r.returncode == 0, r.stderr
    with open(cfg, encoding="utf-8") as fh:
        got = json.load(fh)
    assert got["embed"] == {"hooks": True}
    assert got["stores"]["links"] == [os.path.realpath(target)]
    r2 = subprocess.run([sys.executable, "-m", "inspeximus.cli", "link", str(world / "missing")], capture_output=True, text=True,
                        encoding="utf-8", env=dict(os.environ, PYTHONPATH=ROOT))
    assert r2.returncode == 2 and "does not exist" in r2.stderr


def test_a_git_decision_is_cached_per_link(world, monkeypatch):
    proj = _repo(world)
    target = os.path.dirname(_store(str(world / "dotfiles" / "store" / "coding_memory.json")))
    _mklink(os.path.join(proj, ".inspeximus"), target, True)
    calls = []
    real = _storelink._git_tracks

    def counted(*a, **k):
        calls.append(a)
        return real(*a, **k)
    monkeypatch.setattr(_storelink, "_git_tracks", counted)
    for _ in range(3):
        assert _decide(proj)[0]
    assert len(calls) == 1, calls


def test_a_link_committed_later_is_decided_again(world):
    """The cache key holds the repository's index time: a `git add` of the link must not keep an old yes."""
    proj = _repo(world)
    target = os.path.dirname(_store(str(world / "dotfiles" / "store" / "coding_memory.json")))
    _mklink(os.path.join(proj, ".inspeximus"), target, True)
    assert _decide(proj)[0]
    import time
    time.sleep(0.05)
    _track(proj, ".inspeximus")
    assert not _decide(proj)[0]


def test_git_that_cannot_answer_means_no_store(world, monkeypatch):
    proj = _repo(world)
    target = os.path.dirname(_store(str(world / "dotfiles" / "store" / "coding_memory.json")))
    _mklink(os.path.join(proj, ".inspeximus"), target, True)
    monkeypatch.setattr(_storelink, "_git_tracks", lambda *a, **k: None)
    assert not _decide(proj)[0]


# ── a worktree's main-checkout claim needs the main checkout's own record ─────────────────────────────────────────────

def test_a_crafted_worktree_pointer_cannot_name_another_checkout_as_the_main_one(world):
    """A repository's `.git` FILE can say `gitdir: <anywhere>`. Only a checkout whose own `worktrees/<name>/gitdir` points back
    to this `.git` file counts as the main checkout."""
    victim = _repo(world, "victim")
    _store(os.path.join(victim, ".inspeximus", "coding_memory.json"))
    crafted = str(world / "crafted")
    os.makedirs(crafted)
    gitdir = os.path.join(victim, ".git", "worktrees", "x")
    os.makedirs(gitdir, exist_ok=True)
    open(os.path.join(gitdir, "commondir"), "w").write("../..\n")
    open(os.path.join(gitdir, "gitdir"), "w").write(os.path.join(victim, "elsewhere", ".git") + "\n")    # not our .git
    open(os.path.join(crafted, ".git"), "w").write("gitdir: " + gitdir + "\n")
    assert _storelink.main_checkout(crafted) is None
    _mklink(os.path.join(crafted, ".inspeximus"), os.path.join(victim, ".inspeximus"), True)
    assert not _decide(crafted)[0], "the crafted pointer must not make the victim's checkout part of the project"


# ── the bypass of C that AUDIT-A did not try ──────────────────────────────────────────────────────────────────────────

def test_known_gap_a_crafted_dot_git_in_an_archive_lets_condition_c_through(world):
    """RECORDED, NOT FIXED HERE. An archive that ships its own `.git` (a `git init` whose index does not list the link) is a
    git work tree for which the link is untracked, so C allows a link to the outside. A and B do not depend on git; C does.
    The report names the options. This test pins today's behaviour so that a change to it is a decision."""
    victim = os.path.dirname(_store(str(world / "victim" / ".inspeximus" / "coding_memory.json")))
    arch = _repo(world, "archive")                       # the attacker's `git init`: README only
    _mklink(os.path.join(arch, ".inspeximus"), victim, True)       # shipped beside it, never added
    ok, got = _decide(arch)
    assert ok, "if this fails, condition C no longer lets a crafted .git through: update the report and this test"
    assert _same(got, os.path.join(victim, "coding_memory.json"))
    # The refusal that DOES hold for an archive: no `.git` at all (scenario H4), and a tracked link (H1).


# ── the hook as a process: the other store's text and the capture writes ──────────────────────────────────────────────

def _tree_hash(path):
    h = hashlib.sha256()
    for dp, dn, fn in sorted(os.walk(path)):
        dn.sort()
        for f in sorted(fn):
            p = os.path.join(dp, f)
            h.update(os.path.relpath(p, path).encode())
            with open(p, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()


def _hook(proj, event, tmp):
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    env.update(INSPEXIMUS_KEY_HOME=str(tmp / "keyhome"), INSPEXIMUS_NO_UPDATE_CHECK="1", PYTHONPATH=ROOT,
               HOME=str(tmp / "home"), USERPROFILE=str(tmp / "home"))
    ev = dict(event, cwd=proj, session_id="t")
    return subprocess.run([sys.executable, "-m", "inspeximus.claude_code"], input=json.dumps(ev), env=env, cwd=str(tmp),
                          capture_output=True, text=True, encoding="utf-8", timeout=120)


@pytest.mark.parametrize("shape", ["directory", "file"])
def test_a_shipped_link_gives_the_prompt_hook_no_text_and_the_capture_hook_no_writes(world, shape):
    victim_dir = os.path.dirname(_store(str(world / "victim" / ".inspeximus" / "coding_memory.json")))
    proj = str(world / "clone")
    os.makedirs(os.path.join(proj, ".inspeximus") if shape == "file" else proj)
    if shape == "directory":
        _mklink(os.path.join(proj, ".inspeximus"), victim_dir, True)
        rel = ".inspeximus"
    else:
        _mklink(os.path.join(proj, ".inspeximus", "coding_memory.json"), os.path.join(victim_dir, "coding_memory.json"), False)
        rel = ".inspeximus/coding_memory.json"
    proj = _repo(world, "clone")
    _track(proj, rel)
    before = _tree_hash(victim_dir)
    prompt = _hook(proj, {"hook_event_name": "UserPromptSubmit", "prompt": "what is the private note about the release"}, world)
    assert prompt.returncode == 0, prompt.stderr[-300:]
    assert "private note" not in prompt.stdout, "another project's store text reached the hook output"
    assert prompt.stderr.count("inspeximus link") == 1, prompt.stderr
    assert prompt.stderr.startswith("[inspeximus] ") and "failed" not in prompt.stderr, "a refusal is not a handler bug"
    cap = _hook(proj, {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "echo hi"}}, world)
    assert cap.returncode == 0, cap.stderr[-300:]
    assert _tree_hash(victim_dir) == before, "a capture wrote into the store a link named"
    assert "inspeximus link" in cap.stderr


def test_a_shipped_directory_link_to_an_unrelated_directory_gains_no_files(world):
    outside = str(world / "unrelated")
    os.makedirs(outside)
    open(os.path.join(outside, "keep.txt"), "w").write("x")       # a junction to an empty folder tracks nothing
    proj = str(world / "clone")
    os.makedirs(proj)
    _mklink(os.path.join(proj, ".inspeximus"), outside, True)
    proj = _repo(world, "clone")
    _track(proj, ".inspeximus")
    for ev in ({"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "echo hi"}},
               {"hook_event_name": "UserPromptSubmit", "prompt": "anything about the release"},
               {"hook_event_name": "SessionStart", "source": "startup"}):
        assert _hook(proj, ev, world).returncode == 0
    assert os.listdir(outside) == ["keep.txt"], os.listdir(outside)


def test_a_config_inside_a_refused_link_is_not_read(world):
    outside = str(world / "unrelated")
    os.makedirs(outside)
    with open(os.path.join(outside, "config.json"), "w") as fh:
        json.dump({"inject": {"enabled": False}}, fh)
    proj = str(world / "zip")
    os.makedirs(proj)
    _mklink(os.path.join(proj, ".inspeximus"), outside, True)
    from inspeximus import claude_code as cc
    assert cc._cfg_file(proj) is None
    assert cc._cfg(proj) == {}
