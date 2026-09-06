"""A hook can be installed, enabled, correct, and never run. Three ways, all measured.

WHAT THIS IS ABOUT. Claude Code and Codex have written the same store in this project since
2026-07-18. Codex contributed nothing for seven weeks, and nothing anywhere said so: a hook that
does not run is silent, and a hook that runs and cannot write was silent too, by design.

Six hours of diagnosis produced three separate causes, none of which is exotic, and each of which
would independently produce "installed and doing nothing":

  1. THE CONFIG WAS NEVER READ. `.codex/hooks.json` and `~/.codex/hooks.json` both named our hook.
     Codex reads hooks only from PLUGINS: a directory carrying `.codex-plugin/plugin.json` whose
     manifest declares them. A loose hooks.json is inert. The file had sat there since July.

  2. THE COMMAND WAS NEVER SPAWNED. Codex runs a hook command WITHOUT a shell, so a quoted
     interpreter path is not resolved. `"C:/.../python.exe" -m inspeximus.claude_code` produced
     nothing, twice, while `cmd /c C:/.../python.exe -m inspeximus.claude_code` fired on all five
     events in the same turn.

  3. THE WRITER WAS MISNAMED, WHICH IS THE ONE THAT COST THE MOST. `agent_id()` read the
     environment, and environment leaks downward: Codex launched from a Claude Code shell inherits
     CLAUDE_CODE_*, so a Codex hook stamped its writes `claude-code`. Codex HAD been writing before
     this was found. An hour went into hunting records that were already in the store under the
     wrong name.

WHAT THIS PROBE ASSERTS. Only (3) is testable without Codex installed, so that is what runs here,
with the fix that closes it: identity comes from `transcript_path` in the EVENT, which no child
process can inherit. (1) and (2) are recorded above as facts about the harness, and the plugin that
fixes them ships in `codex-marketplace/`.

It also asserts the fourth thing, which is what should have made all three cheap: a hook that
cannot write now SAYS SO. `_save()` records a failure in `_persist_error` and returns quietly so one
bad value cannot kill a running agent. Nothing read that field. Now `capture()` does, and reports it
to stderr and to `WRITE-FAILURES.log` beside the store, without ever raising.

CONTROLS, because each assertion can pass vacuously:
  - a writable store must record AND must not report a failure, or "reports a failure" is just
    "always writes to that log";
  - the reporter must not raise, or the fix trades a silent loss for a dead agent;
  - identity must fall back to the environment when no event is available, or a non-hook caller
    loses attribution entirely;
  - and with NEITHER an event nor an environment, the answer must be "unknown" rather than a
    default agent name, or detection is a constant wearing a verdict.
"""
import os
import sys
import json
import stat
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import claude_code as cc              # noqa: E402

CODEX_TP = r"C:\Users\D\.codex\sessions\2026\09\06\rollout-2026-09-06T18-36-26-01a077b3.jsonl"
CLAUDE_TP = r"C:\Users\D\.claude\projects\C--Users-D-agora\5d882efe.jsonl"


def identity_arm():
    """The event decides. The leak case is the one that was wrong in production."""
    cases = [
        ("codex transcript", {"transcript_path": CODEX_TP}, {}, "codex"),
        ("claude transcript", {"transcript_path": CLAUDE_TP}, {}, "claude-code"),
        ("codex transcript beats a leaked CLAUDE_CODE_ var",
         {"transcript_path": CODEX_TP}, {"CLAUDE_CODE_SESSION_ID": "leak"}, "codex"),
        ("no event, explicit override", None, {"INSPEXIMUS_AGENT_ID": "cursor"}, "cursor"),
        ("no event, codex env", None, {"CODEX_HOME": r"C:\x"}, "codex"),
        ("no event, claude env", None, {"CLAUDE_CODE_SESSION_ID": "a"}, "claude-code"),
        ("CONTROL neither event nor env", None, {}, "unknown"),
    ]
    saved = dict(os.environ)
    out = []
    try:
        for label, ev, env, want in cases:
            for k in list(os.environ):
                if k.startswith(("CLAUDE_CODE_", "CODEX_", "INSPEXIMUS_AGENT_ID")):
                    del os.environ[k]
            os.environ.update(env)
            got = cc.agent_id(ev)
            out.append({"case": label, "got": got, "want": want, "ok": got == want})
    finally:
        os.environ.clear()
        os.environ.update(saved)
    return out


def write_failure_arm():
    """A capture that cannot be persisted must leave evidence and must not raise."""
    d = tempfile.mkdtemp()
    saved = os.environ.get("INSPEXIMUS_CODING_STORE")
    os.environ["INSPEXIMUS_CODING_STORE"] = d
    ev = {"hook_event_name": "PostToolUse", "session_id": "probe", "cwd": d,
          "tool_name": "Bash", "tool_input": {"command": "echo probe"},
          "transcript_path": CODEX_TP}
    store = os.path.join(d, "coding_memory.json")
    log = os.path.join(d, "WRITE-FAILURES.log")
    try:
        cc.capture(ev)
        wrote = len(json.load(open(store, encoding="utf-8"))) if os.path.exists(store) else 0
        quiet = not os.path.exists(log)

        os.chmod(store, stat.S_IREAD)
        raised = None
        try:
            cc.capture(dict(ev, tool_input={"command": "echo denied"}))
        except Exception as e:                          # noqa: BLE001
            raised = "%s: %s" % (type(e).__name__, e)
        reported = os.path.exists(log)
        os.chmod(store, stat.S_IWRITE)
    finally:
        if saved is None:
            os.environ.pop("INSPEXIMUS_CODING_STORE", None)
        else:
            os.environ["INSPEXIMUS_CODING_STORE"] = saved
    return {"records_when_writable": wrote, "silent_when_writable": quiet,
            "reported_when_read_only": reported, "raised": raised}


def main():
    ident = identity_arm()
    print("  who wrote this")
    for c in ident:
        print("    %-7s %-48s -> %-13r want %r"
              % ("OK" if c["ok"] else "FAIL", c["case"], c["got"], c["want"]))

    w = write_failure_arm()
    print("\n  a capture that cannot be persisted")
    print("    writable store   -> %d record(s), failure log absent: %s"
          % (w["records_when_writable"], w["silent_when_writable"]))
    print("    read-only store  -> failure reported: %s, hook raised: %s"
          % (w["reported_when_read_only"], w["raised"] or "no"))

    bad = [c for c in ident if not c["ok"]]
    assert not bad, "agent identity wrong in %d case(s): %s" % (len(bad), bad)
    assert w["records_when_writable"] > 0, "the writable control captured nothing, so the arm is void"
    assert w["silent_when_writable"], "a healthy write reported a failure, so the report means nothing"
    assert w["reported_when_read_only"], "a lost capture left no evidence, which is the whole defect"
    assert w["raised"] is None, "the reporter raised; a silent loss must not become a dead agent"

    out = {"identity": ident, "write_failure": w,
           "harness_facts_2026_09_06": {
               "codex_reads_hooks_only_from_plugins": True,
               "codex_runs_hook_command_without_a_shell": True,
               "weeks_codex_contributed_nothing": 7,
               "records_in_shared_store": 32467}}
    path = os.path.splitext(os.path.abspath(__file__))[0] + ".result.json"
    open(path, "w", encoding="utf-8", newline="\n").write(json.dumps(out, indent=1))
    print("\n  all controls passed; receipt: %s" % os.path.basename(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
