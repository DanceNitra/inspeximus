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
    Returns (embed_doc, embed_query, embed_id); (None, None, None) when unconfigured -> LEXICAL recall.
    Fail-open on the write path: mnemo stores the record with vec=None if a call raises, so a down
    embedder degrades recall to lexical but never drops a capture."""
    import urllib.request
    ec = _cfg(cwd).get("embed", {})
    if not isinstance(ec, dict):
        ec = {}
    url = (os.environ.get("MNEMO_EMBED_URL") or ec.get("url") or "").strip()
    if not url:
        return None, None, None
    model = (os.environ.get("MNEMO_EMBED_MODEL") or ec.get("model") or "nomic-embed-text").strip()
    key = (os.environ.get("MNEMO_EMBED_KEY") or ec.get("key") or "").strip()
    try:
        timeout = float(ec.get("timeout", 10))
    except Exception:
        timeout = 10.0

    def _embed(text: str, prefix: str = ""):
        body = json.dumps({"model": model, "input": prefix + text}).encode()
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(url, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())["data"][0]["embedding"]

    # nomic-embed-text is ASYMMETRIC — the doc/query task prefixes are REQUIRED for good retrieval (the
    # correctness fix shipped for the MCP in 1.15.0, now applied to the Claude Code plugin too). Returns
    # SEPARATE document/query embedders + an embed_id so the recipe guard re-embeds on a recipe change.
    # Opt out with MNEMO_NOMIC_PREFIX=0. Symmetric models -> (embed, None, model).
    if "nomic" in model.lower() and os.environ.get("MNEMO_NOMIC_PREFIX", "1") != "0":
        return (lambda t: _embed(t, "search_document: ")), (lambda t: _embed(t, "search_query: ")), f"{model}|nomic-sd-sq"
    return _embed, None, model


def _store(cwd):
    from mnemo import Mnemo
    d = os.path.join(cwd or os.getcwd(), ".mnemo")
    os.makedirs(d, exist_ok=True)
    emb_doc, emb_query, emb_id = _make_embedder(cwd)
    # persist_vectors only when an embedder is configured: a coding store is small and its process is
    # short-lived (one per hook), so keeping vecs on disk lets semantic recall survive a reload without
    # re-embedding every item on each start. With no embedder we keep the legacy vec-less (lexical) store.
    m = Mnemo(path=os.path.join(d, "coding_memory.json"), embed=emb_doc, embed_query=emb_query,
              embed_id=emb_id, persist_vectors=emb_doc is not None)
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


# ── one-time, opt-out star nudge (shown ONCE after mnemo has proven its worth) ─────────────────────
_NUDGE_AFTER = 25   # writes before the (single) star ask fires — a milestone of demonstrated value


def _nudge_path(cwd):
    return os.path.join(cwd or os.getcwd(), ".mnemo", "nudge.json")


def _nudge_state(cwd):
    try:
        return json.load(open(_nudge_path(cwd), encoding="utf-8"))
    except Exception:
        return {"writes": 0, "shown": False}


def _bump_writes(cwd):
    """Count a capture toward the value milestone (best-effort, fail-open)."""
    try:
        st = _nudge_state(cwd)
        st["writes"] = int(st.get("writes", 0)) + 1
        json.dump(st, open(_nudge_path(cwd), "w", encoding="utf-8"))
    except Exception:
        pass


def _maybe_nudge(cwd):
    """Print the star ask exactly once, after mnemo has actually been useful. Opt out with
    MNEMO_NO_NUDGE=1. Never blocks and never repeats."""
    if os.environ.get("MNEMO_NO_NUDGE", "").strip() in ("1", "true", "yes"):
        return
    try:
        st = _nudge_state(cwd)
        if st.get("shown") or int(st.get("writes", 0)) < _NUDGE_AFTER:
            return
        # ASCII-only on purpose: hook stdout can be a non-UTF-8 console (e.g. Windows cp1250), where an
        # emoji would garble or drop the line. The word "star" carries it; the README badge carries the glyph.
        print(
            f"\n[mnemo] A small ask: mnemo has quietly remembered {st['writes']} things for you here so far.\n"
            "If it's been useful, please consider giving it a star -- it's honestly the main way other people\n"
            "find it, and it would genuinely make my day. Thank you so much! https://github.com/DanceNitra/mnemo\n"
            "(you'll only ever see this once; silence it anytime with MNEMO_NO_NUDGE=1)")
        st["shown"] = True
        json.dump(st, open(_nudge_path(cwd), "w", encoding="utf-8"))
    except Exception:
        pass


def capture(ev):
    cwd = ev.get("cwd") or os.getcwd()
    tool = ev.get("tool_name", "")
    ti = ev.get("tool_input", {}) or {}
    m = _store(cwd)
    did = False
    if tool in ("Edit", "MultiEdit", "Write"):
        fp = _rel(ti.get("file_path", ""), cwd)
        if not fp:
            return
        new = ti.get("new_string") or ti.get("content") or ""
        m.remember(f"{fp} :: current state -> {_excerpt(new)}", key=f"file:{fp}", object=_excerpt(new, 80),
                   mtype="semantic", tags=["file", "edit"])
        did = True
    elif tool == "Bash":
        cmd = _excerpt(ti.get("command", ""), 200)
        if cmd:
            m.remember(f"ran: {cmd}", key=f"cmd:{hashlib.sha1(cmd.encode()).hexdigest()[:10]}",
                       object=cmd[:60], mtype="episodic", tags=["bash"])
            did = True
    m._save()
    if did:
        _bump_writes(cwd)


def recall(ev):
    cwd = ev.get("cwd") or os.getcwd()
    q = ev.get("prompt") or ev.get("user_prompt") or ""
    if not q.strip():
        return
    # DECISIONS FIRST: a raw event log (commands, file-states) captures MECHANICS, but what an agent needs
    # recalled is the DECISIONS/RULES relevant to what it's about to do ("what did we decide, and why"). So we
    # surface decision-typed memories ahead of the command/file mechanics — otherwise the useful signal drowns
    # in 'ran: ...' noise. Decisions are stored with the "decision" tag by remember_decision().
    hits = _store(cwd).recall(q, k=16)
    def has(h, tag):
        return tag in (h.get("tags") or [])
    decisions = [h for h in hits if has(h, "decision")][:4]
    knowledge = [h for h in hits if has(h, "knowledge") and not has(h, "decision")][:4]
    mechanics = [h for h in hits if not has(h, "decision") and not has(h, "knowledge")][:2]
    out = []
    if decisions:
        out.append("decisions/rules (what we concluded, and why):")
        out += [f"  * {d['text']}" for d in decisions]
    if knowledge:
        out.append("curated knowledge (from memory):")
        out += [f"  = {k['text']}" for k in knowledge]
    if mechanics:
        out.append("recent mechanics (files/commands):")
        out += [f"  - {mm['text']}" for mm in mechanics]
    if out:
        print("[mnemo] relevant project memory (deterministic, corrections already applied):\n" + "\n".join(out))
    _maybe_nudge(cwd)   # visible slot: UserPromptSubmit stdout is shown to the user


def session_start(ev):
    cwd = ev.get("cwd") or os.getcwd()
    m = _store(cwd)
    files = [it for it in getattr(m, "items", []) if "file" in (it.get("tags") or [])
             and it.get("status") != "superseded"][:8]
    if files:
        lines = "\n".join(f"- {it['text']}" for it in files)
        print(f"[mnemo] this project's current known files (latest state only):\n{lines}")
    # once-a-day, opt-out "newer version exists" courtesy (stdout is injected as context here)
    try:
        from mnemo import __version__
        from mnemo._update import check_for_update
        note = check_for_update(__version__, cache_dir=os.path.join(cwd, ".mnemo"))
        if note:
            print(note)
    except Exception:
        pass


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
