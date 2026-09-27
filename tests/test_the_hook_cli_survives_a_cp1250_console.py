"""The hook's command-line messages survive a console that cannot encode the store path.

`main()` reconfigures stdout with `errors="replace"` because a Windows console is cp1250 or cp1252,
and a character those codepages lack raised UnicodeEncodeError inside `print()`. The hook events
no longer depend on it: they print one JSON envelope, which is ASCII. The plain-text messages of
`--merge-store` and `--scrub-secrets` still print a path, and a path under a user or project name
in another script is ordinary. Without the guard the command dies with a traceback.

This test exists because the mutation "the recall hook loses its encoding guard" survived: its only
test exercised the JSON path, which cannot fail on encoding any more.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from inspeximus import _surface


def test_merge_store_prints_a_path_the_console_cannot_encode(tmp_path):
    proj = tmp_path / "記憶proj"                       # a project folder named in kanji
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    home = tmp_path / "home"
    home.mkdir()
    src = str(tmp_path / "old.json")
    old = _surface.open_store(src)
    old.remember("an old decision", key="decision::x", object="x")
    old.flush()
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("INSPEXIMUS_") and k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    env.update(PYTHONPATH=ROOT, USERPROFILE=str(home), HOME=str(home), INSPEXIMUS_NO_UPDATE_CHECK="1",
               PYTHONIOENCODING="cp1250")
    r = subprocess.run([sys.executable, "-m", "inspeximus.claude_code", "--merge-store", src],
                       cwd=str(proj), env=env, capture_output=True, timeout=120)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-400:]
    assert b"Dry run" in r.stdout, "the message naming the destination was not printed"
