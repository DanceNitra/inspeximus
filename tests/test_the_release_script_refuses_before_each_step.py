"""tools/release.py: every guard fires, and it fires BEFORE the step it protects.

v3.15.0 was tagged by hand before CI ran on its commit; the release gate then failed and the tag cannot be
deleted. The script is now the only way to tag, and this file drives it with a fake `sh` (no network, no
repository) through each refusal: a [FAIL] from release_check, a version mismatch, an existing tag here or
on origin, a HEAD that is not a fast-forward, CI not green on the exact commit, and origin/main moving
between the push and the tag. The control runs the green path and checks the order: push, CI, tag.
The PyPI gate is the owner's: no code path may call the deployments API, and the green path ends by
printing the owner's step with the status "waiting for owner".
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEAD = "a" * 40
OTHER = "b" * 40
V = "9.9.9"


def _load():
    spec = importlib.util.spec_from_file_location("release_tool", os.path.join(REPO, "tools", "release.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
            return 0, json.dumps([{"databaseId": 1, "event": "push", "headSha": HEAD, "status": "completed",
                                   "conclusion": "success"}])
        if cmd == "git ls-remote origin refs/heads/main":
            return 0, HEAD + "\trefs/heads/main\n"
        if "--workflow release.yml" in cmd:
            return 0, json.dumps([{"databaseId": 2, "headBranch": "v" + V}])
        return 0, ""

    def index(self, prefix):
        return next((i for i, c in enumerate(self.calls) if c.startswith(prefix)), None)


@pytest.fixture
def root(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "inspeximus"\nversion = "%s"\n' % V, encoding="utf-8")
    return tmp_path


def _run(root, fake, *extra, version=V):
    lines = []
    rc = _load().main([version, *extra], sh=fake, sleep=lambda s: None, out=lines.append, root=root)
    return rc, "\n".join(lines)


def test_the_green_path_pushes_then_waits_for_ci_then_tags(root):
    fake = Fake()
    rc, out = _run(root, fake)
    assert rc == 0, out
    push, ci, tag, tag_push = (fake.index("git push origin %s:main" % HEAD), fake.index("gh run list -R"),
                               fake.index("git tag -a v" + V), fake.index("git push origin v" + V))
    assert None not in (push, ci, tag, tag_push) and push < ci < tag < tag_push, fake.calls
    assert "waiting for owner: approve the PyPI publish of v%s at https://github.com/" % V in out, out
    assert "Actions > run 2 > Review deployments > pypi > Approve" in out, out


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
    """Guard 1 exempts "ci on HEAD": it cannot pass before the push, and guard 5 is its real check."""
    fake = Fake(**{"release_check.py": (1, "  [FAIL] ci on HEAD  tests=failure on the previous head\n")})
    rc, out = _run(root, fake)
    assert rc == 0, out


@pytest.mark.parametrize("name, run", [
    ("CI failed", {"databaseId": 1, "event": "push", "headSha": HEAD, "status": "completed", "conclusion": "failure"}),
    ("CI cancelled", {"databaseId": 1, "event": "push", "headSha": HEAD, "status": "completed", "conclusion": "cancelled"}),
])
def test_ci_not_green_on_the_exact_commit_stops_before_the_tag(root, name, run):
    fake = Fake(**{"--workflow ci.yml": (0, json.dumps([run]))})
    rc, out = _run(root, fake)
    assert rc == 1 and "STOP:" in out and "not success on" in out, (name, out)
    assert fake.index("git push origin %s:main" % HEAD) is not None
    assert fake.index("git tag") is None, fake.calls


def test_a_green_run_of_another_commit_does_not_count(root):
    """A success on OTHER is not a success on HEAD; the script keeps waiting and then stops, untagged."""
    fake = Fake(**{"--workflow ci.yml": (0, json.dumps([{"databaseId": 9, "event": "push", "headSha": OTHER,
                                                           "status": "completed", "conclusion": "success"}]))})
    rc, out = _run(root, fake)
    assert rc == 1 and "no finished push run of ci.yml for " + HEAD in out, out
    assert fake.index("git tag") is None


def test_origin_main_moving_after_the_push_stops_before_the_tag(root):
    fake = Fake(**{"git ls-remote origin refs/heads/main": (0, OTHER + "\trefs/heads/main\n")})
    rc, out = _run(root, fake)
    assert rc == 1 and "origin/main moved to " + OTHER[:12] in out, out
    assert fake.index("git tag") is None


def test_a_dry_run_pushes_nothing(root):
    fake = Fake()
    rc, out = _run(root, fake, "--dry-run")
    assert rc == 0 and "nothing pushed" in out, out
    assert fake.index("git push") is None and fake.index("git tag") is None


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
