"""Re-run the two transcripts published on erasure.html and audit-trail.html.

Each page shows a real CLI run. This script repeats both runs in a fresh temporary directory and
prints every command with its output, so a reader can compare the counts on the page with a run
on their own machine. Ids, hashes, timestamps and the session id differ per run; the counts do not.

    python tools/page_runs.py            # both
    python tools/page_runs.py erasure    # one
    python tools/page_runs.py ledger
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

BASE = [sys.executable, "-m", "inspeximus.cli"]
STORE = ["--path", "mem.json", "--receipts", "--receipt-key-file", "key.txt"]


def run(args: list[str], cwd: str) -> str:
    shown = " ".join("'%s'" % a if " " in a or "{" in a else a for a in args)
    print("$ inspeximus " + shown)
    out = subprocess.run(BASE + args, cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    text = (out.stdout + out.stderr).rstrip()
    if text:
        print(text)
    print()
    return text


def erasure(cwd: str) -> None:
    run(["writer-key", "--new", "--out", "key.txt"], cwd)
    run(STORE + ["remember", "Alice prefers email", "--source", "crm/alice"], cwd)
    run(STORE + ["remember", "Alice phone is +100", "--key", "alice::phone", "--source", "crm/alice"], cwd)
    run(STORE + ["remember", "Bob phone is +300", "--key", "bob::phone", "--source", "crm/bob"], cwd)
    run(STORE + ["forget-subject", "crm/alice", "--request-id", "DSAR-17", "--dry-run"], cwd)
    run(STORE + ["forget-subject", "crm/alice", "--request-id", "DSAR-17", "--basis", "Art. 17(1)(a)"], cwd)
    run(["--path", "mem.json", "--receipts", "erasure-certificate", "--request-id", "DSAR-17",
         "--out", "DSAR-17.cert.json"], cwd)
    run(["erasure-verify", "DSAR-17.cert.json", "--store", "mem.json"], cwd)
    run(["--path", "mem.json", "list"], cwd)


def ledger(cwd: str) -> None:
    run(["writer-key", "--new", "--out", "key.txt"], cwd)
    run(STORE + ["remember", "Alice phone is +100", "--key", "alice::phone", "--source", "crm/alice"], cwd)
    run(STORE + ["actions", "record", "tool:sms", "--input", '{"to": "+100", "text": "hi"}',
                 "--output", '{"sent": true}', "--actor", "support-agent"], cwd)
    run(["--path", "mem.json", "actions", "knew", "0"], cwd)
    for name, body in (("in.json", {"to": "+100", "text": "hi"}), ("in_edited.json", {"to": "+100", "text": "hello"}),
                       ("out.json", {"sent": True})):
        with open(os.path.join(cwd, name), "w", encoding="utf-8") as fh:
            json.dump(body, fh)
    run(["--path", "mem.json", "actions", "matches", "0", "--inputs", "in.json", "--output", "out.json"], cwd)
    run(["--path", "mem.json", "actions", "matches", "0", "--inputs", "in_edited.json", "--output", "out.json"], cwd)
    run(["--path", "mem.json", "actions", "verify"], cwd)
    run(["--path", "mem.json", "actions", "export-trail", "--out", "trail.jsonl",
         "--agent-id", "urn:agent:support.example", "--agent-version", "1.0.0"], cwd)
    run(["actions", "export-trail", "--verify", "trail.jsonl"], cwd)


def main() -> int:
    which = sys.argv[1:] or ["erasure", "ledger"]
    for name in which:
        with tempfile.TemporaryDirectory() as d:
            print("==", name, "==")
            {"erasure": erasure, "ledger": ledger}[name](d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
