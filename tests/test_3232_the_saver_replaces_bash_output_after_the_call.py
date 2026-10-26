"""3.23.1: the context saver replaces a Bash result AFTER the call, through PostToolUse `updatedToolOutput`.

The command runs as the agent wrote it, so no permission mode refuses it; Claude Code applies the replacement before
the result is sent to the agent. These tests run the installed hook entry point (`python -m inspeximus.claude_code`)
on PostToolUse events shaped like the ones Claude Code sent on 2026-10-26, and read what it printed: that envelope is
the whole of what the agent's view depends on.

WHERE THE EVIDENCE STOPS. No test here can run Claude Code itself, so none can see the agent's side directly. What
Claude Code does with an envelope was measured live on 2026-10-26: the agent received only the replacement, in auto
permission mode; a replacement of a different shape (a plain string) was ignored without any signal and the original
was shown; replacements of 1,500 and 12,000 characters arrived whole, and nothing larger was measured. So the shape
test below is exact on purpose: a partial object is the failure the platform would hide, and it must fail here."""
import hashlib
import json
import os
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_3230_the_saver_captures_bash_output_in_the_hook import _big, _config, home  # noqa: E402,F401

from inspeximus import context_saver as cs  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ev(stdout, command="git log --stat", description="find the section that holds zebra-quartz", **resp):
    r = {"stdout": stdout, "stderr": "", "interrupted": False, "isImage": False, "noOutputExpected": False}
    r.update(resp)
    return {"hook_event_name": "PostToolUse", "tool_name": "Bash", "permission_mode": "auto", "session_id": "t",
            "tool_input": {"command": command, "description": description}, "tool_response": r}


