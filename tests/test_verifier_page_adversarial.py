# -*- coding: utf-8 -*-
"""Adversarial review of docs/verify/index.html at 6117486: where can the page say VALID for a file the
CLI fails, or INVALID for one it passes?

The report is audits/2026-09-24/verifier-page-review.md. Every row in its findings table has a test
here, named in the table. Two kinds of test:

  * AGREEMENT GUARDS (plain tests). The cases from the review brief: reordered keys, NFC vs NFD,
    float edge cases, duplicate keys, extra fields, wrong key types, truncated signatures, a
    signature from another key, empty and huge input, and the network policy. The page and the
    Python verifier agree on every one of them today, and these tests keep it that way.

  * FINDINGS (xfail, strict, AssertionError only). Each asserts what the page SHOULD do and fails
    today because it does not. When a fix lands the test passes, strict xfail turns that into a
    failure, and the marker comes off. Preconditions use pytest.fail, not assert, so a broken
    precondition fails the run instead of hiding inside the expected failure.

The oracle is the Python verifier, exactly as in tests/test_verifier_page.py, whose helpers and
fixtures this module reuses. Browser tests need Playwright and a Chromium; set
VERIFIER_PAGE_REQUIRED=1 to make a skip a failure.
"""
from __future__ import annotations

import copy
import http.server
import json
import math
import os
import subprocess
import sys
import threading
import unicodedata

import pytest

pytest.importorskip("cryptography")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed448 import Ed448PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey  # noqa: E402

import test_verifier_page as base  # noqa: E402
from test_verifier_page import browser, documents, page, site  # noqa: E402,F401  (fixtures)
from inspeximus import Inspeximus  # noqa: E402
from inspeximus.audit_bundle import _bundle_hash, verify_bundle  # noqa: E402
from inspeximus.core import (_GENESIS, _canon, _sha256_hex, sth_hash_of,  # noqa: E402
                             verify_erasure_certificate)
from inspeximus.merkle import root as merkle_root  # noqa: E402

GROUP = base.GROUP
python_verdict = base.python_verdict
verify_many = base.verify_many
EXPECT_PAGE = base.EXPECT_PAGE


def finding(fid: str, why: str):
    return pytest.mark.xfail(strict=True, raises=AssertionError, reason=f"{fid}: {why}")


def _require(cond, msg):
    if not cond:
        pytest.fail("precondition: " + msg)


def dumps(doc) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=2)


def _agree(page, cases: dict, expect: dict | None = None):
    """Every case gets the Python verdict on the page; `expect` pins the Python verdict too, so a
    case that stops testing what its name says fails instead of passing vacuously."""
    got = dict(zip(cases, verify_many(page, list(cases.values()))))
    wrong = []
    for name, text in cases.items():
        pv = python_verdict(text)
        if expect and name in expect and pv != expect[name]:
            wrong.append((name, "python", pv, "expected", expect[name]))
        if got[name]["verdict"] != EXPECT_PAGE[pv]:
            wrong.append((name, "python", pv, "page", got[name]["verdict"], got[name]["reason"][:120]))
    assert not wrong, wrong


def _pub(k) -> str:
    return k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def _reanchor(cert):
    """Recompute everything in a certificate's anchor that needs no key: tip, count, Merkle root, sth."""
    toms = cert["tombstones"]
    a = cert["anchor"]
    a["n_tombstones"] = len(toms)
    a["tombstones_tip"] = toms[-1]["hash"] if toms else _GENESIS
    a["tombstones_root"] = merkle_root([_canon(Inspeximus._chain_core(t, "tombstone")) for t in toms]).hex()
    a["sth_hash"] = sth_hash_of(a)
    return cert


def _resign(cert, key):
    prev = _GENESIS
    for t in cert["tombstones"]:
        t["prev"] = prev
        t["hash"] = _sha256_hex(_canon(Inspeximus._tombstone_core(t)))
        t["pubkey"] = _pub(key)
        t["sig"] = key.sign(bytes.fromhex(t["hash"])).hex()
        prev = t["hash"]
    cert["pubkey"] = _pub(key)
    return _reanchor(cert)


# ═══ AGREEMENT GUARDS: the cases in the brief ══════════════════════════════════════════════════════

@GROUP
def test_reordered_keys_at_every_level(page, documents):
    def reverse(o):
        if isinstance(o, dict):
            return {k: reverse(o[k]) for k in reversed(list(o))}
        if isinstance(o, list):
            return [reverse(x) for x in o]
        return o
    cases = {f"{n} reversed": dumps(reverse(documents[n]))
             for n in ("cert-ascii", "cert-unicode", "cert-nested-lists", "bundle-unicode", "bundle-amended")}
    _agree(page, cases, {k: "VALID" for k in cases})


