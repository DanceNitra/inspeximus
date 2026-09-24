# -*- coding: utf-8 -*-
"""The "Try it" examples on the browser verifier: docs/verify/index.html and docs/verify/examples/.

The page offers four files, a valid erasure certificate and a valid audit bundle, each with a copy that
has one byte changed, and it tells the reader which verdict each one gets. So the page, the files and
the verdicts must not drift apart:

  * the copy inside the page is the file, byte for byte. The page cannot fetch the file (its policy
    forbids every request), so the copy is what a reader actually verifies;
  * each changed file differs from its valid one in exactly one byte;
  * the Python verifiers, which the CLI runs, say VALID for the valid files and INVALID for the changed
    ones, and every signature is by the throwaway key make_examples.py derives;
  * in Chromium, clicking each "Try it" link loads the file and shows the verdict the page's table
    promises, and the page makes no request after load;
  * make_examples.py writes the same bytes on every run. `make_examples.py --check` compares a fresh
    run with the committed files; it is not run here, because the bundle records the library version
    and would fail on every release until someone regenerated it.

Browser tests reuse the fixtures of tests/test_verifier_page.py: Playwright and a Chromium, skipped
without them unless VERIFIER_PAGE_REQUIRED=1.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

pytest.importorskip("cryptography")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

import test_verifier_page as base  # noqa: E402
from test_verifier_page import browser, site  # noqa: E402,F401  (fixtures)

GROUP = base.GROUP
EXAMPLES_DIR = os.path.join(base.PAGE_DIR, "examples")
SCRIPT = os.path.join(EXAMPLES_DIR, "make_examples.py")

# The verdict each example must get, in the order the page lists them.
EXPECT = {
    "certificate-valid": "VALID",
    "certificate-one-byte-changed": "INVALID",
    "bundle-valid": "VALID",
    "bundle-one-byte-changed": "INVALID",
}
# The first problem the page reports for a changed file, which its table names.
FIRST_PROBLEM = {
    "certificate-one-byte-changed": "tombstone 0: hash mismatch (tampered)",
    "bundle-one-byte-changed": "bundle_hash MISMATCH -- the bundle was modified after export",
}
PAIRS = [("certificate-valid", "certificate-one-byte-changed"), ("bundle-valid", "bundle-one-byte-changed")]


def _file(name: str) -> str:
    with open(os.path.join(EXAMPLES_DIR, name + ".json"), encoding="utf-8", newline="") as fh:
        return fh.read()


def _generator():
    spec = importlib.util.spec_from_file_location("make_examples", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                 # defines things only; make() is what touches the library
    return mod


# ─── tests that need no browser ─────────────────────────────────────────────────────────────────────

def test_the_page_carries_each_example_byte_for_byte():
    blocks = re.findall(r'<script type="application/json" id="example-([\w-]+)" data-file="([\w.-]+)">(.*?)</script>',
                        base._page_source(), re.S)
    assert [b[0] for b in blocks] == list(EXPECT)
    assert sorted(f for f in os.listdir(EXAMPLES_DIR) if f.endswith(".json")) == sorted(n + ".json" for n in EXPECT)
    for name, data_file, text in blocks:
        assert data_file == name + ".json"
        assert text == _file(name), f"the page's copy of {data_file} is not the file; run {SCRIPT}"


def test_each_changed_example_is_one_byte_away_from_its_valid_one():
    for good, changed in PAIRS:
        a, b = _file(good).encode("utf-8"), _file(changed).encode("utf-8")
        assert len(a) == len(b)
        assert sum(x != y for x, y in zip(a, b)) == 1, changed


def test_the_python_verifiers_give_each_example_its_label():
    for name, want in EXPECT.items():
        assert base.python_verdict(_file(name)) == want, name


def test_every_signature_in_the_examples_is_by_the_throwaway_key():
    """The examples are signed with a key anyone can derive from make_examples.py, and nothing else."""
    sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(_generator().THROWAWAY_KEY))
    pub = sk.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    for good, _ in PAIRS:
        doc = json.loads(_file(good))
        entries = doc["tombstones"] if "tombstones" in doc else doc["write_chain"]
        assert entries and all(e["sig"] for e in entries), good
        keys = {e["pubkey"] for e in entries} | ({doc["pubkey"]} if "pubkey" in doc else set())
        assert keys == {pub}, good


def test_make_examples_writes_the_same_bytes_on_every_run(tmp_path):
    """Two runs, under different hash seeds, into two scratch copies of the page."""
    outs = []
    for seed in ("0", "4242"):
        out = tmp_path / f"run-{seed}"
        out.mkdir()
        page = out / "index.html"
        shutil.copy(base.PAGE, page)
        env = {k: v for k, v in os.environ.items() if k != "PYTHONHASHSEED"} | {"PYTHONHASHSEED": seed}
        r = subprocess.run([sys.executable, SCRIPT, "--out", str(out), "--page", str(page)],
                           capture_output=True, text=True, env=env, timeout=300)
        assert r.returncode == 0, r.stdout[-400:] + r.stderr[-1200:]
        outs.append({p.name: p.read_bytes() for p in sorted(out.iterdir())})
    assert sorted(outs[0]) == sorted([n + ".json" for n in EXPECT] + ["index.html"])
    assert outs[0] == outs[1]


# ─── the browser ────────────────────────────────────────────────────────────────────────────────────

@GROUP
def test_each_try_it_link_gets_the_verdict_the_page_promises(browser, site):
    p = base._open(browser, site)
    try:
        rows = p.eval_on_selector_all("#examples tbody tr", """trs => trs.map(tr => {
            const b = tr.querySelector("button[data-example]");
            return {name: b.dataset.example, label: b.textContent, promised: tr.querySelector("td.expect").textContent.trim()};
        })""")
        assert [r["name"] for r in rows] == list(EXPECT)
        for r in rows:
            name = r["name"]
            assert r["label"] == name + ".json"
            assert r["promised"] == EXPECT[name], (name, r["promised"])
            assert p.evaluate("n => document.getElementById('example-' + n).textContent", name) == _file(name)

            p.evaluate("() => { delete document.getElementById('result').dataset.verdict; }")
            p.click(f'button[data-example="{name}"]')
            verdict, reason = base._shown(p)
            assert verdict == EXPECT[name], (name, verdict, reason)
            assert p.inner_text("#verdict") == EXPECT[name]
            assert p.input_value("#input") == _file(name), "the box does not hold the file that was verified"
            if name in FIRST_PROBLEM:
                assert reason == FIRST_PROBLEM[name], (name, reason)
            else:
                assert reason.startswith("The certificate verifies" if name.startswith("certificate")
                                         else "The audit bundle verifies"), (name, reason)

        after = [u for u in p._verifier_requests if not u.startswith("data:")]
        assert after == [site], f"the page made a request after load: {after[1:]}"
        assert p._verifier_errors == []
    finally:
        p.context.close()
