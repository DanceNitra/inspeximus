"""3.19.1, F9: the secret rules run in linear time, and they find and mask exactly what 3.19.0 did.

THE DEFECT. Four rules of `inspeximus/_secrets.py` backtracked on crafted text with no secret in it:
`_ASSIGN` and `_FLAG` in cubic time ("token." * 667, 4,000 characters: 27 s), `_URL_CRED` and the JWT rule
in quadratic time (200,000 characters: 49 s and 14 s). The prompt and session hooks run `has_secret` over
every record of the project's store until the one-time secrets notice is written, and that store can be a
file a cloned repository ships. Measured on 3.19.0 through the real hook: one record of 6,000 characters
held the prompt hook for 81.6 s, past Claude Code's 30 s limit for that hook, which then drops its output.
The prompt daemon answers through the same `recall`, and the capture hook masks every Write and Bash text.

WHAT IS PINNED HERE.
  * Each slow rule, alone and through `has_secret` and `redact_secrets`, finishes its worst input in a child
    process under a deadline. 3.19.0 needs from 14 s to many minutes for these inputs; 3.19.1 about 0.01 s.
    The deadline is wall-clock with a margin of over 100 times, because `re` reports no step count.
  * The output is unchanged: `tests/fixtures/secret_rules_corpus.json` holds 1,746 strings with the
    `has_secret` result and the full `redact_secrets` output of 3.19.0. Every one must come out the same.
    A control checks that the corpus still exercises each rule, so an emptied fixture cannot pass.
  * The real prompt hook, the session hook and the daemon's `recall` finish on a 21,000-character crafted
    store record under a deadline.
  * The secrets notice writes its marker BEFORE it scans, so a scan that the host cancels is not repeated
    on every later prompt.
"""
import io
import json
import os
import subprocess
import sys
import time
from contextlib import redirect_stdout

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import inspeximus.claude_code as cc
from inspeximus import _secrets, _surface

CORPUS = os.path.join(ROOT, "tests", "fixtures", "secret_rules_corpus.json")

#: (rule, worst input found for it on 3.19.0, its time there for one search() call on the build machine).
#: Each input holds no secret, so every rule must scan all of it and fail.
WORST = [
    ("_ASSIGN", "'token.' * 3400", "4,000 chars: 27 s, cubic"),
    ("_ASSIGN", "'eyJ-' * 50000", "64,000 chars: 117 s"),
    ("_FLAG", "'-token' * 3400", "8,000 chars: 66 s, cubic"),
    ("_FLAG", "'eyJ-' * 50000", "64,000 chars: 55 s"),
    ("_URL_CRED", "'a' * 400000 + '://'", "200,000 chars: 49 s, quadratic"),
    ("_URL_CRED", "'token.' * 66000", "200,000 chars: 47 s"),
    ("jwt", "'eyJ-' * 100000", "200,000 chars: 14 s, quadratic"),
]
DEADLINE_S = 10.0          # child process, interpreter start and import included; 3.19.1 needs about 0.5 s


def _child(code, timeout=DEADLINE_S):
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    t = time.perf_counter()
    try:
        r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, time.perf_counter() - t
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    return r.stdout.decode("utf-8", "replace"), time.perf_counter() - t


@pytest.mark.parametrize("rule,expr,was", WORST, ids=[f"{w[0]}:{w[1]}" for w in WORST])
def test_each_slow_rule_finishes_its_worst_input_under_the_deadline(rule, expr, was):
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "from inspeximus import _secrets as S\n"
        "s = %s\n"
        "p = dict(S._PATTERNS)['jwt'] if %r == 'jwt' else getattr(S, %r)\n"
        "assert p.search(s) is None\n"
        "assert S.has_secret(s) is False\n"
        "out, counts = S.redact_secrets(s)\n"
        "assert out == s and counts == {}\n"
        "print('ok')\n"
    ) % (ROOT, expr, rule, rule)
    out, took = _child(code)
    assert out is not None, (f"{rule} on {expr} did not finish in {DEADLINE_S} s (3.19.0: {was}); "
                             "the rule backtracks again")
    assert out.strip() == "ok"


def _corpus():
    with open(CORPUS, encoding="utf-8") as fh:
        return json.load(fh)["rows"]


def test_the_corpus_still_exercises_every_rewritten_rule():
    """The control. Without it, a corpus that lost its positives would pass the comparison below."""
    rows = _corpus()
    kinds = {}
    for _s, _hit, _out, counts in rows:
        for k, n in counts.items():
            kinds[k] = kinds.get(k, 0) + n
    assert len(rows) >= 1500
    assert sum(1 for r in rows if r[1]) >= 1000 and sum(1 for r in rows if not r[1]) >= 300
    for kind in ("assignment", "url_password", "jwt"):
        assert kinds.get(kind, 0) >= 50, (kind, kinds)
    flags = [s for s, *_ in rows if _secrets._FLAG.search(s)]
    assert len(flags) >= 50, len(flags)
    # The two wrong first drafts of the rewrite each differ from 3.19.0 on these shapes.
    assert any("é" in s and r for s, r, *_ in rows), "no non-ASCII name case"
    assert any("-eyJhbGciOiJIUzI1." in s for s, *_ in rows), "no JWT glued to a dash"


