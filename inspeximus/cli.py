"""inspeximus CLI — script the memory layer from the shell, no Python or MCP server needed.

    inspeximus remember "the deploy channel is BLUE-9" --key deploy-channel
    inspeximus remember "the deploy channel is RED-2"  --key deploy-channel   # supersedes
    inspeximus recall  "what is the deploy channel?"                          # -> RED-2 (current-truth)
    inspeximus revert  deploy-channel                                         # roll back to BLUE-9
    inspeximus list -n 10                                                     # recent active memories
    inspeximus forget --key deploy-channel                                    # or --id <id> / --contains <substr>
    inspeximus stats

Store path: --path, else $INSPEXIMUS_PATH, else ./inspeximus_memory.json (same default as the MCP server, so the CLI
and `inspeximus-mcp` share one store). Recall is lexical by default; set $INSPEXIMUS_EMBED_URL (+ $INSPEXIMUS_EMBED_MODEL) to
any OpenAI-compatible /embeddings endpoint (e.g. local Ollama) for semantic recall. Zero dependencies."""
from __future__ import annotations
import argparse

from . import install as _install
import json
import os
import sys


def _embedder():
    """Optional embedder (urllib, zero-dep) — enabled only if INSPEXIMUS_EMBED_URL is set. Fail-open."""
    url = os.environ.get("INSPEXIMUS_EMBED_URL", "").strip()
    if not url:
        return None
    import urllib.request
    model = os.environ.get("INSPEXIMUS_EMBED_MODEL", "text-embedding-3-small").strip()
    key = os.environ.get("INSPEXIMUS_EMBED_KEY", "").strip()

    def embed(text: str):
        body = json.dumps({"model": model, "input": text}).encode()
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(url, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())["data"][0]["embedding"]

    return embed


def _store(path, persist_vectors: bool = False, receipts: bool = False):
    # Opened through the SHARED SURFACE opener (inspeximus/_surface.py), which is where both of the rules
    # this function used to own now live:
    #   RECEIPTS: a store that ALREADY has a receipt chain keeps it. Without this, a plain
    #   `inspeximus remember` against a receipted store opened receipts OFF and the write silently did not
    #   extend the chain -- so the very next verify_writes() reported a record with no receipt, i.e. the CLI
    #   quietly punched a hole in the evidence it exists to produce. Detected from the sidecar rather than a
    #   flag, because the user who enabled receipts in Python should not have to re-declare them at every
    #   shell call.
    #   ECHO GUARD, matched to the MCP surface. The CLI and `inspeximus-mcp` are documented as sharing one
    #   store, and they disagreed: the MCP turned the guard ON, the CLI left the library's legacy default
    #   OFF. So one CLI write could resurrect a value the MCP had retired -- undoing the measured
    #   0.00 -> 1.00 echo-resistance on the very store that advertises it. Same env var, one posture.
    # Both rules were written HERE and copied nowhere, which is how nine adapters, the MCP server and the
    # editor hook each ended up with a different one.
    # persist_vectors stays OFF by default (vectors are a re-derivable cache; writing them balloons the store
    # file on every command). `reembed` opts in — persisting is the entire point of that command.
    # receipts (OPT-IN): builds the tamper-evident write/erasure chain (persisted to <path>.receipts.json) that
    # `audit-build` exports; reload needs it on too, so audit-build/governance force it regardless of the flag.
    from ._surface import open_store
    _warn_if_store_dir_missing(path)
    return open_store(path, embed=_embedder(), persist_vectors=persist_vectors, receipts=receipts)


def _positive_k(raw: str) -> int:
    """`-k` must be >= 1. argparse's type=int caught `abc` but not 0 or -5, and those returned an empty
    result with exit 0 — a bad request reported as 'you have no memories'."""
    try:
        v = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{raw!r} is not an integer")
    if v < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {v}")
    return v


def _warn_if_store_dir_missing(path) -> None:
    """A --path whose DIRECTORY does not exist is a typo, not an empty store.

    Reads against such a path printed "(nothing in memory for that query)" and exited 0, so a user or a
    script could not tell a mistyped path from a genuinely empty memory. Writes already fail loudly here
    (_flush_or_fail returns 3, 'NOT PERSISTED'), so only the read side was silent.

    This WARNS rather than exits: a missing FILE in an existing directory is the legitimate
    brand-new-store case and must keep working, and changing read exit codes would alter the contract
    that `_flush_or_fail(required=False)` deliberately established for read-only files. Making the typo
    visible is the part that can be done without touching that contract; turning it into a non-zero exit
    for read commands is a behaviour change and is written up instead.
    """
    try:
        parent = os.path.dirname(os.path.abspath(str(path)))
        if parent and not os.path.isdir(parent):
            # ASCII ONLY. This lands on a Windows console that is not UTF-8 (cp1250 here): an em dash
            # rendered as a replacement character, and on a stricter console non-ASCII raises
            # UnicodeEncodeError and takes the whole command down. A diagnostic must never be the thing
            # that crashes the run it is diagnosing.
            print(f"warning: no such directory {parent!r} - nothing can be read from or written to "
                  f"{str(path)!r}. If this is a typo, results below are from an EMPTY store, not your data.",
                  file=sys.stderr)
    except Exception:
        pass          # diagnostics must never break a command
