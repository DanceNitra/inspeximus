"""MUTATION SCORE for claims_audit.py — can each advertised claim actually FAIL?

claims_audit.py runs 13 checks against the README's promises and CI treats a green run as evidence
that the product does what it says. That evidence is only worth what the checks can DETECT. A check
that passes no matter what the library does is not a check, it is decoration -- and it is worse than
nothing, because it manufactures confidence.

tests/test_audit_scripts_have_teeth.py already proves the GOVERNANCE audit fails when its claim is
falsified, and that a skipped claim fails the gate. Nothing proved the same for the individual claims
in claims_audit.py. This file closes that gap the only way that counts: BREAK the library, one
guarantee at a time, and require the matching check to go red.

METHOD (mutation testing, not assertion counting): patch a single behaviour in inspeximus/core.py,
run claims_audit.py --local against the patched tree in a scratch copy, and assert the named check
reports FAIL. A mutation the audit does not notice is a hole in the audit, and the test says which.

Deliberately narrow: each mutation targets exactly one guarantee, so a failure names the guarantee
rather than "something broke". Mutations are applied to a COPY of the repo -- this test never edits
the working tree it runs from.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join("inspeximus", "core.py")

# (test id, claim label as printed by claims_audit, needle in core.py, replacement)
# Each replacement breaks exactly the guarantee the claim advertises.
MUTATIONS = [
    pytest.param(
        "supersession",
        "corrections supersede the predecessor",
        'r["status"] = "superseded"',
        'r["status"] = "active"',
        id="supersession_stops_retiring_the_predecessor",
    ),
    pytest.param(
        "tamper",
        "a silent edit IS reported as tampering",
        "def verify_writes(self",
        "def verify_writes(self, *_a, **_k):\n        return (True, [])\n\n    def _unused_verify_writes(self",
        id="verify_writes_always_reports_clean",
    ),
    # --- tenant isolation: the multi-tenant security boundary ---
    # TENANT ISOLATION IS ENFORCED TWICE, and finding that out is why this mutation looks odd:
    #   1. _TenantView.items is a property scoped to the view's tenant (its own docstring says that
    #      without it, `view.items` would resolve through __getattr__ to the parent's full list), and
    #   2. recall() filters the pool by tenant again before ranking.
    # Two earlier single-edit attempts (one on _tenant_rows, one on the recall filter) both left the
    # audit PASSING, and neither was an audit hole — they were mutations that failed to break anything,
    # because the other guard still held. Verified directly: with the recall filter disabled, a globex
    # view still saw only BETA while the admin view saw both rows. So the mutation has to defeat BOTH
    # guards, and the fact that it must is itself the finding worth recording.
    pytest.param(
        "tenant_isolation",
        "tenant isolation on recall",
        ['        if self.tenant is not None:\n'
         '            pool = [r for r in pool if r.get("tenant") == self.tenant]',
         "        return Inspeximus.items.fget(self)"],
        ["        if False:\n            pass",
         "        return self._parent.items"],
        id="both_tenant_guards_disabled",
    ),
    # --- trusted_only must fail CLOSED with no trust seeds, not open ---
    pytest.param(
        "trusted_only",
        "trusted_only fails closed without trust seeds",
        "        if trusted_only:",
        "        if False and trusted_only:",
        id="trusted_only_stops_filtering_and_fails_open",
    ),
    # --- erasure: the differentiator ---
    pytest.param(
        "pii_sweep",
        "forget_pii sweeps tagged records",
        "def forget_pii(self",
        "def forget_pii(self, *_a, **_k):\n"
        "        return {'erased': 0, 'ids': [], 'request_id': None, 'tombstones': 0}\n\n"
        "    def _unused_forget_pii(self",
        id="forget_pii_becomes_a_no_op",
    ),
    # --- determinism: the moat claim ---
    # c_determinism hashes the stored (text, status, key) triples ITSELF; it never calls state_digest(),
    # so mutating state_digest survived while saying nothing about this claim. Mutate the STORED TEXT,
    # which is what "same writes, same state" is actually about.
    pytest.param(
        "determinism",
        "deterministic: same writes, same state",
        'rec = {"id": mid, "text": text,',
        'rec = {"id": mid, "text": text + __import__("uuid").uuid4().hex,',
        id="stored_text_becomes_nondeterministic",
    ),
    # --- witness: the hydration receipt ---
    pytest.param(
        "witness",
        "witness() returns a state digest",
        "    def witness(self",
        "    def witness(self, *_a, **_k):\n        return {}\n\n    def _unused_witness(self",
        id="witness_returns_nothing",
    ),
]


def _mutate_copy(needle, replacement) -> str:
    """Copy the repo to a temp dir and apply the mutation(s) to core.py. Returns the copy path.

    `needle`/`replacement` may each be a string, or equal-length sequences for a guarantee that is
    enforced in more than one place. Defence in depth means a single edit can leave the guarantee
    intact, and a mutation that fails to break anything proves nothing about the audit.
    """
    needles = [needle] if isinstance(needle, str) else list(needle)
    repls = [replacement] if isinstance(replacement, str) else list(replacement)
    assert len(needles) == len(repls), "needle/replacement counts must match"
    dst = tempfile.mkdtemp(prefix="mutant_")
    shutil.copytree(REPO, dst, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", ".git", "build", "dist",
                                                  ".venv*", "*.egg-info", "site"))
    path = os.path.join(dst, CORE)
    with open(path, encoding="utf-8") as f:
        src = f.read()
    for n, r in zip(needles, repls):
        assert n in src, f"mutation target not found in core.py: {n!r}"
        src = src.replace(n, r, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(src)
    return dst


def _run_audit(cwd: str):
    proc = subprocess.run([sys.executable, "claims_audit.py", "--local"],
                          cwd=cwd, capture_output=True, text=True, timeout=900,
                          encoding="utf-8", errors="replace")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _verdict_for(output: str, claim_label: str):
    """Return 'PASS' / 'FAIL' / None for the line naming this claim."""
    for line in output.splitlines():
        if claim_label in line:
            m = re.search(r"\[(PASS|FAIL|[^\]]*)\]", line)
            if m:
                return m.group(1).strip()
    return None


def test_the_unmutated_audit_passes_every_claim():
    """The baseline the mutations are measured against — if this is red, the rest means nothing."""
    code, out = _run_audit(REPO)
    assert code == 0, f"baseline claims_audit --local must pass, got exit {code}\n{out[-3000:]}"
    assert "0 FAILED" in out, f"baseline must report 0 FAILED\n{out[-2000:]}"


@pytest.mark.parametrize("_id,claim_label,needle,replacement", MUTATIONS)
def test_each_claim_fails_when_its_guarantee_is_broken(_id, claim_label, needle, replacement):
    """Break one guarantee; the check that advertises it must go red.

    If this fails, claims_audit.py reports that claim as holding while the library no longer honours
    it — which is exactly the state a green CI run is supposed to rule out.
    """
    dst = _mutate_copy(needle, replacement)
    try:
        code, out = _run_audit(dst)
        verdict = _verdict_for(out, claim_label)
        assert verdict is not None, (
            f"claim {claim_label!r} did not appear in the audit output at all — the label this test "
            f"matches on has probably been renamed, which silently disables this mutation test.\n"
            f"{out[-2000:]}")
        assert verdict == "FAIL", (
            f"MUTATION SURVIVED: broke the guarantee behind {claim_label!r} and claims_audit still "
            f"reported [{verdict}]. That check cannot detect its own claim being false.\n"
            f"{out[-2500:]}")
        assert code != 0, "a failed claim must also make the script exit non-zero — CI only sees the code"
    finally:
        shutil.rmtree(dst, ignore_errors=True)
