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
    # BOTH documents. The README was cut to a landing page and the command reference moved into
    # docs/DEEP_DIVE.md, which took the executed count from 8 to 1 -- noticed ONLY by the floor below.
    # A test that reads one file cannot see that its subject moved to another: it reports a clean run
    # over a surface that is no longer there. So name every document that documents commands, and
    # assert each EXISTS, so a missing path can never read as a document that happens to have none.
    docs = ["README.md", os.path.join("docs", "DEEP_DIVE.md")]
    readme = ""
    for rel in docs:
        path = os.path.join(ROOT, rel)
        assert os.path.exists(path), f"{rel} is missing; its documented commands would go unchecked"
        with open(path, encoding="utf-8") as fh:
            readme += fh.read() + os.linesep * 2   # keep two documents' fences from fusing into one block

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
            # A READER'S environment, not ours. EIGHTEEN test modules assign INSPEXIMUS_* into
            # os.environ directly rather than through monkeypatch, so it survives for the rest of the
            # worker process; inheriting it sent the documented `remember` to some other tmp store and the
            # later `--store inspeximus_memory.json` then found nothing. That made this test pass or
            # fail on WHICH TESTS RAN BEFORE IT: green locally, red in the integrations job where the
            # optional extras pull in more modules and the ordering changes. A documented command has
            # to work for someone who has none of our variables set, which is what this now measures.
            reader_env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
            r = subprocess.run([sys.executable, "-m", "inspeximus.cli", *argv], cwd=work,
                               capture_output=True, text=True, timeout=300,
                               env={**reader_env, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": ROOT})
            total += 1
            assert r.returncode == 0, (
                f"a documented command fails when pasted: $ {line} -- exit={r.returncode}; "
                f"{(r.stdout + r.stderr)[-500:]}")
    assert total >= 6, (
        f"only {total} documented commands were executed; the extraction is broken -- either the "
        f"blocks moved to a document not named in {docs}, or the fences stopped matching")


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


# ── the README's centrepiece example ────────────────────────────────────────────────────────────────
def test_the_readme_python_example_produces_the_output_it_documents():
    """The first code a visitor reads. It ran; nothing checked that it ran to the DOCUMENTED result.

    A bare `m.recall(...)[0]["text"]` line is an expression statement: in a pasted script it evaluates
    and prints nothing, so the example could return any value at all and still 'work'. The comment under
    it is the actual claim, and until now the comment was the only thing asserting it.
    """
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
        blocks = re.findall(r"```python\n(.*?)```", fh.read(), re.S)
    assert blocks, "the README has no python example; the centrepiece is gone"

    checked = 0
    for block in blocks:
        lines = block.splitlines()
        out = []
        for i, line in enumerate(lines):
            nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
            # A documented result: an expression whose next line is a comment opening with a quote.
            m = re.match(r"^# ('([^']*)'|\"([^\"]*)\")", nxt)
            if m and line.strip() and not line.startswith((" ", "\t", "#")) and "=" not in line.split("(")[0]:
                want = m.group(2) if m.group(2) is not None else m.group(3)
                # Built with %r rather than an f-string: the documented value contains quotes, and
                # interpolating its repr INSIDE a quoted message closed the message early -- the
                # generated program then failed to parse, which reads as "the example is broken".
                msg = "the README documents %r as the result of this line" % (want,)
                out.append("assert (%s) == %r, %r" % (line.strip(), want, msg))
                checked += 1
            else:
                out.append(line)
        work = tempfile.mkdtemp(prefix="readme_py_")
        path = os.path.join(work, "example.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out))
        r = subprocess.run([sys.executable, "-X", "utf8", path], cwd=work, capture_output=True,
                           text=True, timeout=300,
                           env={**os.environ, "PYTHONPATH": ROOT, "PYTHONIOENCODING": "utf-8"})
        assert r.returncode == 0, f"the README example does not do what it says:\n{r.stdout}\n{r.stderr}"

    # Without this floor the rewrite could silently match nothing and assert an empty program. That is
    # the same defect as the block above: a check that never sees its target reporting success.
    assert checked >= 2, (
        f"only {checked} documented outputs were turned into assertions; the example's comments are no "
        f"longer in the '# <result>' shape this reads, so the results are unchecked")
