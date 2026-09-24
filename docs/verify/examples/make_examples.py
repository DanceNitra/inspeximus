#!/usr/bin/env python3
"""Regenerate the example files for the browser verifier's "Try it" section (docs/verify/index.html).

    python docs/verify/examples/make_examples.py           # rewrite the four files and the page's copies
    python docs/verify/examples/make_examples.py --check   # exit 1 if a fresh run would change anything

It writes four files beside itself:

    certificate-valid.json              Inspeximus.erasure_certificate() for one request: two records erased
    certificate-one-byte-changed.json   the same bytes, except one digit of the first tombstone's `ts`
    bundle-valid.json                   build_bundle() (the library's audit_bundle()) of the same store
    bundle-one-byte-changed.json        the same bytes, except one hex digit of a write receipt's content hash

and embeds the same bytes in the page, between the two "Try-it examples" markers. The page never fetches
the files: its policy forbids every request, so the copy it loads is the one inside it.

REAL LIBRARY. The documents come from this checkout's `inspeximus` through its public API, and are saved
the way `inspeximus erasure-certificate` and `inspeximus audit-build` save them:
json.dump(doc, fh, ensure_ascii=False, indent=2). The script also runs the Python verifiers on all four and
stops if a valid file fails or a changed one passes.

MADE-UP DATA, THROWAWAY KEY. The records are about "Example Subject 1" and "Example Subject 2", nobody real.
The signing key is derived below from a fixed public string, so anyone can sign with it. That is what an
example key should be, and it is the page's own point: a signature checked against the key the file
carries proves the signer held a key pair, not whose.

SAME BYTES ON EVERY RUN. The library takes record ids from uuid.uuid4, nonces from os.urandom and times from
time.time / time.gmtime. This script pins all four, in its own process only, to a seeded generator and a
clock that starts at 2026-09-01T09:00:00Z and ticks one second per time.time() call. It also clears INSPEXIMUS_*
variables, which change the library's behaviour. So a re-run leaves `git diff` empty until the library's
output changes, and `--check` confirms the committed files are what this script makes.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import itertools
import json
import os
import random
import re
import sys
import tempfile
import time
import uuid
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
PAGE = os.path.join(os.path.dirname(HERE), "index.html")

# A throwaway Ed25519 key for these examples and nothing else. Its private half is this public string's hash.
THROWAWAY_KEY = hashlib.sha256(b"inspeximus docs/verify examples: a throwaway key, never used for anything else").hexdigest()
REQUEST_ID = "DSAR-EXAMPLE-0001"
CLOCK_START = 1788253200.0            # 2026-09-01T09:00:00Z
SEED = "inspeximus docs/verify examples"

# file name -> (id of its copy in the page, the verdict it must get)
EXAMPLES = {
    "certificate-valid.json": ("example-certificate-valid", "VALID"),
    "certificate-one-byte-changed.json": ("example-certificate-one-byte-changed", "INVALID"),
    "bundle-valid.json": ("example-bundle-valid", "VALID"),
    "bundle-one-byte-changed.json": ("example-bundle-one-byte-changed", "INVALID"),
}
BEGIN = "<!-- Try-it examples: written by docs/verify/examples/make_examples.py, byte for byte the files in examples/. Do not edit by hand. -->\n"
END = "<!-- end of Try-it examples -->\n"


@contextlib.contextmanager
def _pinned():
    rng = random.Random(SEED)
    ticks = itertools.count()
    now = [CLOCK_START]
    real_gmtime = time.gmtime

    def clock():
        now[0] = CLOCK_START + next(ticks)
        return now[0]

    # gmtime() reads the clock without moving it, so `issued_iso` names the second `issued_ts` holds.
    with mock.patch("time.time", clock), \
            mock.patch("time.gmtime", lambda secs=None: real_gmtime(now[0] if secs is None else secs)), \
            mock.patch("uuid.uuid4", lambda: uuid.UUID(int=rng.getrandbits(128), version=4)), \
            mock.patch("os.urandom", lambda n: bytes(rng.getrandbits(8) for _ in range(n))):
        yield


def _saved(doc) -> str:
    """What the CLI writes to disk for this document."""
    return json.dumps(doc, ensure_ascii=False, indent=2)


def _change_one_byte(text: str, at: int, new: str) -> str:
    assert len(new) == 1 and new.isascii() and text[at].isascii() and new != text[at]
    return text[:at] + new + text[at + 1:]


def _backdate_first_tombstone(cert_text: str) -> str:
    """One digit of the first tombstone's `ts`, the 10**7 place: the erasure is dated 115.7 days earlier."""
    m = re.compile(r'"tombstones": \[\s*\{[^{}]*?"ts": (\d{10})\.').search(cert_text)
    assert m, "the certificate's first tombstone has no ten-digit ts"
    at = m.start(1) + 2
    assert cert_text[at] != "0"
    return _change_one_byte(cert_text, at, str(int(cert_text[at]) - 1))


