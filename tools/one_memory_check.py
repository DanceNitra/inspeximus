"""Does `inspeximus install --all` give every agent ONE working memory? Five criteria, each through each
host's own written config, in a sandbox with a fake home holding every host's config directory.

    python tools/one_memory_check.py <workdir> --spec <pip spec> [--find-links DIR] [--expect-fail]
    python tools/one_memory_check.py <workdir> --reuse           (after tools/run_install_page.py)

  C1 recall without hooks: every host's server tells the model, in its handshake `instructions`, to recall
     at the start of a task; hosts whose docs say nothing about instructions got a rules file.
  C2 concurrency: two hosts' servers open on the store at once, 100 interleaved writes each and one
     erasure: no write lost, the erasure stays erased, and each server recalls the other's writes.
  C3 cross-agent: a decision written through each host's config is recalled through every other's.
  C4 version drift: every host is pinned to one launch spec, and an update notice reaches every host.
  C5 one bad writer: a row written around the library into the shared store breaks no host's recall.

Prints one JSON verdict per criterion and writes <workdir>/one_memory.json. Exit 0 when the overall verdict
is what was asked for (PASS, or FAIL with --expect-fail). No Claude, Codex or Gemini binary is needed: a
host's entry is started exactly as the host would start it.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid

WIN = os.name == "nt"
HOSTS = ("claude", "cursor", "windsurf", "codex", "cline", "gemini", "antigravity", "devin")
RULE_HOSTS = ("cursor", "windsurf", "cline", "antigravity", "devin")


def devin_dir(home):
    return os.path.join(home, "AppData", "Roaming", "devin") if WIN else os.path.join(home, ".config", "devin")


def sandbox(work, keep_uv=False):
    shutil.rmtree(work, ignore_errors=True)
    home, proj = os.path.join(work, "home"), os.path.join(work, "proj")
    for d in (os.path.join(home, "AppData", "Roaming"), os.path.join(home, "AppData", "Local"),
              os.path.join(home, ".config"), os.path.join(work, "tmp"), os.path.join(proj, "src"),
              # every host's config directory, as each creates it on first run
              os.path.join(home, ".claude"), os.path.join(home, ".cursor"),
              os.path.join(home, ".codeium", "windsurf"), os.path.join(home, ".codex"),
              os.path.join(home, ".cline"), os.path.join(home, ".gemini", "config"), devin_dir(home)):
        os.makedirs(d, exist_ok=True)
    with open(os.path.join(home, ".gemini", "settings.json"), "w", encoding="utf-8") as fh:
        fh.write("{}\n")
    subprocess.run(["git", "init", "-q", proj], check=True)
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("INSPEXIMUS_", "CLAUDE", "PIP_", "UV_", "VIRTUAL_ENV", "PYTHON",
                                        "CODEX", "CLINE", "HERMES"))}
    env.update(HOME=home, USERPROFILE=home, APPDATA=os.path.join(home, "AppData", "Roaming"),
               LOCALAPPDATA=os.path.join(home, "AppData", "Local"), XDG_CONFIG_HOME=os.path.join(home, ".config"),
               TEMP=os.path.join(work, "tmp"), TMP=os.path.join(work, "tmp"), TMPDIR=os.path.join(work, "tmp"),
               PYTHONIOENCODING="utf-8", PIP_NO_CACHE_DIR="1")
    # NO uv BY DEFAULT: with uvx on PATH the installer writes `uvx --from inspeximus[mcp]==<version>`, and
    # before a release that version resolves to PyPI's previous build, not the code under test. The uvx
    # route is exercised with --with-uv, against a local wheel (UV_FIND_LINKS).
    exe = "uvx.exe" if WIN else "uvx"
    if not keep_uv:
        env["PATH"] = os.pathsep.join(d for d in env.get("PATH", "").split(os.pathsep)
                                      if d and not os.path.isfile(os.path.join(d, exe)))
    return home, proj, env


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def entry_for(host, home, env):
    """The inspeximus entry a host's user-level config holds, or None."""
    paths = {
        "claude": os.path.join(home, ".claude.json"),
        "cursor": os.path.join(home, ".cursor", "mcp.json"),
        "windsurf": os.path.join(home, ".codeium", "windsurf", "mcp_config.json"),
        "codex": os.path.join(home, ".codex", "config.toml"),
        "cline": os.path.join(home, ".cline", "data", "settings", "cline_mcp_settings.json"),
        "gemini": os.path.join(home, ".gemini", "settings.json"),
        "antigravity": os.path.join(home, ".gemini", "config", "mcp_config.json"),
        "devin": os.path.join(devin_dir(home), "mcp_config.json"),
    }
    p = paths[host]
    if not os.path.exists(p):
        return None
    if p.endswith(".toml"):
        try:
            import tomllib
        except ImportError:                                  # Python < 3.11: read the table by hand
            m = re.search(r"\[mcp_servers\.inspeximus\]\s*\ncommand = (.+)\nargs = (\[.*\])", open(p).read())
            env_m = re.findall(r'^(INSPEXIMUS_[A-Z_]+) = (".*")$', open(p).read(), re.M)
            return m and {"command": json.loads(m.group(1)), "args": json.loads(m.group(2)),
                          "env": {k: json.loads(v) for k, v in env_m}}
        return (tomllib.load(open(p, "rb")).get("mcp_servers") or {}).get("inspeximus")
    return (json.load(open(p, encoding="utf-8")).get("mcpServers") or {}).get("inspeximus")


