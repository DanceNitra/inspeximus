"""docs/ERASURE.md is EXECUTED here, command by command, and its printed output asserted.

Documentation that CI does not run rots into fiction, and erasure documentation that rots is worse than
none: it is a promise about deletion that nobody re-checks. So this file does not paraphrase the
quickstart, it parses it. Every ```console block in docs/ERASURE.md is a real transcript -- `$ ` lines
are commands, the lines under them are what the command printed -- and this test replays the whole page
in a throwaway directory and requires the documented output to appear.

WHAT IS ALLOWED TO DRIFT, and nothing else: record ids and key material change every run, so a 10-hex-
character id or a 40+-hex-character key in the documentation is matched as a wildcard, and an id written
in a COMMAND (`--derived-from 295d6c5490`) is rewritten to the id that run actually produced. An `...`
in a documented line matches any text. Everything else -- counts, verdicts, exit codes, the fingerprint,
the wording of a refusal -- is compared literally.

THE CONTROL IS THE POINT, and it is asserted twice: once as part of the page, and once as a standalone
test that does not depend on the parser at all. Delete Alice, confirm she is gone, THEN confirm Bob is
still there. A store that silently wiped everything passes the first half perfectly, so the second half
is what makes the first a measurement rather than a silence. The same shape applies to the certificate:
an honest one must verify AND a tampered one must fail, because a verifier that cannot fail has measured
nothing.

And the harness itself carries a control (`test_the_doc_harness_can_fail`): if the matcher could not
tell a documented line from a wrong one, every assertion above would be decorative.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs" / "ERASURE.md"
EXAMPLE = REPO / "examples" / "11_verifiable_erasure.py"

# What is allowed to differ between the documented run and this one. Deliberately narrow: an id, a key,
# and an explicit `...`. A wildcard that swallowed numbers would let "erased 3 record(s)" pass while the
# code erased one, which is the assertion this file exists to make.
_VOLATILE = re.compile(r"\.\.\.|\b[0-9a-f]{40,}\b|\b[0-9a-f]{10}\b")
_ID = re.compile(r"\b[0-9a-f]{10}\b")

pytestmark = pytest.mark.skipif(not DOC.exists(), reason="docs/ERASURE.md is missing")


class Step:
    def __init__(self, cmd: str, lineno: int):
        self.cmd = cmd
        self.lineno = lineno
        self.expected: list[str] = []
        self.exit = 0

    def __repr__(self) -> str:                      # what a failure report shows
        return f"ERASURE.md:{self.lineno}: $ {self.cmd}"


def parse_doc(text: str) -> list[Step]:
    """Every command in every ```console block, with the output documented under it."""
    steps: list[Step] = []
    in_block = False
    current: Step | None = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        if line.startswith("```"):
            # Only ```console blocks are executable. A plain fence is illustration, and running it would
            # make every code sample in the file a hostage of this test.
            in_block = line.strip() == "```console"
            current = None
            continue
        if not in_block:
            continue
        if line.startswith("$ "):
            current = Step(line[2:].strip(), lineno)
            steps.append(current)
        elif current is None:
            continue
        elif line.startswith("# exit status:"):
            current.exit = int(line.split(":", 1)[1].strip())
        elif line.startswith("#") or not line.strip():
            continue                                # a note to the reader, or a blank line
        else:
            current.expected.append(line)
    return steps


def to_pattern(expected: str) -> re.Pattern:
    """A documented line as a regex: literal everywhere except ids, keys and an explicit `...`."""
    out, i = [], 0
    for m in _VOLATILE.finditer(expected):
        out.append(re.escape(expected[i:m.start()]))
        out.append(".*" if m.group() == "..." else
                   ("[0-9a-f]{40,}" if len(m.group()) >= 40 else "[0-9a-f]{10}"))
        i = m.end()
    out.append(re.escape(expected[i:]))
    return re.compile("".join(out))


