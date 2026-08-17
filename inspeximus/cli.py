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


def _receipt_key(key_file=None):
    """The Ed25519 secret key the tombstones are signed with, as hex, or None.

    Order: --receipt-key-file, then $INSPEXIMUS_RECEIPT_KEY_FILE, then $INSPEXIMUS_RECEIPT_KEY (hex).

    A FILE rather than a flag value on purpose: an argument is visible in `ps`, in shell history and in
    CI logs, and a signing key that leaks makes every tombstone it ever signed forgeable -- which is the
    one property the certificate sells. There is deliberately no `--receipt-key <hex>`.

    The key must be present when the ERASURE runs, not when the certificate is printed: tombstones are
    signed as they are created. Passing it only to `erasure-certificate` yields an unsigned chain, and
    `erasure-verify` then reports `signatures_valid: null` with an UNSIGNED limit rather than pretending.
    """
    p = key_file or os.environ.get("INSPEXIMUS_RECEIPT_KEY_FILE", "").strip()
    if p:
        with open(p, encoding="utf-8") as fh:
            return fh.read().strip()
    return os.environ.get("INSPEXIMUS_RECEIPT_KEY", "").strip() or None


def _store(path, persist_vectors: bool = False, receipts: bool = False, receipt_key=None):
    # Opened through the SHARED SURFACE opener (inspeximus/_surface.py), which is where both of the rules
    # this function used to own now live:
    #   RECEIPTS: a store that ALREADY has a receipt chain keeps it. Without this, a plain
    #   `inspeximus remember` against a receipted store opened receipts OFF and the write silently did not
    #   extend the chain -- so the record ended up covered by no receipt, i.e. the CLI
    #   quietly punched a hole in the evidence it exists to produce. `verify_attribution` and
    #   `verify_bundle` report such a record; `verify_writes` does not, because it walks the
    #   receipts (measured 2026-08-15 -- this comment named verify_writes and was wrong).
    #   Detected from the sidecar rather than a
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
    # A signing key implies receipts: signing an erasure into a chain that is not being kept writes a
    # signature nothing will ever read. Inspeximus() already treats receipt_key as turning receipts on;
    # passing it here too keeps the sidecar-detection branch in open_store from deciding otherwise.
    extra = {"receipt_key": receipt_key} if receipt_key else {}
    return open_store(path, embed=_embedder(), persist_vectors=persist_vectors,
                      receipts=receipts or bool(receipt_key), **extra)


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


def _nonnegative(raw: str) -> int:
    """`--max-followups` must be >= 0. It was a bare `type=int`, so a negative was accepted, clamped to 0 by
    the core, and then REPORTED back as though it were the cap in force -- an invalid request answered with a
    coherent-looking message, which is the same shape as `-k 0` returning "nothing in memory"."""
    try:
        v = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{raw!r} is not an integer")
    if v < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0, got {v}")
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


def _need_ed25519() -> int:
    """0 if Ed25519 is available, else print how to get it and return the exit code.

    inspeximus itself stays zero-dependency; only the SIGNING half of the witness surface needs
    `cryptography`. It names the package directly rather than an extra, because no `[witness]` extra is
    declared in pyproject -- pointing a stuck user at one that does not exist is worse than the raw
    ImportError this replaces."""
    from .core import _HAVE_ED
    if _HAVE_ED:
        return 0
    print("  FAIL this command needs Ed25519 signatures: pip install cryptography\n"
          "       (inspeximus has no required dependencies; only witness signing/verifying uses this one)",
          file=sys.stderr)
    return 4


