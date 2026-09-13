"""Which MEMORY.md pointers Claude Code's loader dropped, by name.

THE PROBLEM. Claude Code loads the first 200 lines or 25,000 UTF-16 units of a project's MEMORY.md,
whichever comes first, cut at a line boundary. Over the cap it warns that the file was partly loaded
and says nothing about WHICH entries went. An entry the session was supposed to honour can be missing
and the session cannot know that it does not know. Measured on the Agora index on 2026-09-13: one
added line pushed four pointers out, and the warning said only "over the cap".

THE RECEIPT. Apply the loader's cut rule to the file and name the pointers that fell outside the
window. That is all. It loads none of them, so the index budget is unchanged, and it prints nothing
when nothing was cut. It reports the loader's omission, which is the one failure the loader cannot
report about itself; it does not say whether a pointer's record is fresh, and an unread record stays
unknown.

Ships in the Claude Code plugin's SessionStart hook (`inspeximus.claude_code`), and runs on its own:

    python -m inspeximus.memory_index_receipt              # the index for the current directory
    python -m inspeximus.memory_index_receipt path/to/MEMORY.md

The cap is what the loader measured on the wire, not what its documentation says: UTF-16 code units
(JavaScript `String.length`), whole lines, carriage returns counted. `len()` on a Python string counts
code points and disagrees by one per astral character, which is why the count below goes through
`utf-16-le`. Asked for, in nearly these words, by kyle641320 on anthropics/claude-code#70555.
"""
from __future__ import annotations

import io
import os
import re
import sys

LINE_CAP, UNIT_CAP = 200, 25_000
LINK = re.compile(r"\]\(([^)]+\.md)\)")


def u16(text: str) -> int:
    """UTF-16 code units, which is what the loader compares against the cap."""
    return len(text.encode("utf-16-le")) // 2


def split_lines(text: str) -> list:
    """Lines, with a trailing newline treated as a terminator and not as an empty last line."""
    out = text.split(chr(10))
    if out and out[-1] == "":
        out.pop()
    return out


def window(text: str) -> str:
    """The first LINE_CAP lines, cut at UNIT_CAP units, backed up to a line boundary."""
    kept = chr(10).join(split_lines(text)[:LINE_CAP])
    if u16(kept) <= UNIT_CAP:
        return kept
    c = kept.rfind(chr(10), 0, UNIT_CAP)
    return kept[:c if c > 0 else UNIT_CAP]


def index_path(project_dir: str | None = None) -> str:
    """Where Claude Code keeps the auto-memory index for a project: the project path with every
    non-alphanumeric character replaced by "-", under ~/.claude/projects. CLAUDE_MEMORY_INDEX overrides."""
    explicit = os.environ.get("CLAUDE_MEMORY_INDEX")
    if explicit:
        return explicit
    project = project_dir or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    slug = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(project))
    return os.path.join(os.path.expanduser("~"), ".claude", "projects", slug, "memory", "MEMORY.md")


def omitted(text: str) -> tuple:
    """(lines_kept, lines_total, units_kept, units_total, [pointers outside the window])."""
    lines = split_lines(text)
    win = window(text)
    seen = win.count(chr(10)) + 1 if win else 0
    in_win = set(LINK.findall(win))
    outside = [p for p in dict.fromkeys(LINK.findall(text)) if p not in in_win]
    return seen, len(lines), u16(win), u16(text), outside


def receipt(text: str) -> str:
    """The block to hand a session. Empty when nothing was cut."""
    seen, total, ukept, utotal, outside = omitted(text)
    if not outside and seen >= total:
        return ""
    head = ("[memory-index receipt] the loader kept %d of %d lines (%s of %s units); "
            "%d pointer(s) are on disk but NOT in this session's context:"
            % (seen, total, format(ukept, ","), format(utotal, ","), len(outside)))
    body = "".join("\n  - " + p for p in outside)
    tail = ("\n  They are readable by path; a pointer you cannot see is not a pointer that does "
            "not exist. To stop losing them, move entries below the cut into an archive file.")
    return head + body + tail


def receipt_for(project_dir: str | None = None) -> str:
    """The receipt for a project's index, or "" when there is no index or it cannot be read.
    Never raises: a receipt that can block a session is worse than the silence it replaces."""
    try:
        path = index_path(project_dir)
        if not os.path.isfile(path):
            return ""
        text = io.open(path, "rb").read().decode("utf-8")   # bytes, so CR is counted as the loader counts it
        return receipt(text)
    except Exception:                                       # noqa: BLE001
        return ""


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        os.environ["CLAUDE_MEMORY_INDEX"] = argv[0]
    out = receipt_for()
    if out:
        sys.stdout.write(out + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