@GROUP
def test_duplicate_keys_last_one_wins_in_both(page, documents):
    """json.loads keeps the LAST value of a repeated key, and so does the page's Map. Both orders,
    inside a hashed tombstone, a signature, a hashed nested block and the bundle hash."""
    cert = documents["cert-ascii"]
    t0 = cert["tombstones"][0]
    text = dumps(cert)
    bad_hash, bad_sig = "1" * 64, "1" * 128

    def dup(field, real, fake, fake_last):
        old = f'"{field}": "{real}"'
        _require(old in text, field)
        pair = f'{old},\n      "{field}": "{fake}"' if fake_last else f'"{field}": "{fake}",\n      {old}'
        return text.replace(old, pair, 1)

    nested = base._synthetic_certificate(
        [{"memory_id": "d", "ts": 1.0, "request_id": "R", "auth": {"basis": "real", "authorized_by": None,
                                                                    "authorization": None}}])
    ntext = dumps(nested)
    bundle = documents["bundle-unicode"]
    btext = dumps(bundle)
    real_bh = bundle["bundle_hash"]
    cases = {
        "tombstone hash: fake first": dup("hash", t0["hash"], bad_hash, False),
        "tombstone hash: fake last": dup("hash", t0["hash"], bad_hash, True),
        "signature: fake first": dup("sig", t0["sig"], bad_sig, False),
        "signature: fake last": dup("sig", t0["sig"], bad_sig, True),
        "auth.basis: fake first": ntext.replace('"basis": "real"', '"basis": "fake", "basis": "real"', 1),
        "auth.basis: fake last": ntext.replace('"basis": "real"', '"basis": "real", "basis": "fake"', 1),
        "bundle_hash: fake first": btext.replace(f'"bundle_hash": "{real_bh}"',
                                                 f'"bundle_hash": "{bad_hash}", "bundle_hash": "{real_bh}"', 1),
        "bundle_hash: fake last": btext.replace(f'"bundle_hash": "{real_bh}"',
                                                f'"bundle_hash": "{real_bh}", "bundle_hash": "{bad_hash}"', 1),
        "whole tombstones array: empty one last": text[:-2] + ',\n  "tombstones": []\n}',
    }
    expect = {k: ("INVALID" if "fake last" in k or "empty one last" in k else "VALID") for k in cases}
    _agree(page, cases, expect)


@GROUP
def test_unicode_normalization_is_not_applied_by_either_side(page):
    """Neither side normalizes. An NFD certificate verifies as issued; re-normalizing its text (an
    editor that saves NFC) breaks the hash in both, and the other way round."""
    key = Ed25519PrivateKey.generate()
    word, rid = "Žofia-é-Å-ﬁ", "DSAR-Ž-가"
    nfd = base._synthetic_certificate([{"memory_id": unicodedata.normalize("NFD", word), "ts": 1.0,
                                        "request_id": unicodedata.normalize("NFD", rid)}], key=key)
    nfc = base._synthetic_certificate([{"memory_id": unicodedata.normalize("NFC", word), "ts": 1.0,
                                        "request_id": unicodedata.normalize("NFC", rid)}], key=key)
    _require(dumps(nfd) != dumps(nfc), "NFC and NFD spell the text differently")
    cases = {
        "NFD as issued": dumps(nfd), "NFD as issued, escaped": json.dumps(nfd),
        "NFC as issued": dumps(nfc), "NFC as issued, escaped": json.dumps(nfc),
        "NFD text saved as NFC": unicodedata.normalize("NFC", dumps(nfd)),
        "NFC text saved as NFD": unicodedata.normalize("NFD", dumps(nfc)),
        "NFKC (ligature folded)": unicodedata.normalize("NFKC", dumps(nfc)),
    }
    expect = {k: ("INVALID" if "saved" in k or "NFKC" in k else "VALID") for k in cases}
    _agree(page, cases, expect)


FLOATS = [1.0, 1, -0.0, 1e16, 1e21, 1e22, 9999999999999998.0, 0.0001, 1e-05, 1e-07, 5e-324,
          1.7976931348623157e308, 9007199254740993, 2**64, math.inf, -math.inf, math.nan, 1e15,
          123456789012345680.0]


@GROUP
def test_float_edge_cases_hash_the_way_python_writes_them(page):
    """Each `ts` below is inside a hashed tombstone. Respelling it as the same Python value keeps
    the certificate VALID; respelling it as a different one (1.0 vs 1, -0.0 vs 0, 1e16 vs the int,
    2**53+1 vs the double JavaScript would round it to) makes it INVALID, in both."""
    cert = base._synthetic_certificate([{"memory_id": f"f{i}", "ts": v, "request_id": "R"}
                                        for i, v in enumerate(FLOATS)])
    text = dumps(cert)

    def ts(i, new):
        k = f'"memory_id": "f{i}",\n      "ts": '
        a = text.index(k) + len(k)
        return text[:a] + new + text[text.index(",", a):]

    same = {"1.0 as 1.00": ts(0, "1.00"), "1.0 as 1e0": ts(0, "1e0"), "1.0 as 10E-1": ts(0, "10E-1"),
            "-0.0 as -0e5": ts(2, "-0e5"), "1e16 as 10000000000000000.0": ts(3, "10000000000000000.0"),
            "1e16 as 1E+16": ts(3, "1E+16"), "1e16 as 10000000000000001.0 (same double)": ts(3, "10000000000000001.0"),
            "inf as 1e400": ts(14, "1e400"), "-inf as -1e400": ts(15, "-1e400"),
            "5e-324 as 4e-324 (same double)": ts(10, "4e-324"), "as issued (NaN included)": text}
    different = {"1.0 as 1": ts(0, "1"), "1 as 1.0": ts(1, "1.0"), "-0.0 as -0 (int 0)": ts(2, "-0"),
                 "-0.0 as 0.0": ts(2, "0.0"), "1e16 as the int": ts(3, "10000000000000000"),
                 "2**53+1 as 2**53": ts(8, "9007199254740992"), "2**53+1 as a float": ts(8, "9007199254740993.0"),
                 "2**64 as 2**64 float": ts(13, "18446744073709551616.0"), "1e21 as the int": ts(4, "1" + "0" * 21)}
    cases = {**same, **different}
    _agree(page, cases, {**{k: "VALID" for k in same}, **{k: "INVALID" for k in different}})


