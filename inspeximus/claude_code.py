#!/usr/bin/env python3
"""inspeximus <-> Claude Code: deterministic, no-LLM auto-capture of coding-agent memory.

Other coding-agent memories (Claude-Mem, agentmemory) auto-capture your session via lifecycle hooks but
LLM-summarize on the write path, which drops facts, leaks on erasure, and is non-reproducible. This does the
same auto-capture with NO LLM: it writes tool events into a deterministic, keyed inspeximus store, so a corrected
fact (a changed API signature, a renamed symbol, a moved file) SUPERSEDES the stale one and cannot be resurrected
by an echo. Persistent across sessions, provably erasable, zero-dependency. The store is a local JSON file at
<project>/.inspeximus/coding_memory.json.

Use it two ways:
  python -m inspeximus.claude_code --install     # write the hooks block into ./.claude/settings.json
  python -m inspeximus.claude_code               # (as a hook) reads a Claude Code event on stdin and acts on it

Hook events handled (dispatched by hook_event_name on stdin JSON):
  PostToolUse       -> capture Edit/Write/MultiEdit/Bash deterministically, keyed by file path.
  UserPromptSubmit  -> recall memory relevant to the prompt; print it (Claude Code injects stdout as context).
  SessionStart      -> print a short digest of the project's known files (latest state only).
Fail-open: any error exits 0 with no output, so the hook never blocks the agent.

Recall is deterministic LEXICAL by default (runs anywhere, no service). For SEMANTIC recall, point the plugin
at any OpenAI-compatible /embeddings endpoint — e.g. local Ollama — via env (INSPEXIMUS_EMBED_URL / INSPEXIMUS_EMBED_MODEL)
or a per-project .inspeximus/config.json: {"embed": {"url": "http://localhost:11434/v1/embeddings",
"model": "nomic-embed-text"}}. Writes stay verbatim, keyed and no-LLM; the embedder only builds a retrieval
index and fails open (a down endpoint silently degrades to lexical, never drops a capture).
"""
import sys, os, json, hashlib


def _cfg(cwd):
    """Per-project plugin config at <project>/.inspeximus/config.json (optional)."""
    try:
        p = os.path.join(cwd or os.getcwd(), ".inspeximus", "config.json")
        if os.path.exists(p):
            c = json.load(open(p, encoding="utf-8"))
            return c if isinstance(c, dict) else {}
    except Exception:
        pass
    return {}


def _make_embedder(cwd):
    """Optional embedder for SEMANTIC recall (zero extra deps — urllib against any OpenAI-compatible
    /embeddings endpoint, e.g. local Ollama at http://localhost:11434/v1/embeddings). Configured by env
    (INSPEXIMUS_EMBED_URL / INSPEXIMUS_EMBED_MODEL / INSPEXIMUS_EMBED_KEY) or .inspeximus/config.json {"embed": {...}}.
    Returns (embed_doc, embed_query, embed_id); (None, None, None) when unconfigured -> LEXICAL recall.
    Fail-open on the write path: inspeximus stores the record with vec=None if a call raises, so a down
    embedder degrades recall to lexical but never drops a capture.

    HOOKS ARE LEXICAL BY DEFAULT (opt in with INSPEXIMUS_EMBED_HOOKS=1 or config {"embed": {"hooks": true}}).
    The hooks run in the agent's hot path — PostToolUse after EVERY Edit/Write/Bash, UserPromptSubmit
    blocking the prompt — and with a local GPU embedder each capture costs one embedding call (~2s on an
    idle GPU, unbounded on a busy one: this plugin's own dogfood machine runs a 21GB LLM on the same card).
    The capture is deterministic and keyed either way; what the embedder buys on THIS store is small (its
    bulk is 'ran: ...' mechanics, the least semantic content there is), so the hot path defaults to the
    zero-network lexical mode and semantic stays a deliberate choice for stores where it earns its cost."""
    import urllib.request
    ec = _cfg(cwd).get("embed", {})
    if not isinstance(ec, dict):
        ec = {}
    hooks_on = os.environ.get("INSPEXIMUS_EMBED_HOOKS", "").strip().lower() in ("1", "true", "yes") \
        or ec.get("hooks") is True
    if not hooks_on:
        return None, None, None
    url = (os.environ.get("INSPEXIMUS_EMBED_URL") or ec.get("url") or "").strip()
    if not url:
        return None, None, None
    model = (os.environ.get("INSPEXIMUS_EMBED_MODEL") or ec.get("model") or "nomic-embed-text").strip()
    key = (os.environ.get("INSPEXIMUS_EMBED_KEY") or ec.get("key") or "").strip()
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
    # Opt out with INSPEXIMUS_NOMIC_PREFIX=0. Symmetric models -> (embed, None, model).
    if "nomic" in model.lower() and os.environ.get("INSPEXIMUS_NOMIC_PREFIX", "1") != "0":
        return (lambda t: _embed(t, "search_document: ")), (lambda t: _embed(t, "search_query: ")), f"{model}|nomic-sd-sq"
    return _embed, None, model


