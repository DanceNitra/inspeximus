"""3.14.2: the Claude Code hook masks secrets AT CAPTURE, for every write it makes.

`test_the_hook_never_stores_a_secret.py` holds the reproducers: a key exported in a Bash
command and a token written to `.env` reached the store and were injected back into the model. This
file pins the fix's CLASS, not those two instances:

  * every `remember` the hook makes goes through ONE masking chokepoint (checked on the source, so a
    new capture site cannot bypass it without failing here);
  * the mask runs BEFORE the excerpt is cut, so a key straddling the 200-character cut is not
    stored as a fragment too short to recognise;
  * a commit message is a capture too (`_capture_commit` stores subject, body and object);
  * a secrets file (`.env*`, `*.pem`, `*.key`, SSH private keys, `credentials*`) stores its path and
    nothing derived from its content, neither an excerpt nor a hash (a hash is a guess oracle);
  * ordinary commands are stored unchanged, so the mask is not a blanket;
  * records captured before 3.14.2 are found by `scrub_secrets`, erased only with `apply=True`
    (through `forget`, so each leaves a tombstone), and announced once by a one-line notice.
"""
import ast
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import inspeximus.claude_code as cc
from inspeximus import _surface

KEY = "sk-proj-FAKEa1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0"
KEY_HEAD = "sk-proj-FAKE"


