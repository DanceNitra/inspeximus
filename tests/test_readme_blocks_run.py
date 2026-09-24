"""Every ```python block in README.md runs, and every result its comments state holds, in both layouts.

`test_every_fenced_python_block_parses` (test_readme_snippets.py) is the floor: it proves a block is
Python. It cannot see the two ways the blocks actually broke for a reader, which the onboarding review of
2026-09-24 found by running them cold (audits/2026-09-24/onboarding-review.md, branch review-onboarding):

  * IN ORDER, IN ONE FOLDER, THEY GIVE THE WRONG ANSWER. Six blocks open `memory.json`. The first ends on
    `revert()`, so the next block's db-7 write is a restatement of a retired value and the echo guard
    retires it without a word. Every block is correct alone; top to bottom, three are not.
  * ONE NEEDS `cryptography`, which `pip install inspeximus` does not bring, and the README never says so.

So each block runs twice: alone, in an empty directory with its own HOME, and after every block above it
in one shared directory. A comment that states a result is asserted (tools/readme_blocks.py says exactly
which comments count). A block that needs an extra declares it by naming `inspeximus[crypto]` between the
previous Python block and itself; without that extra installed it must fail and name the module.

By default the blocks run under this interpreter against this checkout. With INSPEXIMUS_PYTHON set to an
interpreter that has inspeximus installed (a clean venv; the `readme-and-examples` CI job builds one
plain and one with the crypto extra) they run against that installation instead.
"""
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import readme_blocks as rb  # noqa: E402

PYTHON = os.environ.get("INSPEXIMUS_PYTHON") or None
BLOCKS = rb.blocks()
IDS = [b.name for b in BLOCKS]


def _dirs(base):
    work, home = os.path.join(str(base), "work"), os.path.join(str(base), "home")
    os.makedirs(work, exist_ok=True)
    os.makedirs(home, exist_ok=True)
    return work, home


def test_the_sweep_is_not_a_sweep_over_nothing():
    """A renamed README, or a fence the extractor stops recognising, would leave the parametrised tests
    below with zero cases and a green run. Every ```python opener in the file must be a block here."""
    with open(rb.README, encoding="utf-8") as fh:
        openers = len(re.findall(r"(?m)^```python\s*$", fh.read()))
    assert len(BLOCKS) == openers, (len(BLOCKS), openers)
    assert len(BLOCKS) >= 5, "the README has lost most of its Python blocks, or the extractor has"
    assert sum(len(rb.stated_results(b.code)) for b in BLOCKS) >= 10, (
        "the README states results in comments; finding almost none means the reader of them broke")


@pytest.mark.parametrize("block", BLOCKS, ids=IDS)
def test_block_alone_in_an_empty_directory(block, tmp_path):
    work, home = _dirs(tmp_path)
    missing = rb.missing_extras(block.extras, PYTHON)
    why = rb.verdict(rb.run_block(block, work, home, PYTHON), missing)
    assert why is None, "%s, run alone in an empty directory: %s" % (block.name, why)


@pytest.fixture(scope="module")
def shared_directory_run(tmp_path_factory):
    """Every block, top to bottom, in ONE working directory and one HOME: what a reader who keeps one
    folder gets. Each block still starts a fresh interpreter, as a new script would."""
    work, home = _dirs(tmp_path_factory.mktemp("readme_shared"))
    return {b.line: rb.verdict(rb.run_block(b, work, home, PYTHON), rb.missing_extras(b.extras, PYTHON))
            for b in BLOCKS}


# One worker runs the whole sequence once; spread over xdist workers, each would repeat it.
@pytest.mark.xdist_group("readme_blocks_shared_directory")
@pytest.mark.parametrize("block", BLOCKS, ids=IDS)
def test_block_after_every_block_above_it_in_one_directory(block, shared_directory_run):
    why = shared_directory_run[block.line]
    assert why is None, (
        "%s, run after every block above it in one directory: %s" % (block.name, why))


# ── controls: the checker has to be able to fail ────────────────────────────────────────────────────
def _block(code, extras=()):
    """A block whose opening fence is line 0, so its code lines are numbered from 1."""
    return rb.Block(0, code, tuple(extras))


def test_control_a_wrong_stated_result_fails(tmp_path):
    work, home = _dirs(tmp_path)
    r = rb.run_block(_block("x = 1\nx\n# 2\n"), work, home, PYTHON)
    assert not r.ok
    assert "L3: the block states 2; it returned 1" in r.failure, r.failure


def test_control_a_right_stated_result_passes_and_prose_is_not_checked(tmp_path):
    work, home = _dirs(tmp_path)
    code = ("x = 'db-7'\n"
            "x   # 'db-7'          <- trailing text after the literal is allowed\n"
            "x   # a correction\n"
            "[x, None]\n"
            "# ['db-7', None]\n"
            "print(x)\n"
            "# db-7\n")
    assert [(k, v) for _, k, v, _ in rb.stated_results(code)] == [
        ("value", "db-7"), ("value", ["db-7", None]), ("printed", "db-7")]
    r = rb.run_block(_block(code), work, home, PYTHON)
    assert r.ok, r.failure


def test_control_a_comment_that_looks_like_a_result_but_is_not_one_is_an_error():
    """Otherwise a typo in a stated result turns the check off for that line, silently."""
    with pytest.raises(rb.Unreadable):
        rb.stated_results("x = 1\nx\n# 'db-7\n")


def test_control_every_wrong_result_in_a_block_is_named_and_an_exception_stops_it(tmp_path):
    work, home = _dirs(tmp_path)
    r = rb.run_block(_block("1\n# 2\n3\n# 4\n{}['k']\n5\n# 6\n"), work, home, PYTHON)
    assert "L2: the block states 2; it returned 1" in r.failure
    assert "L4: the block states 4; it returned 3" in r.failure
    assert "L5: KeyError" in r.failure
    assert "L7" not in r.failure, "a reader's script stops at the exception, and so does the block"


def test_control_the_block_runs_in_the_directory_it_is_given(tmp_path):
    work, home = _dirs(tmp_path)
    assert rb.run_block(_block("open('here.txt', 'w').write('x')\n"), work, home, PYTHON).ok
    assert os.path.exists(os.path.join(work, "here.txt"))


def test_control_an_extra_is_declared_by_naming_it_before_the_block(tmp_path):
    md = tmp_path / "R.md"
    md.write_text('```python\na = 1\n```\n\n```bash\npip install "inspeximus[crypto]"\n```\n\n'
                  "```python\nb = 2\n```\n\n```python\nc = 3\n```\n", encoding="utf-8")
    assert [(b.line, b.extras) for b in rb.blocks(str(md))] == [(1, ()), (9, ("crypto",)), (13, ())]


def test_control_a_declared_extra_must_be_needed_and_named():
    b = _block("pass\n", ["crypto"])
    passed = rb.Result(b, 0, "", "")
    assert "drop the declaration" in rb.verdict(passed, ["crypto"])
    named = rb.Result(b, 1, "", "FAILED README.md:L2: RuntimeError: needs the `cryptography` package")
    assert rb.verdict(named, ["crypto"]) is None
    unnamed = rb.Result(b, 1, "", "FAILED README.md:L2: KeyError: 'k'")
    assert "never names it" in rb.verdict(unnamed, ["crypto"])
    assert rb.verdict(passed, []) is None
    with pytest.raises(ValueError):
        rb.missing_extras(["no-such-extra"])
