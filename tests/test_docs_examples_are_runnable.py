"""The commands in our own release notes have to work when pasted.

Three findings, one shape — a claim that reads as evidence and is not:

  * `python -m inspeximus.audit_bundle verify … --store missing.json` OPENED the store, which CREATES it,
    and then verified clean against the empty file it had just made. The guard for exactly this was written
    for `inspeximus audit-verify` in 1.79.0 and never reached the other entry point — which is the one that
    release's own CHANGELOG prints. There is one implementation now.
  * The README's audit-bundle block wrote to the default store and then passed `--store m.json`, a file
    those commands never create. Pasted verbatim it exits 1.
  * "Almost every number in this README traces to a runnable probe … the one exception is flagged in
    place" was false: a search of probes/, tests/ and tools/ finds no producing script for three of them.
"""
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from inspeximus import Inspeximus  # noqa: E402
from inspeximus.audit_bundle import build_bundle, load_store_items  # noqa: E402


@pytest.fixture
def bundle_and_store(tmp_path):
    store = str(tmp_path / "m.json")
    s = Inspeximus(path=store, receipts=True)
    s.remember("Revenue is 100M", mtype="semantic")
    s.flush()
    b = str(tmp_path / "b.json")
    with open(b, "w", encoding="utf-8") as fh:
        json.dump(build_bundle(s), fh)
    return b, store, str(tmp_path / "typo.json")


def _run(argv):
    return subprocess.run([sys.executable, *argv], cwd=ROOT, capture_output=True, text=True, timeout=300,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": ROOT})


@pytest.mark.parametrize("entry", [
    ["-m", "inspeximus.audit_bundle", "verify"],
    ["-m", "inspeximus.cli", "audit-verify"],
])
def test_both_entry_points_refuse_a_store_that_is_not_there(entry, bundle_and_store):
    """BOTH, parametrised, because the defect was that one of them had the guard and the other did not."""
    b, _, missing = bundle_and_store
    r = _run([*entry, b, "--store", missing])
    assert r.returncode == 1, f"exit={r.returncode}: {r.stdout[-400:]}"
    assert not os.path.exists(missing), "verifying must not create the store it was pointed at"


@pytest.mark.parametrize("entry", [
    ["-m", "inspeximus.audit_bundle", "verify"],
    ["-m", "inspeximus.cli", "audit-verify"],
])
def test_both_entry_points_still_verify_a_real_store(entry, bundle_and_store):
    b, store, _ = bundle_and_store
    assert _run([*entry, b, "--store", store]).returncode == 0


def test_one_implementation_of_the_guard(bundle_and_store):
    """Asserted at the function, not only through behaviour: two copies drift, and this one already did."""
    _, store, missing = bundle_and_store
    assert load_store_items(missing) is None
    assert not os.path.exists(missing)
    assert load_store_items(store), "a real store must still load"

    with open(os.path.join(ROOT, "inspeximus", "cli.py"), encoding="utf-8") as fh:
        assert "load_store_items" in fh.read(), "cli.py must call the shared guard, not its own copy"


# ── the documented commands ────────────────────────────────────────────────────────────────────────
def test_every_documented_inspeximus_command_block_runs():
    """It said `--store m.json` after commands that write the DEFAULT store -- a file they never create --
    so the block exited 1 when pasted.

    EVERY bash block is run, each in its own empty directory, rather than one selected block. Selecting was
    tried twice and picked the wrong subject both times: matching on `audit-verify` caught an earlier
    block, and matching on `audit-build` caught a different earlier block. Running them all removes the
    choice, and is the stronger claim anyway -- a documented command that does not work is a defect
    wherever it appears."""
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
        readme = fh.read()

    total = 0
    for block in re.finditer("```bash\n([^`]*)```", readme):
        lines = [ln.split("#")[0].strip() for ln in block.group(1).splitlines()]
        lines = [ln for ln in lines if ln.startswith("inspeximus ")]
        # TEMPLATES are not broken examples. `inspeximus check-code src/**/*.py` cannot run for a reader
        # who has no src/ -- it is showing a shape, not a command to paste. Skipped by an explicit marker
        # list rather than by catching the failure, so a genuinely broken command can never hide here.
        placeholders = ("*", "<", ">", "your-", "path/to", "./deployment", "example.com")
        lines = [ln for ln in lines if not any(ph in ln for ph in placeholders)]
        if not lines:
            continue
        work = tempfile.mkdtemp(prefix="readme_block_")
        for line in lines:
            # shlex, not .split(): a documented command contains a quoted sentence, and
            # splitting on spaces turned it into six unrecognised arguments -- the test
            # failing on its own parsing rather than on the example.
            argv = shlex.split(line.replace("inspeximus ", "", 1))
            r = subprocess.run([sys.executable, "-m", "inspeximus.cli", *argv], cwd=work,
                               capture_output=True, text=True, timeout=300,
                               env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": ROOT})
            total += 1
            assert r.returncode == 0, (
                f"a documented command fails when pasted: $ {line} -- exit={r.returncode}; "
                f"{(r.stdout + r.stderr)[-500:]}")
    assert total >= 6, f"only {total} documented commands were executed; the extraction is broken"


def test_the_readme_does_not_claim_more_receipts_than_it_has():
    """The sentence has now been wrong twice: 'every number', then 'the one exception'. It must not
    promise a count that a search of the repository contradicts."""
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
        readme = fh.read()
    assert "The one exception is" not in readme, \
        "the README claims a single exception; there are three"

    sources = ""
    for sub in ("probes", "tests", "tools"):
        d = os.path.join(ROOT, sub)
        for f in os.listdir(d):
            if f.endswith(".py"):
                sources += open(os.path.join(d, f), encoding="utf-8", errors="replace").read()

    for number, ctx in (("19851", "MemOps"), ("1,037", "MemOps"), ("0.397", "LoCoMo")):
        if number.replace(",", "") in sources or number in sources:
            continue                                   # it acquired a producing script: good, nothing to flag
        i = readme.find(number)
        assert i > 0, f"{number} vanished from the README; drop it from this test too"
        near = readme[max(0, i - 400):i + 400]
        assert ("no probe" in near or "not reproducible" in near or "outside" in near), \
            f"{number} ({ctx}) has no producing script and is not flagged in place"
