# -*- coding: utf-8 -*-
"""The browser verifier at docs/verify/ must reach the verdict the Python verifiers reach, byte for byte.

The page re-implements two functions in JavaScript: `verify_erasure_certificate(cert)` and
`verify_bundle(bundle)`, both at their defaults, which is what `inspeximus erasure-verify` and
`inspeximus audit-verify` run with no options. A re-implementation is a second copy of a decision,
and this repository has watched second copies drift before (audit_bundle._cli documents one). So
the Python verifier is the oracle here, not a spec written from memory:

  * every generated certificate and bundle must read VALID in both;
  * a one-byte edit inside hashed or signed content must read INVALID in both;
  * a sample of random one-byte edits must get the SAME verdict from both, including the edits
    Python still calls valid (an unverified field), where the page must not say INVALID;
  * where Python has no verdict (it raises), the page must say CANNOT VERIFY HERE, and where the
    page cannot be sure it agrees (an Ed25519 point browsers and OpenSSL treat differently, a
    browser with no Ed25519), it must say CANNOT VERIFY HERE, never INVALID.

The documents deliberately carry what a naive port gets wrong: non-ASCII and astral text (Python
writes it raw, ensure_ascii=False; JSON.stringify would too, but orders keys by UTF-16 unit, not
code point), floats (Python writes 1e-05, 1e+16 and 100.0 where JavaScript writes 0.00001,
10000000000000000 and 100), and nested lists inside a hashed block.

Browser tests need Playwright and a Chromium. The guard is a fixture, not a module-level
importorskip, so each browser test shows up as its own skip line (see test_skip_census.py). The
`verifier-page` job in ci.yml installs both and sets VERIFIER_PAGE_REQUIRED=1, where a skip is a
failure. The tests without a browser run everywhere.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import http.server
import json
import math
import os
import random
import re
import struct
import sys
import threading

import pytest

cryptography = pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from inspeximus import Inspeximus, new_receipt_keypair, sign_erasure  # noqa: E402
from inspeximus.audit_bundle import BUNDLE_KIND, _bundle_hash, build_bundle, verify_bundle  # noqa: E402
from inspeximus.core import (_CERT_SCOPE, _CERT_SCOPE_COVERS, _CERT_SCOPE_EXCLUDES, _GENESIS,  # noqa: E402
                             _STH_FIELDS, _canon, _sha256_hex, sth_hash_of, verify_erasure_certificate)
from inspeximus.merkle import root as merkle_root  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE_DIR = os.path.join(ROOT, "docs", "verify")
PAGE = os.path.join(PAGE_DIR, "index.html")
GROUP = pytest.mark.xdist_group("verifier_page")     # one browser, one worker


def _page_source() -> str:
    with open(PAGE, encoding="utf-8") as fh:
        return fh.read()


def _page_constants() -> dict:
    m = re.search(r'<script type="application/json" id="inspeximus-constants">(.*?)</script>',
                  _page_source(), re.S)
    assert m, "the constants block is gone from the page"
    return json.loads(m.group(1))


# ─── the oracle: what the CLI would say ─────────────────────────────────────────────────────────────

def _kind(doc):
    """The page's own routing rule, so both sides judge a document with the same verifier."""
    if not isinstance(doc, dict):
        return None
    if "inspeximus_erasure_certificate" in doc:
        return "certificate"
    if "kind" in doc or "bundle_hash" in doc:
        return "bundle"
    if "tombstones" in doc:
        return "certificate"
    return None


def python_verdict(text: str) -> str:
    """VALID / INVALID, or CRASH where the CLI raises instead of printing a verdict."""
    if text.startswith("﻿"):
        return "CRASH"                         # json.load on a utf-8 (not utf-8-sig) file: "Unexpected UTF-8 BOM"
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        return "INVALID"                       # not a document at all; the page says "Not valid JSON"
    except (ValueError, RecursionError):
        return "CRASH"                         # e.g. an integer over sys.get_int_max_str_digits()
    kind = _kind(doc)
    if kind is None:
        return "INVALID"
    try:
        if kind == "certificate":
            return "VALID" if verify_erasure_certificate(doc)["valid"] else "INVALID"
        return "VALID" if verify_bundle(doc)["ok"] else "INVALID"
    except Exception:                          # noqa: BLE001 -- a verifier that raises has no verdict
        return "CRASH"


EXPECT_PAGE = {"VALID": "VALID", "INVALID": "INVALID", "CRASH": "CANNOT"}


# ─── documents ──────────────────────────────────────────────────────────────────────────────────────

