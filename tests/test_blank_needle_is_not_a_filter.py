"""A blank substring is not a narrow delete — it is every memory in the store.

Two surfaces took a `contains` substring and hard-deleted everything matching it. Every string contains
the empty string, and every multi-word text contains a space, so a blank needle selected the whole store.

MEASURED before the fix:

    pydantic_ai toolset  forget("")            -> 3 of 3 deleted, store empty
    CLI                  forget --contains " " -> 3 of 3 deleted ("alice" control deletes 1)
    CLI                  forget --contains ""  -> refused, but only because "" is FALSY, and the message
                                                  said "pass --key, --id, or --contains" to a user who
                                                  had just passed --contains

The pydantic_ai one is the sharper of the two: it is a tool the MODEL calls, so the argument is
model-generated and an empty slot is an ordinary failure, not an exotic one. The delete is irreversible.

Both are fixed the same way, which is the point of testing them in one file: this is a class, not an
instance. A non-blank needle stays deliberately broad ("ann" reaches "Joanna") -- that is the documented
contract and is NOT changed here.
"""
import os
import subprocess
import sys

import pytest

from inspeximus import Inspeximus

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = ("alice lives in prague", "bob salary 50000", "deploy key rotated")


def _seed(path):
    m = Inspeximus(str(path))
    for t in SEED:
        m.remember(t)
    m.flush()
    return m


# ── the CLI surface ────────────────────────────────────────────────────────────────────────────────

def _cli(path, *args):
    return subprocess.run([sys.executable, "-m", "inspeximus.cli", "--path", str(path), "forget", *args],
                          cwd=REPO, capture_output=True, text=True, timeout=180,
                          encoding="utf-8", errors="replace")


def test_control_a_real_substring_still_deletes_exactly_its_matches(tmp_path):
    """Without this, every refusal below is satisfied by a forget that deletes nothing at all."""
    p = tmp_path / "control.json"
    _seed(p)
    r = _cli(p, "--contains", "alice")
    assert r.returncode == 0, r.stderr[-300:]
    assert len(Inspeximus(str(p)).items) == 2, "the control needle must remove exactly one memory"


@pytest.mark.parametrize("needle", ["", " ", "   ", "\t"], ids=["empty", "one-space", "spaces", "tab"])
def test_cli_refuses_a_blank_needle_instead_of_deleting_the_store(tmp_path, needle):
    p = tmp_path / f"blank{len(needle)}{needle.strip()!r}.json".replace("'", "")
    _seed(p)
    r = _cli(p, "--contains", needle)

    assert len(Inspeximus(str(p)).items) == len(SEED), (
        f"--contains {needle!r} deleted memories. A blank needle matches every text, so this is a whole-"
        f"store erasure wearing the clothes of a targeted one, and it cannot be undone.")
    assert r.returncode == 2, r.stdout[-300:]
    assert "non-blank" in (r.stderr or ""), r.stderr[-300:]


def test_the_refusal_names_the_flag_the_user_actually_passed(tmp_path):
    """The empty case used to fall through to "pass --key, --id, or --contains" -- advice to do the thing
    the user had just done. A wrong diagnosis sends the reader looking in the wrong place."""
    p = tmp_path / "msg.json"
    _seed(p)
    err = _cli(p, "--contains", "").stderr or ""
    assert "--contains" in err and "non-blank" in err, err[-300:]
    assert "pass --key, --id, or --contains" not in err, (
        "the empty needle is still being reported as a missing argument")


# ── the pydantic_ai toolset surface ────────────────────────────────────────────────────────────────

def _forget_tool(path):
    """The toolset builds real pydantic_ai objects; take the same store-backed closure the tool wraps."""
    pytest.importorskip("pydantic_ai", reason="the pydantic_ai toolset needs pydantic_ai")
    from inspeximus.integrations.pydantic_ai import inspeximus_toolset
    ts = inspeximus_toolset(path=str(path))
    tools = getattr(ts, "tools", None) or {}
    by_name = {getattr(t, "name", None) or n: t for n, t in
               (tools.items() if isinstance(tools, dict) else ((getattr(t, "name", ""), t) for t in tools))}
    tool = by_name.get("forget")
    assert tool is not None, f"no forget tool in {sorted(by_name)}"
    return getattr(tool, "function", None) or getattr(tool, "func", tool)


@pytest.mark.parametrize("needle", ["", " ", "  "], ids=["empty", "one-space", "spaces"])
def test_the_model_callable_forget_refuses_a_blank_needle(tmp_path, needle):
    """This argument comes from an LLM, so an empty slot is an ordinary failure mode."""
    p = tmp_path / "tool.json"
    _seed(p)
    fn = _forget_tool(p)
    with pytest.raises(ValueError, match="non-blank"):
        fn(needle)
    assert len(Inspeximus(str(p)).items) == len(SEED), "the store was modified despite the refusal"


def test_control_the_model_callable_forget_still_deletes_a_real_match(tmp_path):
    """The refusal must not have turned forget into a no-op."""
    p = tmp_path / "tool_ok.json"
    _seed(p)
    fn = _forget_tool(p)
    assert fn("alice") == 1
    assert len(Inspeximus(str(p)).items) == len(SEED) - 1