@GROUP
def test_extra_fields_are_judged_by_whether_they_are_hashed(page, documents):
    cert = documents["cert-ascii"]

    def edit(fn, doc=cert):
        d = copy.deepcopy(doc)
        fn(d)
        return dumps(d)

    bundle = documents["bundle-unicode"]
    resealed = copy.deepcopy(bundle)
    resealed["x_extra"] = {"é": [1.0, None]}
    resealed["bundle_hash"] = _bundle_hash(resealed)
    cases = {
        "cert: extra top-level field": edit(lambda d: d.update(x_extra={"a": [1.0, "é"]})),
        "cert: extra field inside a tombstone": edit(lambda d: d["tombstones"][0].update(x_note="reverted")),
        "cert: extra field inside the anchor": edit(lambda d: d["anchor"].update(x_extra=1)),
        "cert: auth block added to a tombstone": edit(lambda d: d["tombstones"][0].update(auth={"basis": "x"})),
        "cert: falsy auth added (not hashed)": edit(lambda d: d["tombstones"][0].update(auth=0),
                                                    documents["cert-floats"]),
        "bundle: extra field, not resealed": edit(lambda d: d.update(x_extra=1), bundle),
        "bundle: extra field, resealed": dumps(resealed),
        "bundle: extra field in a write receipt, resealed": _resealed(bundle, lambda b: b["write_chain"][0].update(x=1)),
    }
    expect = {k: "VALID" for k in cases}
    expect["cert: auth block added to a tombstone"] = "INVALID"
    expect["bundle: extra field, not resealed"] = "INVALID"
    _agree(page, cases, expect)


def _resealed(bundle, change):
    b = copy.deepcopy(bundle)
    change(b)
    b["bundle_hash"] = _bundle_hash(b)
    return dumps(b)


@GROUP
def test_wrong_key_types(page, documents):
    cert = documents["cert-ascii"]
    pk = cert["pubkey"]

    def key(v, only_tombstone=False):
        d = copy.deepcopy(cert)
        if not only_tombstone:
            d["pubkey"] = v
        for t in d["tombstones"]:
            t["pubkey"] = v
        return dumps(d)

    raw = serialization.Encoding.Raw, serialization.PublicFormat.Raw
    cases = {
        "int": key(5), "list": key([pk]), "object": key({"hex": pk}), "null": key(None), "empty": key(""),
        "true": key(True), "0x prefix": key("0x" + pk), "33 bytes": key(pk + "00"), "31 bytes": key(pk[:62]),
        "odd length": key(pk[:63]),
        "Ed448 (57 bytes)": key(Ed448PrivateKey.generate().public_key().public_bytes(*raw).hex()),
        "X25519 (32 bytes, other curve form)": key(X25519PrivateKey.generate().public_key().public_bytes(*raw).hex()),
        "secp256k1 compressed": key(ec.generate_private_key(ec.SECP256K1()).public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.CompressedPoint).hex()),
        "fullwidth digits": key(pk.replace("0", "０")),
        "\\x1c between pairs (not fromhex whitespace)": key("\x1c".join(pk[i:i + 2] for i in range(0, 64, 2))),
        "tombstone key int, certificate key right": key(7, only_tombstone=True),
        # Spellings bytes.fromhex accepts: the same key, so the same verdict.
        "uppercase": key(pk.upper()),
        "spaces between pairs": key(" ".join(pk[i:i + 2] for i in range(0, 64, 2))),
        "\\x0b between pairs": key("\x0b".join(pk[i:i + 2] for i in range(0, 64, 2))),
    }
    valid = {"uppercase", "spaces between pairs", "\\x0b between pairs"}
    _agree(page, cases, {k: "VALID" if k in valid else "INVALID" for k in cases})


