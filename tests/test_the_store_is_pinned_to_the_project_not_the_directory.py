"""One project, one memory. Keying the store by `cwd` shatters it into one store per directory.

MEASURED on this plugin's own dogfood repository before the fix: **13 separate coding stores**,
~2,290 records, split 917 / 504 / 374 / 216 / 200 / ... across `server/`, `agora_output/lab/memops/`,
`agora-game-server/`, `agora_output/dmrg/`, `tools/`, `research/probes/` and seven more. Nothing
recalls across them. A question asked while standing in `tools/` cannot see what was learned in
`server/`, and which fragment answers is decided by the shell's working directory.

That is the most plausible explanation for the open gap in this product's own benchmark: the
single-store synthetic fixture scores 11 of 13 facts, while the real store scored 2 of 5. The facts
were not missing and retrieval was not weak -- the query reached one thirteenth of the memory.

`find_project_root` already existed in `_surface` and handles `.git` as a directory or a file, so
worktrees and submodules resolve correctly. The hook simply never called it.

WHAT IS ASSERTED, and why each one has to be:
  * subdirectories of one repository resolve to ONE store -- the fix itself;
  * outside a repository the old cwd behaviour survives, or this breaks every non-git project;
  * the env override still wins, because a user who pinned a path must keep it;
  * fragments are REPORTED and never moved. Silently relocating a user's memory is the class of
    action this codebase has been burned by, and a test that only checked the happy path would not
    notice a future "helpful" auto-migration.
"""
import os
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inspeximus.claude_code import _legacy_fragments, _store_dir  # noqa: E402


def _repo(tmp):
    subprocess.run(["git", "init", "-q", tmp], capture_output=True)
    return tmp


def test_every_subdirectory_of_a_repo_shares_one_store():
    root = _repo(tempfile.mkdtemp())
    deep = os.path.join(root, "a", "b", "c")
    os.makedirs(deep, exist_ok=True)
    here = _store_dir(root)
    assert _store_dir(deep) == here, (
        "a subdirectory got its own store; that is the fragmentation this fix exists to end")
    assert _store_dir(os.path.join(root, "a")) == here


def test_outside_a_repository_the_old_behaviour_survives():
    """No `.git` anywhere up the tree, so there is no project to pin to and cwd is the only answer."""
    d = tempfile.mkdtemp()
    assert os.path.normcase(_store_dir(d)) == os.path.normcase(os.path.join(d, ".inspeximus"))


def test_the_env_override_still_wins():
    root = _repo(tempfile.mkdtemp())
    forced = os.path.join(tempfile.mkdtemp(), "pinned")
    os.environ["INSPEXIMUS_CODING_STORE"] = forced
    try:
        assert _store_dir(root) == forced
    finally:
        del os.environ["INSPEXIMUS_CODING_STORE"]


def test_a_git_file_not_only_a_git_directory_counts():
    """Worktrees and submodules carry `.git` as a FILE. Missing that would silently fragment exactly
    the setups where an agent runs most often."""
    root = tempfile.mkdtemp()
    with open(os.path.join(root, ".git"), "w", encoding="utf-8") as f:
        f.write("gitdir: /elsewhere/.git/worktrees/wt\n")
    deep = os.path.join(root, "x", "y")
    os.makedirs(deep, exist_ok=True)
    assert _store_dir(deep) == _store_dir(root)


def test_fragments_are_reported_and_left_alone():
    root = _repo(tempfile.mkdtemp())
    frag_dir = os.path.join(root, "sub", ".inspeximus")
    os.makedirs(frag_dir, exist_ok=True)
    frag = os.path.join(frag_dir, "coding_memory.json")
    with open(frag, "w", encoding="utf-8") as f:
        f.write("[]")
    found = _legacy_fragments(root)
    assert any(os.path.normcase(p) == os.path.normcase(frag) for p in found), (
        "a fragment under the root was not reported, so a user would never learn their memory is split")
    assert os.path.exists(frag), "the fragment was MOVED or deleted; reporting must not mutate"


def test_the_root_store_is_not_reported_as_its_own_fragment():
    root = _repo(tempfile.mkdtemp())
    os.makedirs(os.path.join(root, ".inspeximus"), exist_ok=True)
    with open(os.path.join(root, ".inspeximus", "coding_memory.json"), "w", encoding="utf-8") as f:
        f.write("[]")
    assert _legacy_fragments(root) == [], (
        "the destination listed itself as a fragment, which would make the notice permanent")


def test_the_control_a_repo_with_no_fragments_reports_none():
    """Without this the reporting test could pass on a function that returns everything it finds."""
    assert _legacy_fragments(_repo(tempfile.mkdtemp())) == []