class Server:
    """One MCP server process started from a host's entry, as that host would start it."""

    def __init__(self, host, entry, env, cwd):
        e = dict(env, **{k: str(v) for k, v in (entry.get("env") or {}).items()})
        if host == "claude":
            e["CLAUDE_PROJECT_DIR"] = cwd
        # STDERR TO A FILE, never to a pipe nobody drains: a long session filled the pipe buffer, the
        # server blocked writing to it, and the harness waited forever for an answer (measured locally).
        import tempfile
        self.err = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
        self.p = subprocess.Popen([entry["command"]] + list(entry.get("args") or []), cwd=cwd, env=e,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.err,
                                  text=True, encoding="utf-8", errors="replace",
                                  start_new_session=not WIN)
        self.n = 0
        init = self._call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                         "clientInfo": {"name": host, "version": "1"}})
        self.instructions = (init.get("result") or {}).get("instructions") or ""
        self.server_version = ((init.get("result") or {}).get("serverInfo") or {}).get("version")
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.p.stdin.flush()

    def _call(self, method, params):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params}) + "\n")
        self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline()
            if not line:
                self.err.seek(0)
                raise RuntimeError("server closed: " + self.err.read()[-800:])
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if m.get("id") == self.n:
                return m

    def tool(self, name, **args):
        r = self._call("tools/call", {"name": name, "arguments": args})
        res = r.get("result") or {}
        if r.get("error") or res.get("isError"):
            raise RuntimeError(f"{name}: {r.get('error') or res}")
        # A tool that returns a list comes back as ONE content item PER ELEMENT (FastMCP), so the items
        # are parsed one by one; joined, they are not one JSON document and every recall read as empty.
        if isinstance(res.get("structuredContent"), dict):
            return res["structuredContent"]
        parts = []
        for c in res.get("content") or []:
            try:
                parts.append(json.loads(c.get("text", "")))
            except ValueError:
                parts.append(c.get("text", ""))
        return parts[0] if len(parts) == 1 else parts

    def close(self):
        """Stop the server and every process it started (uvx starts uv, which starts python), by PID."""
        try:
            self.p.stdin.close()
            self.p.wait(timeout=30)
        except Exception:                                    # noqa: BLE001
            pass
        if self.p.poll() is None:
            if WIN:
                subprocess.run(["taskkill", "/PID", str(self.p.pid), "/T", "/F"], capture_output=True)
            else:
                import signal
                try:
                    os.killpg(self.p.pid, signal.SIGKILL)
                except Exception:                            # noqa: BLE001
                    self.p.kill()


def _texts(recall_result):
    hits = recall_result.get("result") if isinstance(recall_result, dict) else recall_result
    return " ".join(str(h.get("text", "")) for h in (hits or []) if isinstance(h, dict))


