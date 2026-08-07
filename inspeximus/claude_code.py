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
  SessionStart      -> open the session boundary and PRINT THE CROSS-SESSION DIGEST (stdout is injected
                       as context), plus the project's known files.
  SessionEnd        -> close the session boundary: write ONE digest record of what this session
                       established. Stdout is discarded by Claude Code here, so this hook only writes.
Fail-open: any error exits 0 with no output, so the hook never blocks the agent.

THE CROSS-SESSION LOOP (SessionEnd -> SessionStart), and why it needs no LLM. Other coding-agent memories
close the loop by sending the transcript to a model and injecting its prose summary. inspeximus emits a LEDGER
DIFF instead — which keys changed value, which decisions were recorded, what was erased, what is still
open — read straight off the store's own supersession ledger (`Inspeximus.close_session` /
`session_context`). It is instant, free, byte-reproducible, and auditable line by line, and because the
injected block is RE-RESOLVED against the live store at injection time, a decision reversed in a later
session is replaced by the current one rather than living on inside a frozen summary.

OFF SWITCH: `INSPEXIMUS_SESSION_DIGEST=0` (env), or `.inspeximus/config.json`
{"session_digest": {"enabled": false}}. With it off, SessionEnd writes NOTHING (the store's state_digest
is unchanged by the call) and SessionStart injects NOTHING. Tune it with
INSPEXIMUS_SESSION_MAX_CHARS (default 1200 -- the hard size bound on the injected block),
INSPEXIMUS_SESSION_SALIENCE (default 2.5 -- the admission bar; see Inspeximus.session_salience) and
INSPEXIMUS_SESSION_MAX_SESSIONS (default 3 -- how many past sessions the injection draws from).

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


def _store_dir(cwd):
    """Where this project's coding memory lives: the PROJECT ROOT, not the current directory.

    Keying the store by `cwd` shatters one project's memory into one store per directory the agent
    happens to be standing in. Measured on this plugin's own dogfood repo: **13 separate stores**,
    ~2,290 records, split as 917 / 504 / 374 / 216 / 200 / ... across `server/`, `agora_output/lab/`,
    `agora-game-server/`, `tools/`, `research/probes/` and more. Nothing recalls across them, so a
    question asked from one directory cannot see what was learned in another, and the store that
    answers is whichever fragment the shell was in. That is almost certainly why the real-store
    dogfood found 2 of 5 facts where the single-store synthetic fixture found 11 of 13.

    `find_project_root` walks up for `.git` (directory OR file, so worktrees and submodules work) and
    already shipped in `_surface`; the hook simply never called it. Falls back to `cwd` when there is
    no repository, which is the old behaviour, and `INSPEXIMUS_CODING_STORE` overrides both.
    """
    override = (os.environ.get("INSPEXIMUS_CODING_STORE") or "").strip()
    if override:
        return override
    base = cwd or os.getcwd()
    try:
        from ._surface import find_project_root
        root = find_project_root(base)
    except Exception:
        root = None
    return os.path.join(root or base, ".inspeximus")


def _legacy_fragments(cwd):
    """Stores left by the cwd-keyed layout, under this project root but not AT it.

    Reported, never moved. Silently relocating a user's memory is exactly the class of action this
    codebase has been burned by; the caller is told what exists and asked to merge explicitly.
    """
    try:
        from ._surface import find_project_root
        root = find_project_root(cwd or os.getcwd())
    except Exception:
        root = None
    if not root:
        return []
    here = os.path.normcase(os.path.join(root, ".inspeximus", "coding_memory.json"))
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "__pycache__")
                       and not d.startswith(".claude")]
        if "coding_memory.json" in filenames and os.path.basename(dirpath) == ".inspeximus":
            p = os.path.join(dirpath, "coding_memory.json")
            if os.path.normcase(p) != here:
                out.append(p)
    return sorted(out)