def run_doc(workdir: Path) -> list[tuple]:
    """Replay every documented command in `workdir`. Returns [(step, exit_code, output), ...].

    stdout and stderr are MERGED because that is what the reader sees in a terminal, and because the
    refusals this page documents (a zero-erasure certificate, a dangling lineage id) are warnings on
    stderr -- a harness reading only stdout would report them as undocumented silence.
    """
    env = dict(os.environ)
    # Against THIS checkout. A doc test that measured an installed build would be describing code that
    # does not ship in this commit.
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("INSPEXIMUS_PATH", None)
    env.pop("INSPEXIMUS_RECEIPT_KEY_FILE", None)
    env.pop("INSPEXIMUS_RECEIPT_KEY", None)

    idmap: dict[str, str] = {}                      # documented id -> the id this run produced
    results = []
    for step in parse_doc(DOC.read_text(encoding="utf-8")):
        argv = shlex.split(step.cmd)
        # The documentation writes `inspeximus`, the installed console script. `-m inspeximus.cli` is the
        # same program and needs no install step; the page says so.
        if argv[0] == "inspeximus":
            argv = [sys.executable, "-m", "inspeximus.cli"] + argv[1:]
        elif argv[0] == "python":
            argv = [sys.executable] + argv[1:]
        elif argv[0] == "mkdir":
            (workdir / argv[1]).mkdir(parents=True, exist_ok=True)
            results.append((step, 0, ""))
            continue
        elif argv[0] == "export":
            key, _, val = argv[1].partition("=")
            env[key] = val
            results.append((step, 0, ""))
            continue
        else:
            raise AssertionError(f"{step!r}: the harness cannot run this command")

        argv = [idmap.get(a, a) for a in argv]      # `--derived-from <documented id>` -> this run's id
        proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, cwd=str(workdir))
        out = (proc.stdout or "") + (proc.stderr or "")
        # Learn the mapping from the ids the documentation printed to the ones just produced, in order.
        for doc_id, run_id in zip(_ID.findall("\n".join(step.expected)), _ID.findall(out)):
            idmap.setdefault(doc_id, run_id)
        results.append((step, proc.returncode, out))
    return results


@pytest.fixture(scope="module")
def replay(tmp_path_factory):
    return run_doc(tmp_path_factory.mktemp("erasure-quickstart"))


def test_the_doc_has_the_commands_it_promises(replay):
    """A page whose blocks stopped being executable would otherwise pass by having nothing to run."""
    commands = [s.cmd for s, _, _ in replay]
    assert len(commands) >= 12, commands
    for needed in ("forget-subject", "erasure-certificate", "residue", "erasure-verify"):
        assert any(needed in c for c in commands), f"the quickstart no longer runs {needed}"


def test_every_documented_command_prints_what_the_doc_says(replay):
    problems = []
    for step, code, out in replay:
        if code != step.exit:
            problems.append(f"{step!r}\n    exit {code}, documented {step.exit}\n    output: {out[:300]}")
        for expected in step.expected:
            if not any(to_pattern(expected).search(line) for line in out.splitlines()):
                problems.append(f"{step!r}\n    documented: {expected!r}\n    actual:\n{out[:600]}")
    assert not problems, "docs/ERASURE.md no longer matches what the commands do:\n\n" + "\n\n".join(problems)


# --- the control, stated independently of the parser ------------------------------------------------
# If the harness above were subtly broken these would still hold, and they are the two halves the whole
# page rests on. Written against the documented transcript rather than re-deriving it, so a change to
# the page that broke the control shows up here too.

def _step(replay, needle):
    matches = [(s, c, o) for s, c, o in replay if needle in s.cmd]
    assert matches, f"the quickstart no longer contains a command matching {needle!r}"
    return matches[0]


def test_positive_control_the_deleted_subject_is_gone(replay):
    _, code, out = _step(replay, "--value alice@example.com")
    assert code == 0, out
    assert "clean - no residue found" in out, out


def test_positive_control_a_different_record_is_still_there(replay):
    """THE SECOND HALF. Without it a store that silently wiped everything scores a perfect pass."""
    _, code, out = _step(replay, "--value bob@example.com")
    assert code == 1, f"the scanner found nothing for the record that must still exist:\n{out}"
    assert "PLAIN" in out and "residue found" in out, out
    # sha256("bob@example.com")[:12] -- deterministic, so this pins that the finding is about the value
    # asked for and not some other hit. Values are never echoed; the fingerprint is what correlates.
    assert "fp=5ff860bf1190" in out, out

    _, code, out = _step(replay, "list -n 5")
    assert "Bob Weber" in out and "Alice Novak" not in out, out