def _pub_hex(sk: Ed25519PrivateKey) -> str:
    return sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def _synthetic_certificate(tombs, key=None, scoped_to=None, sign=True):
    """A certificate built from the library's own primitives, with contents erasure_certificate()
    would never produce on its own (floats at the repr boundaries, nested lists in `auth`). The
    Python verifier must accept it, or the case proves nothing."""
    sk = key or Ed25519PrivateKey.generate()
    pub = _pub_hex(sk)
    chain, prev = [], _GENESIS
    for i, spec in enumerate(tombs):
        t = {"seq": i, "memory_id": spec["memory_id"], "ts": spec["ts"],
             "request_id": spec.get("request_id"), "prev": prev}
        if "auth" in spec:
            t["auth"] = spec["auth"]
        t["hash"] = _sha256_hex(_canon(Inspeximus._tombstone_core(t)))
        if sign:
            t["pubkey"] = pub
            t["sig"] = sk.sign(bytes.fromhex(t["hash"])).hex()
        chain.append(t)
        prev = t["hash"]
    anchor = {"n_writes": 0, "writes_tip": _GENESIS, "n_tombstones": len(chain), "tombstones_tip": prev,
              "ts": 1790000000.25, "writes_root": merkle_root([]).hex(),
              "tombstones_root": merkle_root([_canon(Inspeximus._chain_core(t, "tombstone"))
                                              for t in chain]).hex(),
              "merkle": "rfc6962-sha256"}
    anchor["sth_hash"] = sth_hash_of(anchor)
    anchor["root_hash"] = _sha256_hex(_canon({k: anchor[k] for k in
                                              ("n_writes", "writes_root", "n_tombstones", "tombstones_root",
                                               "merkle")}))
    scope = chain if scoped_to is None else [t for t in chain if t["request_id"] == scoped_to]
    cert = {"inspeximus_erasure_certificate": "1.0", "issued_ts": 1790000001.0,
            "issued_iso": "2026-09-21T12:00:01Z", "scoped_to": scoped_to,
            "request_ids": sorted({t["request_id"] for t in scope if t["request_id"] is not None}),
            "erased_memory_ids": sorted({t["memory_id"] for t in scope if t["memory_id"]}),
            "count": len({t["memory_id"] for t in scope if t["memory_id"]}),
            "tombstones": chain, "pubkey": pub, "anchor": anchor,
            "self_check": {"verified": True, "problems": []}, "conversion_backup": {},
            "scope": _CERT_SCOPE, "scope_covers": list(_CERT_SCOPE_COVERS),
            "scope_excludes": list(_CERT_SCOPE_EXCLUDES),
            "verify_with": "inspeximus.verify_erasure_certificate(cert, store_path=<file>)"}
    res = verify_erasure_certificate(cert)
    assert res["valid"], res["problems"]
    return cert


REPR_BOUNDARY_FLOATS = [1e-05, 0.0001, 1e16, 1e15, 0.1, 123456789.123, 5e-324, 1.7976931348623157e308,
                        -0.0, 2.5, 1e22, 100.0, 1790228694.912677, 1.5e-7, 123456789012345680.0,
                        9007199254740993.0, 0.30000000000000004, 1e21, 1e-7, 2.2250738585072014e-308]

NESTED_AUTH = {
    "basis": ["čl. 17 GDPR", ["nested", [1.5, [2, [3e-07, [True, None, []]]]], {}],
              {"z": 1, "é": 2, "😀": 3, "￿": 4, "": 5, "a\u0000b": "ctrl\u0001\u001f\u007f",
               "𝄞": [1e+16, -0.0, 100.0, 12345678901234567890123]}],
    "authorized_by": None,
    "authorization": None,
    "extra": [[[[["deep", 0.1]]]], {"quote\"back\\slash": "line\nsep tab\t"}],
}