def merge_fragments(cwd=None, apply=False):
    """Fold the cwd-keyed fragments into the project store. DRY BY DEFAULT; nothing is deleted, ever.

    The old layout put a store in every directory the agent stood in, so one project's memory ends up
    in a dozen files that cannot see each other. This gathers them. What it does NOT do is as
    important: it never removes a fragment, and it backs the destination up before writing, because
    the failure that would matter here is not a bad merge but a lost one.

    Records are merged by id, so re-running is idempotent and a record present in two fragments lands
    once. Returns a report -- per-fragment counts, how many were new, how many collided -- so the
    caller can see what a real run would do before it does it.
    """
    import json as _json
    import shutil as _shutil
    import time as _time
    frags = _legacy_fragments(cwd)
    dest_dir = _store_dir(cwd)
    dest = os.path.join(dest_dir, "coding_memory.json")

    def _load(p, strict=False):
        """Load a store. An UNREADABLE file is not an empty one -- with strict=True it raises.

        This was a silent data-loss trap and it was live. Our own project store is 1.9 MB, ends with
        a complete JSON array followed by a 7-character tail from an older, longer write (a write
        that did not truncate), and therefore does not parse. The first version of this function
        swallowed that and returned [], so the destination read as empty and a real merge would have
        replaced 2,990 records with the fragments. The valid prefix is recoverable -- raw_decode
        stops cleanly at the junk -- so the file is reported, never silently rewritten.
        """
        try:
            with open(p, encoding="utf-8") as f:
                d = _json.load(f)
        except Exception as e:
            if strict:
                raise ValueError(
                    "destination store exists but does not parse (%s: %s). Refusing to merge into "
                    "it: an unreadable store is not an empty one, and writing here would replace "
                    "whatever it holds. Recover or move it first." % (type(e).__name__, e))
            return []
        return d if isinstance(d, list) else (d.get("items") or d.get("records") or [])

    # STRICT on the destination: this is the only file the merge can overwrite.
    existing = _load(dest, strict=True) if os.path.exists(dest) else []
    seen = {r.get("id") for r in existing if isinstance(r, dict)}
    report = {"destination": dest, "already_there": len(existing), "fragments": [],
              "new": 0, "collisions": 0, "applied": False, "backup": None}
    merged = list(existing)
    for p in frags:
        recs = _load(p)
        new = coll = 0
        for r in recs:
            if not isinstance(r, dict):
                continue
            rid = r.get("id")
            if rid in seen:
                coll += 1
                continue
            seen.add(rid)
            merged.append(r)
            new += 1
        report["fragments"].append({"path": p, "records": len(recs), "new": new, "collisions": coll})
        report["new"] += new
        report["collisions"] += coll
    report["total_after"] = len(merged)
    if apply and report["new"]:
        os.makedirs(dest_dir, exist_ok=True)
        if os.path.exists(dest):
            bak = dest + ".bak-merge-" + _time.strftime("%Y%m%d-%H%M%S")
            _shutil.copy2(dest, bak)
            report["backup"] = bak
        tmp = dest + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(merged, f, ensure_ascii=False)
        os.replace(tmp, dest)
        report["applied"] = True
    return report


def injection_enabled(cwd=None):
    """Is this plugin allowed to write into the agent's context at all?

    Both hooks that inject -- SessionStart's digest and UserPromptSubmit's recall block -- are
    silenced by INSPEXIMUS_NO_INJECT=1, or by .inspeximus/config.json {"inject": {"enabled": false}}.

    This exists because a conformance test said so and was right: an injection an operator cannot
    turn off is not a feature they can adopt. Before this, the only switches the package declared
    were for the star ask and the version line -- decorations riding alongside the injection, with
    nothing for the injection itself. INSPEXIMUS_SESSION_DIGEST already gated the digest, but not the
    file list beside it and not the recall block at all, so no single variable silenced the plugin.
    """
    env = os.environ.get("INSPEXIMUS_NO_INJECT", "").strip().lower()
    if env in ("1", "true", "yes"):
        return False
    c = _cfg(cwd).get("inject", {})
    return not (isinstance(c, dict) and c.get("enabled") is False)