def _resolve_store_path(path):
    """The file the CLI will actually use, resolved the same way open_store() resolves it."""
    from ._surface import resolve_path
    return resolve_path(path)


def _out(obj, as_json):
    """Print JSON and return True (handled) when as_json; else return False so a caller's
    `_out(...) or print(human_line)` prints the human line."""
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
        return True
    return False


def _flush_or_fail(m, required: bool = True) -> int:
    """Persist and report. Without this the CLI printed `remembered <id>` and exited 0 on a store that never
    reached disk — a typo'd --path or INSPEXIMUS_PATH silently discarded every write for the whole session,
    while the library had recorded the failure all along.

    `required=False` for READ commands. A recall bumps `last_access`, which marks the store dirty, so on a
    read-only file `inspeximus recall` exited 3 "NOT PERSISTED" after printing the right answer — a reader
    should not need write access. The warning still goes to stderr; only the exit code differs."""
    try:
        m.flush()
        return 0
    except OSError as e:
        if required:
            print(f"NOT PERSISTED: {e}", file=sys.stderr)
            return 3
        print(f"warning: could not update access bookkeeping ({e})", file=sys.stderr)
        return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="inspeximus", description="inspeximus — the self-correcting memory layer (CLI).")
    ap.add_argument("--path", help="store file (default: $INSPEXIMUS_PATH or ./inspeximus_memory.json)")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--receipts", action="store_true",
                    help="enable the tamper-evident write/erasure chain (needed to later `audit-build`)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("remember", help="store a memory (a --key makes it correctable/supersedable)")
    r.add_argument("text")
    r.add_argument("--key", help="supersession key (e.g. subject::relation) — a new value retires the old")
    r.add_argument("--object", dest="object", help="the value/object for this key")
    r.add_argument("--tags", help="comma-separated tags")
    r.add_argument("--source", help="the origin id this fact is attributable to — REQUIRED for the record "
                                    "to be erasable by subject later (see forget-subject)")
    r.add_argument("--derived-from", dest="derived_from", action="append", default=[],
                   help="an id this memory was BUILT FROM (repeatable). Erasing the source then erases "
                        "what was derived from it, and erasure-audit walks these edges — with none "
                        "declared it reports 'unaudited', never a pass")
    r.add_argument("--type", dest="mtype", choices=["episodic", "semantic", "procedural"], help="memory type")

    q = sub.add_parser("recall", help="retrieve current-truth memories (superseded values hidden)")
    q.add_argument("query")
    # RANGE, not just type. argparse already rejected `-k abc` (exit 2), but `-k 0` and `-k -5` were
    # accepted and printed "(nothing in memory for that query)" with exit 0 — a caller that computes k
    # and lands on 0 or negative got an answer indistinguishable from an empty store. An invalid
    # request must not be reported as an empty result.
    q.add_argument("-k", type=_positive_k, default=6, help="how many to return (>= 1)")

    v = sub.add_parser("revert", help="roll a key back to the value it superseded")
    v.add_argument("key")

    f = sub.add_parser("forget", help="hard-delete memories (by --key, --id, or --contains)")
    f.add_argument("--key")
    f.add_argument("--id")
    f.add_argument("--contains", help="delete every memory whose text contains this substring")
    f.add_argument("--dry-run", action="store_true",
                   help="preview what would be deleted (with a text sample) — deletes nothing")

    fs = sub.add_parser("forget-subject",
                        help="right-to-erasure by SUBJECT: delete everything attributable to a source, "
                             "including records that inherited it through lineage")
    fs.add_argument("subject", help="the canonical source string you wrote with (remember --source)")
    fs.add_argument("--request-id", help="the DSAR/ticket id, recorded in the tombstone")
    fs.add_argument("--basis", default="gdpr-art17", help="legal/operational basis for the erasure")
    fs.add_argument("--dry-run", action="store_true",
                    help="preview the blast radius: direct vs inherited, and which OTHER subjects go with it")
    fs.add_argument("--allow-ambiguous", action="store_true",
                    help="proceed when the subject shares a canonical form with a DIFFERENT source "
                         "(read the refusal first — it names the other subject)")

    ls = sub.add_parser("list", help="list recent active memories")
    ls.add_argument("-n", type=int, default=10)

    sub.add_parser("stats", help="store summary")

    br = sub.add_parser("browse", help="render a self-contained offline HTML memory browser")
    br.add_argument("--out", default="inspeximus_browser.html", help="output HTML file")
    br.add_argument("--open", action="store_true", help="open it in the default browser after writing")

    dc = sub.add_parser("decision", help="store a DECISION (what you chose + why), topic-keyed + supersedable")
    dc.add_argument("decision")
    dc.add_argument("--because", help="rationale")
    dc.add_argument("--topic", help="topic slug -> a new decision on it supersedes the old")
    dc.add_argument("--source", help="who or what this decision is ABOUT or came from ('crm/alice') — "
                                     "without it the decision is attributable to nothing but its own id, "
                                     "so forget-subject cannot reach it and it survives a DSAR that "
                                     "erased everything else about that person")
    dc.add_argument("--derived-from", dest="derived_from", action="append", default=[],
                    help="an id this decision was built from (repeatable); erasing the source then "
                         "erases the decision drawn from it")

    sub.add_parser("contradictions", help="list mutually-incompatible memories (flagged, not auto-resolved)")
    sub.add_parser("governance", help="governance/erasure/tamper-evidence snapshot")
    rz = sub.add_parser("residue", help="did the bytes actually go? scan ANY directory for values that "
                                        "should be erased (works on other vendors' stores too)")
    rz.add_argument("--root", required=True, help="directory to search")
    rz.add_argument("--value", action="append", default=[], help="a value that should be gone (repeatable)")
    rz.add_argument("--value-file", help="file with one value per line")
    rz.add_argument("--max-file-mb", type=float, default=512.0)

    co = sub.add_parser("consolidate", help="run the dedup/consolidation pass (optionally prune to --keep)")
    co.add_argument("--keep", type=int, default=None)

    wy = sub.add_parser("why", help="explain why memories were recalled for a query (per-channel breakdown)")
    wy.add_argument("query")

    ea = sub.add_parser("erasure-audit", help="after an erasure: is it actually gone, INCLUDING through "
                                              "everything derived from it? (exit 1 if residue is found)")
    ea.add_argument("--subject", help="the canonical source that was erased (as passed to forget-subject)")
    ea.add_argument("--value", action="append", default=[],
                    help="an erased string to also scan for (heuristic; repeatable)")

    pv = sub.add_parser("provenance", help="where a fact came from: source, lineage, trust grade, what it "
                                           "superseded, and whether it still matches its write receipt")
    pv.add_argument("key", nargs="?", help="the supersession key of the fact (omit when using --id)")
    pv.add_argument("--id", help="look up one record by id instead of by key")

    di = sub.add_parser("distill", help="LLM-distill a transcript into memories (needs INSPEXIMUS_LLM_URL)")
    di.add_argument("--file", help="read text from a file (else stdin)")
    di.add_argument("--source", help="who or what the transcript came from ('crm/alice') — a distilled "
                                     "transcript is usually ABOUT someone, and without this the memories "
                                     "it produces are attributable to nothing, so forget-subject cannot "
                                     "reach them")

    re_ = sub.add_parser("reembed", help="rebuild embeddings for records that have none (after an embed-recipe "
                                         "change dropped them); needs an embedder configured")
    re_.add_argument("--all", action="store_true", help="re-embed EVERY record, not just the ones missing a vector")
    re_.add_argument("--batch", type=int, default=None, help="cap how many records this run re-embeds")

    dep = sub.add_parser("deprecate", help="record a refactor: code symbol OLD was replaced by NEW "
                                           "(coding-agent guard; keyed supersession)")
    dep.add_argument("old", help="the removed/renamed symbol as it appears in code (e.g. db.query)")
    dep.add_argument("new", help="what to use instead (e.g. db.execute)")
    dep.add_argument("--reason", help="one-line why (shown when the old symbol is flagged)")

    ck = sub.add_parser("check-code", help="scan files for any deprecated symbol they RESURRECT and exit "
                                           "non-zero if any (drop into CI / pre-commit)")
    ck.add_argument("paths", nargs="+", help="source files to scan")

    cp_ = sub.add_parser("compliance", help="article-labelled agent-memory compliance EVIDENCE report "
                                            "(EU AI Act Art.12/15/19 + GDPR Art.17/30) — HTML or JSON")
    cp_.add_argument("--out", default=None, help="write a self-contained HTML report here")
    cp_.add_argument("--expected-pubkey", default=None, help="pin the integrity check to this key")
    cp_.add_argument("--check", action="store_true",
                     help="CI GATE: exit non-zero if a compliance invariant is violated (posture regressed)")
    cp_.add_argument("--max-pii-age-days", type=float, default=None,
                     help="with --check: fail if any active PII record is older than this (storage limitation)")
    cp_.add_argument("--prior-anchor", default=None,
                     help="with --check: json anchor to verify the history is an append-only extension of")
    cp_.add_argument("--allow-no-receipts", action="store_true",
                     help="with --check: do not fail when receipts are disabled")

    rt = sub.add_parser("retention", help="storage-limitation enforcement: erase records past a retention age "
                                          "(GDPR Art.5(1)(e)) — DRY-RUN unless --apply")
    rt.add_argument("--max-age-days", type=float, required=True, help="records older than this are eligible")
    rt.add_argument("--all", action="store_true", help="apply to EVERY record (default: PII-tagged only)")
    rt.add_argument("--apply", action="store_true", help="actually erase (default: dry-run, list only)")

    ab = sub.add_parser("audit-build", help="export a portable, content-free audit bundle "
                                            "(EU AI Act Art.12 / GDPR record-keeping) — hand it to an auditor")
    ab.add_argument("--out", default="inspeximus_audit_bundle.json", help="output json path")
    ab.add_argument("--expected-pubkey", default=None, help="pin the signature-authenticity check to this key")

    av = sub.add_parser("audit-verify", help="verify an audit bundle OFFLINE (needs only the file, no store)")
    av.add_argument("bundle", help="the bundle json to verify")
    av.add_argument("--witnesses", default=None, help="comma-separated allowlisted witness pubkeys (hex)")
    av.add_argument("--threshold", type=int, default=1, help="k-of-n witness threshold")
    av.add_argument("--store", default=None,
                    help="the store file the bundle came from; binds the receipts to the CONTENT it "
                         "serves today. Without it a clean chain over substituted text still reads PASS.")

    ins = sub.add_parser("install", help="register the MCP server in an editor's own config file")
    ins.add_argument("--ide", required=True,
                     help="host to configure: " + ", ".join(sorted(_install.HOSTS)))
    ins.add_argument("--scope", choices=["user", "project"], default=None,
                     help="user-level (default) or project-level config, where the host supports it")
    ins.add_argument("--project", default=None, help="project directory for project scope (default: cwd)")
    ins.add_argument("--store", default=None, help="value for INSPEXIMUS_PATH in the written config")
    ins.add_argument("--name", default=_install.SERVER_NAME, help="server name to write")
    ins.add_argument("--dry-run", action="store_true", help="print the exact diff, write nothing")

    a = ap.parse_args(argv)

    # `install` edits an editor's config; it must never touch a memory store. Opening one here would
    # create inspeximus_memory.json in the working directory as a side effect of asking for help.
    if a.cmd == "install":
        p = _install.plan(a.ide, scope=a.scope, project=a.project, store_path=a.store, name=a.name)
        print(_install.render(p, dry_run=a.dry_run))
        if p.get("error"):
            return 2
        if a.dry_run:
            return 0
        ok, msg = _install.apply(p)
        print(f"  {msg}")
        return 0 if ok else 1

    # audit-verify needs only the bundle file — never open a store (that would create one as a side effect).
    if a.cmd == "audit-verify":
        from inspeximus.audit_bundle import verify_bundle
        with open(a.bundle, encoding="utf-8") as f:
            bundle = json.load(f)
        wl = [w.strip() for w in a.witnesses.split(",")] if a.witnesses else None
        items = None
        if a.store:
            # Opening a store CREATES it when the path does not exist, and an auditor who mistyped would
            # then be handed a clean verdict over the empty store they had just made -- the same shape as
            # the erasure certificate that reported valid while its absence proof pointed at a typo.
            from inspeximus.audit_bundle import load_store_items
            items = load_store_items(a.store)     # ONE implementation; see its docstring
            if items is None:
                print(f"  FAIL --store {a.store} does not exist; refusing to create a store while "
                      f"verifying, because an empty one verifies clean")
                return 1
        res = verify_bundle(bundle, witnesses=wl, threshold=a.threshold, store_items=items)
        if a.json:
            _out(res, True)
        else:
            for c in res["checks"]:
                print(f"  OK   {c}")
            for pr in res["problems"]:
                print(f"  FAIL {pr}")
            for lim in res.get("limits") or []:
                print(f"  NOTE {lim}")
            s = res["summary"]
            print(f"\nVERDICT: {'PASS' if res['ok'] else 'FAIL'}  "
                  f"({s.get('writes')} writes, {s.get('erasures')} erasures"
                  f", content {'checked' if s.get('content_checked') else 'NOT checked'}"
                  f"{', operator-adversarial' if s.get('operator_adversarial') else ''})")
        return 0 if res["ok"] else 1

    # audit-build/compliance/retention must have the receipt+tombstone chains, so force receipts on.
    # `provenance` REPORTS on the receipt chain, so it must load it — otherwise a receipted store would be
    # described as "receipts off at write time", which is not merely unhelpful but wrong.
    # Captured BEFORE the store is opened: Inspeximus() CREATES the parent directory, so any
    # "does this store exist?" question asked afterwards always answers yes. (A first version of the
    # check-code gate below tested the directory after this line and could therefore never fire.)
    _store_existed = os.path.exists(str(_resolve_store_path(a.path)))
    m = _store(a.path, receipts=a.receipts or a.cmd in ("audit-build", "compliance", "retention", "provenance"))

    if a.cmd == "retention":
        from inspeximus.compliance import retention_sweep
        res = retention_sweep(m, a.max_age_days, pii_only=not a.all, apply=a.apply)
        if a.apply and res["applied"]:
            m._save(force=True)
        if a.json:
            _out(res, True)
        else:
            scope = "record(s)" if a.all else "PII record(s)"
            if a.apply:
                print(f"retention: erased {res['erased']} {scope} older than {a.max_age_days} days "
                      f"(request_id={res['request_id']})")
            else:
                print(f"retention (DRY-RUN): {res['eligible']} {scope} older than {a.max_age_days} days would be "
                      f"erased. Re-run with --apply to enforce.")
        return 0

    if a.cmd == "compliance" and a.check:
        from inspeximus.compliance import compliance_check
        prior = None
        if a.prior_anchor:
            with open(a.prior_anchor, encoding="utf-8") as f:
                prior = json.load(f)
        res = compliance_check(m, require_receipts=not a.allow_no_receipts,
                               max_pii_age_days=a.max_pii_age_days, prior_anchor=prior)
        if a.json:
            _out(res, True)
        else:
            for v in res["violations"]:
                print(f"  VIOLATION [{v['article']}] {v['code']}: {v['detail']}")
            print(f"compliance --check: {'PASS' if res['ok'] else str(len(res['violations'])) + ' violation(s)'} "
                  f"(checked: {', '.join(res['checked'])})", file=sys.stderr)
        return 0 if res["ok"] else 1

    if a.cmd == "compliance":
        from inspeximus.compliance import compliance_report, render_html
        rep = compliance_report(m, expected_pubkey=a.expected_pubkey)
        if a.json:
            _out(rep, True)
        elif a.out:
            with open(a.out, "w", encoding="utf-8") as f:
                f.write(render_html(rep))
            print(f"wrote compliance report -> {a.out}  "
                  f"({rep['summary']['controls_with_evidence']}/{len(rep['controls'])} controls with live evidence)")
        else:
            from inspeximus.compliance import _STATUS_LABEL
            for c in rep["controls"]:
                print(f"  [{_STATUS_LABEL.get(c['status'], c['status'])}] {c['article']} {c['title']}")
            print(f"\nscope: {rep['scope']}")
        return 0

    if a.cmd == "audit-build":
        from inspeximus.audit_bundle import build_bundle
        bundle = build_bundle(m, expected_pubkey=a.expected_pubkey)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(bundle, f, ensure_ascii=False, indent=2)
        n = bundle["anchor"]["n_writes"]
        _out({"out": a.out, "writes": n, "erasures": bundle["anchor"]["n_tombstones"]}, a.json) or print(
            f"wrote audit bundle -> {a.out}  ({n} writes, {bundle['anchor']['n_tombstones']} erasures)"
            + ("\nnote: 0 writes — this store was not written with receipts enabled; write with "
               "`inspeximus --receipts remember ...` to build an auditable chain." if n == 0 else ""))
        return 0

    if a.cmd == "remember":
        tags = [t.strip() for t in a.tags.split(",")] if a.tags else None
        mid = m.remember(a.text, key=a.key, object=a.object, tags=tags, mtype=a.mtype,
                         source={"doc": a.source} if a.source else None,
                         derived_from=a.derived_from or None)
        m._save(force=True)
        _out({"id": mid, "key": a.key}, a.json) or print(f"remembered {mid}" + (f" [key={a.key}]" if a.key else ""))
        return _flush_or_fail(m)

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
        # A REFUSED revert used to print "reverted region: now -> {'ok': False, ...}" and exit 0, so
        # `inspeximus revert region && echo rolled-back` printed rolled-back after nothing had rolled back.
        # Every other refusal path in this CLI exits 1 or 2; this one is the operation we put on the front
        # page, and no test invoked it. --json exited 0 as well, so a script parsing the payload had to
        # know to look past the exit code.
        if not res.get("ok"):
            reason = res.get("reason") or "refused"
            if a.json:
                _out(res, True)
            else:
                print(f"revert refused for {a.key}: {reason}", file=sys.stderr)
            return 1
        _out(res, a.json) or print(f"reverted {a.key}: now -> {res.get('restored') or res.get('active') or res}")

    elif a.cmd == "forget-subject":
        # The library has had subject erasure since 1.0; the CLI never exposed it, so the one operation a
        # DSAR actually needs was unreachable from the terminal. Found by an audit of the guard's usability,
        # not of the guard.
        from .core import AmbiguousSubject
        try:
            res = m.forget_subject(a.subject, request_id=a.request_id, basis=a.basis,
                                   dry_run=a.dry_run, allow_ambiguous=a.allow_ambiguous)
        except AmbiguousSubject as e:
            print(f"refused: {e}", file=sys.stderr)
            return 2
        if res.get("dry_run"):
            print(f"would erase {res['would_erase']} record(s): "
                  f"{res['direct']} naming the subject, {res['inherited']} reached through lineage")
            if res.get("also_carrying"):
                print("  also carrying data from: "
                      + ", ".join(f"{k} ({v})" for k, v in res["also_carrying"].items()))
            for row in res.get("sample", []):
                print(f"  [{row['why']:<9}] {row['id']}  {row['text'][:70]}")
            if res.get("ambiguous_with"):
                print(f"  EXCLUDED as ambiguous: {res['ambiguous_with']} "
                      f"({res.get('excluded_by_ambiguity')} record(s)); --allow-ambiguous to include")
        else:
            print(f"erased {res['erased']} record(s), {res['tombstones']} tombstone(s)")
        return 0

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
        res = m.forget(ids=ids, where=where, dry_run=a.dry_run)
        if res.get("dry_run"):
            if a.json:
                _out(res, True)
            else:
                print(f"forget (DRY-RUN): {res['would_forget']} memory(ies) would be deleted. Sample:")
                for s in res["sample"]:
                    print(f"  - {s['text']}" + (f"  [key={s['key']}]" if s.get("key") else ""))
                print("Re-run without --dry-run to delete.")
            return 0
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

    elif a.cmd == "reembed":
        if m.embed is None:
            print("reembed: no embedder configured (set INSPEXIMUS_EMBED_URL, or .inspeximus/config.json {\"embed\":{...}})",
                  file=sys.stderr)
            return 2
        m = _store(a.path, persist_vectors=True)      # re-open so the rebuilt vectors actually reach disk
        res = m.reembed(only_missing=not a.all, batch=a.batch)
        _out(res, a.json) or print(
            f"re-embedded {res['reembedded']} ({res['failed']} failed, {res['remaining']} still without a vector)"
            + (f"\n{res['warning']}" if res.get("warning") else ""))

    elif a.cmd == "browse":
        from inspeximus.browser import write_html
        path = write_html(m, a.out)
        if a.open:
            import webbrowser, pathlib
            # as_uri(), not "file://" + abspath: on Windows the latter yields file://C:\... (backslashes,
            # missing third slash) and it mangles spaces/non-ASCII in the path.
            webbrowser.open(pathlib.Path(path).resolve().as_uri())
        _out({"written": path}, a.json) or print(f"wrote memory browser -> {path}" + ("  (opened)" if a.open else ""))

    elif a.cmd == "decision":
        mid = m.remember_decision(a.decision, because=a.because, topic=a.topic,
                                  source=a.source or None, derived_from=a.derived_from or None)
        m._save(force=True)
        _out({"id": mid, "topic": a.topic}, a.json) or print(f"decision stored {mid}" + (f" [topic={a.topic}]" if a.topic else ""))

    elif a.cmd == "contradictions":
        pairs = m.contradictions()
        if a.json:
            _out(pairs, True)
        elif not pairs:
            print("(no contradictions)")
        else:
            for p in pairs:
                print(f"- {p.get('a_text','')}  <>  {p.get('b_text','')}")

    elif a.cmd == "residue":
        from .erasure_residue import scan_residue
        values = list(a.value)
        if a.value_file:
            with open(a.value_file, encoding="utf-8") as fh:
                values += [ln.strip() for ln in fh if ln.strip()]
        rep = scan_residue(a.root, values, max_file_mb=a.max_file_mb)
        if not _out(rep, a.json):
            print(f"checked {rep['checked_files']} file(s) under {a.root}")
            for f in rep["findings"]:
                where = (f" [{f.get('table')}.{f.get('column')} x{f.get('rows')}]"
                         if f["kind"] == "LIVE" else "")
                print(f"  {f['kind']:12s} {f['path']}{where}   fp={f['fingerprint']}")
            for problem in rep["problems"]:
                print(f"  ! {problem}")
            print("RESULT:", "clean - no residue found" if rep["ok"] else "residue found (see above)")
        # a non-zero exit so this is usable as a gate in CI or a DSAR runbook
        raise SystemExit(0 if rep["ok"] else 1)

    elif a.cmd == "governance":
        _out(m.governance_report(), a.json) or print(json.dumps(m.governance_report(), indent=2, default=str))

    elif a.cmd == "consolidate":
        res = m.consolidate(keep=a.keep)
        m._save(force=True)
        _out(res, a.json) or print(f"consolidated: {res}")

    elif a.cmd == "why":
        exp = m.why_recalled(a.query)
        _out(exp, a.json) or print(json.dumps(exp, indent=2, default=str))

    elif a.cmd == "erasure-audit":
        res = m.erasure_audit(subject=a.subject, values=a.value or None)
        if a.json:
            _out(res, True)
        else:
            cov = res["coverage"]
            print(f"scanned {cov['records']} record(s)"
                  + (f" for subject {a.subject!r}" if a.subject else ""))
            # coverage FIRST: a pass on a store with no declared lineage means nothing was inspected
            print(f"  coverage  {cov['with_declared_lineage']}/{cov['records']} record(s) declare lineage "
                  f"(ratio {cov['declared_ratio']})")
            if res["verdict"] == "residue_found":
                print(f"  RESIDUE  {len(res['residue'])} finding(s) tied to a deliberate erasure:")
                for f in res["residue"]:
                    print(f"    [{f['kind']}] {f['id']}")
                    print(f"      {f['detail']}")
            elif res["verdict"] == "unaudited":
                print("  UNAUDITED  no record declares lineage, so nothing structural was inspected. "
                      "This is NOT a pass -- declare derived_from on derived writes to make it mean something.")
            else:
                print("  NO DECLARED RESIDUE  nothing reachable from the erased material through declared "
                      "lineage survived")
            for f in res["advisory"]:
                print(f"    [advisory/{f['kind']}] {f['id']}: {f['detail']}")
                if f.get("cause"):
                    print(f"      cause: {f['cause']}")
            for lim in res["limits"]:
                print(f"  limit  {lim}")
        return 1 if res["verdict"] == "residue_found" else 0

    elif a.cmd == "provenance":
        if (a.key is None) == (a.id is None):
            print("provenance: give a KEY or --id (exactly one)", file=sys.stderr)
            return 2
        p = m.provenance(key=a.key, id=a.id)
        if a.json:
            _out(p, True)
        elif not p["found"]:
            print(f"no such fact: {a.key or a.id}")
            return 1
        else:
            cur, org, integ = p["current"], p["origin"], p["integrity"]
            src = org["source"]
            src = (src.get("doc") or src.get("name") or json.dumps(src)) if isinstance(src, dict) else (src or "-")
            print(f"fact      {p['key'] or '(unkeyed)'}")
            print(f"  now       {cur['object'] or cur['text']}  [{cur['status']}]")
            print(f"  source    {src}" + ("  (attested)" if org["attested"] else "  (not attested)"))
            if org["derived"]:
                print(f"  lineage   derived from {len(org['ancestors'])} ancestor(s); "
                      f"taint: {', '.join(org['inherited_taint']) or '-'}")
            elif org["orphan"]:
                print("  lineage   ORPHAN: no source and no resolvable parent -- earns no corroboration standing")
            else:
                print("  lineage   primary observation")
            if org["actor"]:
                print("  actor     " + ", ".join(f"{k}={v}" for k, v in org["actor"].items()))
            print(f"  trust     {p['trust']['grade']}")
            if p["timeline"]:
                print(f"  history   {len(p['timeline'])} value(s), {p['superseded_count']} retired")
                for h in p["timeline"]:
                    label = h["object"] or (h["text"] or "")[:48]
                    print(f"              {label}  ->  " +
                          (f"retired by {h['policy'] or 'unstamped'}" if h["status"] == "superseded"
                           else h["status"]))
            if not integ["receipted"]:
                print("  integrity no write receipt for this record (receipts off at write time)")
            else:
                bits = ["content " + ("matches" if integ["content_matches_receipt"] else "DIFFERS FROM"),
                        "attribution " + ({True: "matches", False: "DIFFERS FROM", None: "not committed in"}
                                          [integ["attribution_matches_receipt"]]),
                        "chain " + ("ok" if integ["chain_ok"] else "BROKEN"),
                        "signed" if integ["signed"] else "unsigned"]
                print("  integrity " + "; ".join(bits) + " the write receipt")
            for lim in p["limits"]:
                print(f"  limit     {lim}")

    elif a.cmd == "deprecate":
        from inspeximus.code_guard import deprecate_symbol
        try:
            res = deprecate_symbol(m, a.old, a.new, reason=a.reason or "")
        except ValueError as e:
            print(f"deprecate: {e}", file=sys.stderr)
            return 2
        m._save(force=True)
        _out(res, a.json) or print(f"deprecated `{res['symbol']}` -> `{res['replacement']}`"
                                   + (f"  ({res['reason']})" if res['reason'] else ""))

    elif a.cmd == "check-code":
        from inspeximus.code_guard import scan_lines, _deprecations
        # A GATE MUST NOT GO GREEN ON A STORE IT COULD NOT READ. check-code is run in CI and
        # pre-commit to FAIL a build, and it already fails closed on a source file it cannot open
        # (OSError -> exit 2). The store side was the opposite: a mistyped --path produced an empty
        # store, an empty store declares no deprecations, and scan_lines() then returns [] for every
        # file. Measured: the same violating file exits 1 against the real store and 0 with silent
        # output against a --path whose directory does not exist. A green build that checked nothing
        # is the worst outcome a guard can produce, because it is indistinguishable from a clean one.
        #
        # The two cases are NOT the same and are treated differently on purpose:
        #   unusable store path  -> exit 2. There is no honest verdict to give.
        #   zero deprecations    -> still exit 0, because a project that has declared none is a real
        #                           and correct state -- but SAY SO, so "clean" is never silent about
        #                           having had nothing to check against.
        # ASCII only: this prints to a Windows console that is not UTF-8 (cp1250 here), where a
        # non-ASCII character can raise UnicodeEncodeError and kill the command.
        if not _store_existed:
            print(f"check-code: no store at {str(_resolve_store_path(a.path))!r}, so no deprecation "
                  f"could be read and nothing was checked. Refusing to report clean.", file=sys.stderr)
            return 2
        _dep_count = len(_deprecations(m))
        violations = []
        for path in a.paths:
            try:
                code = open(path, encoding="utf-8", errors="replace").read()
            except OSError as e:
                print(f"check-code: {e}", file=sys.stderr)
                return 2
            for h in scan_lines(m, code):
                h = dict(h, file=path)
                violations.append(h)
        if a.json:
            _out(violations, True)
        else:
            for h in violations:
                print(f"{h['file']}:{h['line']}: resurrected `{h['symbol']}` -> use `{h['replacement']}`"
                      + (f" ({h['reason']})" if h['reason'] else ""))
            # Name what was checked AGAINST. "clean" with zero deprecations declared means the gate
            # had nothing to compare to, which reads identically to a real pass unless it says so.
            _against = (f"0 deprecations declared in {_resolve_store_path(a.path)} - nothing to check against"
                        if not _dep_count else f"{_dep_count} deprecation(s) declared")
            print(f"check-code: {'clean' if not violations else str(len(violations)) + ' resurrected deprecated symbol(s)'}"
                  f" ({_against})", file=sys.stderr)
        return 1 if violations else 0

    elif a.cmd == "distill":
        from inspeximus import default_distiller
        try:
            text = open(a.file, encoding="utf-8").read() if a.file else sys.stdin.read()
        except OSError as e:                    # an unreadable --file deserves the same tidy exit as the
            print(f"distill: {e}", file=sys.stderr)   # missing-endpoint case below, not a raw traceback
            return 2
        try:
            distiller = default_distiller()
        except RuntimeError as e:
            print(f"distill: {e}", file=sys.stderr)
            return 2
        # core.distill_and_remember has taken a `source` all along; the CLI simply never offered one, so
        # a transcript distilled here produced records attributable to nothing. Same shape as the
        # `decision` gap, one subcommand over -- found by reading the branch, not by a scan, because the
        # scan that flagged it also flagged two subcommands that do not write at all.
        # `{"doc": ...}`, not the bare string. My first version passed the string straight through, and
        # remember() requires a dict -- every item would have raised and the command would have reported
        # "captured: 0" with no error at all. Caught by running it rather than reading it.
        res = m.distill_and_remember(text, distiller,
                                     source={"doc": a.source} if a.source else None)
        m._save(force=True)
        _out(res, a.json) or print(f"distilled: {res.get('captured',0)} kept "
                                   f"({res.get('decisions',0)} decisions, {res.get('facts',0)} facts, "
                                   f"{res.get('dropped',0)} dropped)")
    return _flush_or_fail(m, required=False)


if __name__ == "__main__":
    raise SystemExit(main())