@GROUP
def test_truncated_and_malformed_signatures(page, documents):
    cert = documents["cert-ascii"]

    def sig(fn, i=0):
        d = copy.deepcopy(cert)
        d["tombstones"][i]["sig"] = fn(d["tombstones"][i]["sig"])
        return dumps(d)

    s_plus_l = lambda s: s[:64] + (int.from_bytes(bytes.fromhex(s[64:]), "little") + base._L).to_bytes(32, "little").hex()
    cases = {
        "63 bytes": sig(lambda s: s[:126]), "32 bytes": sig(lambda s: s[:64]), "1 byte": sig(lambda s: s[:2]),
        "odd hex length": sig(lambda s: s[:127]), "empty string": sig(lambda s: ""), "null": sig(lambda s: None),
        "65 bytes": sig(lambda s: s + "00"), "all zero": sig(lambda s: "0" * 128), "int": sig(lambda s: 12),
        "S + L (malleated)": sig(s_plus_l), "R bit flipped": sig(lambda s: base._flip_hex(s, 3)),
        "S bit flipped": sig(lambda s: base._flip_hex(s, 100)), "last tombstone truncated": sig(lambda s: s[:120], i=-1),
        "uppercase (same bytes)": sig(lambda s: s.upper()),
        "spaces between pairs (same bytes)": sig(lambda s: " ".join(s[i:i + 2] for i in range(0, 128, 2))),
    }
    _agree(page, cases, {k: "VALID" if "same bytes" in k else "INVALID" for k in cases})


@GROUP
def test_a_signature_from_another_key(page, documents):
    cert = documents["cert-ascii"]
    other = Ed25519PrivateKey.generate()

    def by_other(tombstone_key, cert_key):
        d = copy.deepcopy(cert)
        for t in d["tombstones"]:
            t["sig"] = other.sign(bytes.fromhex(t["hash"])).hex()
            if tombstone_key:
                t["pubkey"] = _pub(other)
        if cert_key:
            d["pubkey"] = _pub(other)
        return dumps(d)

    swapped = copy.deepcopy(cert)
    swapped["tombstones"][0]["sig"] = cert["tombstones"][1]["sig"]
    cases = {
        "signed by another key, keys unchanged": by_other(False, False),
        "tombstones name the other key, certificate names the real one": by_other(True, False),
        "certificate names the other key, tombstones the real one": by_other(False, True),
        "signature of tombstone 1 copied onto tombstone 0": dumps(swapped),
        # The documented limit: with no pinned key, a chain re-signed end to end by any key verifies.
        "whole chain re-signed by another key (documented limit)": by_other(True, True),
    }
    expect = {k: "INVALID" for k in cases}
    expect["whole chain re-signed by another key (documented limit)"] = "VALID"
    _agree(page, cases, expect)


@GROUP
def test_empty_and_trivial_input(page):
    cases = {repr(s): s for s in ["", " ", "\n\t", "{}", "[]", "null", "0", '""', "true", "{", '{"a":1}x',
                                   '{"tombstones": []}', '{"kind": null}', '{"bundle_hash": ""}',
                                   '{"inspeximus_erasure_certificate": "1.0"}']}
    _agree(page, cases, {k: "INVALID" for k in cases})
    # Through the controls, whitespace only shows nothing at all rather than a verdict.
    page.fill("#input", "   \n  ")
    page.click("#verify")
    page.wait_for_timeout(200)
    assert page.eval_on_selector("#result", "r => r.hidden") is True


@GROUP
def test_huge_input(page, documents):
    """Big, deep and long: the verdicts still match. (Timing, measured in-page at 6117486: 1000
    signed tombstones in about 1.5 s, a 40 MB unhashed string in under 0.1 s.)"""
    text = json.dumps(documents["cert-ascii"], indent=2)
    at = '"count": '
    cases = {
        "4300-digit int": text.replace(at, '"x": ' + "9" * 4300 + ",\n  " + at, 1),
        "-4300-digit int": text.replace(at, '"x": -' + "9" * 4300 + ",\n  " + at, 1),
        "4301-digit int": text.replace(at, '"x": ' + "9" * 4301 + ",\n  " + at, 1),
        "1000 signed tombstones": json.dumps(base._synthetic_certificate(
            [{"memory_id": f"m{i}", "ts": float(i), "request_id": "R"} for i in range(1000)])),
        "10 000-element list in a hashed auth block": json.dumps(base._synthetic_certificate(
            [{"memory_id": "w", "ts": 1.0, "request_id": "R", "auth": {"basis": list(range(10000))}}])),
    }
    expect = {k: "VALID" for k in cases}
    expect["4301-digit int"] = "CRASH"
    _agree(page, cases, expect)
    # Built inside the page: a 40 MB string is too slow to ship over the test channel, not to verify.
    got = page.evaluate("""async t => {
        const big = t.replace('"count": ', '"x_big": "' + 'é'.repeat(20000000) + '",\\n  "count": ');
        return (await window.inspeximusVerifier.verifyText(big)).verdict;
    }""", text)
    assert got == "VALID"


@GROUP
def test_odd_keys_and_unhashed_surrogates(page):
    k = Ed25519PrivateKey.generate()
    proto = base._synthetic_certificate([{"memory_id": "p", "ts": 1.0, "request_id": "R",
                                          "auth": {"__proto__": {"a": 1}, "constructor": 1, "toString": "x"}}], key=k)
    surrogate = copy.deepcopy(proto)
    surrogate["verify_with"] = "\ud800"          # not hashed, so Python never encodes it
    cases = {"__proto__ / constructor keys in a hashed block": json.dumps(proto),
             "lone surrogate in an unhashed field": json.dumps(surrogate)}
    _agree(page, cases, {k: "VALID" for k in cases})


