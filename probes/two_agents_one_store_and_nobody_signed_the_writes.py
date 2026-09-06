"""One memory, two coding agents, and until today not one write said who made it.

WHAT WAS ALREADY TRUE. Claude Code and Codex have been writing the same store in this project
since 2026-07-18. Both harnesses call `python -m inspeximus.claude_code` on PreToolUse,
PostToolUse, SessionStart and UserPromptSubmit, and both land in
`<project>/.inspeximus/coding_memory.json`. The plumbing for a shared memory was complete and
nobody had measured what it delivered.

WHAT IT DELIVERED. Measured 2026-09-06 on that live store: 32,305 records, `sid` stamped on
27,134 of them across 448 distinct sessions, and `aid` on ZERO. Sessions were distinguished; the
agents behind them were not. Of the 448 sessions, 22 could be matched to a Claude Code transcript
by filename, so 426 were unattributable to either agent. "One memory shared by both agents" was
true of the file and false of every question you could ask it.

THE FIRST NUMBER I GOT WAS WRONG, and the reason is worth keeping. I first measured `agent_id`,
`session_id` and `user_id` as top-level fields and reported zero for all of them. `remember()`
stores them in meta under short keys: `aid`, `uid`, `sid`. The zero for `aid` survived the
correction; the zero for `sid` did not, and it was 27,134. Read the field the writer actually
writes, not the parameter name you passed.

WHAT THIS PROBE ASSERTS, all three of which were false before today:
  1. The agent is detectable from the environment with no config change in either harness.
     Claude Code exports CLAUDE_CODE_*; Codex carries CODEX_HOME and CODEX_CLI_PATH through its
     `[shell_environment_policy]`.
  2. A write carries that agent into the store.
  3. `history()` names the agent and session for each version, so the cross-writer timeline
     ("Codex changed this after Claude Code recorded it") is answerable.

CONTROLS, because each of the three can pass vacuously:
  - a process with NEITHER harness's variables must report "unknown", not a default agent name,
    or detection is just a constant;
  - an explicit override must beat both, or a third harness can never be named;
  - a write with no agent must still appear in `history()` with agent None, or the timeline
    silently drops exactly the records this exists to surface.
"""
import os
import sys
import json
import tempfile
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus                      # noqa: E402
from inspeximus.claude_code import agent_id            # noqa: E402

WORKER = '''
import sys, os
sys.path.insert(0, %r)
from inspeximus.claude_code import agent_id
print(agent_id())
'''


def detection_arm(repo):
    """Each harness's own variables, an override, and the null case."""
    # A UNIQUE directory per run. A fixed name in the system temp dir raced under pytest-xdist:
    # one worker deleted the helper while another was still launching it, and the suite reported
    # FileNotFoundError on a probe that passes perfectly on its own. Same shape as the shared
    # probe cache that corrupted a run before.
    src = os.path.join(tempfile.mkdtemp(prefix="aid_probe_"), "worker.py")
    open(src, "w", encoding="utf-8").write(WORKER % repo)
    cases = [
        ("claude code", {"CLAUDE_CODE_SESSION_ID": "abc"}, "claude-code"),
        ("codex, home", {"CODEX_HOME": r"C:\\Users\\x\\.codex"}, "codex"),
        ("codex, cli path", {"CODEX_CLI_PATH": r"C:\\x\\codex.exe"}, "codex"),
        ("override wins", {"INSPEXIMUS_AGENT_ID": "cursor",
                           "CLAUDE_CODE_SESSION_ID": "abc"}, "cursor"),
        ("CONTROL neither", {}, "unknown"),
    ]
    out = []
    for label, extra, want in cases:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("CLAUDE_CODE_", "CODEX_", "INSPEXIMUS_AGENT_ID"))}
        env.update(extra)
        got = subprocess.run([sys.executable, src], capture_output=True, text=True,
                             env=env).stdout.strip()
        out.append({"case": label, "got": got, "want": want, "ok": got == want})
    return out          # the temp dir is unique per run; nothing to clean up for a peer


def timeline_arm():
    """One file, edited by each agent in turn, then an unattributed write as the control."""
    m = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "shared.json"))
    m.remember("the retry loop lives in the core save path", key="file:core.py",
               mtype="semantic", session_id="s-claude-1", agent_id="claude-code")
    m.remember("the core save path now reloads before retrying", key="file:core.py",
               mtype="semantic", session_id="s-codex-1", agent_id="codex")
    m.remember("an unattributed note", key="file:core.py", mtype="semantic")
    rows = m.history("file:core.py")
    return [{"status": r["status"], "agent": r["agent"], "session": r["session"],
             "text": r["text"][:44]} for r in rows]


def main():
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    det = detection_arm(repo)
    print("  which agent is writing")
    for d in det:
        print("    %-7s %-16s -> %-13r want %r"
              % ("OK" if d["ok"] else "FAIL", d["case"], d["got"], d["want"]))

    rows = timeline_arm()
    print("\n  one file, both agents, one timeline")
    for r in rows:
        print("    %-11s by %-12s session %-11s %s"
              % (r["status"], r["agent"], r["session"], r["text"]))

    fail = [d for d in det if not d["ok"]]
    stamped = [r for r in rows if r["agent"]]
    assert not fail, "agent detection wrong in %d case(s): %s" % (len(fail), fail)
    assert len(rows) == 3, "the unattributed write vanished from the timeline"
    assert {r["agent"] for r in stamped} == {"claude-code", "codex"}, \
        "the timeline does not separate the two agents"
    assert rows[-1]["agent"] is None, \
        "an unstamped write must read as None, not be dropped or given a default"
    # The control write goes to the SAME key on purpose, so it supersedes the Codex version.
    # That is the case worth surfacing rather than avoiding: an UNATTRIBUTED write can retire an
    # attributed one, and a reader has to see both that it happened and that nobody signed it.
    # My first version of this assertion expected the Codex row to stay active, which was the
    # assertion being wrong about last-write-wins rather than the store being wrong.
    assert [r["status"] for r in rows] == ["superseded", "superseded", "active"], \
        "last-write-wins across agents no longer holds, so the timeline means nothing"
    assert rows[1]["agent"] == "codex", "the retired version lost its author"

    out = {"detection": det, "timeline": rows,
           "live_store_when_written": {"records": 32305, "sid_stamped": 27134,
                                       "aid_stamped": 0, "distinct_sessions": 448,
                                       "sessions_matched_to_a_claude_transcript": 22}}
    path = os.path.splitext(os.path.abspath(__file__))[0] + ".result.json"
    open(path, "w", encoding="utf-8", newline="\n").write(json.dumps(out, indent=1))
    print("\n  all controls passed; receipt: %s" % os.path.basename(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
