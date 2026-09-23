#!/usr/bin/env python
"""The verification key is published in two places, and this fails when they drift apart.

WHY TWO PLACES. The whole chain rests on a reader holding the right key. A reader who fetches the
key from the same host they are checking is asking the host how to check the host: an operator who
can rewrite the log can rewrite the key beside it, and every signature still verifies. So the key
also lives in this repository's README, on different infrastructure, under a different account.

WHAT THAT BUYS AND WHAT IT DOES NOT. Two places raise the cost of a silent swap from one write to
two, on two systems, without either copy being noticed. They do not make us trustworthy: both
copies are ours. Independence comes from the external witness, which remembers a head we cannot
reach, and from the Bitcoin anchor. This check is about drift, not about trust.

    python tools/check_published_key.py                # offline: the README copy is self-consistent
    python tools/check_published_key.py --online       # also: the live host matches, and signs

Offline the check is deterministic and needs no network: the four-byte key id inside the line must
recompute from the public key inside the same line, per c2sp.org/signed-note. A hand-edited README
fails here. Online it fetches the host's own copy, requires the two to be byte-identical, and
verifies the live checkpoint's signature under the README's key, which is the part that proves the
published key is the one actually signing rather than merely a matching string.

Exit 0 on agreement, 1 on a mismatch, 2 when an online run could not reach the host.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import os
import re
import ssl
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from inspeximus import checkpoint as cp                                # noqa: E402

try:
    import certifi
    CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:                                                    # noqa: BLE001
    CTX = ssl.create_default_context()

README = os.path.join(os.path.dirname(HERE), "README.md")
HOST = "https://dancenitra.github.io/inspeximus-log/log"

#: The README carries the line between these markers so the check reads the published text rather
#: than a copy kept somewhere else in the repository. A second copy is the problem, not the fix.
BEGIN = "<!-- checkpoint-vkey:begin -->"
END = "<!-- checkpoint-vkey:end -->"

VKEY = re.compile(r"^([^\s+]+)\+([0-9a-f]{8})\+([A-Za-z0-9+/=]+)$")


def vkey_from_readme(path: str = README) -> str:
    """The one line the README publishes, read from between the markers."""
    text = open(path, encoding="utf-8").read()
    if BEGIN not in text or END not in text:
        raise SystemExit("README has no %s block" % BEGIN)
    block = text.split(BEGIN, 1)[1].split(END, 1)[0]
    lines = [ln.strip() for ln in block.splitlines() if ln.strip() and not ln.strip().startswith("`")]
    if len(lines) != 1:
        raise SystemExit("the vkey block must hold exactly one line, found %d" % len(lines))
    return lines[0]


def parse(line: str) -> tuple:
    """(name, key id hex, public key hex) from a signed-note verifier line."""
    m = VKEY.match(line)
    if not m:
        raise SystemExit("not a signed-note verifier line: %r" % line[:80])
    name, kid_hex, b64 = m.groups()
    raw = base64.b64decode(b64)
    if len(raw) != 33 or raw[0] != cp.ED25519_SIGNATURE_TYPE:
        raise SystemExit("the key is %d bytes with type 0x%02x, expected 33 and 0x01"
                         % (len(raw), raw[0] if raw else 0))
    return name, kid_hex, raw[1:].hex()


def fingerprint(line: str) -> str:
    """SHA-256 of the verifier line, for a reader comparing two pages by eye."""
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def offline(line: str) -> list:
    """The key id must recompute from the key beside it. This is what catches an edited README."""
    name, kid_hex, pub_hex = parse(line)
    recomputed = cp.key_id(name, pub_hex).hex()
    if recomputed != kid_hex:
        return ["the line says key id %s and the key inside it derives %s" % (kid_hex, recomputed)]
    return []


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "inspeximus-key-check/1"})
    with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
        return r.read()


def online(line: str, host: str) -> list:
    """The host's copy is byte-identical, and the live checkpoint verifies under this key."""
    problems = []
    theirs = fetch(host + "/checkpoint.vkey").decode("utf-8").strip()
    if theirs != line:
        problems.append("the host publishes %r and the README publishes %r" % (theirs[:80], line[:80]))
        return problems                                                # no point checking further
    name, _kid, pub_hex = parse(line)
    note = fetch(host + "/checkpoint").decode("utf-8")
    try:
        _text, signers = cp.verify_note(note, {name: pub_hex})
    except Exception as exc:                                           # noqa: BLE001
        return ["the live checkpoint does not verify under the published key: %s" % exc]
    if name not in signers:
        problems.append("the live checkpoint carries no signature by %s" % name)
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--online", action="store_true", help="also check the host's copy and signature")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--readme", default=README)
    a = ap.parse_args(argv)

    line = vkey_from_readme(a.readme)
    problems = offline(line)
    print("README key : %s" % line)
    print("fingerprint: %s" % fingerprint(line))

    if a.online and not problems:
        try:
            problems += online(line, a.host.rstrip("/"))
        except Exception as exc:                                       # noqa: BLE001
            print("COULD NOT REACH %s: %s" % (a.host, exc))
            return 2

    for p in problems:
        print("  MISMATCH: %s" % p)
    if problems:
        return 1
    print("OK: the two copies agree%s." % (" and the live checkpoint verifies under them"
                                           if a.online else " (offline check only)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