def _hook(home, tmp_path, ev):
    """The hook as Claude Code runs it: a fresh process, the event on stdin. Returns (stdout, returncode)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    env.update(INSPEXIMUS_KEY_HOME=str(home), INSPEXIMUS_NO_UPDATE_CHECK="1", PYTHONPATH=REPO,
               PYTHONIOENCODING="utf-8", HOME=str(tmp_path / "h"), USERPROFILE=str(tmp_path / "h"))
    os.makedirs(str(tmp_path / "h"), exist_ok=True)
    p = subprocess.run([sys.executable, "-m", "inspeximus.claude_code"], input=json.dumps(ev).encode("utf-8"),
                       capture_output=True, env=env, cwd=str(tmp_path / "proj"), timeout=120)
    return p.stdout.decode("utf-8"), p.returncode


def _envelope(out):
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 1, out
    return json.loads(lines[0])


def _platform_file(tmp_path, text):
    d = tmp_path / "claude" / "projects" / "p" / "s" / "tool-results"
    d.mkdir(parents=True)
    p = d / "b40tloxna.txt"
    p.write_bytes(text.encode("utf-8"))
    return p


def _state(path):
    st = os.stat(str(path))
    return st.st_size, st.st_mtime_ns, hashlib.sha256(open(str(path), "rb").read()).hexdigest()


def test_the_replacement_has_exactly_the_bash_result_shape(home, tmp_path):
    # A wrong shape is ignored by Claude Code without a signal, so a partial object and no object look the same from
    # the agent's side. Only this check tells them apart.
    _config(home, {"saver": {"hook": True}})
    out, rc = _hook(home, tmp_path, _ev(_big("the answer is here: zebra-quartz")[:20000]))
    assert rc == 0
    rep = _envelope(out)["hookSpecificOutput"]
    assert rep["hookEventName"] == "PostToolUse"
    got = rep["updatedToolOutput"]
    assert [(k, type(got[k])) for k in sorted(got)] == sorted(cs.BASH_RESULT_SHAPE), got.keys()
    assert "zebra-quartz" in got["stdout"] and "bytes in, digest" in got["stdout"]
    assert len(got["stdout"]) < 20000 / 2


def test_an_output_shown_whole_is_kept_for_the_follow_up_read(home, tmp_path):
    _config(home, {"saver": {"hook": True}})
    text = _big("the answer is here: zebra-quartz")[:20000]
    out, _rc = _hook(home, tmp_path, _ev(text))
    shown = _envelope(out)["hookSpecificOutput"]["updatedToolOutput"]["stdout"]
    path = shown.rsplit("full output: ", 1)[1].strip()
    assert os.path.dirname(os.path.realpath(path)) == os.path.realpath(cs.capture_dir())
    assert open(path, encoding="utf-8").read() == text
    (rec,) = [json.loads(ln) for n in os.listdir(cs.stats_dir())
              for ln in open(os.path.join(cs.stats_dir(), n), encoding="utf-8")]
    assert rec["source"] == "stdout" and rec["platform_shown"] == rec["bytes_in"] == len(text.encode())
    assert rec["bytes_out"] == len(shown.encode("utf-8")) and rec["mode"] == "digest" and rec["ranking"] == "lexical"


def test_a_persisted_output_is_read_from_the_platforms_file_and_never_written(home, tmp_path):
    _config(home, {"saver": {"hook": True}})
    text = _big("the answer is here: zebra-quartz") * 2       # past the 30,000-character cut
    assert len(text) > cs.PLATFORM_CUT_CHARS
    pf = _platform_file(tmp_path, text)
    before, names = _state(pf), sorted(os.listdir(str(pf.parent)))
    head = text[:cs.PLATFORM_CUT_CHARS]
    out, _rc = _hook(home, tmp_path, _ev(head, persistedOutputPath=str(pf), persistedOutputSize=len(text.encode())))
    shown = _envelope(out)["hookSpecificOutput"]["updatedToolOutput"]["stdout"]
    assert shown.rstrip().endswith("full output: %s" % pf), shown[-200:]
    assert _state(pf) == before and sorted(os.listdir(str(pf.parent))) == names
    assert not os.path.isdir(cs.capture_dir()) or not os.listdir(cs.capture_dir()), "a copy of the platform's file"
    (rec,) = [json.loads(ln) for n in os.listdir(cs.stats_dir())
              for ln in open(os.path.join(cs.stats_dir(), n), encoding="utf-8")]
    assert rec["source"] == "persisted" and rec["bytes_in"] == len(text.encode())
    assert rec["platform_shown"] == cs.PLATFORM_PREVIEW_BYTES and rec["stdout_chars"] == cs.PLATFORM_CUT_CHARS


@pytest.mark.parametrize("case", ["off", "truthy", "escape", "short", "image", "interrupted", "other-tool", "not-a-platform-file",
                                  "bad-utf8"])
def test_these_results_are_left_to_the_platform(home, tmp_path, case, monkeypatch):
    _config(home, {"saver": {"hook": "yes" if case == "truthy" else case != "off"}})
    text = _big("zebra-quartz")[:20000]
    ev = _ev(text)
    if case == "escape":
        monkeypatch.setenv("INSPEXIMUS_SAVER_HOOK", "0")
    elif case == "short":
        ev = _ev("x" * (cs.PASSTHROUGH_BYTES - 1))
    elif case in ("image", "interrupted"):
        ev["tool_response"]["isImage" if case == "image" else "interrupted"] = True
    elif case == "other-tool":
        ev["tool_name"] = "Read"
    elif case == "not-a-platform-file":
        other = tmp_path / "elsewhere.txt"
        other.write_text(text * 2, encoding="utf-8")
        ev = _ev(text, persistedOutputPath=str(other))
    elif case == "bad-utf8":
        bad = _platform_file(tmp_path, "x")
        bad.write_bytes(b"\xff\xfe" * 40000)
        ev = _ev(text, persistedOutputPath=str(bad))
    env_escape = os.environ.get("INSPEXIMUS_SAVER_HOOK")
    assert cs.post_tool_use_replacement(ev) is None
    if case != "escape":
        out, rc = _hook(home, tmp_path, ev)
        assert out.strip() == "" and rc == 0, out
    assert env_escape in (None, "0")


def test_a_failure_prints_nothing_so_the_platforms_result_stands(home, tmp_path, monkeypatch):
    _config(home, {"saver": {"hook": True}})
    monkeypatch.setattr(cs, "save", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert cs.post_tool_use_replacement(_ev(_big("zebra-quartz")[:20000])) is None
    (rec,) = [json.loads(ln) for n in os.listdir(cs.stats_dir())
              for ln in open(os.path.join(cs.stats_dir(), n), encoding="utf-8")]
    assert rec["mode"] == "raw" and rec["why"] == "RuntimeError" and rec["bytes_out"] == 0


def test_the_sweep_never_removes_a_file_in_a_tool_results_directory(home, tmp_path, monkeypatch):
    pf = _platform_file(tmp_path, "x")
    old = pf.parent / "capture-old.txt"                    # a name the sweep would otherwise take
    old.write_text("x", encoding="utf-8")
    t = time.time() - 10 * 86400
    os.utime(str(old), (t, t))
    monkeypatch.setattr(cs, "capture_dir", lambda: str(pf.parent))
    assert cs.sweep_captures() == [] and old.exists() and pf.exists()


def test_a_passthrough_call_sweeps_old_captures_and_keeps_one_a_follow_up_can_read(home, tmp_path):
    # Until this fix the hook swept only after a replacement, so a machine whose outputs all passed through never
    # swept: 328 zero-byte leftovers sat in saver-out on PC1 (2026-10-26) and read as a false gap.
    _config(home, {"saver": {"hook": True}})
    now = time.time()
    stale = cs.new_capture_path()                          # a leftover, past the keep
    os.utime(stale, (now - cs.CAPTURE_KEEP_S - 60,) * 2)
    target = cs.new_capture_path()                         # a digest's file, an hour inside the keep
    with open(target, "wb") as f:
        f.write(b"the whole output a follow-up read asks for")
    os.utime(target, (now - cs.CAPTURE_KEEP_S + 3600,) * 2)
    assert cs.post_tool_use_replacement(_ev("x" * (cs.PASSTHROUGH_BYTES - 1))) is None
    # Defends the fix: a passthrough call sweeps. Fails on the code that swept only after a replacement.
    assert not os.path.exists(stale), "a passthrough call left a capture past the keep"
    # Defends against widening it: a capture inside the keep is a follow-up read's target and must survive.
    assert open(target, "rb").read() == b"the whole output a follow-up read asks for"


@pytest.mark.parametrize("which", ["sweep_captures", "sweep_stats"])
def test_a_sweep_that_fails_never_costs_the_output(home, tmp_path, monkeypatch, which):
    """Each sweep raises in turn, and each case fails when the hook's try/except around the sweeps is removed. The
    sweep_stats case also fails on f270af09, where a stray unguarded sweep_stats() sat inside the except block and
    ran only after the guarded pair had raised."""
    _config(home, {"saver": {"hook": True}})
    monkeypatch.setattr(cs, which, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert cs.post_tool_use_replacement(_ev("x" * (cs.PASSTHROUGH_BYTES - 1))) is None
    rep = cs.post_tool_use_replacement(_ev(_big("the answer is here: zebra-quartz")[:20000]))
    assert "zebra-quartz" in rep["hookSpecificOutput"]["updatedToolOutput"]["stdout"]


def test_a_read_of_a_capture_or_a_platform_file_is_recorded_as_a_follow_up(home, tmp_path):
    _config(home, {"saver": {"hook": True}})
    text = _big("zebra-quartz")[:20000]
    cs.post_tool_use_replacement(_ev(text, command="sed -n 1,400p ~/.claude/projects/p/s/tool-results/b40tloxna.txt"))
    cs.post_tool_use_replacement(_ev(text, command="git log"))
    recs = [json.loads(ln) for n in os.listdir(cs.stats_dir())
            for ln in open(os.path.join(cs.stats_dir(), n), encoding="utf-8")]
    assert [r["follow_up"] for r in recs] == [True, False]


def test_the_pre_tool_use_hook_no_longer_rewrites_a_bash_call(home, tmp_path):
    _config(home, {"saver": {"hook": True}})
    ev = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "permission_mode": "default",
          "tool_input": {"command": "git log", "description": "x"}}
    out, rc = _hook(home, tmp_path, ev)
    assert "updatedInput" not in out and rc == 0, out


def test_a_record_of_the_replacement_holds_no_command_output_intent_or_path(home, tmp_path):
    _config(home, {"saver": {"hook": True}})
    mark = "ISXMARK%d" % os.getpid()
    text = _big("zebra-quartz %s-output" % mark) * 2
    pf = _platform_file(tmp_path, text)
    ev = _ev(text[:cs.PLATFORM_CUT_CHARS], command="echo %s-command" % mark, description=mark + "-intent",
             persistedOutputPath=str(pf), persistedOutputSize=len(text.encode()))
    assert cs.post_tool_use_replacement(ev) is not None
    raw = "".join(open(os.path.join(cs.stats_dir(), n), encoding="utf-8").read() for n in os.listdir(cs.stats_dir()))
    assert json.loads(raw)["intent_chars"] == len(mark + "-intent"), "no proof the record saw this intent"
    for leak in (mark, "b40tloxna", "tool-results"):
        assert leak not in raw, leak
