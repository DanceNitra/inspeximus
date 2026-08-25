"""There must be exactly ONE implementation of audit-verify, and this fails if a copy comes back.

`python -m inspeximus.audit_bundle verify` was a full second copy of the `inspeximus audit-verify`
handler. It drifted the way a second implementation of one decision always does: when
`--expected-pubkey` and `--require-signed` were added -- the fix for "the pin is reachable only from
the side that does not need it" -- only one of the two surfaces got them, so an auditor who reached
for the other got the unpinned verdict with nothing telling them a stronger check existed.

Nobody noticed for a release. It surfaced only because the mutation harness refused a spec whose
target line had moved, which is a lucky way to find a user-facing gap.

THE RULE, already learned in this repo and applied here: DELETE the copy, do not port the guard into
it. Porting buys one round of agreement and diverges again on the next change. `_cli` is a
translator onto the real CLI now, so old invocations keep working and there is one implementation.

This file is the guard on that, because a docstring saying "there is one implementation" is a
comment, and a comment is what was wrong the last three times.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from inspeximus import Inspeximus
from inspeximus.audit_bundle import build_bundle
from inspeximus.core import new_ed25519_keypair

SK, PK = new_ed25519_keypair()
_OTHER_SK, OTHER_PK = new_ed25519_keypair()
SRC = Path(__file__).resolve().parents[1] / "inspeximus"
ENV = {**os.environ, "PYTHONUTF8": "1"}


def test_verify_bundle_is_called_from_exactly_one_command_handler():
    """The source-level guard. A second `verify_bundle(...)` call inside an argv handler is a second
    implementation, whatever it is named."""
    hits = []
    for f in SRC.glob("*.py"):
        src = f.read_text(encoding="utf-8")
        for m in re.finditer(r"^\s*res\s*=\s*verify_bundle\(", src, re.M):
            hits.append(f"{f.name}:{src[:m.start()].count(chr(10)) + 1}")
    assert hits == ["cli.py:%s" % hits[0].split(":")[1]] or len(hits) == 1, (
        f"audit-verify is implemented in {len(hits)} places: {hits}. Delete the copy and have it "
        f"translate onto the one in cli.py -- porting the flags across keeps two implementations "
        f"and they diverge again on the next change.")


def test_the_alias_forwards_rather_than_reimplementing():
    """`_cli` must not grow its own verdict printing again. If it does, this test is the reminder
    that the last copy shipped a weaker check for a whole release."""
    src = (SRC / "audit_bundle.py").read_text(encoding="utf-8")
    body = src[src.index("def _cli("):]
    assert "from .cli import main" in body, "the alias no longer forwards to the real CLI"
    assert "verify_bundle(" not in body, "the alias is verifying on its own again"
    assert "VERDICT" not in body, "the alias is printing its own verdict again"


@pytest.fixture
def bundle_file():
    d = tempfile.mkdtemp()
    ix = Inspeximus(path=os.path.join(d, "s.json"), receipts=True, receipt_key=SK)
    ix.remember("deployment needs two approvers", key="pol", object="two")
    ix.flush()
    bp = os.path.join(d, "b.json")
    json.dump(build_bundle(ix), open(bp, "w", encoding="utf-8"))
    return d, bp


def _alias(*argv):
    return subprocess.run([sys.executable, "-m", "inspeximus.audit_bundle", *argv],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=ENV)


def test_the_alias_now_offers_the_pin_it_used_to_lack(bundle_file):
    """The behaviour the divergence cost: this entrypoint could not pin a key at all."""
    _d, bp = bundle_file
    good = _alias("verify", bp, "--expected-pubkey", PK)
    assert good.returncode == 0 and "VERIFY against the pinned key" in good.stdout, good.stdout

    bad = _alias("verify", bp, "--expected-pubkey", OTHER_PK)
    assert bad.returncode == 1 and "NOT the one pinned" in bad.stdout, bad.stdout


def test_control_the_old_invocations_still_work(bundle_file):
    """The must-not-brick control. Collapsing a duplicate is only correct if the surface it served
    keeps serving -- otherwise this is a removal wearing a refactor's clothes."""
    d, bp = bundle_file
    assert _alias("verify", bp).returncode == 0
    out = os.path.join(d, "o.json")
    assert _alias("build", "--path", os.path.join(d, "s.json"), "--out", out).returncode == 0
    assert os.path.exists(out), "build wrote nothing"
    assert json.load(open(out, encoding="utf-8"))["anchor"]["n_writes"] == 1