def _store(cwd):
    from ._surface import open_store
    d = _store_dir(cwd)
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


# ── the cross-session digest: settings + off switch ──────────────────────────────────────────────────
_SESSION_DEFAULTS = {"enabled": True, "max_chars": 1200, "max_items": 10, "max_sessions": 3,
                     "salience": None, "max_entry_chars": 200, "files": 5, "files_max_chars": 600}
_OFF = ("0", "false", "no", "off")


def _session_cfg(cwd):
    """Resolve the digest settings: defaults <- .inspeximus/config.json {"session_digest": {...}} <- env.
    ENV WINS, because the off switch has to be reachable without editing a file in the project."""
    cfg = dict(_SESSION_DEFAULTS)
    fc = _cfg(cwd).get("session_digest", {})
    if isinstance(fc, dict):
        for k in cfg:
            if k in fc:
                cfg[k] = fc[k]
    env = os.environ.get("INSPEXIMUS_SESSION_DIGEST", "").strip().lower()
    if env:
        cfg["enabled"] = env not in _OFF
    for key, var, cast in (("max_chars", "INSPEXIMUS_SESSION_MAX_CHARS", int),
                           ("max_items", "INSPEXIMUS_SESSION_MAX_ITEMS", int),
                           ("max_sessions", "INSPEXIMUS_SESSION_MAX_SESSIONS", int),
                           ("salience", "INSPEXIMUS_SESSION_SALIENCE", float)):
        raw = os.environ.get(var, "").strip()
        if raw:
            try:
                cfg[key] = cast(raw)
            except ValueError:
                pass                    # a typo in an env var must not disable the feature silently
    cfg["enabled"] = bool(cfg["enabled"])
    return cfg


def session_digest_enabled(cwd=None):
    """The documented off switch, in one place so every caller reads the same answer."""
    return _session_cfg(cwd or os.getcwd())["enabled"]


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


#: A command that WRITES a commit. `git commit`, `git merge`, `git revert` and `git cherry-pick` all
#: land one; `git log`, `git show`, `git status` and `git diff` all mention commits and write none.
#: The distinction has to be made on the verb, because HEAD moves for the first group only.
_COMMIT_VERBS = ("commit", "merge", "revert", "cherry-pick", "am")


