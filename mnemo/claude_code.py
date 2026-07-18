#!/usr/bin/env python3
"""mnemo <-> Claude Code: deterministic, no-LLM auto-capture of coding-agent memory.

Other coding-agent memories (Claude-Mem, agentmemory) auto-capture your session via lifecycle hooks but
LLM-summarize on the write path, which drops facts, leaks on erasure, and is non-reproducible. This does the
same auto-capture with NO LLM: it writes tool events into a deterministic, keyed mnemo store, so a corrected
fact (a changed API signature, a renamed symbol, a moved file) SUPERSEDES the stale one and cannot be resurrected
by an echo. Persistent across sessions, provably erasable, zero-dependency. The store is a local JSON file at
<project>/.mnemo/coding_memory.json.

Use it two ways:
  python -m mnemo.claude_code --install     # write the hooks block into ./.claude/settings.json
  python -m mnemo.claude_code               # (as a hook) reads a Claude Code event on stdin and acts on it

Hook events handled (dispatched by hook_event_name on stdin JSON):
  PostToolUse       -> capture Edit/Write/MultiEdit/Bash deterministically, keyed by file path.
  UserPromptSubmit  -> recall memory relevant to the prompt; print it (Claude Code injects stdout as context).
  SessionStart      -> print a short digest of the project's known files (latest state only).
Fail-open: any error exits 0 with no output, so the hook never blocks the agent.

Recall is deterministic LEXICAL by default (runs anywhere, no service). For SEMANTIC recall, point the plugin
at any OpenAI-compatible /embeddings endpoint — e.g. local Ollama — via env (MNEMO_EMBED_URL / MNEMO_EMBED_MODEL)
or a per-project .mnemo/config.json: {"embed": {"url": "http://localhost:11434/v1/embeddings",
"model": "nomic-embed-text"}}. Writes stay verbatim, keyed and no-LLM; the embedder only builds a retrieval
index and fails open (a down endpoint silently degrades to lexical, never drops a capture).
"""
import sys, os, json, hashlib


def _cfg(cwd):
    """Per-project plugin config at <project>/.mnemo/config.json (optional)."""
    try:
        p = os.path.join(cwd or os.getcwd(), ".mnemo", "config.json")
        if os.path.exists(p):
            c = json.load(open(p, encoding="utf-8"))
            return c if isinstance(c, dict) else {}
    except Exception:
        pass
    return {}


def _make_embedder(cwd):
    """Optional embedder for SEMANTIC recall (zero extra deps — urllib against any OpenAI-compatible
    /embeddings endpoint, e.g. local Ollama at http://localhost:11434/v1/embeddings). Configured by env
    (MNEMO_EMBED_URL / MNEMO_EMBED_MODEL / MNEMO_EMBED_KEY) or .mnemo/config.json {"embed": {...}}.
    Returns None when unconfigured -> deterministic LEXICAL recall (runs anywhere, no service needed).
    Fail-open on the write path: mnemo stores the record with vec=None if a call raises, so a down
    embedder degrades recall to lexical but never drops a capture."""
    import urllib.request
    ec = _cfg(cwd).get("embed", {})
    if not isinstance(ec, dict):
        ec = {}
    url = (os.environ.get("MNEMO_EMBED_URL") or ec.get("url") or "").strip()
    if not url:
        return None
    model = (os.environ.get("MNEMO_EMBED_MODEL") or ec.get("model") or "nomic-embed-text").strip()
    key = (os.environ.get("MNEMO_EMBED_KEY") or ec.get("key") or "").strip()
    try:
        timeout = float(ec.get("timeout", 10))
    except Exception:
        timeout = 10.0

    def embed(text: str):
        body = json.dumps({"model": model, "input": text}).encode()
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(url, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())["data"][0]["embedding"]

    return embed


def _store(cwd):
    from mnemo import Mnemo
    d = os.path.join(cwd or os.getcwd(), ".mnemo")
    os.makedirs(d, exist_ok=True)
    emb = _make_embedder(cwd)
    # persist_vectors only when an embedder is configured: a coding store is small and its process is
    # short-lived (one per hook), so keeping vecs on disk lets semantic recall survive a reload without
    # re-embedding every item on each start. With no embedder we keep the legacy vec-less (lexical) store.
    m = Mnemo(path=os.path.join(d, "coding_memory.json"), embed=emb, persist_vectors=emb is not None)
    m.echo_guard = True
    return m


