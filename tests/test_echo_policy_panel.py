"""The numbers printed in the shipped docstring, checked against the shipped behaviour.

`route()`'s docstring tells a reader "measured 1.00 echo-blocked / 0.00 reaffirm-honored" for
the default policy. That number came from `echo_attack_probe_v2.py`, which cannot run here -- it imports a
sibling module that was never committed and needs a MemBench fixture plus LLM paraphrases. A claim printed
in the source rested on evidence nobody could execute.

`probes/echo_policy_panel.py` re-measures the same two rates deterministically -- no dataset, no network,
no LLM -- because the property is about our policy, not a benchmark. This runs it and holds the docstring
to it, in both directions: if the behaviour changes the test fails, and if the docstring is edited away
from the measurement it fails too.

The echo and the reaffirm are byte-identical by construction. No classifier can separate them from text,
so a policy only chooses which failure it accepts; `safe` scoring anything but 1.00/0.00 would mean it had
started guessing.
"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# SHARED ARTIFACT: this module and tests/test_probes_cited_by_docs.py both execute
# probes/echo_policy_panel.py, which writes ONE probes/echo_policy_panel_result.json.
# Serial they queue; under xdist they raced and clobbered it. Same worker, so they cannot
# overlap. The mark goes on the TESTS -- a mark on a fixture has no effect and pytest errors.
pytestmark = pytest.mark.xdist_group("echo_policy_panel")

@pytest.fixture(scope="module")
def panel():
    r = subprocess.run([sys.executable, os.path.join("probes", "echo_policy_panel.py")],
                       cwd=ROOT, capture_output=True, text=True, timeout=180,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8",
                            "PYTHONPATH": ROOT + os.pathsep + os.environ.get("PYTHONPATH", "")})
    assert r.returncode == 0, f"the panel reports a mismatch with the docstring:\n{r.stdout[-1200:]}"
    with open(os.path.join(ROOT, "probes", "echo_policy_panel_result.json"), encoding="utf-8") as fh:
        return {row["policy"]: row for row in json.load(fh)["rows"]}


def test_safe_blocks_every_echo_and_honors_no_reaffirm(panel):
    """The documented trade, stated as an absolute because it IS one: `safe` cannot tell the two apart, so
    it refuses both. A value strictly between 0 and 1 would mean it had started guessing."""
    assert panel["safe"]["echo_blocked"] == 1.00
    assert panel["safe"]["reaffirm_honored"] == 0.00


def test_trusting_is_the_exact_mirror(panel):
    assert panel["trusting"]["echo_blocked"] == 0.00
    assert panel["trusting"]["reaffirm_honored"] == 1.00


def test_context_separates_the_honest_twins(panel):
    """And this is why the `context` policy exists -- at the cost, stated in the same docstring, of being
    forgeable by anyone who can write two turns."""
    assert panel["context"]["echo_blocked"] == 1.00
    assert panel["context"]["reaffirm_honored"] == 1.00


def test_the_docstring_still_states_the_measured_numbers():
    """A number in shipped source is a claim to every reader of the code. If the behaviour moves, the
    tests above fail; if the copy moves, this one does."""
    from inspeximus import Inspeximus

    # The numbers are in route()'s docstring, not _supersede_by_key's -- I asserted against the wrong
    # one first. Check where they actually are, or the guard passes while guarding nothing.
    doc = Inspeximus.route.__doc__ or ""
    assert "1.00 echo-blocked / 0.00 reaffirm-honored" in doc
    assert "echo_policy_panel.py" in doc, \
        "the docstring must cite the probe that reproduces it, not one that cannot run"


def test_the_default_policy_is_the_safe_one(panel):
    """The docstring says "safe (default)", so every caller who never passes `policy=` depends on it.
    Measuring only the explicit policies left a flipped default invisible: mutating route()'s default from
    "safe" to "trusting" survived this file until this test existed."""
    assert panel["(default)"]["echo_blocked"] == 1.00, \
        "an unmarked replay of a superseded value must not resurrect it by default"


def test_the_default_is_declared_in_the_signature_too():
    """Belt and braces, and it fails faster with a clearer message than a behavioural test would."""
    import inspect

    from inspeximus import Inspeximus
    assert inspect.signature(Inspeximus.route).parameters["policy"].default == "safe"