def _read_json_file(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _witness_allowlist(csv_value, file_value) -> list:
    """The client's allowlist of witness pubkeys, from --witnesses and/or --witnesses-file (one per line).
    De-duplicated, order preserved. Blank lines and `#` comments are ignored so the file can be annotated
    with which party each key belongs to -- the allowlist is a trust decision and deserves notes."""
    out = []
    if csv_value:
        out += [w.strip() for w in csv_value.split(",") if w.strip()]
    if file_value:
        with open(file_value, encoding="utf-8") as f:
            out += [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]
    return list(dict.fromkeys(out))


def _load_cosigs(paths) -> list:
    """Load (pubkey, sig) pairs from `witness cosign` output files. Each file is one {pubkey,sig} object or
    a list of them."""
    pairs = []
    for p in paths or []:
        d = _read_json_file(p)
        for item in (d if isinstance(d, list) else [d]):
            if isinstance(item, dict) and item.get("pubkey") and item.get("sig"):
                pairs.append((item["pubkey"], item["sig"]))
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                pairs.append((item[0], item[1]))
    return pairs


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


def _witness_cmd(a) -> int:
    """`inspeximus witness ...` — the transparency/witness surface. Touches only files, never a store.

    Exit codes are the contract a CI job or a cron auditor reads:
      0 = the check passed (co-signed, or verified at threshold, or no split view)
      1 = the check FAILED (below threshold, or a split view was proven)
      2 = the witness REFUSED to co-sign (a fork/rollback — the defence firing, not an error), or usage
      3 = undetermined (heads of different sizes: not decidable from head commitments alone)
      4 = Ed25519 unavailable
    """
    rc = _need_ed25519()
    if rc:
        return rc
    from .core import Inspeximus

    if a.witness_cmd == "keygen":
        from .core import new_ed25519_keypair
        if os.path.exists(a.out):
            print(f"  FAIL {a.out} already exists - refusing to overwrite a witness secret. Replacing the "
                  f"key silently invalidates every co-signature it ever made, and the old ones would then "
                  f"read as forgeries rather than as history.", file=sys.stderr)
            return 2
        sk, pk = new_ed25519_keypair()
        fd = os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(sk + "\n")
        if a.allowlist:
            with open(a.allowlist, "a", encoding="utf-8") as f:
                f.write(pk + "\n")
        if a.json:
            _out({"secret_file": a.out, "pubkey": pk, "allowlist": a.allowlist}, True)
            return 0
        print(f"witness secret -> {a.out}   (never share or commit this file)")
        print(f"witness pubkey: {pk}")
        if a.allowlist:
            print(f"pubkey appended -> {a.allowlist}")
        return 0

    if a.witness_cmd == "cosign":
        from .witness_pool import Witness
        anchor = _read_json_file(a.anchor)
        with open(a.key, encoding="utf-8") as f:
            sk = f.read().strip()
        state = a.state or (a.key + ".state.json")
        w = Witness(secret_hex=sk, state_path=state, strict=a.strict,
                    require_authenticated_state=a.require_authenticated_state)
        try:
            pk, sig = w.cosign(a.store_id, anchor)
        except ValueError as e:
            # Do not restate a cause here: witness_cosign refuses for TWO reasons -- a fork/rollback of a
            # head this witness already signed, and a head whose sth_hash does not commit to its own
            # fields. Naming only the first would mislabel the second every time it fired.
            print(f"  REFUSED {e}", file=sys.stderr)
            print("VERDICT: REFUSED TO CO-SIGN - the reason is above. A refusal is this layer working, "
                  "not an error: an honest witness declines rather than signing something it cannot "
                  "stand behind.", file=sys.stderr)
            return 2
        rec = {"store_id": a.store_id, "pubkey": pk, "sig": sig, "sth_hash": anchor.get("sth_hash")}
        if a.out:
            with open(a.out, "w", encoding="utf-8") as f:
                json.dump(rec, f, ensure_ascii=False, indent=2)
        if a.json:
            _out(rec, True)
            return 0
        print(f"co-signed head sth={str(anchor.get('sth_hash'))[:16]}... for store {a.store_id!r}")
        print(f"  pubkey: {pk}")
        print(f"  state:  {state}")
        if a.out:
            print(f"  -> {a.out}")
        return 0

    if a.witness_cmd == "bootstrap":
        # WHY THIS COMMAND EXISTS. `--strict` refuses a store the witness has no memory of, which is
        # the amnesia defence -- and without a way to declare a genuine first contact it is not a
        # defence, it is a brick. The help text for --strict named this command before it existed.
        from .witness_pool import Witness
        with open(a.key, encoding="utf-8") as f:
            sk = f.read().strip()
        w = Witness(secret_hex=sk, state_path=(a.state or (a.key + ".state.json")), strict=True)
        w.bootstrap(a.store_id)
        print(f"  bootstrapped {a.store_id} for witness {w.public[:16]}...")
        print(f"  persisted to {a.state or (a.key + '.state.json')}")
        return 0

    if a.witness_cmd == "serve":
        from .witness_server import serve
        sk = None
        if a.key:
            with open(a.key, encoding="utf-8") as f:
                sk = f.read().strip()
        serve(a.port, a.host, a.state, sk, strict=a.strict,
              require_authenticated_state=a.require_authenticated_state,
              bootstrap_token=a.bootstrap_token)
        return 0

    # verify / split-view only: both are CLIENT-side trust decisions and both need the allowlist.
    allow = _witness_allowlist(getattr(a, "witnesses", None), getattr(a, "witnesses_file", None))
    if not allow:
        # An empty allowlist verifies every head, honest or forged, at count 0. Reporting that as a result
        # would make "no witnesses configured" indistinguishable from "the signatures are bad".
        print("  FAIL no witness allowlist given (--witnesses / --witnesses-file). Every head, honest or "
              "forged, scores 0 against an empty allowlist - refusing to report that as a verdict.",
              file=sys.stderr)
        return 2

    if a.witness_cmd == "verify":
        if a.threshold < 1:
            print(f"  FAIL --threshold must be >= 1 (got {a.threshold}); a threshold of 0 passes for any "
                  f"anchor with no signatures at all.", file=sys.stderr)
            return 2
        res = Inspeximus.verify_cosigned_anchor(_read_json_file(a.anchor), _load_cosigs(a.cosig),
                                                allow, threshold=a.threshold)
        if a.json:
            _out(res, True)
            return 0 if res["ok"] else 1
        if res.get("error"):
            print(f"  FAIL {res['error']}")
        print(f"  {'OK  ' if res['ok'] else 'FAIL'} {res['count']} of {len(allow)} allowlisted witnesses "
              f"co-signed this exact head (threshold {a.threshold})")
        for s in res["signers"]:
            print(f"    signer: {s}")
        for lim in res.get("limits") or []:
            # A PASS over an empty history is the one way this report can be true and still mislead, so it
            # is printed on the verdict itself rather than left in a field nobody reads.
            print(f"  NOTE {lim}")
        print(f"VERDICT: {'PASS' if res['ok'] else 'FAIL'}")
        return 0 if res["ok"] else 1

    if a.witness_cmd == "split-view":
        A, B = _read_json_file(a.anchor_a), _read_json_file(a.anchor_b)
        res = Inspeximus.detect_split_view(A, _load_cosigs(a.cosig_a), B, _load_cosigs(a.cosig_b), allow)
        code = 1 if (res["fork"] or res["inconsistent"]) else (3 if res["undetermined"] else 0)
        if a.json:
            _out(res, True)
            return code
        print(f"  head A: n_writes={A.get('n_writes')} tip={str(A.get('writes_tip'))[:16]}...")
        print(f"  head B: n_writes={B.get('n_writes')} tip={str(B.get('writes_tip'))[:16]}...")
        if res.get("malformed"):
            print(f"  MALFORMED anchor(s) {'/'.join(res['malformed'])}: {res['note']}")
        if res["inconsistent"]:
            print(f"  INCONSISTENT at {', '.join(res['at'])}: the same log size carries a different tip, "
                  f"so these two heads cannot both be the history of one store")
        if res["fork"]:
            for w in res["evidence"]:
                print(f"  EVIDENCE witness {w} validly co-signed BOTH heads")
            print("VERDICT: SPLIT VIEW PROVEN")
        elif res["inconsistent"]:
            print("VERDICT: HEADS INCONSISTENT - no witness co-signed both, so the divergence is shown "
                  "but not cryptographically attributable to a named witness")
        elif res["undetermined"]:
            print(f"  UNDETERMINED {res['note']}")
            print("VERDICT: UNDETERMINED")
        else:
            print("  no inconsistency: the heads agree at every shared log size")
            print("VERDICT: NO SPLIT VIEW")
        return code
    return 2


def _survive_a_narrow_console() -> None:
    """A console that cannot render a key must never turn a SUCCESSFUL write into a failure.

    Measured 2026-08-16 on a cp1250 console, which is the Windows default here:

        inspeximus remember "a value" --key "sedácia"     # the accented key in NFD form
        -> UnicodeEncodeError: 'charmap' codec can't encode character '\\u0301'
        -> rc 1 ... and the record IS on disk

    The write had already succeeded; the crash was in the line PRINTING the confirmation. So the
    user sees a traceback and a non-zero exit, concludes the write failed, and writes it again --
    producing exactly the duplicate that supersession exists to prevent. A successful write
    reported as a failure is worse than a refusal, because a refusal is honest.

    There are 153 print() calls in this file and `--json` uses ensure_ascii=False, so any of them
    can carry a character the console cannot encode. Guarding them one at a time is the shape that
    leaves the 153rd unguarded; this is the choke point.

    `backslashreplace`, not `replace`: `replace` substitutes `?` and destroys the very identifier
    the operator needs to see, silently. `backslashreplace` prints `sedaÌ\\u0301cia` -- ugly, lossless,
    and never a crash. The bytes on disk are UTF-8 either way; only the terminal echo changes.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if (getattr(stream, "encoding", "") or "").lower() not in ("utf-8", "utf8"):
                stream.reconfigure(errors="backslashreplace")
        except Exception:
            pass          # a redirected or exotic stream is not worth failing a command over


def main(argv=None):
    _survive_a_narrow_console()
    ap = argparse.ArgumentParser(prog="inspeximus", description="inspeximus — the self-correcting memory layer (CLI).")
    ap.add_argument("--path", help="store file (default: $INSPEXIMUS_PATH or ./inspeximus_memory.json)")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--receipts", action="store_true",
                    help="enable the tamper-evident write/erasure chain (needed to later `audit-build`)")
    ap.add_argument("--receipt-key-file", dest="receipt_key_file", default=None,
                    help="file holding the hex Ed25519 SECRET key that signs tombstones (or "
                         "$INSPEXIMUS_RECEIPT_KEY_FILE). A file, not a flag value: an argument is visible "
                         "in ps and in shell history. Needed at ERASURE time, not certificate time.")
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
    q.add_argument("--as-agent", dest="as_agent", metavar="AGENT",
                   help="read AS this agent: only what it owns or has been granted (fail-closed)")

    # ── agent-to-agent read grants ────────────────────────────────────────────────────────────────
    def _selector_args(p):
        """One selector per grant, matched EXACTLY on a stored field. No query selector: a grant whose
        membership came from a similarity score would cover a different set after a re-embed."""
        p.add_argument("--scope", help="grant records whose meta scope equals this")
        p.add_argument("--tag", help="grant records carrying this tag")
        p.add_argument("--key", help="grant records under this supersession key")
        p.add_argument("--ids", help="grant these record ids (comma-separated)")
        p.add_argument("--by", help="the granting AGENT (its grant covers only records it owns); "
                                    "omit for an operator-wide grant")
        p.add_argument("--note", help="free-text note recorded with the act")

    gr = sub.add_parser("grant", help="give an agent READ access to a subset of memories (scoped, revocable)")
    gr.add_argument("agent", help="the agent being granted access")
    _selector_args(gr)

    rv = sub.add_parser("revoke", help="end a grant (effective on the next read; deletes nothing)")
    rv.add_argument("agent", help="the agent whose access ends")
    _selector_args(rv)

    gl = sub.add_parser("grants", help="what access is in force — or, with --log, every grant and revocation")
    gl.add_argument("--agent", help="restrict to one agent")
    gl.add_argument("--log", action="store_true",
                    help="show EVERY act incl. revoked/retired ones, not just what is in force")

    ri = sub.add_parser("recall-iterative",
                        help="MULTI-HOP recall: round-1 plus the follow-up queries YOUR model writes "
                             "(the one retrieval lever measured to move multi-hop; no LLM here)")
    ri.add_argument("query")
    ri.add_argument("-k", type=_positive_k, default=6, help="how many per retrieval (>= 1)")
    ri.add_argument("--followup", action="append", default=[],
                    help="a follow-up query your model wrote after reading round-1 (repeatable). "
                         "Omit to get round-1 plus the instruction to hand your model.")
    ri.add_argument("--max-followups", dest="max_followups", type=_nonnegative, default=3,
                    help="cap on follow-ups honoured (hard ceiling 8) - this IS the cost bound")
    ri.add_argument("--prior-id", dest="prior_ids", action="append", default=[],
                    help="an id you already hold (repeatable). Given, round 1 is SKIPPED and this runs "
                         "phase 2 only - the stateless shape the MCP surface uses. Omitted with "
                         "--followup, round 1 runs here and both phases happen in this one call.")

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

    wk = sub.add_parser("writer-key",
                        help="mint a writer identity so this store signs its own writes (attested_key)")
    wk.add_argument("--new", action="store_true", help="mint a fresh Ed25519 keypair")
    wk.add_argument("--out", default=None,
                    help="write the SECRET to this file (referenced by INSPEXIMUS_WRITER_KEY_FILE); "
                         "printed to stdout if omitted")

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

    ec = sub.add_parser("erasure-certificate",
                        help="write the portable, independently-verifiable erasure certificate for a "
                             "request (the receipt you hand an auditor)")
    ec.add_argument("--out", default="erasure_certificate.json", help="output json path")
    ec.add_argument("--request-id", help="scope the certificate to ONE request id (omit for the whole "
                                         "erasure history)")
    ec.add_argument("--expected-pubkey", default=None,
                    help="pin the certificate's own self-check to this public key")

    ev = sub.add_parser("erasure-verify",
                        help="verify an erasure certificate WITHOUT the operator's private key (exit 1 if "
                             "it does not verify) -- the auditor's side of the receipt")
    ev.add_argument("certificate", help="the certificate json to verify")
    ev.add_argument("--store", default=None,
                    help="the store the certificate came from; without it the erased ids are NOT checked "
                         "for absence, which is the strongest proof in the document")
    ev.add_argument("--expected-pubkey", default=None, help="the public key you expect it signed by (hex)")
    ev.add_argument("--expected-pubkey-file", default=None,
                    help="read that public key from a file instead")

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
    # PINNING BELONGS HERE, AND ONLY HERE. `audit-build --expected-pubkey` has existed since 2.10.2
    # and is nearly useless: the operator builds the bundle, so an operator checking their own key
    # against their own artifact learns nothing. The auditor is the party who holds a key from out of
    # band, and the auditor runs THIS command -- which had no way to supply it. So over the shell,
    # the strongest attribution check in the package was reachable only from the side that does not
    # need it.
    av.add_argument("--expected-pubkey", default=None,
                    help="pin the chain signatures to a key you got OUT OF BAND. Without it they can "
                         "only be checked against a key carried inside the bundle itself, which "
                         "proves the writer owned a keypair -- not which one.")
    av.add_argument("--require-signed", action="store_true",
                    help="refuse a bundle whose chain is unsigned or only present-but-unverified")

    # ── tamper-evident / transparency-log surface: anchor + the witness network ──────────────────────────
    # These existed as Python + an HTTP server since 1.34.0 and were reachable from no shell command at all,
    # so the strongest operator-adversarial property in the package was invisible to anyone who did not read
    # the source. See docs/TRANSPARENCY.md.
    an = sub.add_parser("anchor", help="emit the SIGNED HEAD COMMITMENT over the whole write+erasure history "
                                       "- publish it where the operator cannot alter it (CT witnessing model, "
                                       "hash chain not a Merkle tree: no inclusion proofs)")
    an.add_argument("--out", default=None, help="write the anchor json here (default: stdout)")

    wt = sub.add_parser("witness", help="witness network: independent co-signers that make a SPLIT VIEW "
                                        "(one history shown to one reader, another to another) detectable")
    wsub = wt.add_subparsers(dest="witness_cmd", required=True)

    wkg = wsub.add_parser("keygen", help="mint an Ed25519 witness keypair (the independent co-signer)")
    wkg.add_argument("--out", required=True, help="file for the SECRET key hex - keep it on the witness host")
    wkg.add_argument("--allowlist", default=None,
                     help="append the PUBLIC key to this allowlist file (what `witness verify` reads)")

    wcs = wsub.add_parser("cosign", help="WITNESS side: co-sign a store's anchor. REFUSES (exit 2) a fork or "
                                         "rollback of the last head this witness signed for that store")
    wcs.add_argument("anchor", help="the anchor json to co-sign")
    wcs.add_argument("--store-id", required=True, help="which store this head belongs to (one memory per store)")
    wcs.add_argument("--key", required=True, help="the witness SECRET key file (from `witness keygen`)")
    wcs.add_argument("--state", default=None,
                     help="json file persisting the last head signed per store. Defaults to <key>.state.json "
                          "and is never optional: each CLI call is a fresh process, so a witness with no "
                          "state file has no memory and can never refuse anything")
    wcs.add_argument("--out", default=None, help="write {pubkey,sig} here (default: stdout)")
    wcs.add_argument("--strict", action="store_true",
                     help="refuse a store this witness has no memory of. Record a genuine first "
                          "contact with `inspeximus witness bootstrap` first -- the declaration is "
                          "persisted in the same state file")
    wcs.add_argument("--require-authenticated-state", action="store_true",
                     help="refuse a state file carrying no MAC")

    wvf = wsub.add_parser("verify", help="CLIENT side: k-of-n check that allowlisted INDEPENDENT witnesses "
                                         "co-signed this exact head (exit 1 below threshold)")
    wvf.add_argument("anchor")
    wvf.add_argument("--cosig", action="append", default=[],
                     help="a {pubkey,sig} json from `witness cosign` (repeatable)")
    wvf.add_argument("--witnesses", default=None, help="comma-separated allowlisted witness pubkeys (hex)")
    wvf.add_argument("--witnesses-file", default=None,
                     help="allowlist file, one pubkey hex per line (`witness keygen --allowlist` writes it)")
    wvf.add_argument("--threshold", type=int, default=1, help="how many DISTINCT witnesses must have signed")

    wsv = wsub.add_parser("split-view", help="AUDITOR side: given the two heads a store showed two readers, "
                                             "prove a FORK - a witness that validly co-signed both "
                                             "inconsistent heads (exit 1 = split view)")
    wsv.add_argument("--anchor-a", required=True); wsv.add_argument("--cosig-a", action="append", default=[])
    wsv.add_argument("--anchor-b", required=True); wsv.add_argument("--cosig-b", action="append", default=[])
    wsv.add_argument("--witnesses", default=None, help="comma-separated allowlisted witness pubkeys (hex)")
    wsv.add_argument("--witnesses-file", default=None, help="allowlist file, one pubkey hex per line")

    wsr = wsub.add_parser("serve", help="run the reference witness HTTP server (stdlib only) on this host")
    wsr.add_argument("--port", type=int, default=9700); wsr.add_argument("--host", default="127.0.0.1")
    wsr.add_argument("--state", default=None, help="json file persisting the per-store last-signed head")
    wsr.add_argument("--key", default=None, help="witness SECRET key file (omit to mint an ephemeral key)")
    wsr.add_argument("--strict", action="store_true",
                     help="refuse a store this witness has no memory of. Deleting the state file is "
                          "otherwise a way to launder a rollback: an amnesiac witness co-signs one")
    wsr.add_argument("--require-authenticated-state", action="store_true",
                     help="refuse a fork-memory file carrying no MAC. Turn this on once the witness "
                          "has started (and so re-persisted) at least once on 2.10.6+")
    wsr.add_argument("--bootstrap-token", default=None,
                     help="shared secret enabling POST /bootstrap (needs --strict). Callers send it "
                          "as X-Bootstrap-Token. Without this the route is 403: an unauthenticated "
                          "bootstrap would defeat --strict, since anyone could declare any store id")

    wbs = wsub.add_parser("bootstrap", help="declare a legitimate FIRST CONTACT with a store, for a "
                                            "strict witness. Persisted in the state file")
    wbs.add_argument("--store-id", required=True, help="the store id this witness may see for the "
                                                       "first time (the bundle's store_id_derived)")
    wbs.add_argument("--key", required=True, help="the witness SECRET key file (from `witness keygen`)")
    wbs.add_argument("--state", default=None,
                     help="the state file this witness persists to; defaults to <key>.state.json, "
                          "and must be the SAME file the witness serves from")

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
        receipts = None   # bound here too: without --store the verify call would NameError
        if a.store:
            # Opening a store CREATES it when the path does not exist, and an auditor who mistyped would
            # then be handed a clean verdict over the empty store they had just made -- the same shape as
            # the erasure certificate that reported valid while its absence proof pointed at a typo.
            from inspeximus.audit_bundle import load_store_items, load_store_receipts
            items = load_store_items(a.store)     # ONE implementation; see its docstring
            # The LIVE chain, which is what separates ordinary growth from an injected record
            # without trusting `ts` -- a field the writer controls.
            receipts = load_store_receipts(a.store)
            if items is None:
                print(f"  FAIL --store {a.store} does not exist; refusing to create a store while "
                      f"verifying, because an empty one verifies clean")
                return 1
        res = verify_bundle(bundle, witnesses=wl, threshold=a.threshold,
                            store_items=items, store_receipts=receipts,
                            expected_pubkey=a.expected_pubkey,
                            require_signed=a.require_signed)
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

    # erasure-verify is the AUDITOR's command and must never open a store: Inspeximus() creates the path it
    # is given, so a mistyped --store would mint an empty store and every erased id would verify ABSENT from
    # it -- a clean bill of health produced by the typo itself. This is the defect load_store_items() was
    # written for in audit-verify, and the erasure certificate is where it was first measured.
    if a.cmd == "erasure-verify":
        from inspeximus.core import verify_erasure_certificate
        with open(a.certificate, encoding="utf-8") as f:
            cert = json.load(f)
        pub = a.expected_pubkey
        if a.expected_pubkey_file:
            if pub:
                print("erasure-verify: pass --expected-pubkey OR --expected-pubkey-file, not both",
                      file=sys.stderr)
                return 2
            with open(a.expected_pubkey_file, encoding="utf-8") as f:
                pub = f.read().strip()
        items = None
        if a.store:
            from inspeximus.audit_bundle import load_store_items
            items = load_store_items(a.store)          # ONE implementation; see its docstring
            if items is None:
                print(f"  FAIL --store {a.store} does not exist; refusing to create a store while "
                      f"verifying, because every erased id is absent from an empty one")
                return 1
        res = verify_erasure_certificate(cert, store_items=items, expected_pubkey=pub)
        if a.json:
            _out(res, True)
        else:
            for name, val in res["checks"].items():
                print(f"  {'OK  ' if val is True else 'FAIL' if val is False else 'n/a '} {name}")
            for pr in res["problems"]:
                print(f"  FAIL {pr}")
            for lim in res.get("limits") or []:
                print(f"  NOTE {lim}")
            print(f"\nVERDICT: {'PASS' if res['valid'] else 'FAIL'}  "
                  f"({res['count']} erasure(s) attested, absence "
                  f"{'checked' if res['checks'].get('store_absent') is not None else 'NOT checked'})")
        return 0 if res["valid"] else 1

    # The witness commands operate on FILES (anchors, keys, signatures) and must never open a store —
    # opening one CREATES it, and an auditor who mistyped a path would be handed a verdict about a store
    # the verification itself had just made. Same rule as `audit-verify` and `erasure-verify` above.
    if a.cmd == "witness":
        return _witness_cmd(a)

    # audit-build/compliance/retention must have the receipt+tombstone chains, so force receipts on.
    # `provenance` REPORTS on the receipt chain, so it must load it — otherwise a receipted store would be
    # described as "receipts off at write time", which is not merely unhelpful but wrong.
    # Captured BEFORE the store is opened: Inspeximus() CREATES the parent directory, so any
    # "does this store exist?" question asked afterwards always answers yes. (A first version of the
    # check-code gate below tested the directory after this line and could therefore never fire.)
    _store_existed = os.path.exists(str(_resolve_store_path(a.path)))
    try:
        _rk = _receipt_key(a.receipt_key_file)
    except OSError as e:
        # A key file that cannot be read must NOT fall through to unsigned. The operator asked for signed
        # tombstones; producing unsigned ones and reporting success is how an erasure ends up with evidence
        # nobody can attribute, discovered only when the auditor pins a key months later.
        print(f"cannot read the receipt key: {e}", file=sys.stderr)
        return 2
    # `anchor` joins the forced-receipts list: the signed head commitment IS the receipt+tombstone chain's
    # commitment, so opening the store with receipts off would emit a head over an empty chain.
    m = _store(a.path, receipts=a.receipts or a.cmd in ("audit-build", "compliance", "retention",
                                                        "provenance", "erasure-certificate", "anchor"),
               receipt_key=_rk)

    if a.cmd == "anchor":
        anc = m.anchor()
        if a.out:
            with open(a.out, "w", encoding="utf-8") as f:
                json.dump(anc, f, ensure_ascii=False, indent=2)
        if a.json:
            _out(anc, True)
        else:
            print(f"anchor{' -> ' + a.out if a.out else ''}")
            print(f"  n_writes={anc['n_writes']} n_tombstones={anc['n_tombstones']} "
                  f"sth={anc['sth_hash'][:16]}...")
            if anc["n_writes"] == 0 and anc["n_tombstones"] == 0:
                # An anchor over an empty chain is a valid signed head of NOTHING. It co-signs and verifies
                # perfectly, so without this line the user gets every green tick in the quickstart while
                # committing to no history at all.
                print("  note: 0 writes and 0 erasures - this store has no receipt chain, so this head "
                      "commits to nothing. Write with `inspeximus --receipts remember ...` first.")
        return 0

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
        # A --derived-from id that does not resolve is the quietest way to lose a DSAR. The library keeps the
        # evidence (`derived_from_unresolved` + `orphan`), but THIS surface printed "remembered <id>" and
        # exited 0, so the operator has been told the write succeeded and nothing has been told about the
        # lineage. It has not: the record inherits no taint, forget_subject cannot reach it, and it survives
        # a DSAR that erased everything else about that person -- which is the exact opposite of what
        # --derived-from's own help text promises. Measured 2026-08-01 by mistyping one id in this file's
        # own quickstart. Warn, do not fail: the write itself is legitimate, and a parent erased by an
        # EARLIER DSAR is an honest reason for an id not to resolve.
        _rec = next((r for r in m.items if r["id"] == mid), None)
        _unres = (_rec or {}).get("derived_from_unresolved") or []
        if _unres:
            print(f"warning: {len(_unres)} --derived-from id(s) do not exist in this store and were NOT "
                  f"linked: {', '.join(_unres)}. This record inherits no lineage from them, so "
                  f"`forget-subject` will NOT reach it and it will survive their erasure. "
                  f"Check the id, or re-write the record once the parent exists.", file=sys.stderr)
        return _flush_or_fail(m)

    elif a.cmd == "recall":
        # --as-agent narrows the handle BEFORE the read, so there is no path where the scope is computed
        # and then not applied. An unknown/typo'd agent name is not an error: it owns nothing and has been
        # granted nothing, so it correctly reads an empty store. Fail-closed means a typo loses access,
        # never gains it.
        reader = m.as_agent(a.as_agent) if getattr(a, "as_agent", None) else m
        hits = reader.recall(a.query, k=a.k) or []
        if a.json:
            _out(hits, True)
        elif not hits:
            print("(nothing in memory for that query)")
        else:
            for h in hits:
                print(f"- {h.get('text','')}")

    elif a.cmd == "recall-iterative":
        # The two-phase loop, shaped for a shell. The library's recall_iterative() takes a CALLABLE, which a
        # terminal cannot supply -- so the phases are split exactly as they are over MCP, and which of them
        # runs is decided by the flags rather than by a mode switch:
        #   no --followup                -> phase 1 only: round-1 hits + the instruction to hand your model.
        #   --followup, no --prior-id    -> both phases here, one call. Equivalent to recall_iterative() with
        #                                   a callable returning those queries. Costs 1 + f retrievals.
        #   --followup with --prior-id   -> phase 2 only, stateless, the same shape the MCP client uses.
        # ASCII-only output: this prints to a Windows console that is not UTF-8 (cp1250 here), where a
        # non-ASCII character can raise UnicodeEncodeError and take down the command that produced it.
        start = None
        prior = list(a.prior_ids)
        if not prior:
            start = m.recall_iterative_start(a.query, k=a.k, max_followups=a.max_followups)
            prior = list(start["prior_ids"])
        if not a.followup:
            if start is None:                     # --prior-id with no --followup asks for nothing at all
                print("recall-iterative: --prior-id given with no --followup, so there is nothing to "
                      "retrieve. Pass --followup, or drop --prior-id to get round 1.", file=sys.stderr)
                return 2
            if a.json:
                _out({"mode": "phase1", "round_1": start, "followup": None}, True)
            else:
                if not start["hits"]:
                    print("(nothing in memory for that query)")
                for h in start["hits"]:
                    print(f"- {h.get('text','')}")
                print(f"\nask your model: {start['ask']}")
                print(f"then: inspeximus recall-iterative {a.query!r} --followup '<query>'"
                      + "".join(f" --prior-id {i}" for i in prior))
            return _flush_or_fail(m, required=False)
        res = m.recall_iterative_followup(a.query, followups=a.followup, prior_ids=prior,
                                          k=a.k, max_followups=a.max_followups)
        if a.json:
            # ONE envelope for all three modes. It used to be a flat dict for phase 1 and a two-key wrapper
            # otherwise, so a script consuming --json had to branch on which shape arrived and could not
            # tell "phase 2 only" (round_1 null) from a malformed payload.
            _out({"mode": "phase2" if start is None else "both", "round_1": start, "followup": res}, True)
        else:
            for h in (start or {}).get("hits", []):
                print(f"- {h.get('text','')}")
            if res["followups_dropped"]:
                # the EFFECTIVE cap, not the requested one: core clamps to Inspeximus.MAX_FOLLOWUPS, so
                # echoing the request would report a ceiling that was not the one applied.
                print(f"  ({res['followups_dropped']} follow-up(s) dropped: the effective cap is "
                      f"{res['bounds']['max_recall_calls']})", file=sys.stderr)
            if not res["new_hits"]:
                print("(the follow-up queries added nothing new)")
            for h in res["new_hits"]:
                print(f"+ {h.get('text','')}")
            print(f"\n{res['bridged']} new record(s) from {res['recall_calls']} follow-up retrieval(s)",
                  file=sys.stderr)

    elif a.cmd in ("grant", "revoke"):
        sel = {"scope": a.scope, "tag": a.tag, "key": a.key,
               "ids": [x.strip() for x in a.ids.split(",") if x.strip()] if a.ids else None}
        try:
            res = (m.grant if a.cmd == "grant" else m.revoke)(a.agent, by=a.by, note=a.note, **sel)
        except (ValueError, PermissionError) as e:
            # Exit 2, not 0 with an explanation. A refused access-control change that reports success is
            # the failure this whole feature exists to avoid: `inspeximus grant bob && echo shared` must
            # not print `shared` when nothing was granted.
            print(f"{a.cmd} refused: {e}", file=sys.stderr)
            return 2
        rc = _flush_or_fail(m)
        if rc:
            return rc
        if not _out(res, a.json):
            what = f"{res['kind']}={res['value']!r}"
            who = "the operator" if res["by"] == "*" else f"agent {res['by']!r}"
            if a.cmd == "grant":
                print(f"granted: agent {res['agent']!r} may now read {what} (by {who})")
            else:
                had = "" if res.get("was_granted") else " (nothing was in force; recorded anyway)"
                print(f"revoked: agent {res['agent']!r} can no longer read {what} (by {who}){had}")

    elif a.cmd == "grants":
        rows = m.grant_log(a.agent) if a.log else m.grants(a.agent)
        if not _out(rows, a.json):
            if not rows:
                print("(no grants in this store)" if not a.log else "(no access-control acts in this store)")
            for r in rows:
                who = "operator" if r["by"] == "*" else r["by"]
                state = f" [{r['state']}/{r['status']}]" if a.log else ""
                print(f"- {who} -> {r['agent']}: {r['kind']}={r['value']!r}{state}")

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
        if a.contains is not None:
            # A blank needle is not a narrow query, it is "everything": MEASURED, `--contains " "` deleted
            # 3 of 3 memories, because every multi-word text contains a space. `--contains ""` was already
            # refused, but only as an accident of falsiness -- and the message it printed ("pass --key,
            # --id, or --contains") told the user to pass the flag they had just passed. `is not None`
            # routes both to the same honest refusal.
            needle = a.contains.lower()
            if not needle.strip():
                print("forget: --contains needs a non-blank substring; every memory contains a blank one, "
                      "so this would delete the whole store and the delete is irreversible", file=sys.stderr)
                return 2
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

    elif a.cmd == "writer-key":
        # A writer identity is how `attested_key` stops being 0.0000% (measured across 111,264 live
        # records on 2026-08-08). Minting it needs a command, not a paragraph telling someone to.
        import pathlib
        from .core import new_source_keypair
        if not a.new:
            print("nothing to do: pass --new to mint a keypair")
            return 2
        sk, pk = new_source_keypair()
        if a.out:
            p = pathlib.Path(a.out)
            p.write_text(sk + "\n", encoding="utf-8")
            try:                                   # best-effort: keep the secret off other accounts
                os.chmod(p, 0o600)
            except OSError:
                pass
            print(f"secret written to {p} (0600)\npublic key: {pk}\n\n"
                  f"point the server at it:  INSPEXIMUS_WRITER_KEY_FILE={p}\n"
                  f"pin this writer as trusted with trust_seeds={{'key:{pk}'}}\n"
                  f"KEEP THE SECRET OUT OF GIT. It attests AUTHORSHIP, not truth.")
        else:
            print(f"secret: {sk}\npublic: {pk}\n\n"
                  f"KEEP THE SECRET OUT OF GIT. It attests AUTHORSHIP, not truth.")
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

    elif a.cmd == "erasure-certificate":
        cert = m.erasure_certificate(request_id=a.request_id, expected_pubkey=a.expected_pubkey)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(cert, f, ensure_ascii=False, indent=2)
        signed = sum(1 for t in cert["tombstones"] if t.get("sig"))
        _out({"out": a.out, "count": cert["count"], "scoped_to": cert["scoped_to"],
              "signed_tombstones": signed}, a.json) or print(
            f"wrote erasure certificate -> {a.out}  ({cert['count']} erasure(s) attested"
            + (f", scoped to {cert['scoped_to']}" if cert["scoped_to"] else ", whole history") + ")")
        # A certificate for zero erasures is a document that certifies nothing, and it is exactly what an
        # operator gets from a typo in --request-id. `erasure-verify` refuses it, so saying so HERE -- at
        # the moment it is produced, not when the auditor rejects it -- is the difference between a typo
        # and a compliance incident. Non-zero exit so a DSAR runbook cannot step past it.
        if cert["count"] == 0:
            print("REFUSED as evidence: this certificate attests to ZERO erasures"
                  + (f" for request {a.request_id!r}" if a.request_id else " (nothing was ever erased)")
                  + ". It was still written so you can inspect it, but `erasure-verify` will FAIL it.",
                  file=sys.stderr)
            return 1
        if not signed:
            print("NOTE: the tombstones are UNSIGNED, so the chain proves integrity but not authorship. "
                  "Sign at ERASURE time with --receipt-key-file (or $INSPEXIMUS_RECEIPT_KEY_FILE); "
                  "supplying it now cannot retro-sign what is already erased.", file=sys.stderr)

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
            if cov.get("undeclared_derived"):
                print(f"            {cov['undeclared_derived']} record(s) announced derivation and "
                      "resolved no parent -- the walk has a hole of known size")
            if a.subject and cov.get("subject_reachable_records") == 0:
                print(f"            0 surviving record(s) the walk could follow to {a.subject!r}: the "
                      "store-wide ratio above says nothing about THIS subject")
            if res["verdict"] == "residue_found":
                print(f"  RESIDUE  {len(res['residue'])} finding(s) tied to a deliberate erasure:")
                for f in res["residue"]:
                    print(f"    [{f['kind']}] {f['id']}")
                    print(f"      {f['detail']}")
            elif res["verdict"] == "unaudited":
                print("  UNAUDITED  no record declares lineage, so nothing structural was inspected. "
                      "This is NOT a pass -- declare derived_from on derived writes to make it mean something.")
            elif res["verdict"] == "partially_audited":
                print(f"  PARTIALLY AUDITED  {cov['undeclared_derived']} record(s) claim derivation the walk "
                      "could not resolve, so coverage is incomplete by a known amount. This is NOT a pass.")
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