def _swap_content_hash(bundle_text: str) -> str:
    """One hex digit of the last write receipt's `content_sha256`: the record's content is not what was written."""
    hits = [m.start(1) for m in re.finditer(r'"content_sha256": "([0-9a-f])', bundle_text)]
    assert hits, "the bundle has no write receipt"
    at = hits[-1]
    return _change_one_byte(bundle_text, at, "0123456789abcdef"[(int(bundle_text[at], 16) + 1) % 16])


def make() -> dict:
    """{file name: text}, fresh from the library."""
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        del os.environ[k]
    sys.path.insert(0, ROOT)
    from inspeximus import Inspeximus
    from inspeximus.audit_bundle import build_bundle, verify_bundle
    from inspeximus.core import verify_erasure_certificate

    with tempfile.TemporaryDirectory() as tmp, _pinned():
        m = Inspeximus(os.path.join(tmp, "store.json"), receipts=True, receipt_key=THROWAWAY_KEY)
        m.remember("Example Subject 1 asked to be contacted by email only.", source={"doc": "crm/example-subject-1"})
        m.remember("Example Subject 1 has a phone number on file.", key="example-subject-1::phone",
                   source={"doc": "crm/example-subject-1"})
        m.remember("Example Subject 2 prefers a call in the morning.", source={"doc": "crm/example-subject-2"})
        m.forget_subject("crm/example-subject-1", request_id=REQUEST_ID, basis="GDPR Art. 17 (example request)")
        cert = m.erasure_certificate(request_id=REQUEST_ID)
        bundle = build_bundle(m)

    assert cert["count"] == 2 and all(t.get("sig") for t in cert["tombstones"]), "expected two signed erasures"
    cert_text, bundle_text = _saved(cert), _saved(bundle)
    out = {
        "certificate-valid.json": cert_text,
        "certificate-one-byte-changed.json": _backdate_first_tombstone(cert_text),
        "bundle-valid.json": bundle_text,
        "bundle-one-byte-changed.json": _swap_content_hash(bundle_text),
    }

    # The Python verifiers are the oracle the page is tested against; hold every file to its label.
    for name, text in out.items():
        doc = json.loads(text)
        ok = (verify_erasure_certificate(doc)["valid"] if name.startswith("certificate")
              else verify_bundle(doc)["ok"])
        assert ok == (EXAMPLES[name][1] == "VALID"), f"{name}: the Python verifier says {'VALID' if ok else 'INVALID'}"
        # Raw text inside <script>: these three change how the HTML parser ends the element, and it
        # rewrites CR and NUL. Anything else, "<file>" in `verify_with` included, reads back unchanged.
        assert not re.search(r"<!--|</?script|[\r\x00]", text, re.I), f"{name} cannot be embedded in the page as is"
    for good, changed in (("certificate-valid.json", "certificate-one-byte-changed.json"),
                          ("bundle-valid.json", "bundle-one-byte-changed.json")):
        a, b = out[good].encode("utf-8"), out[changed].encode("utf-8")
        assert len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1, f"{changed} differs by more than one byte"
    return out


def page_with(examples: dict, page: str) -> str:
    blocks = "".join(f'<script type="application/json" id="{EXAMPLES[name][0]}" data-file="{name}">{text}</script>\n'
                     for name, text in examples.items())
    start, end = page.index(BEGIN), page.index(END)
    return page[:start + len(BEGIN)] + blocks + page[end:]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="change nothing; exit 1 if a fresh run differs from what is committed")
    ap.add_argument("--out", default=HERE, help="directory for the four files (default: beside this script)")
    ap.add_argument("--page", default=PAGE, help="the verifier page to embed them in (default: docs/verify/index.html)")
    a = ap.parse_args(argv)

    examples = make()
    with open(a.page, encoding="utf-8", newline="") as fh:
        page = fh.read()
    want = {os.path.join(a.out, n): t for n, t in examples.items()}
    want[a.page] = page_with(examples, page)

    stale = []
    for path, text in want.items():
        try:
            with open(path, encoding="utf-8", newline="") as fh:
                same = fh.read() == text
        except FileNotFoundError:
            same = False
        if same:
            continue
        stale.append(os.path.relpath(path, ROOT))
        if not a.check:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
    if a.check:
        print("up to date" if not stale else "differs from a fresh run: " + ", ".join(stale))
        return 1 if stale else 0
    print("wrote " + ", ".join(stale) if stale else "nothing changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