# ═══ NETWORK AND SCRIPTS ═══════════════════════════════════════════════════════════════════════════

def test_the_page_has_no_html_sink_for_untrusted_text():
    """The no-exfiltration claim rests on the page never running injected script (see the navigation
    test below). Every string from the file reaches the DOM through textContent."""
    src = base._page_source()
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
                 "setTimeout(\"", "srcdoc", "createContextualFragment"):
        assert sink not in src, sink


@GROUP
def test_no_external_script_and_no_request_after_load(browser, site, documents):
    ctx = browser.new_context()
    p = ctx.new_page()
    cdp = ctx.new_cdp_session(p)
    cdp.send("Network.enable")
    seen, sockets = [], []
    cdp.on("Network.requestWillBeSent", lambda e: seen.append(e["request"]["url"]))
    p.on("websocket", lambda w: sockets.append(w.url))
    p.goto(site)
    p.wait_for_function("() => !!window.inspeximusVerifier")
    remote = lambda: [u for u in seen if not u.startswith("data:")]
    try:
        assert remote() == [site], seen
        scripts = p.evaluate("() => [...document.scripts].map(s => [s.type, s.src])")
        assert scripts == [["application/json", ""], ["", ""]], scripts
        refs = p.evaluate("() => [...document.querySelectorAll('[src],[href],[srcset],[action],[data],[poster]')]"
                          ".map(e => e.getAttribute('src') || e.getAttribute('href') || e.getAttribute('action'))")
        assert all(r.startswith("data:") for r in refs), refs
        p.evaluate("() => { window.__v = []; document.addEventListener('securitypolicyviolation',"
                   " e => window.__v.push(e.violatedDirective)); }")
        text = dumps(documents["cert-unicode"])
        p.fill("#input", text)
        p.click("#verify")
        assert base._shown(p)[0] == "VALID"
        p.set_input_files("#file", {"name": "b.json", "mimeType": "application/json",
                                    "buffer": dumps(documents["bundle-grants"]).encode()})
        p.wait_for_function("() => document.getElementById('input').value.includes('write_chain')")
        assert base._shown(p)[0] == "VALID"
        p.evaluate("""t => { const dt = new DataTransfer(); dt.setData('text/plain', t);
            document.getElementById('drop').dispatchEvent(new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true})); }""",
                   "https://example.com/x.json")
        assert base._shown(p)[0] == "INVALID"
        p.click("#clear")
        p.wait_for_timeout(300)
        assert remote() == [site], f"requests after load: {remote()[1:]}"
        assert sockets == []
        assert p.evaluate("() => window.__v") == [], "the page itself tried something its policy blocks"
    finally:
        ctx.close()


class _Sink(http.server.BaseHTTPRequestHandler):
    hits: list = []

    def do_GET(self):
        self.hits.append(self.path)
        self.send_response(204)
        self.end_headers()

    do_POST = do_GET

    def log_message(self, *a):
        pass


@pytest.fixture
def sink():
    _Sink.hits = []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Sink)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", _Sink.hits
    srv.shutdown()


@GROUP
def test_the_policy_stops_every_request_channel_at_a_live_server(browser, site, sink):
    """Script running in the page tries every channel CSP governs. None reaches a server that is
    listening. (Playwright reports a `request` event even for a CSP-blocked fetch, which is why this
    counts what ARRIVES.)"""
    url, hits = sink
    p = base._open(browser, site)
    try:
        p.evaluate("""async X => {
          try { await fetch(X + '/fetch'); } catch (e) {}
          try { const x = new XMLHttpRequest(); x.open('GET', X + '/xhr'); x.send(); } catch (e) {}
          try { navigator.sendBeacon(X + '/beacon', 'd'); } catch (e) {}
          try { new WebSocket(X.replace('http', 'ws') + '/ws'); } catch (e) {}
          try { new EventSource(X + '/es'); } catch (e) {}
          try { new Worker(X + '/worker'); } catch (e) {}
          const add = (tag, props, where) => { const e = document.createElement(tag); Object.assign(e, props); (where || document.body).appendChild(e); return e; };
          add('img', {src: X + '/img'});
          add('script', {src: X + '/script'});
          add('link', {rel: 'stylesheet', href: X + '/css'}, document.head);
          add('link', {rel: 'prefetch', href: X + '/prefetch'}, document.head);
          add('link', {rel: 'preload', as: 'fetch', href: X + '/preload'}, document.head);
          add('iframe', {src: X + '/iframe'});
          add('object', {data: X + '/object'});
          add('audio', {src: X + '/audio'});
          add('style', {textContent: '@import url(' + X + '/import); body{background:url(' + X + '/bg)}'}, document.head);
          const f = add('form', {action: X + '/form', method: 'post'}); try { f.submit(); } catch (e) {}
          await new Promise(r => setTimeout(r, 1500));
        }""", url)
        p.wait_for_timeout(500)
        assert hits == [], hits
    finally:
        p.context.close()