def _store(cwd):
    from ._surface import open_store
    d = os.path.join(cwd or os.getcwd(), ".inspeximus")
    os.makedirs(d, exist_ok=True)
    emb_doc, emb_query, emb_id = _make_embedder(cwd)
    # Opened through the SHARED SURFACE opener (inspeximus/_surface.py). This hook set echo_guard=True by
    # hand and never applied the receipts-sidecar rule, so a hook write against a receipted coding store
    # left the record uncovered by the chain — the same hole the CLI had, at the surface that writes most
    # often. The guard's posture is unchanged except that INSPEXIMUS_ECHO_GUARD=0 now reaches here too,
    # which is the point of having one posture.
    # persist_vectors is ALWAYS on: a store that acquired vectors during a semantic session must keep them
    # across a lexical open — persist_vectors=False strips vecs on save, so one hook run with the embedder
    # off would silently erase every persisted vector. On a store that never had vecs it is a no-op. The
    # matching core guarantee: _save leaves the .embedid sidecar untouched when embed_id is None, so a
    # lexical open can never mislabel (or blank) the recipe the persisted vectors were made with.
    return open_store(os.path.join(d, "coding_memory.json"), embed=emb_doc, embed_query=emb_query,
                      embed_id=emb_id, persist_vectors=True)


def _rel(p, cwd):
    try:
        return os.path.relpath(p, cwd) if cwd and p else p
    except Exception:
        return p


def _excerpt(s, n=180):
    s = (s or "").strip().replace("\n", " ")
    return (s[:n] + "…") if len(s) > n else s


# ── one-time, opt-out star nudge (shown ONCE after inspeximus has proven its worth) ─────────────────────
_NUDGE_AFTER = 25   # writes before the (single) star ask fires — a milestone of demonstrated value


def _nudge_path(cwd):
    return os.path.join(cwd or os.getcwd(), ".inspeximus", "nudge.json")


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
    """Print the star ask exactly once, after inspeximus has actually been useful. Opt out with
    INSPEXIMUS_NO_NUDGE=1. Never blocks and never repeats."""
    if os.environ.get("INSPEXIMUS_NO_NUDGE", "").strip() in ("1", "true", "yes"):
        return
    try:
        st = _nudge_state(cwd)
        if st.get("shown") or int(st.get("writes", 0)) < _NUDGE_AFTER:
            return
        # ASCII-only on purpose: hook stdout can be a non-UTF-8 console (e.g. Windows cp1250), where an
        # emoji would garble or drop the line. The word "star" carries it; the README badge carries the glyph.
        print(
            f"\n[inspeximus] A small ask: inspeximus has quietly remembered {st['writes']} things for you here so far.\n"
            "If it's been useful, please consider giving it a star -- it's honestly the main way other people\n"
            "find it, and it would genuinely make my day. Thank you so much! https://github.com/DanceNitra/inspeximus\n"
            "(you'll only ever see this once; silence it anytime with INSPEXIMUS_NO_NUDGE=1)")
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
        print("[inspeximus] relevant project memory (deterministic, corrections already applied):\n" + "\n".join(out))
    _maybe_nudge(cwd)   # visible slot: UserPromptSubmit stdout is shown to the user