def _real_documents(tmp):
    """Certificates and bundles made by the library's public API, the way an operator makes them."""
    docs = {}

    def store(name, **kw):
        return Inspeximus(os.path.join(tmp, name + ".json"), receipts=True, **kw)

    m = store("ascii", receipt_key=os.urandom(32).hex())
    m.remember("Alice prefers email", source={"doc": "crm/alice"})
    m.remember("Alice phone is +100", key="alice::phone", source={"doc": "crm/alice"})
    m.remember("Bob phone is +300", key="bob::phone", source={"doc": "crm/bob"})
    m.forget_subject("crm/alice", request_id="DSAR-17")
    docs["cert-ascii"] = m.erasure_certificate(request_id="DSAR-17")

    m = store("unicode", receipt_key=os.urandom(32).hex())
    m.remember("Žofia Nováková má telefón +421 900", source={"doc": "crm/žofia"})
    m.remember("Žofia býva v Nitre", key="žofia::adresa", source={"doc": "crm/žofia"})
    m.remember("Ωmega 😀 keeps this", source={"doc": "crm/ωmega"})
    psk, ppk = new_receipt_keypair()
    rid = "DSAR-Žofia-😀-「請求」"
    m.forget_subject("crm/žofia", request_id=rid, basis="čl. 17 GDPR — žiadosť „Žofia“",
                     authorized_by=ppk, authorization=sign_erasure(psk, "crm/žofia", rid))
    docs["cert-unicode"] = m.erasure_certificate(request_id=rid)
    docs["bundle-unicode"] = build_bundle(m, store_id="sklad-Žilina-😀")

    m = store("unsigned")
    m.remember("Carol likes tea", source={"doc": "crm/carol"})
    m.remember("Dan likes coffee", source={"doc": "crm/dan"})
    m.forget_subject("crm/carol", request_id="REQ-1")
    docs["cert-unsigned"] = m.erasure_certificate(request_id="REQ-1")
    docs["bundle-unsigned"] = build_bundle(m)

    m = store("two-requests", receipt_key=os.urandom(32).hex())
    for who in ("erin", "frank", "gina"):
        m.remember(f"{who} note", source={"doc": f"crm/{who}"})
    m.forget_subject("crm/erin", request_id="DSAR-A")
    m.forget_subject("crm/frank", request_id="DSAR-B")
    ids = [r["id"] for r in m.items if "gina" in r.get("text", "")]
    m.forget(ids)                                          # no request_id: an unattributed tombstone
    docs["cert-second-request"] = m.erasure_certificate(request_id="DSAR-B")
    docs["cert-unscoped"] = m.erasure_certificate()

    m = store("grants", receipt_key=os.urandom(32).hex())
    m.remember("billing A", tags=["billing"], key="acct::1")
    m.remember("billing B", tags=["billing"], key="acct::1")
    m.grant("agent-b", tag="billing", by="ops")
    m.revoke("agent-b", tag="billing", by="ops")
    docs["bundle-grants"] = build_bundle(m)

    docs["bundle-empty"] = build_bundle(store("empty"))

    # Receipts turned on late (a `backfill` block) and a slash() amendment (`amends`, `amend_reason`):
    # the three optional fields of the write receipt's preimage, all inside the hash.
    path = os.path.join(tmp, "amended.json")
    Inspeximus(path).remember("written before the chain existed", source={"doc": "crm/early"})
    m = Inspeximus(path, receipts=True, receipt_key=os.urandom(32).hex())
    m.enable_receipts(reason="turned on late — Žilina")
    mid = m.remember("a claim later caught as poisoned", mtype="semantic")
    m.slash([mid if isinstance(mid, str) else mid["id"]], scope="memory", reason="corrected: měření bylo chybné")
    amended = build_bundle(m)
    assert any(r.get("amends") and r.get("amend_reason") for r in amended["write_chain"])
    assert any(r.get("backfill") for r in amended["write_chain"])
    docs["bundle-amended"] = amended

    extra = copy.deepcopy(docs["bundle-unicode"])
    extra["x_extra"] = {"floats": REPR_BOUNDARY_FLOATS, "nested": NESTED_AUTH, "Ünïcødé 🔑": [[[1.0]], [2.5e-8]]}
    extra["bundle_hash"] = _bundle_hash(extra)
    docs["bundle-extra-nested"] = extra
    return docs


def _synthetic_documents():
    key = Ed25519PrivateKey.generate()
    return {
        "cert-floats": _synthetic_certificate(
            [{"memory_id": f"m{i:02d}", "ts": f, "request_id": "R-float"} for i, f in enumerate(REPR_BOUNDARY_FLOATS)],
            key=key),
        "cert-nested-lists": _synthetic_certificate(
            [{"memory_id": "n0", "ts": 1.0, "request_id": "R-nested", "auth": NESTED_AUTH},
             {"memory_id": "n1", "ts": 2.0, "request_id": "R-nested", "auth": {"basis": [[["x"]], [[]]]}}],
            key=key),
        "cert-names": _synthetic_certificate(
            [{"memory_id": "Žofia-1", "ts": 10.5, "request_id": "DSAR-Žofia-😀"},
             {"memory_id": "😀-2", "ts": 11.5, "request_id": "DSAR-Žofia-😀"},
             {"memory_id": 'quote"back\\slash', "ts": 12.5, "request_id": "DSAR-Žofia-😀"},
             {"memory_id": "line\nsep \ttab", "ts": 13.5, "request_id": "other-Ω"},
             {"memory_id": "-private-use", "ts": 14.5, "request_id": "DSAR-Žofia-😀"}],
            key=key, scoped_to="DSAR-Žofia-😀"),
        "cert-unsigned-floats": _synthetic_certificate(
            [{"memory_id": "u0", "ts": 1e-05, "request_id": None},
             {"memory_id": "u1", "ts": 1e+16, "request_id": None}], sign=False),
    }


def _texts(doc):
    """Two spellings of the same document: what an operator saves (pretty, raw UTF-8), and the
    all-escaped form json.dumps writes by default (\\uXXXX, surrogate pairs for astral text)."""
    return {"pretty": json.dumps(doc, ensure_ascii=False, indent=2),
            "escaped": json.dumps(doc)}