def _invokes_commit(raw_cmd):
    """Does this command line actually RUN one of those git verbs?

    The first version asked `any(verb in command.lower())` and it fired on three of five controls:
    `git log --oneline | grep commit` (the word is an argument to grep), `echo 'remember to git
    commit later'` (the word is inside a quoted string), and -- the one worth remembering --
    `git diff HEAD~0 --name-only`, because the verb `am` is a substring of `--n[am]e-only`. A
    substring test over a shell command line matches text the shell never executes.

    So parse instead: split on the separators that start a new command, tokenise each segment, skip
    leading environment assignments, require the program to be `git`, and require the first
    non-flag argument after it to be the verb. `--dry-run` writes nothing and is excluded.
    """
    import shlex
    for sep in ("&&", "||", "|", ";", "\n"):
        raw_cmd = raw_cmd.replace(sep, "\x00")
    for seg in raw_cmd.split("\x00"):
        seg = seg.strip()
        if not seg:
            continue
        try:
            toks = shlex.split(seg, posix=True)
        except ValueError:                       # unbalanced quotes: not something a shell would run
            continue
        while toks and "=" in toks[0] and not toks[0].startswith("-"):
            toks = toks[1:]                      # FOO=bar git commit ...
        if not toks:
            continue
        prog = toks[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
        if prog not in ("git", "git.exe"):
            continue
        rest = toks[1:]
        if any(t == "--dry-run" for t in rest):
            continue
        verb = next((t for t in rest if not t.startswith("-")), "")
        if verb.lower() in _COMMIT_VERBS:
            return True
    return False


def _capture_commit(m, raw_cmd, cwd, sid):
    """If the command just wrote a commit, store its MESSAGE as a decision. Returns True if it did.

    Deterministic and model-free. The guards are the whole design:

      * the verb must be one that moves HEAD, so `git log --oneline` and `git show HEAD` -- which
        contain the word `commit` and a full message -- capture nothing;
      * `--dry-run` and `--amend`-less failures write no commit, so HEAD's age is checked: a commit
        older than the window belonged to some earlier run and is not this event's outcome. Without
        that check every `git status` after a commit would re-capture it;
      * a subject with no body yields no `because`, and is stored anyway with an empty one rather than
        dropped -- "we did X" with no stated reason is still the decision, and pretending we have the
        rationale would be worse than admitting we do not.

    Fail-open like the rest of this module: no repo, no git, a detached HEAD or a broken encoding all
    return False silently rather than costing the agent its tool call.
    """
    import shlex
    import subprocess
    if not _invokes_commit(raw_cmd):
        return False
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%H%x00%ct%x00%s%x00%b"],
            cwd=cwd, capture_output=True, timeout=10,
        ).stdout.decode("utf-8", "replace")
        parts = out.split("\x00")
        if len(parts) < 4:
            return False
        sha, ct, subject, body = parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3].strip()
        if not sha or not subject:
            return False
        import time as _t
        if abs(_t.time() - float(ct or 0)) > 300:      # not this event's commit
            return False
        files = subprocess.run(
            ["git", "show", "--name-only", "--format=", sha],
            cwd=cwd, capture_output=True, timeout=10,
        ).stdout.decode("utf-8", "replace").split()
    except Exception:
        return False
    text = "DECISION: " + subject
    if body:
        text += " -- because: " + _excerpt(body, 600)
    try:
        m.remember(text, key="commit::" + sha[:12], object=subject[:80], mtype="semantic",
                   tags=["decision", "commit"], session_id=sid,
                   source={"doc": "git:" + sha[:12]},
                   meta={"files": files[:20], "sha": sha})
    except TypeError:                                   # older signature: no meta/source kwargs
        try:
            m.remember(text, key="commit::" + sha[:12], mtype="semantic", tags=["decision", "commit"])
        except Exception:
            return False
    except Exception:
        return False
    return True


def capture(ev):
    cwd = ev.get("cwd") or os.getcwd()
    tool = ev.get("tool_name", "")
    ti = ev.get("tool_input", {}) or {}
    m = _store(cwd)
    # STAMP THE SESSION. Every hook event carries `session_id`; passing it to remember() puts it in
    # meta['sid'], which is what lets SessionEnd digest exactly THIS session's writes instead of guessing
    # a time window. It is inert for recall: the hierarchy filter only applies when a QUERY names a
    # level, and recall() below names none, so stamped and unstamped records rank identically.
    sid = ev.get("session_id") or None
    did = False
    if tool in ("Edit", "MultiEdit", "Write"):
        fp = _rel(ti.get("file_path", ""), cwd)
        if not fp:
            return
        new = ti.get("new_string") or ti.get("content") or ""
        m.remember(f"{fp} :: current state -> {_excerpt(new)}", key=f"file:{fp}", object=_excerpt(new, 80),
                   mtype="semantic", tags=["file", "edit"], session_id=sid)
        did = True
    elif tool == "Bash":
        raw = ti.get("command", "")
        cmd = _excerpt(raw, 200)
        if cmd:
            m.remember(f"ran: {cmd}", key=f"cmd:{hashlib.sha1(cmd.encode()).hexdigest()[:10]}",
                       object=cmd[:60], mtype="episodic", tags=["bash"], session_id=sid)
            did = True
        # A COMMIT IS A DECISION THAT IS ALREADY WRITTEN DOWN. Everything above this line is mechanics:
        # which command ran, which file holds which bytes. Measured on this plugin's own dogfood store,
        # that is 100% of what months of capture produced -- 917 records, tagged `bash` and `file`, zero
        # decisions. The decisions were not missing from the project, they were sitting in `git log`:
        # 92% of the last 200 commits there carry a substantive body and 80% state a reason in it. A
        # commit is the one moment an agent writes down a choice AND its rationale in a structured form,
        # and the hook was recording that `git commit` ran instead of what it said.
        # No model is involved: the subject is the decision, the body is the `because`, the paths are the
        # provenance. Keyed by SHA because two commits are two decisions, not a correction chain --
        # supersession is for values that change, and a commit does not retract its predecessor.
        if raw:
            _capture_commit(m, raw, cwd, sid) and (did := True)
    m._save()
    if did:
        _bump_writes(cwd)