def session_start(ev):
    cwd = ev.get("cwd") or os.getcwd()
    m = _store(cwd)
    files = [it for it in getattr(m, "items", []) if "file" in (it.get("tags") or [])
             and it.get("status") != "superseded"][:8]
    if files:
        lines = "\n".join(f"- {it['text']}" for it in files)
        print(f"[inspeximus] this project's current known files (latest state only):\n{lines}")
    # once-a-day, opt-out "newer version exists" courtesy (stdout is injected as context here)
    try:
        from inspeximus import __version__
        from inspeximus._update import check_for_update
        note = check_for_update(__version__, cache_dir=os.path.join(cwd, ".inspeximus"))
        if note:
            print(note)
    except Exception:
        pass


_HOOK = {"hooks": [{"type": "command", "command": "python -m inspeximus.claude_code"}]}

# Hooks written before the 1.25.0 rename invoke `python -m inspeximus.claude_code`, which still works
# through the compatibility alias. Both spellings must be RECOGNISED, or install() would add a second
# hook next to the old one and uninstall() would leave it behind.
_HOOK_MARKERS = ("inspeximus.claude_code", "inspeximus.claude_code")


def _atomic_write_json(path, obj):
    """Write via a temp file in the same directory, then replace. A direct `json.dump(open(p,"w"))`
    truncates the target the moment it opens it, so a crash or a full disk mid-write leaves the user with
    a half-written settings.json -- and this one is not our file."""
    tmp = path + ".inspeximus-tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


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
        except Exception as e:
            # REFUSE. The previous version fell back to `cfg = {}` and then WROTE it, so an unparseable
            # settings.json -- a trailing comma is the usual cause -- silently lost the user's model,
            # permissions and their OWN hooks, in a file we do not own, from a function whose docstring
            # says "merging, not clobbering". Measured. Never overwrite a config you could not read.
            print(f"inspeximus: {p} is not valid JSON ({type(e).__name__}), so nothing was changed.")
            print("            Fix the file (a trailing comma is the usual cause) and re-run --install.")
            return False
    hooks = cfg.setdefault("hooks", {})
    for evt in ("PostToolUse", "UserPromptSubmit", "SessionStart"):
        existing = json.dumps(hooks.get(evt, []))
        if not any(mark in existing for mark in _HOOK_MARKERS):
            hooks.setdefault(evt, []).append(dict(_HOOK))
    _atomic_write_json(p, cfg)
    print(f"inspeximus: installed Claude Code hooks into {p}")
    print("Restart Claude Code in this project. Memory lands in ./.inspeximus/coding_memory.json (deterministic, "
          "no LLM, provably erasable). Run `python -m inspeximus.claude_code --uninstall` to remove.")
    return True


def uninstall(cwd=None):
    p = os.path.join(cwd or os.getcwd(), ".claude", "settings.json")
    if not os.path.exists(p):
        return False
    try:
        cfg = json.load(open(p, encoding="utf-8"))
    except Exception as e:
        # The bare json.load here RAISED on a malformed file, so a user whose settings.json had been
        # broken could not even undo the install.
        print(f"inspeximus: {p} is not valid JSON ({type(e).__name__}), so nothing was changed.")
        return False
    for evt, arr in list(cfg.get("hooks", {}).items()):
        cfg["hooks"][evt] = [h for h in arr
                             if not any(mark in json.dumps(h) for mark in _HOOK_MARKERS)]
    _atomic_write_json(p, cfg)
    print(f"inspeximus: removed Claude Code hooks from {p}")


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
