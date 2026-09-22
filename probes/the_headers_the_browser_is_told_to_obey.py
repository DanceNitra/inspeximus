#!/usr/bin/env python
"""Every published response carries the four headers, and every artifact names its own type.

WHAT THIS IS NOT. None of these headers protects the log. The log is protected by the Ed25519
signature over the checkpoint, which a reader verifies without trusting this server, this
certificate, or these headers. What they do is stop a browser being turned against the reader.

THE CONTROL IS A SECOND HOST. A probe that only visits the host we just configured cannot tell
"the headers are there" from "the check cannot fail". So it also visits our GitHub Pages site,
which sends none of them, and the run fails if that host passes.

    python probes/the_headers_the_browser_is_told_to_obey.py

Exit 0 when the hardened host has all four on every URL, every artifact names a type, and the
control host is still bare.
"""
from __future__ import annotations

import json
import ssl
import sys
import urllib.request

try:
    import certifi
    CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:                                              # noqa: BLE001
    CTX = ssl.create_default_context()

HOST = "https://92.5.74.17.sslip.io"
CONTROL = "https://dancenitra.github.io/agora/"

REQUIRED = {
    "strict-transport-security": "max-age=31536000",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "content-security-policy": "default-src 'none'",
}

#: Every artifact the log publishes, and the type it must name. Five of these carried no
#: Content-Type at all before this change, which is what let a browser guess.
TYPED = {
    "/log/checkpoint": "text/plain",
    "/log/checkpoint.vkey": "text/plain",
    "/log/log.jsonl": "text/plain",
    "/log/verify.py": "text/plain",
    "/log/entries/0.cose": "application/cose",
    "/log/head.json": "application/json",
    "/log/keys.json": "application/json",
    "/log/keys.cbor": "application/cbor",
    "/log/entries/0.leaf.json": "application/json",
    "/log/self-verification.json": "application/json",
    "/log/index.html": "text/html",
    "/mirror/": "text/html",
}


def headers(url: str) -> dict:
    req = urllib.request.Request(url, method="HEAD",
                                 headers={"User-Agent": "inspeximus-header-probe/1"})
    with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
        return {k.lower(): v for k, v in r.headers.items()}


def check(url: str) -> list:
    got = headers(url)
    problems = []
    for name, must_contain in REQUIRED.items():
        value = got.get(name, "")
        if must_contain not in value:
            problems.append("%s: %s is %r" % (url, name, value or "(absent)"))
    return problems


def main() -> int:
    problems, typed_problems = [], []
    for path in TYPED:
        problems += check(HOST + path)
    for path, expected in TYPED.items():
        ctype = headers(HOST + path).get("content-type", "")
        if expected not in ctype:
            typed_problems.append("%s: content-type is %r, expected %s" % (path, ctype or "(absent)", expected))

    control = check(CONTROL)
    out = {
        "kind": "inspeximus.header-probe/1",
        "host": HOST,
        "urls_checked": len(TYPED),
        "missing_headers": problems,
        "untyped_artifacts": typed_problems,
        "control_host": CONTROL,
        "control_missing": control,
        "control_can_fail": bool(control),
    }
    print(json.dumps(out, indent=2))
    if not control:
        print("CONTROL FAILED: the bare host passed, so this probe proves nothing.")
        return 2
    if problems or typed_problems:
        return 1
    print("OK: %d URLs, four headers each, every artifact names its type; "
          "the control host is missing %d of them." % (len(TYPED), len(control)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