def criteria(home, proj, env, launch_cwd):
    out = {}
    entries = {h: entry_for(h, home, env) for h in HOSTS}
    missing = [h for h, e in entries.items() if not e]
    stores = {h: (e.get("env") or {}).get("INSPEXIMUS_PATH") for h, e in entries.items() if e}
    out["wiring"] = {"hosts_wired": sorted(h for h in entries if entries[h]), "missing": missing,
                     "stores": sorted(set(stores.values()))}
    if missing:
        for c in ("C1", "C2", "C3", "C4", "C5"):
            out[c] = {"PASS": False, "why": f"no inspeximus entry for {', '.join(missing)}"}
        return out
    store = next(iter(set(stores.values())))

    # C4 first half: one launch spec for every host
    specs = {json.dumps([e["command"]] + list(e.get("args") or [])) for e in entries.values()}
    # C4 second half: an update notice in the shared cache reaches every host's handshake
    cache = os.path.join(home, ".inspeximus", ".update_check.json")
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    json.dump({"checked_at": time.time(), "latest": "99.0.0"}, open(cache, "w", encoding="utf-8"))

    tokens, instr, versions, errors = {}, {}, {}, {}
    log("C3/C1/C4: one decision written through each host's entry")
    for h in HOSTS:
        log("  write via " + h)
        tok = "tok-%s-%s" % (h, uuid.uuid4().hex[:8])
        tokens[h] = tok
        s = Server(h, entries[h], env, launch_cwd[h])
        try:
            instr[h] = s.instructions
            versions[h] = s.server_version
            s.tool("remember_decision", decision=f"Decision made in {h}: deploy freeze token {tok}",
                   because="cross-agent check", topic=f"freeze-{h}")
        except Exception as ex:                              # noqa: BLE001
            errors[h] = repr(ex)[:300]
        finally:
            s.close()
    # C1: the recall instruction, and a rules file where the docs say nothing about instructions
    rules_ok = {}
    for h in RULE_HOSTS:
        paths = {"antigravity": os.path.join(home, ".gemini", "config", "rules", "inspeximus.md"),
                 "windsurf": os.path.join(home, ".codeium", "windsurf", "memories", "global_rules.md"),
                 "cline": os.path.join(home, "Documents", "Cline", "Rules", "inspeximus.md"),
                 "cursor": os.path.join(proj, ".cursor", "rules", "inspeximus.mdc"),
                 "devin": os.path.join(devin_dir(home), "AGENTS.md")}
        rules_ok[h] = os.path.exists(paths[h]) and "inspeximus:recall" in open(paths[h], encoding="utf-8").read()
    told = {h: ("call `recall`" in instr.get(h, "")) for h in HOSTS}
    out["C1"] = {"PASS": all(told.values()) and all(rules_ok.values()),
                 "instructions_tell_recall": told, "rules_file_written": rules_ok}

    # C3: every host recalls every other host's decision, and the first-run decision
    seen = {}
    log("C3: every host recalls every host's decision")
    for g in HOSTS:
        log("  recall via " + g)
        s = Server(g, entries[g], env, launch_cwd[g])
        try:
            got = {}
            for h, tok in tokens.items():
                got[h] = tok in _texts(s.tool("recall", query=tok, k=3))
            got["first-run"] = "every AI agent on this machine shares one inspeximus memory" in \
                _texts(s.tool("recall", query="inspeximus-setup shares one memory", k=5))
            seen[g] = got
        except Exception as ex:                              # noqa: BLE001
            seen[g] = {"error": repr(ex)[:300]}
        finally:
            s.close()
    pairs_ok = sum(1 for g in HOSTS for h in HOSTS if seen.get(g, {}).get(h))
    out["C3"] = {"PASS": pairs_ok == len(HOSTS) ** 2 and not errors
                 and all(seen[g].get("first-run") for g in HOSTS),
                 "pairs_recalled": f"{pairs_ok}/{len(HOSTS) ** 2}", "write_errors": errors,
                 "first_run_decision_seen_by": sorted(g for g in HOSTS if seen.get(g, {}).get("first-run"))}

    notified = {h: "A new version is available: 99.0.0" in instr.get(h, "") for h in HOSTS}
    out["C4"] = {"PASS": len(specs) == 1 and all(notified.values()) and len(set(versions.values())) == 1,
                 "launch_specs": sorted(specs), "server_versions": versions, "update_notice_reached": notified}
    os.remove(cache)

    # C2: two hosts' servers at once, interleaved writes and one erasure
    a, b = Server("codex", entries["codex"], env, launch_cwd["codex"]), \
        Server("gemini", entries["gemini"], env, launch_cwd["gemini"])
    ids_a, ids_b, errs = [], [], []
    log("C2: codex and gemini servers open together, 100 interleaved writes each")
    try:
        for i in range(100):
            if i % 25 == 0:
                log("  write pair %d/100" % i)
            for srv, ids, tag in ((a, ids_a, "a"), (b, ids_b, "b")):
                try:
                    r = srv.tool("remember", text=f"concurrent write {tag}{i:03d} from host {tag}",
                                 tags=["conc", "conc-" + tag])
                    ids.append(r.get("id") if isinstance(r, dict) else None)
                except Exception as ex:                      # noqa: BLE001
                    errs.append(repr(ex)[:200])
            if i == 50:
                erased = ids_a[10]
                b.tool("forget", ids=[erased])               # host B erases a record host A wrote
        tail_a = _texts(b.tool("recall", query="concurrent write a099 from host a", k=3))
        tail_b = _texts(a.tool("recall", query="concurrent write b099 from host b", k=3))
    finally:
        a.close()
        b.close()
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import importlib
    alive = set()
    try:
        m = importlib.import_module("inspeximus").Inspeximus(store)
        alive = {r["id"] for r in m.items}
    except Exception as ex:                                  # noqa: BLE001
        errs.append("reopen: " + repr(ex)[:200])
    written = [i for i in ids_a + ids_b if i]
    lost = [i for i in written if i not in alive and i != erased]
    out["C2"] = {"PASS": not errs and len(written) == 200 and not lost and erased not in alive
                 and "a099" in tail_a and "b099" in tail_b,
                 "writes_acknowledged": len(written), "lost": len(lost), "erasure_stayed_erased": erased not in alive,
                 "each_recalls_the_other": "a099" in tail_a and "b099" in tail_b, "errors": errs[:3]}

    # C5: a row written around the library (the 2026-09-26 shape), then recall through every host
    try:
        con = sqlite3.connect(store)
        con.execute("INSERT INTO records (id, ord, doc) VALUES (?, ?, ?)",
                    ("f0reign0001", 10 ** 9, json.dumps({"id": "f0reign0001", "key": "crew::persona::x",
                                                         "text": "foreign persona row", "status": "active",
                                                         "mtype": "semantic", "tags": ["persona"],
                                                         "ts": "2026-09-25T18:40:03Z"})))
        con.commit()
        con.close()
        planted = True
    except Exception as ex:                                  # noqa: BLE001
        planted = "could not plant: " + repr(ex)[:200]
    ok5 = {}
    log("C5: a foreign row planted; recall through every host")
    for g in HOSTS:
        s = Server(g, entries[g], env, launch_cwd[g])
        try:
            ok5[g] = tokens["claude"] in _texts(s.tool("recall", query=tokens["claude"], k=3))
        except Exception as ex:                              # noqa: BLE001
            ok5[g] = "error: " + repr(ex)[:160]
        finally:
            s.close()
    out["C5"] = {"PASS": planted is True and all(v is True for v in ok5.values()), "planted": planted,
                 "recall_ok": ok5}
    return out