def test_the_erased_subject_is_gone_and_the_store_still_serves_the_neighbour(replay):
    """Both halves in one assertion, the way an operator would check it."""
    _, gone_code, gone = _step(replay, "--value alice@example.com")
    _, here_code, here = _step(replay, "--value bob@example.com")
    assert (gone_code, here_code) == (0, 1), (gone, here)
    assert "clean" in gone and "residue found" in here


# --- the certificate must verify, and must be able to fail ------------------------------------------

def test_an_honest_certificate_verifies(replay):
    _, code, out = _step(replay, "erasure-verify cert.json")
    assert code == 0, out
    assert "VERDICT: PASS" in out, out
    # The absence proof is the strongest check in the document; a certificate that verified without it
    # would be a chain-only verdict wearing a PASS.
    assert "OK   store_absent" in out, out


def test_a_tampered_certificate_fails(replay):
    _, code, out = _step(replay, "erasure-verify cert-tampered.json")
    assert code == 1, f"a rewritten scope statement verified:\n{out}"
    assert "FAIL scope_intact" in out and "VERDICT: FAIL" in out, out


def test_a_certificate_that_attests_to_nothing_is_refused_at_both_ends(replay):
    """Measured 2026-08-01: this used to verify `valid: true` -- see docs/ERASURE.md.

    Every other check in the verifier is a consistency check, and all of them pass VACUOUSLY on an empty
    scope, so a certificate for a request that was never performed came back valid with every field in
    it honest.
    """
    _, code, out = _step(replay, "--request-id DSAR-2026-999")
    assert code == 1, f"the producer accepted a zero-erasure certificate as evidence:\n{out}"
    assert "ZERO erasures" in out, out


# --- the harness must be able to go red -------------------------------------------------------------