@GROUP
def test_navigation_is_outside_the_policy(browser, site, sink):
    """INFO, not a defect: CSP does not govern navigation, so script in the page COULD send data out
    by navigating. The page runs no injected script (test_the_page_has_no_html_sink_for_untrusted_text),
    so nothing does; this pins the boundary of the "enforced by the browser" sentence."""
    url, hits = sink
    p = base._open(browser, site)
    try:
        p.evaluate("X => { location.href = X + '/navigate?d=1'; }", url)
        p.wait_for_timeout(1000)
        assert hits == ["/navigate?d=1"], hits
    finally:
        p.context.close()


# ═══ FINDINGS: the page diverges from the CLI ═══════════════════════════════════════════════════════

def _bundle_with_certificate_fields(documents, tamper_bundle: bool):
    """A bundle that ALSO carries a complete erasure certificate built from its own tombstone chain.
    Every field is copied out of the bundle; no key is needed."""
    b = copy.deepcopy(documents["bundle-unicode"])
    if tamper_bundle:
        b["write_chain"][0]["commit"]["mtype"] = "semantic"     # a rewritten write receipt
        b["bundle_hash"] = _bundle_hash(b)
    toms = b["tombstone_chain"]
    ids = sorted({t["memory_id"] for t in toms})
    b.update({"inspeximus_erasure_certificate": "1.0", "tombstones": toms,
              "pubkey": next(r["pubkey"] for r in b["write_chain"] if r.get("pubkey")), "scoped_to": None,
              "request_ids": sorted({t["request_id"] for t in toms if t["request_id"] is not None}),
              "erased_memory_ids": ids, "count": len(ids)})
    return b


def _cli(tmp_path, cmd, doc):
    f = tmp_path / "doc.json"
    f.write_text(json.dumps(doc), encoding="utf-8")          # escaped: a lone surrogate must survive
    r = subprocess.run([sys.executable, "-m", "inspeximus.cli", cmd, str(f)], capture_output=True, text=True,
                       cwd=str(tmp_path), timeout=120)
    return "PASS" if "VERDICT: PASS" in r.stdout else "FAIL" if "VERDICT: FAIL" in r.stdout else "CRASH"


@GROUP
def test_F1_a_tampered_bundle_with_certificate_fields_is_not_valid(page, documents, tmp_path):
    doc = _bundle_with_certificate_fields(documents, tamper_bundle=True)
    _require(_cli(tmp_path, "audit-verify", doc) == "FAIL", "audit-verify fails the tampered bundle")
    _require(_cli(tmp_path, "erasure-verify", doc) == "PASS", "erasure-verify passes its certificate half")
    _require(not verify_bundle(doc)["ok"] and verify_erasure_certificate(doc)["valid"], "function verdicts")
    got = verify_many(page, [dumps(doc)])[0]
    assert got["verdict"] != "VALID", ("audit-verify says FAIL, the page says", got)


@GROUP
def test_F1_a_failing_certificate_with_bundle_fields_is_not_valid(page, documents, tmp_path):
    cert = copy.deepcopy(documents["cert-ascii"])
    del cert["inspeximus_erasure_certificate"]                   # erasure-verify never reads the marker
    cert["erased_memory_ids"] = cert["erased_memory_ids"] + ["never-erased"]   # a forged summary
    cert["count"] += 1
    bundle = copy.deepcopy(documents["bundle-unsigned"])
    doc = {**cert, **{k: v for k, v in bundle.items() if k not in cert}, "anchor": bundle["anchor"]}
    doc["bundle_hash"] = _bundle_hash(doc)
    _require(_cli(tmp_path, "erasure-verify", doc) == "FAIL", "erasure-verify fails the forged summary")
    _require(_cli(tmp_path, "audit-verify", doc) == "PASS", "audit-verify passes its bundle half")
    got = verify_many(page, [dumps(doc)])[0]
    assert got["verdict"] != "VALID", ("erasure-verify says FAIL, the page says", got)


@GROUP
def test_F2_replacing_the_text_clears_a_valid_verdict(browser, site, documents):
    p = base._open(browser, site)
    try:
        good = dumps(documents["cert-ascii"])
        bad = base._required_edits("cert-ascii", good)["memory_id"]
        _require(python_verdict(bad) == "INVALID", "the edit breaks the certificate")
        p.fill("#input", good)
        p.click("#verify")
        _require(base._shown(p)[0] == "VALID", "the original verifies")
        p.fill("#input", bad)                  # what a paste does; nobody clicked Verify yet
        p.wait_for_timeout(300)
        hidden, verdict = p.eval_on_selector("#result", "r => [r.hidden, r.dataset.verdict]")
        assert hidden or verdict != "VALID", "VALID is still on screen beside a tampered certificate"
    finally:
        p.context.close()


# A large file, in effect: every signature takes 700 ms, and the page counts them so a test can wait
# for the run to FINISH instead of guessing how long it takes (signatures are checked one by one).
SLOW_VERIFY = """(() => { const v = crypto.subtle.verify.bind(crypto.subtle);
  window.__calls = 0; window.__pending = 0;
  crypto.subtle.verify = (...a) => { window.__calls++; window.__pending++;
    return new Promise(r => setTimeout(() => { window.__pending--; r(v(...a)); }, 700)); }; })();"""