def recall(ev):
    cwd = ev.get("cwd") or os.getcwd()
    if not injection_enabled(cwd):
        return
    q = ev.get("prompt") or ev.get("user_prompt") or ""
    if not q.strip():
        return
    # DECISIONS FIRST: a raw event log (commands, file-states) captures MECHANICS, but what an agent needs
    # recalled is the DECISIONS/RULES relevant to what it's about to do ("what did we decide, and why"). So we
    # surface decision-typed memories ahead of the command/file mechanics — otherwise the useful signal drowns
    # in 'ran: ...' noise. Decisions are stored with the "decision" tag by remember_decision().
    m = _store(cwd)
    hits = m.recall(q, k=16)
    def has(h, tag):
        return tag in (h.get("tags") or [])
    # STANDING DECISIONS ARE NOT SEARCHED FOR, THEY ARE ENUMERATED. Similarity is the wrong instrument
    # for "what is in force": measured on the cross-session corpus, the question "how does a release get
    # published these days?" shares ZERO tokens with the decision that answers it, so it ranked 25th of
    # 2,571 and no sane k reached it. Keyed supersession leaves exactly one active record per topic, so
    # the current value is a scan. These are prepended, de-duplicated against the ranked hits, and
    # bounded -- the block is prompt budget spent before the user has typed anything.
    # Only the VALUE namespace (`decision::<topic>`): commit-message decisions are events keyed by SHA,
    # never retracted, and enumerating them would paste the project's whole history into every prompt.
    try:
        standing = m.decisions_in_force(limit=4)
    except Exception:
        standing = []
    seen_ids = {h.get("id") for h in hits}
    standing = [s for s in standing if s.get("id") not in seen_ids]
    decisions = standing + [h for h in hits if has(h, "decision")][:4]
    # A SECOND STORE HOLDS THE DECISIONS, AND THIS HOOK WAS NOT READING IT.
    #
    # The ordering above is correct and was never the problem. The problem is WHICH FILE it orders.
    # This hook reads the project's coding store; `remember_decision` over MCP writes to the store the
    # MCP server was configured with, and those are different files. Measured on this repo's own
    # deployment: the project store held 6,779 records of which 5,857 are `ran: ...` bash captures and
    # exactly 16 are decisions, while the MCP store held 350 records, ALL of them decisions. So the
    # writes were happening, correctly typed, into a file the reader never opened -- and the hook
    # printed "recent mechanics" while the decision that answered the prompt sat one path away.
    #
    # That is the same class the `_store_dir` docstring above records for INTRA-project fragmentation
    # ("13 separate stores... nothing recalls across them"), one level up: this time the split is
    # between the hook's store and the agent's own decision store.
    #
    # Explicit, not guessed. Pointing at `~/.inspeximus/mcp_memory.json` by default would be inventing
    # the user's configuration; a store that is silently consulted is as bad as one silently ignored.
    # DECISIONS ONLY, and read-only: the second store is somebody else's memory, so its mechanics stay
    # out of this prompt and nothing here writes to it. Fail-open, like every other read on this path --
    # a hook that raises costs the user their turn.
    ext = (os.environ.get("INSPEXIMUS_DECISION_STORE") or "").strip()
    if ext and decisions is not None:
        try:
            if os.path.abspath(ext) != os.path.abspath(getattr(m, "path", "") or ""):
                from ._surface import open_store
                em = open_store(ext, resolve=False)
                extra = list(em.decisions_in_force(limit=4))
                extra += [h for h in em.recall(q, k=8) if has(h, "decision")]
                have = {d.get("id") for d in decisions}
                for e in extra:
                    if e.get("id") not in have and len(decisions) < 8:
                        decisions.append(e); have.add(e.get("id"))
        except Exception:
            pass
    knowledge = [h for h in hits if has(h, "knowledge") and not has(h, "decision")][:4]
    mechanics = [h for h in hits if not has(h, "decision") and not has(h, "knowledge")][:2]
    out = []
    if decisions:
        out.append("decisions/rules (what we concluded, and why):")
        # BOUNDED. A decision record is prose written for a human, and ours run to ten thousand
        # characters; eight of them unbounded put 18 KB into the context before the user finished
        # typing. The block is a POINTER -- the subject plus the head of the `because` is what makes an
        # agent stop and go read the record, and the full text is one `recall` away. Measured: 18,032
        # bytes unbounded against ~4 KB at this cap, on the same eight decisions.
        out += [f"  * {_excerpt(d['text'], 480)}" for d in decisions]
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
    """SessionStart: open the boundary and INJECT the cross-session digest. Claude Code adds this hook's
    stdout to the model's context, so what is printed here is literally what the next session knows on
    its first token. Two blocks, in priority order and each separately bounded:

      1. the DIGEST -- decisions, corrections and open threads carried over from previous sessions,
         re-resolved against the live store so a reversed decision is replaced by the current one.
      2. the known-files line -- raw tool mechanics, kept because it orients a coding agent, but printed
         AFTER the digest, capped, and labelled as mechanics so it is not mistaken for the resumable set.
         It is exactly the class of content the digest's salience threshold excludes on purpose.

    Silenced entirely by INSPEXIMUS_NO_INJECT=1: the boundary is still OPENED (the session record is
    written, so the ledger stays continuous) but nothing is printed, because stdout here IS the
    injection."""
    cwd = ev.get("cwd") or os.getcwd()
    if not injection_enabled(cwd):
        try:
            # ONE handle. `_store` constructs a fresh Inspeximus on every call, so opening on one and
            # saving on another writes a store that never saw the open -- the two-handles-on-one-file
            # shape that has already cost this project a dataset.
            m = _store(cwd)
            m.open_session(ev.get("session_id"))
            m.flush()
        except Exception:
            pass
        return
    cfg = _session_cfg(cwd)
    m = _store(cwd)
    if cfg["enabled"]:
        # A `compact` SessionStart is the SAME session continuing after a context compaction, not a new
        # one. Opening a boundary there would split one session into two digests and orphan the first.
        if (ev.get("source") or "") != "compact":
            try:
                m.open_session(ev.get("session_id"))
            except Exception:
                pass
        ctx = m.session_context(max_sessions=int(cfg["max_sessions"]), max_items=int(cfg["max_items"]),
                                max_chars=int(cfg["max_chars"]),
                                max_entry_chars=int(cfg["max_entry_chars"]),
                                threshold=cfg["salience"])
        if ctx.get("text"):
            print(ctx["text"])
    files = [it for it in getattr(m, "items", []) if "file" in (it.get("tags") or [])
             and it.get("status") != "superseded"][:int(cfg["files"])]
    if files:
        lines = "\n".join(f"- {it['text']}" for it in files)
        block = f"[inspeximus] this project's current known files (mechanics, latest state only):\n{lines}"
        print(block[:int(cfg["files_max_chars"])])
    # once-a-day, opt-out "newer version exists" courtesy (stdout is injected as context here)
    try:
        from inspeximus import __version__
        from inspeximus._update import check_for_update
        note = check_for_update(__version__, cache_dir=os.path.join(cwd, ".inspeximus"))
        if note:
            print(note)
    except Exception:
        pass