def _bump(ch: str) -> str:
    if ch.isdigit():
        return "0" if ch == "9" else chr(ord(ch) + 1)
    if "a" <= ch <= "z":
        return "a" if ch == "z" else chr(ord(ch) + 1)
    if "A" <= ch <= "Z":
        return "A" if ch == "Z" else chr(ord(ch) + 1)
    raise ValueError(ch)


def _edit_at(text: str, pos: int) -> str:
    """A ONE-BYTE edit: the first ASCII letter or digit at or after `pos`, replaced by the next one.
    ASCII for ASCII, so the UTF-8 byte length is unchanged and exactly one byte differs."""
    while not (text[pos].isascii() and text[pos].isalnum()):
        pos += 1
    return text[:pos] + _bump(text[pos]) + text[pos + 1:]


def _required_edits(name: str, text: str) -> dict:
    """Edits inside content the verifier hashes or signs. Every one must make the document INVALID."""
    def after(needle, which=0):
        hits = [m.end() for m in re.finditer(re.escape(needle), text)]
        assert hits, (name, needle)
        return hits[which]
    out = {}
    if name.startswith("cert"):
        out["tombstone hash"] = _edit_at(text, after('"hash": "') + 7)
        out["memory_id"] = _edit_at(text, after('"memory_id": "', -1))
        out["tombstone ts"] = _edit_at(text, after('"ts": '))
        if '"sig": "' in text:
            out["signature"] = _edit_at(text, after('"sig": "') + 30)
        if '"auth": {' in text:
            out["auth block"] = _edit_at(text, after('"auth": {'))
    else:
        out["bundle_hash"] = _edit_at(text, after('"bundle_hash": "') + 11)
        out["n_records"] = _edit_at(text, after('"n_records": '))
        if '"write_chain": [\n' in text and '"write_chain": []' not in text:
            out["write receipt hash"] = _edit_at(text, after('"hash": "') + 3)
        if "x_extra" in text:
            out["nested extra field"] = _edit_at(text, after('"floats": [') + 3)
        if '"amend_reason": "' in text:
            out["amendment reason"] = _edit_at(text, after('"amend_reason": "'))
    return out


def _random_edits(text: str, n: int, seed: int) -> list:
    rng = random.Random(seed)
    spots = [i for i, c in enumerate(text) if c.isascii() and c.isalnum()]
    return [_edit_at(text, p) for p in rng.sample(spots, min(n, len(spots)))]


@pytest.fixture(scope="module")
def documents(tmp_path_factory):
    docs = _real_documents(str(tmp_path_factory.mktemp("verifier_page_stores")))
    docs.update(_synthetic_documents())
    for name, doc in docs.items():
        for spelling, text in _texts(doc).items():
            assert python_verdict(text) == "VALID", (name, spelling)
    return docs


# ─── tests that need no browser ─────────────────────────────────────────────────────────────────────

def test_the_page_compares_against_the_library_constants():
    """The scope sentence, its lists and the anchor field names are compared, not derived. If the
    library changes one and the page does not, every certificate would read INVALID in the browser."""
    c = _page_constants()
    assert c["cert_scope"] == _CERT_SCOPE
    assert c["cert_scope_covers"] == list(_CERT_SCOPE_COVERS)
    assert c["cert_scope_excludes"] == list(_CERT_SCOPE_EXCLUDES)
    assert c["sth_fields"] == list(_STH_FIELDS)
    assert c["bundle_kind"] == BUNDLE_KIND
    assert c["genesis"] == _GENESIS
    assert c["root_fields"] == ["n_writes", "writes_root", "n_tombstones", "tombstones_root", "merkle"]


def test_the_page_states_the_webcrypto_ed25519_minimums():
    src = _page_source()
    for row in ("<td>Chrome, Chrome for Android, Android WebView</td><td>137</td>",
                "<td>Edge</td><td>137</td>", "<td>Firefox, Firefox for Android</td><td>129</td>",
                "<td>Safari, Safari on iOS</td><td>17</td>"):
        assert row in src, row


def test_the_page_cannot_reach_the_network_by_policy():
    """No build step, no library, no request after load: a CSP that forbids connections, and no
    attribute that points anywhere."""
    src = _page_source()
    csp = re.search(r'http-equiv="Content-Security-Policy"\s+content="([^"]+)"', src).group(1)
    assert "default-src 'none'" in csp and "connect-src 'none'" in csp
    assert not re.search(r'\b(?:src|href)\s*=\s*"(?:https?:)?//', src), "the page loads something remote"
    assert "fetch(" not in src and "XMLHttpRequest" not in src and "import(" not in src


# ─── the browser ────────────────────────────────────────────────────────────────────────────────────

def _chromium_candidates():
    env = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    return [None] + [p for p in (env, "/opt/pw-browsers/chromium") if p and os.path.exists(p)]


