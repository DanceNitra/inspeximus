"""tools/release.py: every guard fires, and it fires BEFORE the step it protects.

v3.15.0 was tagged by hand before CI ran on its commit; the release gate then failed and the tag cannot be
deleted. The script is now the only way to tag, and this file drives it with a fake `sh` (no network, no
repository) through each refusal: a [FAIL] from release_check, a version mismatch, an existing tag here or
on origin, a HEAD that is not a fast-forward, a branch run that is not green on the exact commit, CI not
green on main, and origin/main moving between the push and the tag. The control runs the green path and
checks the order: release branch, its runs green, main, main CI, tag.
MAIN IS NEVER PUSHED BEFORE A GREEN BRANCH RUN ON THE SAME SHA (2026-09-28): 3.15.2's first release commit
went to main red, because the first version of the script pushed main and then waited for CI.
The PyPI gate is the owner's: no code path may call the deployments API, and the green path ends by
printing the owner's step with the status "waiting for owner".
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEAD = "a" * 40
OTHER = "b" * 40
V = "9.9.9"
BRANCH = "release/" + V


def _load():
    spec = importlib.util.spec_from_file_location("release_tool", os.path.join(REPO, "tools", "release.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _dispatched(conclusion="success", sha=HEAD, branch=BRANCH, run_id=3):
    return {"databaseId": run_id, "event": "workflow_dispatch", "headSha": sha, "headBranch": branch,
            "status": "completed", "conclusion": conclusion}


def _pushed(conclusion="success", sha=HEAD):
    return {"databaseId": 1, "event": "push", "headSha": sha, "headBranch": "main", "status": "completed",
            "conclusion": conclusion}


class Fake:
    """A scripted `sh`. `over` replaces the answer for any command whose joined args contain the key."""

    def __init__(self, **over):
        self.calls = []
        self.over = over

    def __call__(self, args, cwd=None):
        cmd = " ".join(str(a) for a in args)
        self.calls.append(cmd)
        for key, answer in self.over.items():
            if key in cmd:
                return answer
        if "release_check.py" in cmd:
            return 0, "  [PASS] version carriers\n  [SKIP] ci on HEAD  no completed run yet\n"
        if cmd.startswith("git rev-parse -q --verify"):
            return 1, ""
        if cmd.startswith("git ls-remote --tags"):
            return 0, ""
        if cmd.startswith("git merge-base --is-ancestor"):
            return 0, ""
        if cmd == "git rev-parse HEAD":
            return 0, HEAD + "\n"
        if "--workflow ci.yml" in cmd:
            return 0, json.dumps([_dispatched(), _pushed()])
        if "--workflow one-memory.yml" in cmd or "--workflow clean-install.yml" in cmd:
            return 0, json.dumps([_dispatched(run_id=4)])
        if cmd == "git ls-remote origin refs/heads/main":
            return 0, HEAD + "\trefs/heads/main\n"
        if "--workflow release.yml" in cmd:
            return 0, json.dumps([{"databaseId": 2, "headBranch": "v" + V}])
        return 0, ""

    def index(self, prefix):
        return next((i for i, c in enumerate(self.calls) if c.startswith(prefix)), None)

    def last(self, prefix):
        found = [i for i, c in enumerate(self.calls) if c.startswith(prefix)]
        return found[-1] if found else None


MAIN_PUSH = "git push origin %s:main" % HEAD
BRANCH_PUSH = "git push origin %s:refs/heads/%s" % (HEAD, BRANCH)


@pytest.fixture
def root(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "inspeximus"\nversion = "%s"\n' % V, encoding="utf-8")
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    for name in ("ci.yml", "one-memory.yml", "clean-install.yml"):                # the real path lists
        shutil.copyfile(os.path.join(REPO, ".github", "workflows", name), str(wf / name))
    return tmp_path


def _run(root, fake, *extra, version=V):
    lines = []
    rc = _load().main([version, *extra], sh=fake, sleep=lambda s: None, out=lines.append, root=root)
    return rc, "\n".join(lines)


def test_the_green_path_clears_the_branch_then_main_then_tags(root):
    fake = Fake()
    rc, out = _run(root, fake)
    assert rc == 0, out
    order = (fake.index(BRANCH_PUSH), fake.index("gh workflow run ci.yml -R"),
             fake.index("gh run list -R %s --workflow ci.yml" % _load().REPO), fake.index(MAIN_PUSH),
             fake.index("git tag -a v" + V), fake.index("git push origin v" + V))
    assert None not in order and list(order) == sorted(order), fake.calls
    polls = [i for i, c in enumerate(fake.calls) if c.startswith("gh run list") and "--workflow ci.yml" in c]
    assert polls[0] < fake.index(MAIN_PUSH) < polls[-1], "a branch poll before main, a main poll after it"
    assert "waiting for owner: approve the PyPI publish of v%s at https://github.com/" % V in out, out
    assert "Actions > run 2 > Review deployments > pypi > Approve" in out, out
    assert not [c for c in fake.calls if "push origin --delete" in c or c.startswith("git push origin :")]


@pytest.mark.parametrize("name, over", [
    ("branch run failed", {"--workflow ci.yml": (0, json.dumps([_dispatched("failure"), _pushed()]))}),
    ("branch run cancelled", {"--workflow ci.yml": (0, json.dumps([_dispatched("cancelled"), _pushed()]))}),
    ("green on another sha", {"--workflow ci.yml": (0, json.dumps([_dispatched(sha=OTHER), _pushed()]))}),
    ("green on another branch", {"--workflow ci.yml": (0, json.dumps([_dispatched(branch="main"), _pushed()]))}),
    ("only a push run", {"--workflow ci.yml": (0, json.dumps([_pushed()]))}),
    ("dispatch refused", {"gh workflow run ci.yml": (1, "HTTP 422: workflow does not have dispatch")}),
    ("branch push refused", {BRANCH_PUSH: (1, "! [rejected] (non-fast-forward)")}),
])
def test_main_is_never_pushed_before_a_green_branch_run_on_the_same_sha(root, name, over):
    fake = Fake(**over)
    rc, out = _run(root, fake)
    assert rc == 1 and "STOP:" in out, (name, out)
    assert fake.index(MAIN_PUSH) is None and fake.index("git tag") is None, (name, fake.calls)


def test_an_install_change_runs_the_install_workflows_and_a_red_one_keeps_main_untouched(root):
    diff = {"git diff --name-only origin/main HEAD": (0, "inspeximus/install_all.py\nREADME.md\n")}
    fake = Fake(**diff)
    rc, out = _run(root, fake)
    assert rc == 0, out
    for wf in ("ci.yml", "one-memory.yml", "clean-install.yml"):
        assert fake.index("gh workflow run %s -R" % wf) is not None, (wf, fake.calls)
        first = (fake.index if wf == "ci.yml" else fake.last)("gh run list -R %s --workflow %s" % (_load().REPO, wf))
        assert first < fake.index(MAIN_PUSH), wf              # ci.yml is polled again on main, after the push
    fake = Fake(**diff, **{"--workflow clean-install.yml": (0, json.dumps([_dispatched("failure", run_id=5)]))})
    rc, out = _run(root, fake)
    assert rc == 1 and "clean-install.yml run 5" in out and "main was not touched" in out, out
    assert fake.index(MAIN_PUSH) is None


def test_an_unrelated_change_runs_ci_only(root):
    fake = Fake(**{"git diff --name-only origin/main HEAD": (0, "README.md\ntools/release.py\n")})
    rc, out = _run(root, fake)
    assert rc == 0, out
    assert fake.index("gh workflow run ci.yml") is not None
    assert fake.index("gh workflow run one-memory.yml") is None and fake.index("gh workflow run clean-install.yml") is None


def test_the_path_lists_are_read_from_the_workflows_themselves():
    mod = _load()
    with open(os.path.join(REPO, ".github", "workflows", "one-memory.yml"), encoding="utf-8") as fh:
        globs = mod.push_paths(fh.read())
    assert "inspeximus/install_all.py" in globs and "docs/install/**" in globs, globs
    with open(os.path.join(REPO, ".github", "workflows", "ci.yml"), encoding="utf-8") as fh:
        assert mod.push_paths(fh.read()) == []                                         # control: no paths filter


@pytest.mark.parametrize("name, over, says", [
    ("release_check FAIL", {"release_check.py": (1, "  [FAIL] mutation targets  1 of 431 absent\n")},
     "release_check reports a failure"),
    ("tag exists here", {"git rev-parse -q --verify": (0, HEAD)}, "already exists here"),
    ("tag exists on origin", {"git ls-remote --tags": (0, HEAD + "\trefs/tags/v" + V)}, "already exists on origin"),
    ("not a fast-forward", {"git merge-base --is-ancestor": (1, "")}, "not a fast-forward"),
])
def test_a_preflight_guard_stops_before_the_push(root, name, over, says):
    fake = Fake(**over)
    rc, out = _run(root, fake)
    assert rc == 1 and "STOP:" in out and says in out, (name, out)
    assert fake.index("git push") is None, (name, fake.calls)


def test_a_version_mismatch_stops_before_the_push(root):
    fake = Fake()
    rc, out = _run(root, fake, version="9.9.8")
    assert rc == 1 and "pyproject.toml says 9.9.9, not 9.9.8" in out, out
    assert fake.index("git push") is None


def test_ci_on_head_alone_does_not_stop_the_push(root):
    """Guard 1 exempts "ci on HEAD": it cannot pass before the push, and the branch and main runs check it."""
    fake = Fake(**{"release_check.py": (1, "  [FAIL] ci on HEAD  tests=failure on the previous head\n")})
    rc, out = _run(root, fake)
    assert rc == 0, out


@pytest.mark.parametrize("name, run", [
    ("CI failed", _pushed("failure")),
    ("CI cancelled", _pushed("cancelled")),
])
def test_main_ci_not_green_on_the_exact_commit_stops_before_the_tag(root, name, run):
    """Green on the branch and red on main can still happen (a flaky test, a runner change)."""
    fake = Fake(**{"--workflow ci.yml": (0, json.dumps([_dispatched(), run]))})
    rc, out = _run(root, fake)
    assert rc == 1 and "STOP:" in out and "main CI run 1 is" in out and "not success on" in out, (name, out)
    assert fake.index(MAIN_PUSH) is not None
    assert fake.index("git tag") is None, fake.calls


def test_a_green_main_run_of_another_commit_does_not_count(root):
    """A success on OTHER is not a success on HEAD; the script keeps waiting and then stops, untagged."""
    fake = Fake(**{"--workflow ci.yml": (0, json.dumps([_dispatched(), _pushed(sha=OTHER)]))})
    rc, out = _run(root, fake)
    assert rc == 1 and "no finished push run of ci.yml for " + HEAD in out, out
    assert fake.index("git tag") is None


def test_origin_main_moving_after_the_push_stops_before_the_tag(root):
    fake = Fake(**{"git ls-remote origin refs/heads/main": (0, OTHER + "\trefs/heads/main\n")})
    rc, out = _run(root, fake)
    assert rc == 1 and "origin/main moved to " + OTHER[:12] in out, out
    assert fake.index("git tag") is None


class _MainMovesAfterTheFirstLook(Fake):
    def __call__(self, args, cwd=None):
        if " ".join(args) == "git ls-remote origin refs/heads/main":
            self.calls.append(" ".join(args))
            looks = sum(1 for c in self.calls if c == "git ls-remote origin refs/heads/main")
            return 0, (HEAD if looks == 1 else OTHER) + "\trefs/heads/main\n"
        return super().__call__(args, cwd)


def test_origin_main_moving_during_main_ci_stops_before_the_tag(root):
    fake = _MainMovesAfterTheFirstLook()
    rc, out = _run(root, fake)
    assert rc == 1 and "moved to %s during main CI" % OTHER[:12] in out, out
    assert fake.index("git tag") is None


def test_a_dry_run_pushes_nothing(root):
    fake = Fake(**{"git diff --name-only origin/main HEAD": (0, "docs/install/index.md\n")})
    rc, out = _run(root, fake, "--dry-run")
    assert rc == 0 and "nothing pushed" in out, out
    assert "ci.yml, one-memory.yml, clean-install.yml on " + BRANCH in out, out
    assert fake.index("git push") is None and fake.index("git tag") is None and fake.index("gh workflow run") is None


def test_no_code_path_touches_the_owners_pypi_gate(root):
    """The `pypi` environment's reviewer is the owner; approving it from a session bypasses his review."""
    src = open(os.path.join(REPO, "tools", "release.py"), encoding="utf-8").read()
    for word in ("pending_deployments", "environment_ids", "state=approved", "approve-pypi"):
        assert word not in src, word
    fake = Fake()
    rc, out = _run(root, fake)
    assert rc == 0, out
    assert not [c for c in fake.calls if "deployment" in c or "gh api" in c], fake.calls
    with pytest.raises(SystemExit):
        _run(root, Fake(), "--approve-pypi")                    # the flag is gone, not ignored
