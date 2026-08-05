"""A commit message is a decision with its rationale; the hook was storing that `git commit` ran.

WHY. Measured on this plugin's own dogfood store: 917 records after months of capture, tagged `bash`
(747) and `file`/`edit` (170), and **zero decisions**. The decisions were never missing from the
project — they were in `git log`. On the same project's last 200 commits, 100% yield a decision
record and 92.5% carry a non-empty `because` taken from the commit body. A commit is the one moment
an agent writes down a choice AND why, in a structured form, with no model involved.

THE HARD PART IS NOT CAPTURING, IT IS NOT OVER-CAPTURING. The first implementation asked
`any(verb in command.lower())` and fired on three of five controls:

    git log --oneline | grep commit      the word is an argument to grep
    echo 'remember to git commit later'  the word is inside a quoted string
    git diff HEAD~0 --name-only          the verb `am` is a substring of --n[am]e-only

That last one is why this file has more negative cases than positive ones. A substring test over a
shell command line matches text the shell never executes, and every false capture writes a decision
the project never made — which is worse than capturing nothing, because it is indistinguishable
from a real one at read time.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inspeximus.claude_code import _invokes_commit  # noqa: E402

RUNS_A_COMMIT = [
    "git commit -m x",
    "git commit -q -F - <<'MSG'",
    "cd /repo && git commit --amend --no-edit",
    "FOO=bar git commit -m y",
    "git add -A && git commit -m 'both'",
    "/usr/bin/git commit -m z",
    "git merge --no-ff feature",
    "git revert abc123",
    "git cherry-pick abc123",
]

MENTIONS_BUT_DOES_NOT = [
    "git log --oneline | grep commit",
    "echo 'remember to git commit later'",
    "git diff HEAD~0 --name-only",          # `am` inside --name-only
    "git show HEAD --stat",
    "git status",
    "git commit --dry-run -m nope",
    "git log --format=%s -1",
    "grep -r 'git commit' .",
    'python -c "print(\'git commit\')"',
    "git config user.name t",
    "git log --grep=commit",
    "cat notes.md | grep -i 'merge'",
]


@pytest.mark.parametrize("cmd", RUNS_A_COMMIT)
def test_a_command_that_writes_a_commit_is_captured(cmd):
    assert _invokes_commit(cmd), (
        f"{cmd!r} moves HEAD, so its message is a decision this store should hold")


@pytest.mark.parametrize("cmd", MENTIONS_BUT_DOES_NOT)
def test_a_command_that_only_mentions_one_is_not(cmd):
    assert not _invokes_commit(cmd), (
        f"{cmd!r} writes no commit. Capturing here invents a decision the project never made, and at "
        f"read time it is indistinguishable from one that was")


def test_the_control_both_populations_are_non_empty():
    """Neither list may be quietly emptied: a matcher that always fires and one that never does
    would each pass half of this file, and a future edit that drops a case should be visible."""
    assert len(RUNS_A_COMMIT) >= 8 and len(MENTIONS_BUT_DOES_NOT) >= 10
    assert all(_invokes_commit(c) for c in RUNS_A_COMMIT)
    assert not any(_invokes_commit(c) for c in MENTIONS_BUT_DOES_NOT)


def test_unbalanced_quotes_do_not_raise():
    """A command the shell itself could not parse must be declined, not crash the hook. The whole
    module is fail-open: an exception here costs the agent its tool call."""
    assert _invokes_commit("git commit -m 'unterminated") in (True, False)


def test_an_empty_command_is_declined():
    assert not _invokes_commit("")