def session_end(ev):
    """SessionEnd: write ONE digest record for the session that just finished — a deterministic ledger
    diff, no LLM, nothing sent anywhere. Claude Code DISCARDS this hook's stdout, so this handler only
    writes; everything it produces is read back by the next SessionStart.

    Returns the store's report so a caller (and the off-switch test) can see what happened; the hook
    itself ignores it. With the digest disabled this returns {"enabled": False, "written": False} and
    does not touch the store at all -- not a no-content digest, no write, no state change.

    BUDGET: Claude Code gives all SessionEnd hooks 1.5s unless the settings block raises it, which is why
    close_session()'s O(n^2) sleep pass is off by default and why install() writes an explicit timeout."""
    cwd = ev.get("cwd") or os.getcwd()
    cfg = _session_cfg(cwd)
    if not cfg["enabled"]:
        # A report that only OMITS the fields it did not fill makes "nothing happened" and "something
        # happened and I forgot to say so" look identical to a caller reading with .get(). State them.
        return {"enabled": False, "written": False, "reason": "disabled",
                "id": None, "items": 0, "chars": 0}
    m = _store(cwd)
    rep = m.close_session(ev.get("session_id"), max_chars=int(cfg["max_chars"]),
                          max_items=int(cfg["max_items"]),
                          max_entry_chars=int(cfg["max_entry_chars"]),
                          threshold=cfg["salience"])
    rep["enabled"] = True
    return rep