def _rel(p, cwd):
    try:
        return os.path.relpath(p, cwd) if cwd and p else p
    except Exception:
        return p


def _excerpt(s, n=180):
    s = (s or "").strip().replace("\n", " ")
    return (s[:n] + "…") if len(s) > n else s


def capture(ev):
    cwd = ev.get("cwd") or os.getcwd()
    tool = ev.get("tool_name", "")
    ti = ev.get("tool_input", {}) or {}
    m = _store(cwd)
    if tool in ("Edit", "MultiEdit", "Write"):
        fp = _rel(ti.get("file_path", ""), cwd)
        if not fp:
            return
        new = ti.get("new_string") or ti.get("content") or ""
        m.remember(f"{fp} :: current state -> {_excerpt(new)}", key=f"file:{fp}", object=_excerpt(new, 80),
                   mtype="semantic", tags=["file", "edit"])
    elif tool == "Bash":
        cmd = _excerpt(ti.get("command", ""), 200)
        if cmd:
            m.remember(f"ran: {cmd}", key=f"cmd:{hashlib.sha1(cmd.encode()).hexdigest()[:10]}",
                       object=cmd[:60], mtype="episodic", tags=["bash"])
    m._save()


def recall(ev):
    q = ev.get("prompt") or ev.get("user_prompt") or ""
    if not q.strip():
        return
    hits = _store(ev.get("cwd") or os.getcwd()).recall(q, k=5)
    if hits:
        lines = "\n".join(f"- {h['text']}" for h in hits)
        print(f"[mnemo] relevant project memory (deterministic, corrections already applied):\n{lines}")


def session_start(ev):
    m = _store(ev.get("cwd") or os.getcwd())
    files = [it for it in getattr(m, "items", []) if "file" in (it.get("tags") or [])
             and it.get("status") != "superseded"][:8]
    if files:
        lines = "\n".join(f"- {it['text']}" for it in files)
        print(f"[mnemo] this project's current known files (latest state only):\n{lines}")


_HOOK = {"hooks": [{"type": "command", "command": "python -m mnemo.claude_code"}]}


def install(cwd=None):
    """Write the three hooks into ./.claude/settings.json (merging, not clobbering)."""
    cwd = cwd or os.getcwd()
    d = os.path.join(cwd, ".claude")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "settings.json")
    cfg = {}
    if os.path.exists(p):
        try:
            cfg = json.load(open(p, encoding="utf-8"))
        except Exception:
            cfg = {}
    hooks = cfg.setdefault("hooks", {})
    for evt in ("PostToolUse", "UserPromptSubmit", "SessionStart"):
        existing = json.dumps(hooks.get(evt, []))
        if "mnemo.claude_code" not in existing:
            hooks.setdefault(evt, []).append(dict(_HOOK))
    json.dump(cfg, open(p, "w", encoding="utf-8"), indent=2)
    print(f"mnemo: installed Claude Code hooks into {p}")
    print("Restart Claude Code in this project. Memory lands in ./.mnemo/coding_memory.json (deterministic, "
          "no LLM, provably erasable). Run `python -m mnemo.claude_code --uninstall` to remove.")


def uninstall(cwd=None):
    p = os.path.join(cwd or os.getcwd(), ".claude", "settings.json")
    if not os.path.exists(p):
        return
    cfg = json.load(open(p, encoding="utf-8"))
    for evt, arr in list(cfg.get("hooks", {}).items()):
        cfg["hooks"][evt] = [h for h in arr if "mnemo.claude_code" not in json.dumps(h)]
    json.dump(cfg, open(p, "w", encoding="utf-8"), indent=2)
    print(f"mnemo: removed Claude Code hooks from {p}")


def main():
    if "--install" in sys.argv:
        install(); return
    if "--uninstall" in sys.argv:
        uninstall(); return
    try:
        ev = json.load(sys.stdin)
    except Exception:
        return
    try:
        name = ev.get("hook_event_name", "")
        if name == "PostToolUse":
            capture(ev)
        elif name == "UserPromptSubmit":
            recall(ev)
        elif name == "SessionStart":
            session_start(ev)
    except Exception:
        pass


if __name__ == "__main__":
    main()