def test_the_doc_harness_can_fail(tmp_path):
    """A matcher that cannot tell a documented line from a wrong one has measured nothing.

    Both directions: a line the command really prints must match, and a plausible-but-wrong variant of
    the SAME line must not. Asserting only the second would pass on a matcher that rejects everything.
    """
    assert to_pattern("erased 3 record(s), 3 tombstone(s)").search("erased 3 record(s), 3 tombstone(s)")
    assert not to_pattern("erased 3 record(s), 3 tombstone(s)").search("erased 1 record(s), 1 tombstone(s)")
    # ids and keys drift and are wildcards; counts and verdicts never are
    assert to_pattern("remembered 295d6c5490 [key=alice::contact]").search(
        "remembered ff00ff00ff [key=alice::contact]")
    assert not to_pattern("RESULT: clean - no residue found").search("RESULT: residue found (see above)")
    assert not to_pattern("VERDICT: PASS  (3 erasure(s) attested, absence checked)").search(
        "VERDICT: FAIL  (3 erasure(s) attested, absence checked)")

    # And end to end: a doc whose documented output is wrong must produce a mismatch, not a pass.
    fake = tmp_path / "FAKE.md"
    fake.write_text("```console\n$ inspeximus --path ./s.json stats\n"
                    "this is not what stats prints\n```\n", encoding="utf-8")
    steps = parse_doc(fake.read_text(encoding="utf-8"))
    assert len(steps) == 1 and steps[0].expected == ["this is not what stats prints"]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("INSPEXIMUS_PATH", None)
    proc = subprocess.run([sys.executable, "-m", "inspeximus.cli", "--path", "./s.json", "stats"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, cwd=str(tmp_path))
    out = (proc.stdout or "") + (proc.stderr or "")
    assert not any(to_pattern(steps[0].expected[0]).search(line) for line in out.splitlines()), out


# --- the scope statements must stay in the page -----------------------------------------------------

def test_the_documented_scope_survives_an_edit():
    """The limits are the part a rewrite quietly drops, so they are pinned like any other claim.

    A verification tool that overstates its reach is worse than none; these three sentences are what
    keep this page from being one.
    """
    text = DOC.read_text(encoding="utf-8").lower()
    for phrase in ("logical residue, not at-rest security",
                   "over-provisioned ssd blocks",
                   "crypto-erasure",
                   "literal bytes",
                   "evidence, not proof",
                   "deliberate design choice",
                   "not a compliance certification"):
        assert phrase in text, f"docs/ERASURE.md no longer states: {phrase!r}"

    # Never a claim about other systems we have not measured; describe what this one does.
    for forbidden in ("competitors can", "nobody else", "no other library", "unlike every"):
        assert forbidden not in text, f"docs/ERASURE.md makes an unmeasured claim about others: {forbidden!r}"


def test_the_example_script_runs_and_checks_both_halves():
    """The example is executable documentation too, and it exits non-zero when a check stops holding."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("INSPEXIMUS_PATH", None)
    proc = subprocess.run([sys.executable, str(EXAMPLE)], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, out
    # The script's own check() prefix, not a bare "FAIL": the transcripts it prints legitimately contain
    # `FAIL scope_intact` and `VERDICT: FAIL` (those are the tamper checks doing their job), and so does
    # its docstring. Matching the prefix asks the precise question -- did any assertion fail?
    assert not [ln for ln in out.splitlines() if ln.startswith("  FAIL  ")], out
    for half in ("HALF 1 - the erased subject is GONE",
                 "HALF 2 - the neighbour is STILL PRESENT"):
        assert f"PASS  {half}" in out, out
    assert "PASS  a rewritten scope statement is REJECTED" in out, out


def test_an_unsigned_certificate_says_so_instead_of_reporting_valid_signatures(tmp_path):
    """A check that did not run is not a check that passed -- and the limit must reach the operator.

    `sigs_ok` starts True and is only ever set False by a FAILING signature, so a certificate whose
    tombstones carry none reported `signatures_valid: true` -- the verifier telling a DPA the signatures
    are valid about a document that has none. This asserts the honest reading (`n/a`) AND that the NOTE
    explaining it is printed, because a limit computed and not shown is a limit nobody acts on.
    """
    store = tmp_path / "store.json"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    env.pop("INSPEXIMUS_PATH", None)
    env.pop("INSPEXIMUS_RECEIPT_KEY_FILE", None)
    env.pop("INSPEXIMUS_RECEIPT_KEY", None)

    def cli(*args):
        p = subprocess.run([sys.executable, "-m", "inspeximus.cli", *args],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, cwd=str(tmp_path))
        return p.returncode, (p.stdout or "") + (p.stderr or "")

    # No --receipt-key-file anywhere: the tombstones are written unsigned.
    cli("--path", str(store), "--receipts", "remember", "Alice Novak, alice@example.com",
        "--key", "alice::contact", "--source", "alice@example.com")
    cli("--path", str(store), "forget-subject", "alice@example.com", "--request-id", "DSAR-1")
    cert = tmp_path / "cert.json"
    code, out = cli("--path", str(store), "erasure-certificate", "--request-id", "DSAR-1",
                    "--out", str(cert))
    assert code == 0, out
    assert "UNSIGNED" in out, f"the producer did not say the chain is unsigned:\n{out}"

    code, out = cli("erasure-verify", str(cert), "--store", str(store))
    assert "n/a  signatures_valid" in out, out
    assert "NOTE UNSIGNED" in out, f"the UNSIGNED limit was computed but never printed:\n{out}"
    # The chain itself is still evidence of integrity, just not of authorship, so this must still PASS.
    assert code == 0 and "VERDICT: PASS" in out, out


def test_the_certificate_carries_no_personal_data(tmp_path):
    """Content-free BY CONSTRUCTION -- checked on the artifact, not asserted in prose.

    A hash of PII is still PII, so a receipt that commits to the content would re-expose what it exists
    to prove was erased. This reads the certificate the library actually produces.
    """
    from inspeximus import Inspeximus
    store = tmp_path / "store.json"
    m = Inspeximus(path=str(store), receipts=True)
    m.remember("Alice Novak, alice@example.com, lives in Frankfurt", key="alice::contact",
               source={"doc": "alice@example.com"})
    m.flush()
    res = m.forget_subject("alice@example.com", request_id="DSAR-1", basis="GDPR Art.17")
    m.flush()
    assert res["erased"] == 1
    blob = json.dumps(m.erasure_certificate(request_id="DSAR-1"))
    for secret in ("Alice Novak", "alice@example.com", "Frankfurt"):
        assert secret not in blob, f"the certificate leaks {secret!r}"
