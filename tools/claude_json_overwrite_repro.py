"""Does a running Claude Code process overwrite ~/.claude.json with its in-memory copy? (sandbox only)

    python tools/claude_json_overwrite_repro.py <workdir> <claude executable> <installer|claude-mcp-add> [--auth]

Measured 2026-09-27 on Windows: Claude Code 2.1.280 and 2.1.283, logged in or not, KEPT an entry the
installer wrote while the session ran. Logged in, 2.1.280 wrote the file again at exit and our entry
survived. Without a login Claude Code writes only at startup, so that run cannot answer the question;
the watcher below is what tells the two apart. tests/test_a_running_claude_code_keeps_our_entry.py runs
this when INSPEXIMUS_CLAUDE_REPRO_BIN is set. `--auth` copies the owner's Claude login file into the
sandbox for the run and deletes the sandbox afterwards; check that the access token is not about to
expire, so the sandbox session does not refresh it.

A watcher thread reads the file every 0.5 s for the whole run and logs every change, so the result can
tell "Claude wrote its copy back" from "Claude never wrote the file again". --auth copies the owner's
Claude login file into the sandbox's .claude folder for the run and deletes the sandbox afterwards; the
session then runs real turns on the cheapest model.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time

work, claude, mode = os.path.abspath(sys.argv[1]), sys.argv[2], sys.argv[3]
auth = "--auth" in sys.argv
shutil.rmtree(work, ignore_errors=True)
home = os.path.join(work, "home")
os.makedirs(os.path.join(home, ".claude"))
cfg = os.path.join(home, ".claude.json")
json.dump({"numStartups": 3, "hasCompletedOnboarding": True,
           "mcpServers": {"inspeximus": {"type": "stdio", "command": "uvx",
                                         "args": ["--from", "inspeximus[mcp]==3.14.0", "inspeximus-mcp"],
                                         "env": {"INSPEXIMUS_PATH": "C:/sandbox/store.json"}}}},
          open(cfg, "w", encoding="utf-8"), indent=2)
if auth:
    shutil.copy2(os.path.join(os.path.expanduser("~"), ".claude", ".credentials.json"),
                 os.path.join(home, ".claude", ".credentials.json"))
env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CLAUDE", "ANTHROPIC"))}
env.update(HOME=home, USERPROFILE=home, APPDATA=os.path.join(home, "AppData", "Roaming"),
           LOCALAPPDATA=os.path.join(home, "AppData", "Local"))
log, stop, phase = [], threading.Event(), {"now": "0 baseline"}


def snap():
    try:
        d = json.load(open(cfg, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    e = (d.get("mcpServers") or {}).get("inspeximus") or {}
    pin = next((a for a in e.get("args") or [] if "==" in a), None)
    return (os.path.getmtime(cfg), pin, (e.get("env") or {}).get("INSPEXIMUS_PATH"), d.get("numStartups"),
            len(json.dumps(d)))


def watch():
    last = None
    while not stop.is_set():
        s = snap()
        if s and s != last:
            log.append((time.strftime("%H:%M:%S"), phase["now"], s))
            last = s
        time.sleep(0.5)


threading.Thread(target=watch, daemon=True).start()
time.sleep(1)
phase["now"] = "1 Claude Code starting"
a = subprocess.Popen([claude, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                      "--model", "claude-haiku-4-5-20251001"],
                     cwd=home, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                     text=True, encoding="utf-8", errors="replace")


def say(text):
    a.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n")
    a.stdin.flush()


time.sleep(8)
if auth:
    phase["now"] = "1b first turn (before the write)"
    say("Reply with the single word: one")
    time.sleep(20)
phase["now"] = "2 installer write"
if mode == "installer":
    code = ("from inspeximus import install as I, __version__ as v; "
            "p = I.plan('claude', env={'INSPEXIMUS_PATH': r'C:\\sandbox\\store.json', 'INSPEXIMUS_SCOPE': None}); "
            "p['hooks'] = None; print(v, I.apply(p))")
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the checkout under test
    w = subprocess.run([sys.executable, "-c", code], cwd=home, env=dict(env, PYTHONPATH=repo),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
else:
    block = json.dumps({"type": "stdio", "command": "uvx", "args": ["--from", "inspeximus[mcp]==3.14.1", "inspeximus-mcp"],
                        "env": {"INSPEXIMUS_PATH": "C:\\sandbox\\store.json"}})
    subprocess.run([claude, "mcp", "remove", "inspeximus", "--scope", "user"], cwd=home, env=env, capture_output=True)
    w = subprocess.run([claude, "mcp", "add-json", "inspeximus", block, "--scope", "user"],
                       cwd=home, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
print("writer exit", w.returncode, (w.stdout or "").strip()[-160:], (w.stderr or "").strip()[-200:], flush=True)
time.sleep(2)
phase["now"] = "3 session activity after the write"
say("Reply with the single word: two")
time.sleep(25)
say("Reply with the single word: three")
time.sleep(25)
phase["now"] = "4 exit"
a.stdin.close()
try:
    out, err = a.communicate(timeout=90)
except subprocess.TimeoutExpired:
    a.kill()
    out, err = a.communicate()
time.sleep(2)
stop.set()
time.sleep(0.6)
results = [ln for ln in (out or "").splitlines() if '"type":"result"' in ln]
print("claude exit", a.returncode, "| results:", [json.loads(r).get("result") for r in results], flush=True)
for t, ph, s in log:
    print(f"  {t}  [{ph}]  mtime={s[0]:.3f} pin={s[1]} path={s[2]} numStartups={s[3]} bytes={s[4]}")
final = snap()
written = next((x[2][1] for x in log if x[1].startswith("2")), None)     # the pin right after the write
wrote_after = [x for x in log if x[1].startswith(("3", "4"))]
verdict = ("REVERTED to the stale copy" if final[1] and final[1].endswith("3.14.0") and written != final[1] else
           "kept the new entry" if final[1] and final[1] == written else "entry missing")
print("installer wrote pin:", written)
print("Claude wrote the file after the installer:", bool(wrote_after))
print("VERDICT:", verdict)
if auth:
    shutil.rmtree(work, ignore_errors=True)
    print("sandbox (with the login copy) deleted:", not os.path.exists(work))
# 0: Claude wrote after us and our entry held. 1: reverted. 3: Claude never wrote again, so this run cannot
# tell, which is what an unauthenticated session produces.
sys.exit(1 if verdict.startswith("REVERTED") or verdict == "entry missing" else 0 if wrote_after else 3)