def _unavailable(why):
    if os.environ.get("VERIFIER_PAGE_REQUIRED"):
        pytest.fail("VERIFIER_PAGE_REQUIRED is set but " + why)
    pytest.skip(why)


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def browser():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _unavailable("playwright is not installed (pip install playwright; python -m playwright install chromium)")
    pw = sync_playwright().start()
    last = None
    for exe in _chromium_candidates():
        try:
            b = pw.chromium.launch(**({"executable_path": exe} if exe else {}))
            break
        except Exception as e:                 # noqa: BLE001
            last = e
    else:
        pw.stop()
        _unavailable(f"no Chromium could be launched ({str(last).splitlines()[0][:160]})")
    yield b
    b.close()
    pw.stop()


@pytest.fixture(scope="module")
def site():
    """GitHub Pages serves the directory as static files; so does this."""
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=PAGE_DIR))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/"
    srv.shutdown()


def _open(browser, site, init_script=None):
    ctx = browser.new_context()
    if init_script:
        ctx.add_init_script(init_script)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    requests = []
    page.on("request", lambda r: requests.append(r.url))
    page.goto(site)
    page.wait_for_load_state("load")
    page.wait_for_function("() => !!window.inspeximusVerifier")
    page._verifier_errors = errors
    page._verifier_requests = requests
    return page


@pytest.fixture(scope="module")
def page(browser, site):
    p = _open(browser, site)
    yield p
    assert p._verifier_errors == [], p._verifier_errors
    p.context.close()


def verify_many(page, texts):
    return page.evaluate("""async texts => {
        const out = [];
        for (const t of texts) {
            const r = await window.inspeximusVerifier.verifyText(t);
            out.push({verdict: r.verdict, reason: r.reason});
        }
        return out;
    }""", texts)


@GROUP
def test_every_generated_document_is_valid_in_the_browser(page, documents):
    cases = [(n, s, t) for n, d in documents.items() for s, t in _texts(d).items()]
    got = verify_many(page, [t for _, _, t in cases])
    wrong = [(n, s, g) for (n, s, _), g in zip(cases, got) if g["verdict"] != "VALID"]
    assert not wrong, wrong
    assert len(documents) >= 14 and len(cases) == 2 * len(documents)


@GROUP
def test_a_one_byte_edit_of_each_is_invalid_in_both(page, documents):
    """Each document, each spelling, each hashed or signed region: flip one byte, and the Python
    verifier and the page must both refuse it."""
    cases = []
    for name, doc in documents.items():
        for spelling, text in _texts(doc).items():
            for where, edited in _required_edits(name, text).items():
                assert len(edited.encode("utf-8")) == len(text.encode("utf-8"))
                assert sum(a != b for a, b in zip(edited.encode("utf-8"), text.encode("utf-8"))) == 1
                cases.append((name, spelling, where, edited))
    got = verify_many(page, [c[3] for c in cases])
    for (name, spelling, where, edited), g in zip(cases, got):
        assert python_verdict(edited) == "INVALID", (name, spelling, where)
        assert g["verdict"] == "INVALID", (name, spelling, where, g)
    names = {c[0] for c in cases}
    assert names == set(documents), "a document got no required edit"


@GROUP
def test_random_one_byte_edits_get_the_python_verdict(page, documents):
    """Not only the edits chosen to break something. An edit to an unhashed field (issued_ts,
    verify_with, self_check) leaves the certificate VALID in Python, and the page must agree; an
    edit inside a \\uXXXX escape can make a lone surrogate, where Python raises and the page must
    say CANNOT VERIFY HERE."""
    cases = []
    for k, (name, doc) in enumerate(sorted(documents.items())):
        for j, (spelling, text) in enumerate(sorted(_texts(doc).items())):
            for edited in _random_edits(text, 25, seed=1000 * k + j):
                cases.append((name, spelling, edited, python_verdict(edited)))
    got = verify_many(page, [c[2] for c in cases])
    mismatches = [(n, s, pv, g) for (n, s, _, pv), g in zip(cases, got) if g["verdict"] != EXPECT_PAGE[pv]]
    assert not mismatches, mismatches[:10]
    seen = {pv for *_, pv in cases}
    assert {"VALID", "INVALID"} <= seen, seen


def _resealed(bundle, change):
    """What an editor does to a bundle: change it, then recompute the unkeyed bundle_hash. Every check
    after (1) then has to catch the change on its own."""
    b = copy.deepcopy(bundle)
    change(b)
    b["bundle_hash"] = _bundle_hash(b)
    return json.dumps(b, ensure_ascii=False, indent=2)


def _flip_hex(s: str, i: int) -> str:
    return s[:i] + ("0" if s[i] != "0" else "1") + s[i + 1:]