@pytest.fixture
def project(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith("INSPEXIMUS_"):
            monkeypatch.delenv(k, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("INSPEXIMUS_NO_NUDGE", "1")
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    for k, v in (("user.email", "t@example.org"), ("user.name", "t")):
        subprocess.run(["git", "-C", str(proj), "config", k, v], check=True)
    return str(proj)


def _bytes(proj):
    out = b""
    for dp, _, fs in os.walk(os.path.join(proj, ".inspeximus")):
        for f in fs:
            with open(os.path.join(dp, f), "rb") as fh:
                out += fh.read()
    return out


def _texts(proj):
    return [r.get("text") or "" for r in _surface.open_store(_surface.coding_store_path(proj)).items]


def _bash(proj, command):
    cc.capture({"cwd": proj, "session_id": "s1", "hook_event_name": "PostToolUse", "tool_name": "Bash",
                "tool_input": {"command": command}})


def _write(proj, name, content):
    cc.capture({"cwd": proj, "session_id": "s1", "hook_event_name": "PostToolUse", "tool_name": "Write",
                "tool_input": {"file_path": os.path.join(proj, name), "content": content}})


def test_every_hook_write_goes_through_the_masking_chokepoint():
    """Structural: a `remember(` call in claude_code.py outside the chokepoint is a capture that
    skips the mask. The first version of the hook had three, and each was a separate leak."""
    tree = ast.parse(open(cc.__file__, encoding="utf-8").read())
    owner = {}
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef):
            for node in ast.walk(fn):
                owner.setdefault(id(node), fn.name)
    calls = [(node.lineno, owner.get(id(node))) for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr == "remember"]
    if not calls:
        pytest.fail("control: found no remember() call at all, so the scan is not reading the hook")
    outside = [c for c in calls if c[1] != "_remember_masked"]
    assert not outside, f"remember() called outside the masking chokepoint at {outside}"


def test_a_key_across_the_excerpt_cut_is_not_stored_as_a_fragment(project):
    """The key starts 14 characters before the 200-character cut and carries no secret-sounding
    NAME, so only its prefix identifies it. Masked after the cut, the 14-character head is too
    short for the prefix rule and lands in the store; masked before, nothing of it does."""
    prefix = "echo " + "x" * 163 + " && python run.py "
    command = prefix + KEY
    if len(prefix) != 186:
        pytest.fail(f"control: the key must start at 186, it starts at {len(prefix)}")
    _bash(project, command)
    if not any(t.startswith("ran: echo xxx") for t in _texts(project)):
        pytest.fail("control: the command was not captured")
    assert KEY_HEAD.encode() not in _bytes(project), "a fragment of the key was stored at the cut"


def test_a_commit_message_is_masked_like_any_other_capture(project):
    with open(os.path.join(project, "a.txt"), "w") as fh:
        fh.write("x")
    subprocess.run(["git", "-C", project, "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", project, "commit", "-q", "-m", f"rotate the key to {KEY}",
                    "-m", f"because OPENAI_API_KEY={KEY} leaked"], check=True)
    _bash(project, "git commit -m 'rotate the key'")
    if not any(t.startswith("DECISION: rotate the key") for t in _texts(project)):
        pytest.fail("control: the commit was not captured as a decision")
    assert KEY_HEAD.encode() not in _bytes(project), "the key in the commit message reached the store"


@pytest.mark.parametrize("name", [".env", ".env.local", "server.pem", "deploy.key", "id_rsa",
                                  "credentials.json"])
def test_a_secrets_file_stores_its_path_and_nothing_derived_from_its_content(project, name):
    """A hash of the content is a guess-confirmation oracle: whoever holds the store hashes a
    candidate `.env` and compares. A salt kept beside the store does not help, because the holder
    has it too. So two writes of different content, one of them a weak one-line password, must
    leave byte-identical text and object, and no sha256 of either content may appear anywhere."""
    import hashlib
    bodies = ["SOME_VALUE=plain-looking-but-private-0123456789\n", "PASSWORD=hunter2\n"]
    rows = []
    for body in bodies:
        _write(project, name, body)
        m = _surface.open_store(_surface.coding_store_path(project))
        mine = [r for r in m.items if (r.get("key") or "") == f"file:{name}" and r.get("status") == "active"]
        if len(mine) != 1:
            pytest.fail(f"control: the write to {name} did not land as the one current record")
        rows.append((mine[0].get("text"), mine[0].get("object")))
    raw = _bytes(project)
    assert b"plain-looking-but-private" not in raw and b"hunter2" not in raw, f"an excerpt of {name} was stored"
    assert rows[0] == rows[1], f"the capture of {name} varies with its content: {rows!r}"
    for body in bodies:
        h = hashlib.sha256(body.encode()).hexdigest()
        assert h[:12].encode() not in raw, f"a sha256 of the {name} content is in the store"


def test_an_ordinary_command_and_file_are_stored_unchanged(project):
    cmd = "git commit -m 'raise max_tokens to 16000' && pytest -k token_budget -n 4"
    _bash(project, cmd)
    _write(project, "notes.md", "the password policy requires 12 characters")
    texts = _texts(project)
    assert f"ran: {cmd}" in texts
    assert any("the password policy requires 12 characters" in t for t in texts)


def _plant_old_secrets(proj):
    """What a pre-3.14.2 hook left behind: written straight to the store, around the mask."""
    m = _surface.open_store(_surface.coding_store_path(proj))
    m.remember(f"ran: export OPENAI_API_KEY={KEY}", key="cmd:old1", object="old", tags=["bash"])
    m.remember(".env :: current state -> GITHUB_TOKEN=ghp_FAKE0123456789abcdefghijklmnopqrstuv",
               key="file:.env", object="old", tags=["file", "edit"])
    m.remember("ran: ls -la", key="cmd:old2", object="ls", tags=["bash"])
    m.flush()


def test_scrub_is_a_dry_run_until_told_otherwise_then_erases_through_forget(project):
    _plant_old_secrets(project)
    dry = cc.scrub_secrets(cwd=project)
    assert dry["found"] == 2 and dry["applied"] is False
    assert KEY_HEAD.encode() in _bytes(project), "a dry run changed the store"
    done = cc.scrub_secrets(cwd=project, apply=True)
    assert done["applied"] is True and done["erased"] == 2
    m = _surface.open_store(_surface.coding_store_path(project))
    assert len(m._tombstones) == 2, "the scrub did not go through forget, so it left no tombstones"
    assert KEY_HEAD.encode() not in _bytes(project)
    assert any("ls -la" in t for t in _texts(project)), "the scrub erased a record with no secret"
    assert cc.scrub_secrets(cwd=project)["found"] == 0


def test_the_notice_is_one_line_shown_once(project):
    _plant_old_secrets(project)

    def start(sid):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cc.session_start({"cwd": project, "session_id": sid, "hook_event_name": "SessionStart",
                              "source": "startup"})
        out = buf.getvalue()
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else ""

    first = start("s1")
    lines = [ln for ln in first.splitlines() if "--scrub-secrets" in ln]
    assert len(lines) == 1 and "2 " in lines[0], f"expected one notice naming 2 records: {lines!r}"
    assert KEY_HEAD not in first
    assert "--scrub-secrets" not in start("s2"), "the notice was shown twice"


def test_the_scrub_command_runs_from_the_command_line(project):
    _plant_old_secrets(project)
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    env.update(PYTHONPATH=ROOT, INSPEXIMUS_NO_UPDATE_CHECK="1")
    r = subprocess.run([sys.executable, "-m", "inspeximus.claude_code", "--scrub-secrets"],
                       cwd=project, env=env, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout.split("\n\n")[0])["found"] == 2
    assert KEY_HEAD not in r.stdout, "the scrub report printed a secret"


POSITIVES = {
    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----",
    "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    "api_key": "use sk-ant-FAKE0123456789abcdefghijkl here",
    "stripe": "sk_live_FAKE0123456789abcdef",
    "github": "ghp_FAKE0123456789abcdefghijklmnopqrstuv",
    "github_pat": "github_pat_FAKE0123456789abcdefghijklmnopq",
    "gitlab": "glpat-FAKE0123456789abcdefgh",
    "aws": "AKIAFAKE0123456789AB",
    "slack": "xoxb-FAKE-0123456789",
    "google": "AIzaFAKE0123456789abcdefghijklmnopqrstu",
    "huggingface": "hf_FAKE0123456789abcdefghijklmnopqrstu",
    "npm": "npm_FAKE0123456789abcdefghijklmnopqrstuv",
    "webhook": "https://hooks.slack.com/services/T0000/B0000/FAKE0123456789abcdef",
    "bearer": "Authorization: Bearer FAKE0123456789abcdefXYZ",
    "url_password": "postgres://admin:FAKEpassw0rd@db.internal/app",
    "assignment": "DB_PASSWORD='FAKEhunter2hunter2'",
    "flag": "mytool --api-key FAKEabcd1234efgh5678",
}


@pytest.mark.parametrize("kind", sorted(POSITIVES))
def test_the_fast_detector_sees_every_kind_the_mask_masks(kind):
    """`has_secret` skips strings with none of its trigger substrings. A pattern added without its
    trigger would be masked at capture and missed by the scan and the notice; this catches that."""
    from inspeximus._secrets import has_secret, redact_secrets
    sample = POSITIVES[kind]
    if redact_secrets(sample)[0] == sample:
        pytest.fail(f"control: the mask does not recognise the {kind} example, so it proves nothing")
    assert has_secret(sample), f"has_secret misses a {kind} that redact_secrets masks"


def test_the_fast_detector_agrees_with_the_mask_on_a_random_corpus():
    import random
    import string
    from inspeximus._secrets import has_secret, redact_value
    rnd = random.Random(20260927)
    alpha = string.ascii_letters + string.digits + "_-=:/ @.'\"$%{}"
    tails = ["", " api_key=", " token: ", " sk-", " --password ", " AKIA", " Bearer ", " eyJ", " ://u:"]
    corpus = ["".join(rnd.choice(alpha) for _ in range(rnd.randint(5, 60))) + rnd.choice(tails)
              + "".join(rnd.choice(alpha) for _ in range(rnd.randint(0, 60))) for _ in range(5000)]
    positives = sum(1 for c in corpus if redact_value(c)[1])
    if positives < 500:
        pytest.fail(f"control: only {positives} positives, too few for the comparison to mean anything")
    bad = [c for c in corpus if has_secret(c) != bool(redact_value(c)[1])]
    assert not bad, f"has_secret and the mask disagree on {len(bad)} strings, e.g. {bad[:2]!r}"


@pytest.mark.parametrize("field", ["text", "object", "meta", "source"])
def test_the_chokepoint_masks_each_field_on_its_own(project, field):
    """Today every call site masks before it cuts, so the chokepoint's own pass is never the only
    thing between a key and the disk -- which means removing it would survive every test above.
    This calls it directly with the key in ONE field at a time, so each of its lines has a test."""
    m = _surface.open_store(_surface.coding_store_path(project))
    kw = {"key": "cmd:direct", "tags": ["bash"]}
    text = "ran: a direct write"
    if field == "text":
        text = f"ran: export OPENAI_API_KEY={KEY}"
    elif field == "object":
        kw["object"] = KEY
    elif field == "meta":
        kw["meta"] = {"note": [f"token {KEY}"]}
    else:
        kw["source"] = {"doc": f"https://u:{KEY}@example.org/x"}
    cc._remember_masked(m, text, **kw)
    m.flush()
    if not any(t.startswith("ran: ") for t in _texts(project)):
        pytest.fail("control: the direct write did not land")
    assert KEY_HEAD.encode() not in _bytes(project), f"the chokepoint stored the key from {field}"


@pytest.mark.parametrize("where", ["subject", "body"])
def test_a_key_across_a_commit_cut_is_not_stored_as_a_fragment(project, where):
    """The commit capture cuts twice: the subject to 80 characters for the object, the body to 600
    for the text. A key whose head sits just before either cut, with no secret-sounding name, is
    recognisable only whole, so each field must be masked before it is cut."""
    with open(os.path.join(project, "a.txt"), "w") as fh:
        fh.write("x")
    subprocess.run(["git", "-C", project, "add", "a.txt"], check=True)
    if where == "subject":
        subject, body = "y" * 66 + " " + KEY, "no body worth reading"
        head_at = len(subject) - len(KEY)
        if head_at != 67:
            pytest.fail(f"control: the key must start at 67 of the subject, it starts at {head_at}")
    else:
        subject, body = "a commit with a long body", "z" * 586 + " " + KEY
        if len(body) - len(KEY) != 587:
            pytest.fail("control: the key must start at 587 of the body")
    subprocess.run(["git", "-C", project, "commit", "-q", "-m", subject, "-m", body], check=True)
    _bash(project, "git commit -m x")
    if not any(t.startswith("DECISION: ") for t in _texts(project)):
        pytest.fail("control: the commit was not captured as a decision")
    assert KEY_HEAD.encode() not in _bytes(project), f"a fragment of the key was stored at the {where} cut"


@pytest.mark.parametrize("kind", sorted(POSITIVES))
def test_a_masked_string_holds_no_secret_and_masking_again_changes_nothing(kind):
    """The scan and `--scrub-secrets` read records the fixed hook wrote. If a mask were itself
    recognised as a secret, every record masked since 3.14.2 would be counted, announced and
    erased as a leak. So the mask must be a fixed point: no secret in it, and a second pass
    changes nothing."""
    from inspeximus._secrets import has_secret, redact_secrets
    once, n = redact_secrets(POSITIVES[kind])
    if not n:
        pytest.fail(f"control: the {kind} example was not masked, so this proves nothing")
    assert not has_secret(once), f"the {kind} mask is itself detected as a secret: {once!r}"
    assert redact_secrets(once) == (once, {}), f"masking the {kind} mask again changed it"
