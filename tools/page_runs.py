"""Re-run the two transcripts published on erasure.html and audit-trail.html.

Each page shows a real CLI run. This script repeats both runs in a fresh temporary directory and
prints every command with its output, so a reader can compare the counts on the page with a run
on their own machine. Ids, hashes, timestamps and the session id differ per run; the counts do not.

    python tools/page_runs.py            # both page flows
    python tools/page_runs.py erasure    # one
    python tools/page_runs.py ledger
    python tools/page_runs.py article    # the dev.to article: one store, both halves in sequence
    python tools/page_runs.py erasure --out run.json   # also save [{cmd, out}, ...] for the page builder

The verify step pins the writer's public key (`--expected-pubkey`, read from writer-key's own
output), because a certificate re-signed with a different key passes an unpinned check. The two
NOTE lines the verifier prints about a missing witnessed anchor and a store with no chain are part
of the run and stay on the page.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

BASE = [sys.executable, "-m", "inspeximus.cli"]
STORE = ["--path", "mem.json", "--receipts", "--receipt-key-file", "key.txt"]


LOG: list[dict] = []


def run(args: list[str], cwd: str) -> str:
    shown = " ".join("'%s'" % a if " " in a or "{" in a else a for a in args)
    print("$ inspeximus " + shown)
    out = subprocess.run(BASE + args, cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    text = (out.stdout + out.stderr).rstrip()
    if text:
        print(text)
    print()
    LOG.append({"cmd": "inspeximus " + shown, "out": text})
    return text


def cat(name: str, cwd: str) -> None:
    """Show a retained transcript file the way `cat` would; the bytes are read from disk."""
    text = open(os.path.join(cwd, name), encoding="utf-8").read().rstrip()
    print("$ cat " + name)
    print(text)
    print()
    LOG.append({"cmd": "cat " + name, "out": text})


def pubkey_of(writer_key_output: str) -> str:
    for line in writer_key_output.splitlines():
        if line.startswith("public key:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError("writer-key printed no public key")


def erasure(cwd: str) -> None:
    pub = pubkey_of(run(["writer-key", "--new", "--out", "key.txt"], cwd))
    run(STORE + ["remember", "Alice prefers email", "--source", "crm/alice"], cwd)
    run(STORE + ["remember", "Alice phone is +100", "--key", "alice::phone", "--source", "crm/alice"], cwd)
    run(STORE + ["remember", "Bob phone is +300", "--key", "bob::phone", "--source", "crm/bob"], cwd)
    run(STORE + ["forget-subject", "crm/alice", "--request-id", "DSAR-17", "--dry-run"], cwd)
    run(STORE + ["forget-subject", "crm/alice", "--request-id", "DSAR-17", "--basis", "Art. 17(1)(a)"], cwd)
    run(STORE + ["erasure-certificate", "--request-id", "DSAR-17", "--out", "DSAR-17.cert.json"], cwd)
    run(["erasure-verify", "DSAR-17.cert.json", "--store", "mem.json", "--expected-pubkey", pub], cwd)
    run(["--path", "mem.json", "list"], cwd)


def ledger(cwd: str, pub: str | None = None) -> None:
    if pub is None:
        pub = pubkey_of(run(["writer-key", "--new", "--out", "key.txt"], cwd))
        run(STORE + ["remember", "Alice phone is +100", "--key", "alice::phone", "--source", "crm/alice"], cwd)
    run(STORE + ["actions", "record", "tool:sms", "--input", '{"to": "+100", "text": "hi"}',
                 "--output", '{"sent": true}', "--actor", "support-agent"], cwd)
    run(["--path", "mem.json", "actions", "knew", "0"], cwd)
    for name, body in (("in.json", {"to": "+100", "text": "hi"}), ("in_edited.json", {"to": "+100", "text": "hello"}),
                       ("out.json", {"sent": True})):
        with open(os.path.join(cwd, name), "w", encoding="utf-8") as fh:
            json.dump(body, fh)
    cat("in.json", cwd)
    run(["--path", "mem.json", "actions", "matches", "0", "--inputs", "in.json", "--output", "out.json"], cwd)
    cat("in_edited.json", cwd)
    run(["--path", "mem.json", "actions", "matches", "0", "--inputs", "in_edited.json", "--output", "out.json"], cwd)
    run(["--path", "mem.json", "actions", "verify", "--expected-pubkey", pub], cwd)
    run(["--path", "mem.json", "actions", "export-trail", "--out", "trail.jsonl",
         "--agent-id", "urn:agent:support.example", "--agent-version", "1.0.0"], cwd)
    run(["actions", "export-trail", "--verify", "trail.jsonl"], cwd)


def article(cwd: str) -> None:
    """One store, both halves: the action ledger first, then the erasure of the same Alice."""
    pub = pubkey_of(run(["writer-key", "--new", "--out", "key.txt"], cwd))
    run(STORE + ["remember", "Alice prefers email", "--source", "crm/alice"], cwd)
    run(STORE + ["remember", "Alice phone is +100", "--key", "alice::phone", "--source", "crm/alice"], cwd)
    run(STORE + ["remember", "Bob phone is +300", "--key", "bob::phone", "--source", "crm/bob"], cwd)
    ledger(cwd, pub)
    run(STORE + ["forget-subject", "crm/alice", "--request-id", "DSAR-17", "--dry-run"], cwd)
    run(STORE + ["forget-subject", "crm/alice", "--request-id", "DSAR-17", "--basis", "Art. 17(1)(a)"], cwd)
    run(STORE + ["erasure-certificate", "--request-id", "DSAR-17", "--out", "DSAR-17.cert.json"], cwd)
    run(["erasure-verify", "DSAR-17.cert.json", "--store", "mem.json", "--expected-pubkey", pub], cwd)
    run(["--path", "mem.json", "list"], cwd)


def main() -> int:
    argv = sys.argv[1:]
    out_path = None
    if "--out" in argv:
        i = argv.index("--out")
        out_path = argv[i + 1]
        del argv[i:i + 2]
    which = argv or ["erasure", "ledger"]
    for name in which:
        with tempfile.TemporaryDirectory() as d:
            print("==", name, "==")
            {"erasure": erasure, "ledger": ledger, "article": article}[name](d)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(LOG, fh, indent=1, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