_HOOK = {"hooks": [{"type": "command", "command": "python -m inspeximus.claude_code"}]}
# SessionEnd shares a 1.5s budget across every SessionEnd hook unless the settings raise it. The digest
# is a ledger scan, not a model call, so it is fast -- but on a large store plus a cold interpreter 1.5s
# is not a margin, and a hook that is killed mid-write writes nothing. Asking for the budget is cheaper
# than losing the session.
_HOOK_SESSION_END = {"hooks": [{"type": "command", "command": "python -m inspeximus.claude_code",
                                "timeout": 15}]}
_EVENT_HOOK = {"SessionEnd": _HOOK_SESSION_END}

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
    for evt in ("PostToolUse", "UserPromptSubmit", "SessionStart", "SessionEnd"):
        existing = json.dumps(hooks.get(evt, []))
        if not any(mark in existing for mark in _HOOK_MARKERS):
            hooks.setdefault(evt, []).append(dict(_EVENT_HOOK.get(evt, _HOOK)))
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
    # A HOOK MUST NEVER BE SILENCED BY THE CONSOLE CODEPAGE, AND THIS ONE WAS.
    #
    # Every line this module prints goes through the host's stdout, which on a Windows console is
    # cp1250/cp1252, not UTF-8. Our decision records routinely contain characters those codepages have
    # no mapping for -- an em dash, an arrow, Cyrillic, Chinese -- so `print()` raised
    # UnicodeEncodeError, the caller swallowed it, and the process exited 0 with an EMPTY stdout and an
    # EMPTY stderr. Measured on this deployment: the same event that emits 18,032 bytes under
    # PYTHONIOENCODING=utf-8 emits 0 bytes under cp1250. Not a truncated block, not a mojibake block --
    # nothing, indistinguishable from "no relevant memory found".
    #
    # That is the worst possible failure for a recall hook: the richer the memory, the more likely it
    # is to contain a character that deletes the entire block, so the hook goes quiet exactly when it
    # has the most to say. A day was lost to it -- decisions that answered the prompt were retrieved,
    # ranked and then thrown away at the last statement.
    #
    # `errors="replace"` and NOT a switch to UTF-8: the encoding is a contract with whatever reads this
    # pipe, and changing it would trade a silent crash for silent mojibake. Replacing the unmappable
    # character keeps the contract and costs one '?'.
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
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
        elif name == "SessionEnd":
            session_end(ev)
    except Exception:
        pass


if __name__ == "__main__":
    main()
