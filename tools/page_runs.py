"""Re-run the two transcripts published on erasure.html and audit-trail.html.

Each page shows a real CLI run. This script repeats both runs in a fresh temporary directory and
prints every command with its output, so a reader can compare the counts on the page with a run
on their own machine. Ids, hashes, timestamps and the session id differ per run; the counts do not.

    python tools/page_runs.py            # both page flows
    python tools/page_runs.py erasure    # one
    python tools/page_runs.py ledger
    python tools/page_runs.py article    # the dev.to article: one store, both halves in sequence
    python tools/page_runs.py mem0       # the migration page: a mem0 export imported, twice
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
    shown = " ".join(('"%s"' % a if "'" in a else "'%s'" % a) if (" " in a or "{" in a or "'" in a) else a
                     for a in args)
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


MEM0_EXPORT = {"results": [
    {"id": "8f3c2a1e-4b6d-4f1a-9c2e-1a2b3c4d5e6f", "memory": "Alice prefers email over phone calls",
     "hash": "3bdbc25483ecfd293568596e79ba9105", "created_at": "2026-09-01T10:15:00Z",
     "updated_at": "2026-09-01T10:15:00Z", "user_id": "alice"},
    {"id": "2b7e9d4c-8a1f-4e3b-b5c6-7d8e9f0a1b2c", "memory": "Alice's phone number is +100",
     "hash": "07465ab59b99af58d34359d8e0e96d85", "created_at": "2026-09-02T08:00:00Z",
     "updated_at": "2026-09-02T08:00:00Z", "user_id": "alice", "metadata": {"key": "phone"}},
    {"id": "c1d2e3f4-a5b6-4c7d-8e9f-0a1b2c3d4e5f", "memory": "Bob's phone number is +300",
     "hash": "44d851a18e9a1991e3a6880db89edd4c", "created_at": "2026-09-03T12:30:00Z",
     "updated_at": "2026-09-03T12:30:00Z", "user_id": "bob", "agent_id": "support-bot", "metadata": {"key": "phone"}},
    {"id": "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d", "memory": "Promo code SUMMER26 is valid",
     "hash": "45e6da7c61dc19063ec2509dbae3d34e", "created_at": "2026-06-01T00:00:00Z",
     "updated_at": "2026-06-01T00:00:00Z", "user_id": "bob", "expiration_date": "2026-09-01"},
]}


def mem0(cwd: str) -> None:
    """The migration page: a mem0 get_all(show_expired=True) export (the 2.0.11 item shape, written by
    hand with the real md5 of each text, so the run needs no mem0 install and no model) imported
    twice, a correction, a recall, a subject erasure preview, the erasure, and the import once more."""
    with open(os.path.join(cwd, "mem0_export.json"), "w", encoding="utf-8") as fh:
        json.dump(MEM0_EXPORT, fh, indent=1)
    cat("mem0_export.json", cwd)
    pub = pubkey_of(run(["writer-key", "--new", "--out", "key.txt"], cwd))
    run(STORE + ["import-mem0", "mem0_export.json", "--dry-run"], cwd)
    run(STORE + ["import-mem0", "mem0_export.json"], cwd)
    run(STORE + ["import-mem0", "mem0_export.json"], cwd)
    run(["--path", "mem.json", "list"], cwd)
    run(STORE + ["remember", "Alice's phone number is +200", "--key", "alice::phone", "--source", "mem0/user/alice"], cwd)
    run(["--path", "mem.json", "recall", "Alice phone", "-k", "2"], cwd)
    run(STORE + ["forget-subject", "mem0/user/alice", "--request-id", "DSAR-3", "--dry-run"], cwd)
    run(STORE + ["forget-subject", "mem0/user/alice", "--request-id", "DSAR-3"], cwd)
    run(STORE + ["import-mem0", "mem0_export.json"], cwd)
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
            {"erasure": erasure, "ledger": ledger, "article": article, "mem0": mem0}[name](d)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(LOG, fh, indent=1, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