def _wait_run_finished(p, n_sigs):
    p.wait_for_function(f"() => window.__calls >= {n_sigs} && window.__pending === 0", timeout=30000)
    p.wait_for_timeout(300)


@GROUP
def test_F3_an_older_run_cannot_overwrite_the_verdict_for_a_newer_file(browser, site, documents):
    p = base._open(browser, site, init_script=SLOW_VERIFY)
    cert = documents["cert-ascii"]
    try:
        p.fill("#input", dumps(cert))
        p.click("#verify")
        p.wait_for_timeout(200)
        p.set_input_files("#file", {"name": "next.json", "mimeType": "application/json", "buffer": b'{"x": "\xff"}'})
        p.wait_for_timeout(300)
        _require(p.eval_on_selector("#result", "r => r.dataset.verdict") == "CANNOT", "the new file is refused")
        _require(p.evaluate("() => window.__pending") > 0, "the first run is still in flight")
        _wait_run_finished(p, len(cert["tombstones"]))
        verdict = p.eval_on_selector("#result", "r => r.dataset.verdict")
        assert verdict == "CANNOT", f"the page now shows {verdict} for the file just chosen"
    finally:
        p.context.close()


@GROUP
def test_F3_clear_during_a_run_stays_clear(browser, site, documents):
    p = base._open(browser, site, init_script=SLOW_VERIFY)
    cert = documents["cert-ascii"]
    try:
        p.fill("#input", dumps(cert))
        p.click("#verify")
        p.wait_for_timeout(200)
        p.click("#clear")
        _require(p.eval_on_selector("#result", "r => r.hidden") and p.evaluate("() => window.__pending") > 0,
                 "cleared while the run is in flight")
        _wait_run_finished(p, len(cert["tombstones"]))
        hidden, verdict = p.eval_on_selector("#result", "r => [r.hidden, r.dataset.verdict]")
        assert hidden, f"{verdict} is shown over an empty input"
    finally:
        p.context.close()


def _strict_type_cases(docs):
    """Documents where Python RETURNS a verdict and the page says CANNOT VERIFY HERE, with a reason
    that says the Python verifier stops with an error."""
    key = Ed25519PrivateKey.generate()
    base_cert = base._synthetic_certificate([{"memory_id": "m", "ts": 1.0, "request_id": "R"}], key=key)
    out = {}

    def c(name, fn, resign=False):
        d = copy.deepcopy(base_cert)
        fn(d)
        if resign:
            _resign(d, key)
        out[name] = dumps(d)

    c("int memory_id", lambda d: (d["tombstones"][0].update(memory_id=7), d.update(erased_memory_ids=[7])), True)
    c("int request_id", lambda d: (d["tombstones"][0].update(request_id=5), d.update(scoped_to=5, request_ids=[5])), True)
    c("scope_excludes as an object with the right keys", lambda d: d.update(scope_excludes=dict.fromkeys(d["scope_excludes"])))
    c("scope_excludes as an object with other keys", lambda d: d.update(scope_excludes={"nothing": 1}))
    c("erased_memory_ids as the string 'm'", lambda d: d.update(erased_memory_ids="m"))
    c("erased_memory_ids as an object", lambda d: d.update(erased_memory_ids={"m": 1}))
    c("erased_memory_ids [1]", lambda d: d.update(erased_memory_ids=[1]))
    c("no scoped_to, request_ids the string 'R'", lambda d: (d.pop("scoped_to"), d.update(request_ids="R")))
    c("no scoped_to, request_ids [NaN] and a NaN request_id (one shared NaN object in Python)",
      lambda d: (d.pop("scoped_to"), d["tombstones"][0].update(request_id=math.nan), d.update(request_ids=[math.nan])), True)
    b = copy.deepcopy(docs["bundle-unicode"])
    for v in b["governance"]["by_request"].values():
        v["erased"] = float(v.get("erased", 0))
    b["bundle_hash"] = _bundle_hash(b)
    out["bundle: by_request erased counts as floats"] = dumps(b)
    out["bundle: a grant id that is an int"] = _resealed(docs["bundle-grants"], lambda b: b["grants"][0].update(id=12))
    return out


STRICT_TYPE_EXPECT = {
    "int memory_id": "VALID", "int request_id": "VALID",
    "scope_excludes as an object with the right keys": "VALID",
    "scope_excludes as an object with other keys": "INVALID",
    "erased_memory_ids as the string 'm'": "VALID", "erased_memory_ids as an object": "VALID",
    "erased_memory_ids [1]": "INVALID", "no scoped_to, request_ids the string 'R'": "VALID",
    "no scoped_to, request_ids [NaN] and a NaN request_id (one shared NaN object in Python)": "VALID",
    "bundle: by_request erased counts as floats": "VALID", "bundle: a grant id that is an int": "INVALID",
}


@GROUP
@pytest.mark.parametrize("name", list(STRICT_TYPE_EXPECT))
def test_F4_python_verdicts_are_not_reported_as_python_errors(page, documents, name):
    text = _strict_type_cases(documents)[name]
    _require(python_verdict(text) == STRICT_TYPE_EXPECT[name], f"python says {python_verdict(text)}")
    got = verify_many(page, [text])[0]
    assert got["verdict"] == STRICT_TYPE_EXPECT[name], got


