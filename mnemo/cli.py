"""mnemo CLI — script the memory layer from the shell, no Python or MCP server needed.

    mnemo remember "the deploy channel is BLUE-9" --key deploy-channel
    mnemo remember "the deploy channel is RED-2"  --key deploy-channel   # supersedes
    mnemo recall  "what is the deploy channel?"                          # -> RED-2 (current-truth)
    mnemo revert  deploy-channel                                         # roll back to BLUE-9
    mnemo list -n 10                                                     # recent active memories
    mnemo forget --key deploy-channel                                    # or --id <id> / --contains <substr>
    mnemo stats

Store path: --path, else $MNEMO_PATH, else ./mnemo_memory.json (same default as the MCP server, so the CLI
and `mnemo-mcp` share one store). Recall is lexical by default; set $MNEMO_EMBED_URL (+ $MNEMO_EMBED_MODEL) to
any OpenAI-compatible /embeddings endpoint (e.g. local Ollama) for semantic recall. Zero dependencies."""
from __future__ import annotations
import argparse
import json
import os
import sys


def _embedder():
    """Optional embedder (urllib, zero-dep) — enabled only if MNEMO_EMBED_URL is set. Fail-open."""
    url = os.environ.get("MNEMO_EMBED_URL", "").strip()
    if not url:
        return None
    import urllib.request
    model = os.environ.get("MNEMO_EMBED_MODEL", "text-embedding-3-small").strip()
    key = os.environ.get("MNEMO_EMBED_KEY", "").strip()

    def embed(text: str):
        body = json.dumps({"model": model, "input": text}).encode()
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(url, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())["data"][0]["embedding"]

    return embed


def _store(path):
    from mnemo import Mnemo
    p = path or os.environ.get("MNEMO_PATH") or "mnemo_memory.json"
    return Mnemo(path=p, embed=_embedder())


def _out(obj, as_json):
    """Print JSON and return True (handled) when as_json; else return False so a caller's
    `_out(...) or print(human_line)` prints the human line."""
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
        return True
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mnemo", description="mnemo — the self-correcting memory layer (CLI).")
    ap.add_argument("--path", help="store file (default: $MNEMO_PATH or ./mnemo_memory.json)")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("remember", help="store a memory (a --key makes it correctable/supersedable)")
    r.add_argument("text")
    r.add_argument("--key", help="supersession key (e.g. subject::relation) — a new value retires the old")
    r.add_argument("--object", dest="object", help="the value/object for this key")
    r.add_argument("--tags", help="comma-separated tags")
    r.add_argument("--type", dest="mtype", choices=["episodic", "semantic", "procedural"], help="memory type")

    q = sub.add_parser("recall", help="retrieve current-truth memories (superseded values hidden)")
    q.add_argument("query")
    q.add_argument("-k", type=int, default=6, help="how many to return")

    v = sub.add_parser("revert", help="roll a key back to the value it superseded")
    v.add_argument("key")

    f = sub.add_parser("forget", help="hard-delete memories (by --key, --id, or --contains)")
    f.add_argument("--key")
    f.add_argument("--id")
    f.add_argument("--contains", help="delete every memory whose text contains this substring")

    ls = sub.add_parser("list", help="list recent active memories")
    ls.add_argument("-n", type=int, default=10)

    sub.add_parser("stats", help="store summary")

    a = ap.parse_args(argv)
    m = _store(a.path)

    if a.cmd == "remember":
        tags = [t.strip() for t in a.tags.split(",")] if a.tags else None
        mid = m.remember(a.text, key=a.key, object=a.object, tags=tags, mtype=a.mtype)
        m._save(force=True)
        _out({"id": mid, "key": a.key}, a.json) or print(f"remembered {mid}" + (f" [key={a.key}]" if a.key else ""))

    elif a.cmd == "recall":
        hits = m.recall(a.query, k=a.k) or []
        if a.json:
            _out(hits, True)
        elif not hits:
            print("(nothing in memory for that query)")
        else:
            for h in hits:
                print(f"- {h.get('text','')}")

    elif a.cmd == "revert":
        res = m.revert(a.key)
        m._save(force=True)
        _out(res, a.json) or print(f"reverted {a.key}: now -> {res.get('restored') or res.get('active') or res}")

    elif a.cmd == "forget":
        where = None
        if a.contains:
            needle = a.contains.lower()
            where = lambda rec: needle in (rec.get("text") or "").lower()
        elif a.key:
            where = lambda rec: rec.get("key") == a.key
        ids = [a.id] if a.id else None
        if not ids and where is None:
            print("forget: pass --key, --id, or --contains", file=sys.stderr)
            return 2
        res = m.forget(ids=ids, where=where)
        m._save(force=True)
        _out(res, a.json) or print(f"forgot {res.get('forgotten', 0)} memory(ies)")

    elif a.cmd == "list":
        rows = [r for r in getattr(m, "items", []) if r.get("status") == "active"]
        rows.sort(key=lambda r: r.get("ts", 0), reverse=True)
        rows = rows[: a.n]
        if a.json:
            _out([{"id": r["id"], "key": r.get("key"), "text": r.get("text", "")} for r in rows], True)
        else:
            for r in rows:
                k = f" [key={r['key']}]" if r.get("key") else ""
                print(f"- {r.get('text','')}{k}")

    elif a.cmd == "stats":
        items = getattr(m, "items", [])
        active = sum(1 for r in items if r.get("status") == "active")
        superseded = sum(1 for r in items if r.get("status") == "superseded")
        keyed = sum(1 for r in items if r.get("key"))
        st = {"path": str(m.path), "total": len(items), "active": active,
              "superseded": superseded, "keyed": keyed}
        _out(st, a.json) or print(
            f"{st['path']}: {st['total']} total ({active} active, {superseded} superseded, {keyed} keyed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