@GROUP
def test_a_resealed_bundle_is_judged_on_its_chains_and_signatures(page, documents):
    signed = documents["bundle-unicode"]
    cases = {
        "a write signature forged": lambda b: b["write_chain"][1].update(sig=_flip_hex(b["write_chain"][1]["sig"], 70)),
        "a signature stripped": lambda b: b["write_chain"][0].pop("sig"),
        "a write receipt rewritten": lambda b: b["write_chain"][0]["commit"].update(mtype="semantic"),
        "the anchor count": lambda b: b["anchor"].update(n_writes=b["anchor"]["n_writes"] + 1),
        "the governance total": lambda b: b["governance"].update(erasures_total=7),
        "a merkle root": lambda b: b["anchor"].update(writes_root="00" * 32),
        "the root commitment": lambda b: b["anchor"].update(root_hash=_flip_hex(b["anchor"]["root_hash"], 0)),
        "a refusal recorded": lambda b: b["anchor"].update(witness_refusals=[{"reason": "fork at 2"}]),
        "an unreceipted grant": lambda b: b.update(grants=[{"id": "nope", "status": "active", "state": "granted"}]),
    }
    harmless = {
        "the export timestamp": lambda b: b.update(generated_ts=1.5),
        "the store label": lambda b: b.update(store_id="renamed-Ω"),
        "the supersession summary": lambda b: b["supersession"].update(superseded_total=99),
        # PYTHON'S BEHAVIOUR, MATCHED, NOT ENDORSED. _content_free_tombstones exports `sig` without
        # `pubkey`, and verify_bundle checks a signature only against the key its entry names (or a
        # pinned one), so an unpinned bundle never checks tombstone signatures. The page must agree
        # with the CLI here; the fix belongs in the exporter, not in a verifier that disagrees.
        "a tombstone signature forged": lambda b: b["tombstone_chain"][0].update(sig=_flip_hex(b["tombstone_chain"][0]["sig"], 5)),
    }
    texts = {k: _resealed(signed, f) for k, f in {**cases, **harmless}.items()}
    got = dict(zip(texts, verify_many(page, list(texts.values()))))
    for name in cases:
        assert python_verdict(texts[name]) == "INVALID", name
        assert got[name]["verdict"] == "INVALID", (name, got[name])
    for name in harmless:
        assert python_verdict(texts[name]) == "VALID", name
        assert got[name]["verdict"] == "VALID", (name, got[name])


@GROUP
def test_canonical_json_matches_python_byte_for_byte(page):
    """The encoding itself, on documents built to break a port: random doubles from raw bit
    patterns, integers past 2**53, every control character, keys that sort differently by code
    point and by UTF-16 unit."""
    rng = random.Random(20260924)
    pool = ["a", "Z", "é", "Ž", " ", "\u007f", "", "￿", "😀", "𝄞", '"', "\\", "/", " ",
            "\t", "\n", "\x00", "\x1f", "0", "中"]

    def rstr():
        return "".join(rng.choice(pool) for _ in range(rng.randint(0, 6)))

    def rfloat():
        x = struct.unpack("<d", struct.pack("<Q", rng.getrandbits(64)))[0]
        return 0.5 if math.isnan(x) and rng.random() < 0.9 else x

    def rval(depth):
        r = rng.random()
        if depth > 3 or r < 0.35:
            return rng.choice([None, True, False, rstr(), rfloat(), rfloat(), rng.randint(-2**70, 2**70),
                               rng.randint(-5, 5), rng.choice(REPR_BOUNDARY_FLOATS)])
        if r < 0.65:
            return [rval(depth + 1) for _ in range(rng.randint(0, 4))]
        return {rstr(): rval(depth + 1) for _ in range(rng.randint(0, 5))}

    docs = [rval(0) for _ in range(150)]
    docs.append([rfloat() for _ in range(3000)])
    docs.append({chr(c): c for c in list(range(0x20)) + [0x7f, 0xe9, 0xe000, 0xffff, 0x10000, 0x1f600, 0x10ffff]})
    texts = [json.dumps(d, ensure_ascii=bool(i % 2)) for i, d in enumerate(docs)]
    got = page.evaluate("ts => ts.map(t => window.inspeximusVerifier.canonicalize(t))", texts)
    for text, g in zip(texts, got):
        want = _canon(json.loads(text)).decode("utf-8")
        assert g == want, (text[:200], g[:200], want[:200])
        assert hashlib.sha256(g.encode("utf-8")).hexdigest() == _sha256_hex(_canon(json.loads(text)))


# ─── cannot verify here, never INVALID ──────────────────────────────────────────────────────────────

# RFC 8032 arithmetic, only to MAKE a signature over a small-order key. Nothing here verifies.
_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_BY = 4 * pow(5, _P - 2, _P) % _P


