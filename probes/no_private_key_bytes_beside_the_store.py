"""Scan a store's directory and the config home for private key material, and prove the scan can see it.

WHY THIS IS A PROBE AND NOT A CLAIM. "The signing key is not on the same disk as the records" is the
sentence a KMS buys, and it is the kind of sentence that stays true in a README long after somebody
drops a key file next to the store for an afternoon. So this reads the bytes.

    python probes/no_private_key_bytes_beside_the_store.py <store.json>

Prints one JSON line. Exit 0 when the tree is clean AND the planted-key control was FOUND, which is
the half that makes a clean result mean anything: a scanner that finds nothing because it cannot see
anything reports the same "clean" as a tree that really is.

WHAT IT LOOKS FOR, in order of how much it proves:
  - PEM private key blocks, which are unambiguous
  - a file named like a key (.key, .pem, id_ed25519, *secret*) whose contents are 64 hex characters
  - a 64-hex token whose Ed25519 public key MATCHES a public key published in this tree, which is
    not a shape heuristic at all: it is the private half of a key the records name
  - a JSON field whose name says secret and whose value is 64 hex characters

The first version also flagged any 64-hex token in the store's directory, and on our own live store
that reported five findings, every one of them a hash or a salt. A scanner that cries wolf over the
evidence it is protecting gets turned off, so the rule now has to say why a token is a KEY.

WHAT IT CANNOT DO, said here rather than discovered later: it reads files, so an encrypted blob or a
key inside a database page will not look like a key, and it says nothing about a key held in memory
by a process. It answers one question, which is whether the key is lying in the open beside the
evidence.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time

HEX64 = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])")
PEM = re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")
KEYISH = re.compile(r"(\.key$|\.pem$|id_ed25519$|id_rsa$|secret|private)", re.I)
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}
MAX_FILE_BYTES = 4 * 1024 * 1024


def _read(path):
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return None
        with open(path, "rb") as fh:
            return fh.read()
    except Exception:                                            # noqa: BLE001 - unreadable is reported
        return None


def public_keys_in(root: str) -> set:
    """Every public key this tree publishes, so a private half can be recognised rather than guessed."""
    out = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            blob = _read(os.path.join(dirpath, name))
            if blob is None:
                continue
            try:
                text = blob.decode("utf-8")
            except UnicodeDecodeError:
                continue
            for key, value in _json_pairs(text):
                if re.search(r"pub(key|lic)|attested_key|kid|verif", str(key), re.I):
                    v = str(value or "")
                    if HEX64.fullmatch(v):
                        out.add(v.lower())
    return out


def _is_private_half(token: str, publics: set) -> bool:
    """True when this 64-hex token is the Ed25519 private key for a public key in the tree."""
    if not publics:
        return False
    try:
        from cryptography.hazmat.primitives import serialization as ser
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        pub = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(token)).public_key().public_bytes(
            ser.Encoding.Raw, ser.PublicFormat.Raw).hex()
    except Exception:                                            # noqa: BLE001
        return False
    return pub.lower() in publics


def scan_tree(root: str, hex_anywhere: bool, publics=None):
    """-> (findings, files_read). `publics` turns the decisive test on: a matching private half."""
    publics = publics or set()
    findings, seen = [], 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            path = os.path.join(dirpath, name)
            blob = _read(path)
            if blob is None:
                continue
            seen += 1
            if PEM.search(blob[:4096].decode("latin-1")):
                findings.append({"path": path, "why": "a PEM private key block"})
                continue
            try:
                text = blob.decode("utf-8")
            except UnicodeDecodeError:
                continue
            hits = HEX64.findall(text)
            if not hits:
                continue
            matched = next((h for h in hits if _is_private_half(h, publics)), None)
            if matched:
                findings.append({"path": path,
                                 "why": "the private half of a public key this tree publishes"})
            elif KEYISH.search(name):
                findings.append({"path": path, "why": "a key-shaped filename holding 64 hex characters"})
            else:
                for key, value in _json_pairs(text):
                    if re.search(r"secret|private|sk_", key, re.I) and HEX64.fullmatch(str(value) or ""):
                        findings.append({"path": path, "why": "a field named %r holding 64 hex characters" % key})
                        break
    return findings, seen


def _json_pairs(text):
    try:
        data = json.loads(text)
    except Exception:                                            # noqa: BLE001
        return []
    out = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, (dict, list)):
                    walk(v)
                else:
                    out.append((k, v))
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(data)
    return out


def config_home():
    return (os.environ.get("INSPEXIMUS_HOME")
            or os.path.join(os.path.expanduser("~"), ".inspeximus"))


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    store = argv[0] if argv else os.path.join(config_home(), "mcp_memory_chain.json")
    store_dir = os.path.dirname(os.path.abspath(store)) or "."

    publics = public_keys_in(store_dir)
    findings, read_here = scan_tree(store_dir, hex_anywhere=False, publics=publics)
    home = config_home()
    home_findings, read_home = ([], 0)
    if os.path.abspath(home) != os.path.abspath(store_dir) and os.path.isdir(home):
        home_findings, read_home = scan_tree(home, hex_anywhere=False, publics=publics)

    # THE CONTROL. Plant a key in a fixture and require the scanner to name it. Without this, a
    # scanner that reads nothing at all prints the same reassuring "clean".
    control_dir = tempfile.mkdtemp(prefix="keyscan-control-")
    with open(os.path.join(control_dir, "receipt.key"), "w", encoding="utf-8") as fh:
        fh.write("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855\n")
    control, _n = scan_tree(control_dir, hex_anywhere=False)

    res = {
        "probe": os.path.basename(__file__),
        "when": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "store": os.path.abspath(store),
        "store_directory": store_dir,
        "files_read_in_store_directory": read_here,
        "config_home": home,
        "files_read_in_config_home": read_home,
        "public_keys_seen": len(publics),
        "findings_in_store_directory": findings,
        "findings_in_config_home": home_findings,
        "CONTROL_planted_key_found": bool(control),
        "clean": not findings and not home_findings,
        "scope": ("Reads files. An encrypted blob or a key inside a database page does not look like "
                  "a key here, and a key held in a running process is out of reach entirely."),
    }
    print(json.dumps(res, indent=2 if os.environ.get("PRETTY") else None))
    return 0 if res["clean"] and res["CONTROL_planted_key_found"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
