"""Clean-install check of inspeximus from PyPI, the way a new user installs it for Claude Code.

    python tools/clean_install_check.py <version|latest> <workdir> [--expect-fail]

The cross-platform form of the tester kit's pip route (agora_output/tester_kit/check_route.py), for
CI on Linux and macOS, where the Windows kit cannot run. No Claude credentials, no claude CLI.

1. A fresh venv from this interpreter. HOME and every config and temp directory point into
   <workdir>; every INSPEXIMUS_*, CLAUDE*, PIP_*, UV_* and PYTHON* variable is removed.
2. `pip install "inspeximus[mcp]"` from PyPI (`==<version>` unless the version is `latest`).
3. `inspeximus install --ide claude` in a fresh git repository, as a user would run it.
4. Process 1: start the MCP server exactly as the installer wrote it into the sandboxed
   ~/.claude.json, do the stdio handshake, call remember_decision.
5. Process 2: run the SessionStart hook exactly as written into ~/.claude/settings.json and require
   the decision in its stdout; then start a fresh MCP server and require recall("indentation") to
   return it.

Prints one JSON verdict and writes <workdir>/result.json. Exit 0 when the verdict is what was asked
for: PASS normally, FAIL with --expect-fail (the control against a release known to lose the decision
between processes). A control that passes is a failure, because then the check measured nothing.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

VER, WORK = sys.argv[1], os.path.abspath(sys.argv[2])
EXPECT_FAIL = "--expect-fail" in sys.argv
WIN = os.name == "nt"
shutil.rmtree(WORK, ignore_errors=True)
home, proj = os.path.join(WORK, "home"), os.path.join(WORK, "proj")
for d in (home, os.path.join(home, "AppData", "Roaming"), os.path.join(home, "AppData", "Local"),
          os.path.join(home, ".config"), os.path.join(WORK, "tmp"), os.path.join(proj, "src")):
    os.makedirs(d, exist_ok=True)
subprocess.run(["git", "init", "-q", proj], check=True)

env = {k: v for k, v in os.environ.items()
       if not k.upper().startswith(("INSPEXIMUS_", "CLAUDE", "PIP_", "UV_", "VIRTUAL_ENV", "PYTHON"))}
env.update(HOME=home, USERPROFILE=home, APPDATA=os.path.join(home, "AppData", "Roaming"),
           LOCALAPPDATA=os.path.join(home, "AppData", "Local"), XDG_CONFIG_HOME=os.path.join(home, ".config"),
           TEMP=os.path.join(WORK, "tmp"), TMP=os.path.join(WORK, "tmp"), TMPDIR=os.path.join(WORK, "tmp"),
           PYTHONIOENCODING="utf-8", PIP_NO_CACHE_DIR="1", UV_CACHE_DIR=os.path.join(WORK, "uvcache"))
log, checks = [], {}


def run(cmd, cwd=None, inp=None, extra_env=None, shell=False, timeout=600):
    e = dict(env, **(extra_env or {}))
    t = time.time()
    p = subprocess.run(cmd, cwd=cwd, input=inp, capture_output=True, text=True, env=e, shell=shell,
                       encoding="utf-8", errors="replace", timeout=timeout)
    log.append({"cmd": cmd if isinstance(cmd, str) else " ".join(cmd), "rc": p.returncode,
                "s": round(time.time() - t, 1), "out": p.stdout[-2000:], "err": p.stderr[-2000:]})
    return p


venv = os.path.join(WORK, "venv")
subprocess.run([sys.executable, "-m", "venv", venv], check=True)
bindir = os.path.join(venv, "Scripts" if WIN else "bin")
py = os.path.join(bindir, "python.exe" if WIN else "python")
env["PATH"] = bindir + os.pathsep + env["PATH"]          # an activated venv
checks["uv_on_path"] = bool(shutil.which("uvx", path=env["PATH"]))
spec = "inspeximus[mcp]" + ("" if VER == "latest" else "==%s" % VER)
pip = run([py, "-m", "pip", "install", "-q", "--index-url", "https://pypi.org/simple", spec])
checks["pip_rc"] = pip.returncode
# From the sandbox, never from the checkout: `python -c` puts the working directory first on sys.path,
# and the repository root holds its own inspeximus/ package, which reported 3.13.0 for a 3.9.5 install.
got = run([py, "-c", "import inspeximus;print(inspeximus.__version__)"], cwd=WORK).stdout.strip()
checks["installed_version"] = got
inst = run([os.path.join(bindir, "inspeximus.exe" if WIN else "inspeximus"), "install", "--ide", "claude"],
           cwd=proj)
checks["install_rc"] = inst.returncode
checks["install_out"] = (inst.stdout + inst.stderr)[-1500:]
cfg_path = os.path.join(home, ".claude.json")
set_path = os.path.join(home, ".claude", "settings.json")
cfg = json.load(open(cfg_path, encoding="utf-8")) if os.path.exists(cfg_path) else {}
settings = json.load(open(set_path, encoding="utf-8")) if os.path.exists(set_path) else {}
srv = (cfg.get("mcpServers") or {}).get("inspeximus")
checks["mcp_block_written"] = bool(srv)
checks["mcp_block"] = srv
checks["hook_events_written"] = sorted((settings.get("hooks") or {}).keys())

# Claude Code starts both MCP servers and hooks with CLAUDE_PROJECT_DIR = the launch dir.
launch = os.path.join(proj, "src")
cc_env = {"CLAUDE_PROJECT_DIR": launch}


def mcp_session(calls):
    """One MCP server process: initialize, initialized, then the given tools/call list."""
    if not srv:
        return None, "no mcpServers.inspeximus block"
    # Claude Code expands ${VAR} in an MCP server block before it starts the server; do the same.
    base = dict(env, **cc_env)
    x = lambda v: re.sub(r"\$\{(\w+)\}", lambda m: base.get(m.group(1), m.group(0)), v)  # noqa: E731
    cmd = [x(srv["command"])] + [x(a) for a in srv.get("args") or []]
    e = dict(base, **{k: x(v) for k, v in (srv.get("env") or {}).items()})
    try:
        p = subprocess.Popen(cmd, cwd=launch, env=e, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    except OSError as ex:
        # A server that cannot even start is the route failing, not the check: 3.9.5 wrote a bare `uvx`
        # launch command, so on a machine without uv this is where it stops.
        return {"error": "the MCP server could not start: %r (command %s)" % (ex, cmd[0])}, None
    out = {}
    try:
        def send(m):
            p.stdin.write(json.dumps(m) + "\n")
            p.stdin.flush()

        def recv(want):
            while True:
                line = p.stdout.readline()
                if not line:
                    raise RuntimeError("server closed stdout; stderr: " + p.stderr.read()[-1500:])
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if m.get("id") == want:
                    return m
        send({"jsonrpc": "2.0", "id": 0, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                         "clientInfo": {"name": "clean-install-check", "version": "1"}}})
        out["initialize"] = recv(0).get("result", {}).get("serverInfo")
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        for i, (name, args) in enumerate(calls, 1):
            send({"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args}})
            r = recv(i)
            out[name] = r.get("result") or r.get("error")
    except Exception as ex:  # noqa: BLE001 -- recorded, and the verdict fails on it
        out["error"] = repr(ex)
    finally:
        try:
            p.stdin.close()
            p.wait(timeout=60)
        except Exception:  # noqa: BLE001
            p.kill()
    return out, None


# Process 1
DEC = "Use tabs, not spaces, in every Python file"  # the topic word is NOT in the text: recall must find it by topic
p1, err = mcp_session([("remember_decision", {"decision": DEC, "because": "the linter expects tabs",
                                               "topic": "indentation"})])
checks["p1"] = p1 or err
checks["p1_handshake"] = bool(p1 and p1.get("initialize"))
checks["p1_remember_ok"] = bool(p1 and "remember_decision" in p1 and not p1["remember_decision"].get("isError")
                                and "error" not in p1)

# Process 2a: SessionStart hook as configured
ss = (settings.get("hooks") or {}).get("SessionStart") or []
hook_cmd = next((h["command"] for m in ss for h in m.get("hooks", []) if "inspeximus" in h.get("command", "")), None)
checks["sessionstart_cmd"] = hook_cmd
if hook_cmd:
    hp = run(hook_cmd, cwd=launch, shell=True, extra_env=cc_env,
             inp=json.dumps({"session_id": "ci-2", "hook_event_name": "SessionStart", "source": "startup",
                             "cwd": launch, "transcript_path": os.path.join(WORK, "t.jsonl")}))
    checks["sessionstart_has_decision"] = "tab" in hp.stdout.lower()
    checks["sessionstart_stdout"] = hp.stdout[-800:]
else:
    checks["sessionstart_has_decision"] = False

# Process 2b: fresh MCP server, recall by the topic word
p2, err = mcp_session([("recall", {"query": "indentation"})])
txt = json.dumps(p2) if p2 else (err or "")
checks["recall_has_decision"] = "tabs, not spaces" in txt
checks["recall_raw"] = txt[-800:]

stores = sorted(os.path.relpath(os.path.join(d, f), WORK) for d, _, fs in os.walk(WORK)
                if "venv" not in d and "uvcache" not in d for f in fs if f.endswith("memory.json"))
checks["stores"] = stores
checks["inspeximus_env_in_sandbox"] = sorted(k for k in env if k.upper().startswith("INSPEXIMUS_"))
version_ok = bool(got) and (VER == "latest" or got == VER)
PASS = version_ok and all(checks.get(k) for k in ("p1_handshake", "p1_remember_ok", "sessionstart_has_decision",
                                                   "recall_has_decision"))
res = {"version": VER, "PASS": PASS, "expect_fail": EXPECT_FAIL, "platform": sys.platform, "checks": checks,
       "log": log}
json.dump(res, open(os.path.join(WORK, "result.json"), "w", encoding="utf-8"), indent=1)
short = {k: checks.get(k) for k in ("installed_version", "uv_on_path", "pip_rc", "install_rc", "mcp_block_written",
                                    "hook_events_written", "p1_handshake", "p1_remember_ok",
                                    "sessionstart_has_decision", "recall_has_decision", "stores",
                                    "inspeximus_env_in_sandbox")}
print(json.dumps({"version": VER, "platform": sys.platform, "PASS": PASS, "expect_fail": EXPECT_FAIL, **short},
                 indent=1))
if EXPECT_FAIL and PASS:
    print("CONTROL PASSED: the release that should lose the decision kept it, so this check measured nothing")
if not version_ok:
    print("NO VERSION: inspeximus did not install, so neither verdict is evidence")
    sys.exit(2)
sys.exit(0 if PASS != EXPECT_FAIL else 1)