def _recover_x(y, sign):
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * pow(2, (_P - 1) // 4, _P) % _P
    return _P - x if (x & 1) != sign else x


def _padd(p, q):
    a, b = (p[1] - p[0]) * (q[1] - q[0]) % _P, (p[1] + p[0]) * (q[1] + q[0]) % _P
    c, d = 2 * p[3] * q[3] * _D % _P, 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _pmul(s, p):
    q = (0, 1, 1, 0)
    while s:
        if s & 1:
            q = _padd(q, p)
        p, s = _padd(p, p), s >> 1
    return q


def _encode(p):
    zi = pow(p[2], _P - 2, _P)
    x, y = p[0] * zi % _P, p[1] * zi % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


_BASE = (_recover_x(_BY, 0), _BY, 1, _recover_x(_BY, 0) * _BY % _P)


def _is_identity(q):
    return q[0] % _P == 0 and (q[1] - q[2]) % _P == 0


def _order_8_point():
    rng = random.Random(8)
    while True:
        y = rng.randrange(_P)
        x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
        x = pow(x2, (_P + 3) // 8, _P)
        if (x * x - x2) % _P:
            x = x * pow(2, (_P - 1) // 4, _P) % _P
        if (x * x - x2) % _P:
            continue
        t = _pmul(_L, (x, y, 1, x * y % _P))
        if not _is_identity(_pmul(4, t)):
            return t


def _small_order_signature(msg: bytes):
    """The identity point as a public key, and a signature cofactorless verification accepts:
    R = [r]B, S = r, because [k]A is the identity for every k."""
    r = random.Random(7).randrange(1, _L)
    return (1).to_bytes(32, "little").hex(), (_encode(_pmul(r, _BASE)) + r.to_bytes(32, "little")).hex()


def _mixed_order_signature(msg: bytes, openssl_accepts: bool):
    """A = [a]B + T with T of order 8, R = [r]B, S = r + k*a. Then [S]B - [k]A = R - [k]T: OpenSSL's
    cofactorless check passes only when k = 0 mod 8, while a cofactored verifier ([8]-multiplied, which
    RFC 8032 also allows and a browser may use) passes for every k."""
    rng = random.Random(9 + openssl_accepts)
    a = rng.randrange(1, _L)
    pub = _encode(_padd(_pmul(a, _BASE), _order_8_point()))
    while True:
        r = rng.randrange(1, _L)
        big_r = _encode(_pmul(r, _BASE))
        k = int.from_bytes(hashlib.sha512(big_r + pub + msg).digest(), "little") % _L
        if (k % 8 == 0) == openssl_accepts:
            return pub.hex(), (big_r + ((r + k * a) % _L).to_bytes(32, "little")).hex()


def _cert_signed_with(pub_and_sig):
    cert = _synthetic_certificate([{"memory_id": "e0", "ts": 1.0, "request_id": "R"}], sign=False)
    t = cert["tombstones"][0]
    t["pubkey"], t["sig"] = pub_and_sig(bytes.fromhex(t["hash"]))
    cert["pubkey"] = t["pubkey"]
    return json.dumps(cert, indent=2)


@GROUP
def test_a_degenerate_ed25519_key_is_cannot_verify_when_openssl_accepts_it(page):
    """OpenSSL (Python's `cryptography`) accepts these, and a browser may refuse small-order keys or
    check the cofactored equation. The page must not claim either answer."""
    for make in (_small_order_signature, lambda m: _mixed_order_signature(m, True)):
        text = _cert_signed_with(make)
        assert python_verdict(text) == "VALID"
        got = verify_many(page, [text])[0]
        assert got["verdict"] == "CANNOT", got
        assert "use the CLI" in got["reason"] and "OpenSSL" in got["reason"]


@GROUP
def test_a_degenerate_ed25519_key_is_invalid_when_openssl_rejects_it(page):
    """The case that makes the page compute OpenSSL's equation itself: a cofactored verifier accepts
    this signature and OpenSSL does not. Trusting the browser here could print VALID for a
    certificate the CLI fails."""
    text = _cert_signed_with(lambda m: _mixed_order_signature(m, False))
    assert python_verdict(text) == "INVALID"
    got = verify_many(page, [text])[0]
    assert got["verdict"] == "INVALID", got
    assert "invalid signature" in got["reason"]


@GROUP
def test_what_python_cannot_parse_or_encode_is_cannot_verify(page, documents):
    cert = copy.deepcopy(documents["cert-ascii"])
    huge = json.dumps(cert, indent=2).replace('"count": ', '"issued_digits": ' + "9" * 5000 + ',\n  "count": ', 1)
    lone = json.dumps(cert, indent=2).replace('"DSAR-17"', '"DSAR-\\ud800-17"')          # inside hashed content
    bom = "﻿" + json.dumps(cert, indent=2)
    for text in (huge, lone, bom):
        if text is huge and not hasattr(sys, "get_int_max_str_digits"):
            continue                          # interpreters before the 4300-digit limit parse it
        assert python_verdict(text) == "CRASH"
    got = verify_many(page, [huge, lone, bom])
    assert [g["verdict"] for g in got] == ["CANNOT"] * 3, got


NO_ED25519 = """
(() => {
  const orig = crypto.subtle.importKey.bind(crypto.subtle);
  crypto.subtle.importKey = function (fmt, data, alg, ...rest) {
    const name = typeof alg === "string" ? alg : alg && alg.name;
    if (String(name).toLowerCase() === "ed25519") {
      return Promise.reject(new DOMException("Unrecognized name.", "NotSupportedError"));
    }
    return orig(fmt, data, alg, ...rest);
  };
})();
"""


@GROUP
def test_a_browser_without_ed25519_says_cannot_verify_for_signed_files_only(browser, site, documents):
    p = _open(browser, site, init_script=NO_ED25519)
    try:
        names = ["cert-ascii", "bundle-unicode", "cert-unsigned", "bundle-unsigned", "cert-unsigned-floats"]
        got = verify_many(p, [json.dumps(documents[n], indent=2) for n in names])
        assert [g["verdict"] for g in got] == ["CANNOT", "CANNOT", "VALID", "VALID", "VALID"], got
        assert "Chrome/Edge 137+, Firefox 129+ or Safari 17+" in got[0]["reason"]

        # Without Ed25519 the page still settles what it can without it. A broken chain is INVALID
        # whatever the signatures say, and so is S >= L, which OpenSSL refuses before any curve math.
        broken = _required_edits("cert-ascii", json.dumps(documents["cert-ascii"], indent=2))["tombstone hash"]
        malleable = copy.deepcopy(documents["cert-ascii"])
        t = malleable["tombstones"][0]
        s = int.from_bytes(bytes.fromhex(t["sig"])[32:], "little") + _L
        t["sig"] = t["sig"][:64] + s.to_bytes(32, "little").hex()
        texts = [broken, json.dumps(malleable, indent=2)]
        assert [python_verdict(x) for x in texts] == ["INVALID", "INVALID"]
        assert [g["verdict"] for g in verify_many(p, texts)] == ["INVALID", "INVALID"]
        assert p._verifier_errors == []
    finally:
        p.context.close()


# ─── the page as a person uses it ───────────────────────────────────────────────────────────────────

def _shown(page):
    page.wait_for_function("() => { const r = document.getElementById('result');"
                           " return !r.hidden && r.dataset.verdict && r.dataset.verdict !== 'WORKING'; }")
    return page.eval_on_selector("#result", "r => r.dataset.verdict"), page.inner_text("#reason")


@GROUP
def test_paste_drop_and_choose_file_and_nothing_goes_out(browser, site, documents):
    """Through the controls, not the test hook. After load, the page makes no request at all."""
    p = _open(browser, site)
    try:
        loaded = [u for u in p._verifier_requests if not u.startswith("data:")]
        assert loaded == [site], loaded

        text = json.dumps(documents["cert-unicode"], ensure_ascii=False, indent=2)
        p.fill("#input", text)
        p.click("#verify")
        verdict, reason = _shown(p)
        assert verdict == "VALID" and "erasure(s) attested" in reason
        assert p.inner_text("#verdict") == "VALID"

        p.fill("#input", _required_edits("cert-unicode", text)["memory_id"])
        p.click("#verify")
        verdict, reason = _shown(p)
        assert verdict == "INVALID" and p.inner_text("#verdict") == "INVALID"
        assert reason.strip()

        bundle = json.dumps(documents["bundle-grants"], ensure_ascii=False, indent=2).encode("utf-8")
        p.set_input_files("#file", {"name": "bundle.json", "mimeType": "application/json", "buffer": bundle})
        assert _shown(p)[0] == "VALID"

        # A real drag and drop carries a File in a DataTransfer.
        p.evaluate("""bytes => {
            const dt = new DataTransfer();
            dt.items.add(new File([new Uint8Array(bytes)], "cert.json", {type: "application/json"}));
            document.getElementById("drop").dispatchEvent(new DragEvent("drop", {dataTransfer: dt, bubbles: true, cancelable: true}));
        }""", list(json.dumps(documents["cert-floats"], indent=2).encode("utf-8")))
        p.wait_for_function("() => document.getElementById('input').value.includes('R-float')")
        assert _shown(p)[0] == "VALID"

        bad_utf8 = json.dumps(documents["cert-ascii"]).encode("utf-8").replace(b"DSAR-17", b"DSAR-\xff7", 1)
        p.set_input_files("#file", {"name": "c.json", "mimeType": "application/json", "buffer": bad_utf8})
        verdict, reason = _shown(p)
        assert verdict == "CANNOT" and "use the CLI" in reason
        assert p.inner_text("#verdict") == "CANNOT VERIFY HERE"

        with_bom = b"\xef\xbb\xbf" + json.dumps(documents["cert-ascii"]).encode("utf-8")
        p.set_input_files("#file", {"name": "c.json", "mimeType": "application/json", "buffer": with_bom})
        assert _shown(p)[0] == "CANNOT"

        after = [u for u in p._verifier_requests if not u.startswith("data:")]
        assert after == [site], f"the page made a request after load: {after[1:]}"
        assert p._verifier_errors == []
    finally:
        p.context.close()