def main():
    work = os.path.abspath(sys.argv[1])
    args = sys.argv[2:]
    expect_fail = "--expect-fail" in args
    if "--reuse" in args:
        home, proj = os.path.join(work, "home"), os.path.join(work, "proj")
        env = json.load(open(os.path.join(work, "env.json"), encoding="utf-8"))
        bindir = env["_BINDIR"]
    else:
        spec = args[args.index("--spec") + 1]
        home, proj, env = sandbox(work)
        venv = os.path.join(work, "venv")
        subprocess.run([sys.executable, "-m", "venv", venv], check=True)
        bindir = os.path.join(venv, "Scripts" if WIN else "bin")
        env["PATH"] = bindir + os.pathsep + env["PATH"]
        pip = [os.path.join(bindir, "python"), "-m", "pip", "install", "-q", spec]
        if "--find-links" in args:
            pip += ["--find-links", args[args.index("--find-links") + 1]]
        log("pip install " + spec)
        subprocess.run(pip, env=env, check=True)
        log("inspeximus install --all")
        r = subprocess.run([os.path.join(bindir, "inspeximus"), "install", "--all", "--rules", "yes",
                            "--hermes-provider", "yes"], cwd=proj, env=env, capture_output=True, text=True)
        print(r.stdout[-3000:] + r.stderr[-1500:])
    launch_cwd = {h: os.path.join(proj, "src") for h in HOSTS}
    launch_cwd.update(cursor=home, windsurf=home, cline=home, antigravity=home, devin=home)   # no documented cwd
    try:
        res = criteria(home, proj, env, launch_cwd)
    except Exception as ex:                                  # noqa: BLE001 -- a crash is a failed route
        res = {"crash": repr(ex)[:500]}
    verdict = all(res.get(c, {}).get("PASS") for c in ("C1", "C2", "C3", "C4", "C5"))
    res["PASS"] = verdict
    json.dump(res, open(os.path.join(work, "one_memory.json"), "w", encoding="utf-8"), indent=1)
    print(json.dumps(res, indent=1)[:6000])
    if expect_fail and verdict:
        print("CONTROL PASSED: the old release gave every agent one memory, so this check measured nothing")
    sys.exit(0 if verdict != expect_fail else 1)


if __name__ == "__main__":
    main()