@GROUP
@pytest.mark.parametrize("depth", [201, 500, 900])
def test_F5_a_deep_unhashed_field_does_not_block_a_verdict(page, documents, depth):
    cert = copy.deepcopy(documents["cert-ascii"])
    cert["x_deep"] = json.loads("[" * depth + "]" * depth)
    text = json.dumps(cert)
    _require(python_verdict(text) == "VALID", "python parses and passes it")
    assert verify_many(page, [text])[0]["verdict"] == "VALID"


@GROUP
def test_F6_a_degenerate_signature_under_the_wrong_key_is_invalid(page):
    cert = base._synthetic_certificate([{"memory_id": "e0", "ts": 1.0, "request_id": "R"}], sign=False)
    t = cert["tombstones"][0]
    t["pubkey"], t["sig"] = base._small_order_signature(bytes.fromhex(t["hash"]))
    cert["pubkey"] = "11" * 32
    text = dumps(cert)
    _require(python_verdict(text) == "INVALID", "OpenSSL accepts the signature, then Python sees the key differ")
    got = verify_many(page, [text])[0]
    assert got["verdict"] == "INVALID", got


@GROUP
def test_F8_a_nan_inside_a_list_scope_is_cannot_verify(page):
    key = Ed25519PrivateKey.generate()
    cert = base._synthetic_certificate([{"memory_id": "m", "ts": 1.0, "request_id": "R"}], key=key)
    cert["tombstones"][0]["request_id"] = [math.nan]
    cert["scoped_to"] = [math.nan]
    cert["request_ids"] = []
    text = dumps(_resign(cert, key))
    _require(python_verdict(text) == "CRASH", "Python puts the tombstone in scope, then cannot hash its list id")
    got = verify_many(page, [text])[0]
    assert got["verdict"] == "CANNOT", got


# ═══ SHARED WITH THE CLI: the page matches Python, and Python is where the gap is ═══════════════════

@GROUP
@finding("F7", "a certificate's PARTIALLY SIGNED is a note, where a bundle's is a failure")
def test_F7_an_unsigned_tombstone_appended_without_the_key_is_not_valid(page, documents):
    cert = copy.deepcopy(documents["cert-unscoped"])
    _require(all(t.get("sig") for t in cert["tombstones"]), "a fully signed certificate")
    prev = cert["tombstones"][-1]["hash"]
    t = {"seq": len(cert["tombstones"]), "memory_id": "never-erased", "ts": 1.0, "request_id": None, "prev": prev}
    t["hash"] = _sha256_hex(_canon(Inspeximus._tombstone_core(t)))
    cert["tombstones"].append(t)
    _reanchor(cert)
    cert["erased_memory_ids"] = sorted({x["memory_id"] for x in cert["tombstones"]})
    cert["count"] = len(cert["erased_memory_ids"])
    text = dumps(cert)
    got = verify_many(page, [text])[0]
    assert (python_verdict(text), got["verdict"]) == ("INVALID", "INVALID"), (python_verdict(text), got)


@GROUP
def test_F7_a_tail_trimmed_and_reanchored_without_the_key_is_valid_in_both(page, documents):
    """Documents the wording finding: the page says a rewrite needs "whoever holds the signing key".
    Dropping the last tombstone and recomputing the unsigned anchor needs no key at all."""
    cert = copy.deepcopy(documents["cert-unscoped"])
    cert["tombstones"] = cert["tombstones"][:-1]
    _reanchor(cert)
    cert["erased_memory_ids"] = sorted({t["memory_id"] for t in cert["tombstones"]})
    cert["count"] = len(cert["erased_memory_ids"])
    cert["request_ids"] = sorted({t["request_id"] for t in cert["tombstones"] if t["request_id"] is not None})
    _agree(page, {"trimmed": dumps(cert)}, {"trimmed": "VALID"})


def test_I1_the_cli_prints_a_traceback_where_the_function_returns_invalid(documents, tmp_path):
    """INFO. The page's oracle is verify_erasure_certificate(); the CLI then prints the problems, and
    a problem that quotes a lone surrogate kills the print. The page says INVALID; the CLI prints a
    traceback and no VERDICT line (exit 1 either way)."""
    cert = copy.deepcopy(documents["cert-ascii"])
    cert["count"] = "\ud800"
    assert python_verdict(json.dumps(cert)) == "INVALID"
    assert _cli(tmp_path, "erasure-verify", cert) == "CRASH"


def test_I2_without_cryptography_the_cli_fails_every_signed_certificate(documents, monkeypatch):
    """INFO. The page models the CLI WITH `cryptography` installed. On a base install the CLI reports
    "cannot verify signatures" and FAILS a certificate the page calls VALID."""
    import inspeximus.core as core
    monkeypatch.setattr(core, "_HAVE_ED", False)
    res = verify_erasure_certificate(copy.deepcopy(documents["cert-ascii"]))
    assert res["valid"] is False and any("cryptography not installed" in p for p in res["problems"])