def test_every_corpus_string_is_found_and_masked_exactly_as_in_3190():
    diffs = []
    for s, hit, out, counts in _corpus():
        got_out, got_counts = _secrets.redact_secrets(s)
        if _secrets.has_secret(s) != hit or got_out != out or got_counts != counts:
            diffs.append((s, hit, out, counts, _secrets.has_secret(s), got_out, got_counts))
    assert not diffs, f"{len(diffs)} differences from 3.19.0, first: {diffs[:3]!r}"


@pytest.mark.parametrize("text,masked", [
    ("x-auth-token value12345", "x-auth-token [REDACTED:assignment]"),
    ("export OPENAI_API_KEY=abcdefghijk", "export OPENAI_API_KEY=[REDACTED:assignment]"),
    ("git clone https://bob:hunter2x@github.com/a", "git clone https://bob:[REDACTED:url_password]@github.com/a"),
    ("1abc://u:passw0rd@h", "1abc://u:[REDACTED:url_password]@h"),
    ("-eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4", "-[REDACTED:jwt]"),
    ("x-eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4", "x-[REDACTED:jwt]"),
    ("étoken=abcdefgh", "étoken=abcdefgh"),           # no word boundary before the name, as in 3.19.0
])
def test_the_shapes_the_rewrite_had_to_keep(text, masked):
    assert _secrets.redact_secrets(text)[0] == masked


@pytest.fixture
def project(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    return str(proj)


def _plant(proj, text):
    path = _surface.coding_store_path(proj)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    m = _surface.open_store(path)
    m.remember(text)
    m.flush()


CRAFTED = "token." * 3500                       # 21,000 characters, no secret; 3.19.0: hours by the cubic fit


@pytest.mark.parametrize("event", ["UserPromptSubmit", "SessionStart"])
def test_the_real_hook_finishes_on_a_crafted_store_record(project, tmp_path, event):
    _plant(project, CRAFTED)
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    env.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(home), INSPEXIMUS_NO_UPDATE_CHECK="1",
               INSPEXIMUS_NO_NUDGE="1")
    ev = {"hook_event_name": event, "cwd": project, "prompt": "what about the build", "session_id": "s1",
          "source": "startup"}
    code = "import sys; sys.path.insert(0, %r); from inspeximus.claude_code import main; main()" % ROOT
    try:
        r = subprocess.run([sys.executable, "-c", code], input=json.dumps(ev).encode(), cwd=project, env=env,
                           capture_output=True, timeout=DEADLINE_S)
    except subprocess.TimeoutExpired:
        pytest.fail(f"the {event} hook did not finish in {DEADLINE_S} s on a 21,000-character record")
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert os.path.exists(os.path.join(cc._store_dir(project), "secrets_notice.json"))


def test_the_daemon_answer_finishes_on_a_crafted_store_record(project):
    """The prompt daemon answers through `claude_code.recall` with its held handle; that call runs the scan."""
    _plant(project, CRAFTED)
    code = (
        "import io, sys; sys.path.insert(0, %r)\n"
        "from contextlib import redirect_stdout\n"
        "import inspeximus.claude_code as cc\n"
        "m = cc._store(%r)\n"
        "with redirect_stdout(io.StringIO()):\n"
        "    cc.recall({'hook_event_name': 'UserPromptSubmit', 'cwd': %r, 'prompt': 'what about the build'},\n"
        "              store=lambda _c: m)\n"
        "print('ok')\n"
    ) % (ROOT, project, project)
    out, took = _child(code)
    assert out is not None, f"the daemon's recall did not finish in {DEADLINE_S} s on a 21,000-character record"
    assert out.strip().endswith("ok")


def test_the_notice_marker_is_written_before_the_scan(project, monkeypatch):
    """A scan the host cancels must not run again on the next prompt: the marker exists while it scans."""
    _plant(project, "sk-proj-FAKEa1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0")
    marker = os.path.join(cc._store_dir(project), "secrets_notice.json")
    seen = []
    real = cc._record_has_secret

    def watching(r):
        seen.append(os.path.exists(marker))
        return real(r)

    monkeypatch.setattr(cc, "_record_has_secret", watching)
    m = cc._store(project)
    first = cc._secrets_notice(project, m)
    assert seen and all(seen), "the scan ran before the marker existed"
    assert first and "1 stored record" in first
    assert json.load(open(marker, encoding="utf-8"))["found"] == 1
    assert cc._secrets_notice(project, m) is None, "the notice was shown twice"


def test_a_scan_cut_off_midway_is_not_repeated(project, monkeypatch):
    """The cancelled-hook case: the scan stops before it can write its count. The next call does not scan."""
    _plant(project, "plain text")
    m = cc._store(project)

    def cut(_r):
        raise KeyboardInterrupt                   # what a cancelled hook looks like from inside: the scan never returns

    monkeypatch.setattr(cc, "_record_has_secret", cut)
    with pytest.raises(KeyboardInterrupt):
        cc._secrets_notice(project, m)
    calls = []
    monkeypatch.setattr(cc, "_record_has_secret", lambda r: calls.append(r) or False)
    assert cc._secrets_notice(project, m) is None
    assert calls == [], "the cut-off scan ran again"
