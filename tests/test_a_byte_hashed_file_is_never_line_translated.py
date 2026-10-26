"""Every file the verifier hashes as raw bytes must be exempt from line-ending translation.

A published leaf is tied to the tree by `sha256(0x00 || open(leaf, "rb").read())`, so two extra
characters per line change the leaf hash. Git rewrites LF to CRLF on a Windows checkout unless
`.gitattributes` says otherwise, and `verify.py` then reports FAILED on an honest log for the one
reader we asked to distrust us. CI runs on Linux and never sees it, which is the worst shape a
defect can have.

`.gitattributes` already carried the rule for the log's top-level files and stopped at the
directory, leaving `entries/*.leaf.json` unspecified. Today that is harmless, because each leaf is
single-line JSON with no newline byte to translate. This test does not rest on that: it asserts the
attribute, so a writer that later emits an indented leaf cannot make the log unverifiable on Windows
only.

THE CONTROL MATTERS MORE THAN THE ASSERTION HERE. A first attempt rewrote 604 entry files to CRLF,
saw `verify.py` pass, and read it as safety; the probe had written the leaves back byte-identically
and measured nothing. So `test_the_check_fails_when_the_attribute_is_absent` runs the same question
against a repository WITHOUT the rule and requires the answer to change.
"""
from __future__ import annotations

import os
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Paths whose BYTES a hash commits to. Written as the pattern plus one real file, because
# `git check-attr` answers about a path rather than about a pattern.
BYTE_HASHED = [
    "transparency/claims.log",
    "transparency/log.jsonl",
    "transparency/head.json",
    "transparency/entries/0.leaf.json",
    "transparency/entries/0.cose",
]


def attr(repo, path, name="text"):
    """What git would do to `path` on checkout: 'unset' or 'set' for -text/text, 'unspecified' if no
    rule covers it. Asked of git rather than parsed out of the file, so a pattern that does not match
    the way we think it does is caught instead of assumed."""
    out = subprocess.run(["git", "check-attr", name, "--", path], cwd=repo,
                         capture_output=True, text=True, check=True).stdout
    return out.rsplit(":", 1)[-1].strip()


@pytest.mark.parametrize("path", BYTE_HASHED)
def test_every_byte_hashed_path_is_exempt_from_translation(path):
    assert os.path.exists(os.path.join(ROOT, path)), "%s does not exist, so this asks nothing" % path
    # 'unset' is -text; a `binary` rule also unsets text, which is why both answers pass.
    assert attr(ROOT, path) == "unset", (
        "%s is hashed as raw bytes but git may translate its line endings on a Windows checkout. "
        "Add it to .gitattributes with -text (or binary)." % path)


def test_the_check_fails_when_the_attribute_is_absent(tmp_path):
    """The control. Without the rule the same question must answer 'unspecified', or the assertion
    above would pass on a repository that has no protection at all."""
    repo = tmp_path / "bare"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    leaf = repo / "transparency" / "entries"
    leaf.mkdir(parents=True)
    (leaf / "0.leaf.json").write_bytes(b'{"a":1}')
    assert attr(str(repo), "transparency/entries/0.leaf.json") == "unspecified"
