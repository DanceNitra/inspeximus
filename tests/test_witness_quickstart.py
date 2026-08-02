"""docs/TRANSPARENCY.md is EXECUTED here, not proof-read.

Documentation that is not run rots; documentation CI runs cannot. Every ```console block in the page is
parsed out, the commands are run in one shared temp directory (state carries between them -- that is the
point of a quickstart), and the printed output is matched against what the page claims. `<...>` in an
expected line matches any run of characters, so ids, hashes and public keys stay wildcards while the
words, the counts and the VERDICT lines are literal. A `# exit: N` line pins the exit code.

Beyond the page, this file carries the two controls the witness layer is worthless without, asserted in
BOTH directions:

  * the split-view detector must FIRE on a genuinely divergent pair and stay SILENT on an identical one
  * verify_cosigned_anchor must FAIL on a tampered anchor -- and the honest anchor beside it must PASS,
    or the failure proves only that the verifier rejects everything

and the vacuous-pass checks: a quorum of zero, an empty allowlist, and an anchor over an empty history
must not be reported as evidence.
"""
import os
import re
import shlex
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

pytest.importorskip("cryptography", reason="the witness surface needs Ed25519")

from inspeximus.core import Inspeximus  # noqa: E402
from inspeximus.witness_pool import Witness  # noqa: E402

DOC = os.path.join(ROOT, "docs", "TRANSPARENCY.md")


# ── parsing the page ────────────────────────────────────────────────────────────────────────────────
def _blocks(text):
    """Every ```console block, as a list of (command, expected_lines, expected_exit)."""
    steps = []
    for body in re.findall(r"^```console\n(.*?)^```", text, re.M | re.S):
        cur = None
        for line in body.split("\n"):
            if line.startswith("$ "):
                if cur:
                    steps.append(cur)
                cur = [line[2:].strip(), [], 0]
            elif cur is None:
                continue
            elif line.startswith("# exit:"):
                cur[2] = int(line.split(":", 1)[1].strip())
            elif line.strip():
                cur[1].append(line)
        if cur:
            steps.append(cur)
    return steps


def _argv(cmd):
    """The doc says `inspeximus ...` (what a pip install gives you); a source checkout runs it as
    `python -m inspeximus.cli ...`. test_the_console_script_name_is_what_the_doc_promises pins the two
    together so this substitution cannot quietly become a lie."""
    parts = shlex.split(cmd, posix=True)
    if parts[0] == "inspeximus":
        return [sys.executable, "-m", "inspeximus.cli", *parts[1:]]
    if parts[0] == "python":
        return [sys.executable, *parts[1:]]
    raise AssertionError(f"the doc runs a command this harness does not know how to launch: {parts[0]!r}")


def _matches(expected, actual):
    """`<...>` is a wildcard for ids/hashes/keys; every other character is literal.

    Both sides are stripped: the page indents continuation lines for readability and a captured stream
    does not preserve that reliably (merging stderr, and a first line whose leading spaces are eaten by
    an outer strip, both cost a real debugging round here). The words, counts and verdicts are what the
    page is promising -- the indentation is typography."""
    pattern = "".join(".*?" if p == "<...>" else re.escape(p)
                      for p in re.split(r"(<\.\.\.>)", expected.strip()))
    return re.fullmatch(pattern, actual.strip()) is not None


@pytest.fixture(scope="module")
def transcript(tmp_path_factory):
    """Run the page top to bottom ONCE, in one directory, and hand back what each command printed."""
    with open(DOC, encoding="utf-8") as f:
        steps = _blocks(f.read())
    assert len(steps) >= 15, f"the page lost its worked examples: only {len(steps)} commands parsed"

    cwd = str(tmp_path_factory.mktemp("quickstart"))
    env = {**os.environ, "PYTHONPATH": ROOT, "PYTHONIOENCODING": "utf-8"}
    env.pop("INSPEXIMUS_PATH", None)          # the page passes --path explicitly; an inherited one would win
    out = []
    for cmd, expected, want_exit in steps:
        # stderr is merged: the REFUSED verdict is a diagnostic and the page shows it inline.
        r = subprocess.run(_argv(cmd), cwd=cwd, env=env, capture_output=True, text=True, timeout=120,
                           stdin=subprocess.DEVNULL)
        out.append({"cmd": cmd, "expected": expected, "want_exit": want_exit,
                    "got": (r.stdout + r.stderr).strip(), "exit": r.returncode})
    return out


def test_every_documented_command_runs_and_prints_what_the_page_claims(transcript):
    problems = []
    for s in transcript:
        if s["exit"] != s["want_exit"]:
            problems.append(f"$ {s['cmd']}\n    exit {s['exit']}, page says {s['want_exit']}\n"
                            f"    output: {s['got'][:400]}")
            continue
        actual = s["got"].split("\n")
        for want in s["expected"]:
            if not any(_matches(want, line) for line in actual):
                problems.append(f"$ {s['cmd']}\n    page claims: {want}\n"
                                f"    printed:     {s['got'][:400]}")
    assert not problems, ("docs/TRANSPARENCY.md no longer describes what these commands do:\n\n"
                          + "\n\n".join(problems))


def test_the_page_actually_reaches_a_verified_cosigned_anchor(transcript):
    """The control on the harness itself: a parser that silently matched nothing would pass the test
    above over an empty expectation list. The quickstart's whole promise is one PASS line."""
    passes = [s for s in transcript if any("VERDICT: PASS" in ln for ln in s["expected"])]
    assert passes, "the page no longer documents a single successful k-of-n verification"
    assert any("3 of 3 allowlisted witnesses" in ln for s in passes for ln in s["expected"]), \
        "the quickstart must document the actual k-of-n count, not just a bare PASS"


def test_the_page_documents_both_a_fired_and_a_silent_split_view(transcript):
    """Both directions, in the DOC. A page that only ever shows the alarm firing is selling a detector
    that could be `return True`."""
    verdicts = [ln for s in transcript for ln in s["expected"] if ln.startswith("VERDICT:")]
    assert "VERDICT: SPLIT VIEW PROVEN" in verdicts, "the page must show the detector FIRE"
    assert "VERDICT: NO SPLIT VIEW" in verdicts, "the page must show the detector stay SILENT"


def test_the_console_script_name_is_what_the_doc_promises():
    """The page writes `inspeximus ...`; this harness runs `python -m inspeximus.cli ...`. If the entry
    point were renamed, every command on the page would break for a pip user while CI stayed green."""
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
        pyproject = f.read()
    assert re.search(r"^inspeximus\s*=\s*[\"']inspeximus\.cli:main[\"']", pyproject, re.M), \
        "docs/TRANSPARENCY.md tells readers to run `inspeximus`; pyproject no longer maps that name"


# ── the controls, independent of the page ───────────────────────────────────────────────────────────
def _store(*rows):
    m = Inspeximus(path=None, receipts=True)
    for text, key, obj in rows:
        m.remember(text, key=key, object=obj)
    return m


@pytest.fixture()
def cosigned():
    """An honest head with three independent witnesses over it."""
    s = _store(("invoice 7 total is 100 EUR", "inv7::total", "100"),
               ("invoice 8 total is 250 EUR", "inv8::total", "250"))
    head = s.anchor()
    ws = [Witness() for _ in range(3)]
    return head, ws, [w.public for w in ws], [w.cosign("acme-prod", head) for w in ws]


def test_control_the_honest_anchor_passes(cosigned):
    """Without this, every refusal below is satisfied by a verifier that rejects everything."""
    head, _, allow, sigs = cosigned
    r = Inspeximus.verify_cosigned_anchor(head, sigs, allow, threshold=3)
    assert r["ok"] is True and r["count"] == 3, r


@pytest.mark.parametrize("field,value", [
    ("writes_tip", "f" * 64),
    ("n_writes", 99),
    ("n_tombstones", 7),
    ("tombstones_tip", "a" * 64),
])
def test_a_tampered_anchor_fails_even_with_genuine_signatures(field, value, cosigned):
    """THE defect this unit found. The signature covers `sth_hash`; every consumer reads these FIELDS
    (verify_consistency pins a store to writes_tip, detect_split_view compares them). Unbound, an
    operator kept a genuine sth_hash and genuine signatures, substituted the tip of a REWRITTEN history,
    and collected ok=True from 3 of 3 honest witnesses.

    Parametrised over all four committed fields: a guard that binds only the one field the bug report
    named would pass a single-case test and leave the other three open."""
    head, _, allow, sigs = cosigned
    tampered = dict(head)
    tampered[field] = value
    assert tampered[field] != head[field], "fixture: the tamper must actually change the field"

    r = Inspeximus.verify_cosigned_anchor(tampered, sigs, allow, threshold=3)
    assert r["ok"] is False, f"a substituted {field} verified as co-signed by 3 of 3 witnesses"
    assert r["count"] == 0, r
    assert "does not commit" in (r.get("error") or ""), r


def test_a_witness_refuses_to_cosign_an_anchor_that_does_not_bind_its_fields(cosigned):
    """The same class closed at the write end: signing an incoherent head mints a signature over a
    commitment no reader can re-derive, which is the material the substitution attack needs."""
    head, ws, _, _ = cosigned
    incoherent = dict(head)
    incoherent["writes_tip"] = "f" * 64
    with pytest.raises(ValueError, match="does not commit"):
        Witness().cosign("acme-prod", incoherent)
    assert ws        # the fixture's honest witnesses are untouched


def test_the_end_to_end_forgery_that_inverted_the_guarantee():
    """Not the unit -- the CONSEQUENCE. Before the fix, this sequence made an auditor certify a rewritten
    store as append-only and report the honest store as the fork."""
    honest = _store(("invoice 7 total is 100 EUR", "inv7::total", "100"))
    rewritten = _store(("invoice 7 total is 900 EUR", "inv7::total", "900"))
    head = honest.anchor()
    ws = [Witness() for _ in range(3)]
    sigs = [w.cosign("acme-prod", head) for w in ws]

    doctored = dict(head)
    doctored["writes_tip"] = rewritten.anchor()["writes_tip"]

    assert Inspeximus.verify_cosigned_anchor(doctored, sigs, [w.public for w in ws], 3)["ok"] is False, (
        "the doctored anchor verified as 3-of-3 co-signed; an auditor would then run "
        "verify_consistency against it and certify the REWRITTEN store while flagging the honest one")
    # the control: had the auditor trusted it, this is what they would have concluded
    assert rewritten.verify_consistency(doctored)[0] is True, "fixture: the doctored tip must fit the rewrite"
    assert honest.verify_consistency(doctored)[0] is False, "fixture: and must NOT fit the honest store"


def test_a_fork_cannot_reach_threshold_with_three_witnesses():
    """THE control. Three witnesses have signed the honest head; the operator forks at the same log size
    and asks them all again. If the fork ever reaches threshold, the guarantee is void."""
    honest = _store(("invoice 7 total is 100 EUR", "inv7::total", "100"),
                    ("invoice 8 total is 250 EUR", "inv8::total", "250"))
    rewritten = _store(("invoice 7 total is 900 EUR", "inv7::total", "900"),
                       ("invoice 8 total is 250 EUR", "inv8::total", "250"))
    head, forked = honest.anchor(), rewritten.anchor()
    assert forked["n_writes"] == head["n_writes"] and forked["writes_tip"] != head["writes_tip"], \
        "fixture: the fork must sit at a size already witnessed and actually differ"

    ws = [Witness() for _ in range(3)]
    allow = [w.public for w in ws]
    for w in ws:
        w.cosign("acme-prod", head)

    obtained, refused = [], 0
    for w in ws:
        try:
            obtained.append(w.cosign("acme-prod", forked))
        except ValueError:
            refused += 1
    assert refused == 3, f"only {refused} of 3 honest witnesses refused the fork"
    for k in (1, 2, 3):
        assert Inspeximus.verify_cosigned_anchor(forked, obtained, allow, threshold=k)["ok"] is False, \
            f"the forked head reached {k}-of-3"


def test_the_detector_fires_on_divergence_and_is_silent_on_an_identical_pair():
    """Both directions in one test, so neither can be dropped without the other going red."""
    honest = _store(("invoice 7 total is 100 EUR", "inv7::total", "100"))
    rewritten = _store(("invoice 7 total is 900 EUR", "inv7::total", "900"))
    head, forked = honest.anchor(), rewritten.anchor()

    w = Witness()
    sig_a = w.cosign("acme-prod", head)
    sig_b = Witness(secret_hex=w._secret).cosign("acme-prod", forked)   # same key, state lost / colluding
    allow = [w.public]

    fired = Inspeximus.detect_split_view(head, [sig_a], forked, [sig_b], allow)
    assert fired["fork"] is True and fired["evidence"] == [w.public], fired
    assert fired["inconsistent"] is True and "n_writes" in fired["at"], fired

    silent = Inspeximus.detect_split_view(head, [sig_a], head, [sig_a], allow)
    assert silent["fork"] is False and silent["inconsistent"] is False, silent
    assert silent["undetermined"] is False, silent


def test_divergent_heads_without_a_common_witness_are_not_reported_as_proof():
    """The middle verdict has to exist, or `fork` degrades into `inconsistent` and the word 'proof'
    stops meaning attributable-to-a-key."""
    head = _store(("a", "k", "1")).anchor()
    forked = _store(("b", "k", "2")).anchor()
    w1, w2 = Witness(), Witness()
    r = Inspeximus.detect_split_view(head, [w1.cosign("s", head)], forked, [w2.cosign("s", forked)],
                                     [w1.public, w2.public])
    assert r["inconsistent"] is True and r["fork"] is False and r["evidence"] == [], r


# ── vacuous passes: the verifier must not succeed over nothing ──────────────────────────────────────
@pytest.mark.parametrize("threshold", [0, -1, -99])
def test_a_quorum_of_zero_is_not_a_quorum(threshold, cosigned):
    """`count >= threshold` is true for an anchor NO witness signed once threshold hits 0 -- including
    with an empty allowlist and no witnesses in existence. A caller computing k from a config that
    failed to load lands on 0 and gets 'externally witnessed' for free."""
    head, _, allow, _ = cosigned
    assert Inspeximus.verify_cosigned_anchor(head, [], allow, threshold)["ok"] is False
    assert Inspeximus.verify_cosigned_anchor(head, [], [], threshold)["ok"] is False


def test_an_anchor_over_an_empty_history_says_so(cosigned):
    """A valid signed head of NOTHING. It verifies -- correctly, the signature is real -- so the scope
    is reported beside the verdict instead of being folded into it."""
    empty = Inspeximus(path=None, receipts=True)
    head = empty.anchor()
    w = Witness()
    r = Inspeximus.verify_cosigned_anchor(head, [w.cosign("s", head)], [w.public], threshold=1)
    assert r["ok"] is True, "the signature is genuine; ok must keep its narrow contract"
    assert r["covers_history"] is False, r
    assert r.get("limits"), "an empty-scope verdict must carry its limit in words"

    # the control: a head over real history must NOT carry that caveat
    real, _, allow, sigs = cosigned
    ok = Inspeximus.verify_cosigned_anchor(real, sigs, allow, threshold=3)
    assert ok["covers_history"] is True and not ok.get("limits"), ok


def test_the_cli_refuses_the_two_configurations_that_read_as_success(tmp_path):
    """An empty allowlist scores every head at 0; a threshold of 0 passes anything. Both must be
    refusals with a distinct exit code, not verdicts."""
    env = {**os.environ, "PYTHONPATH": ROOT, "PYTHONIOENCODING": "utf-8"}
    env.pop("INSPEXIMUS_PATH", None)
    d = str(tmp_path)

    def run(*args):
        return subprocess.run([sys.executable, "-m", "inspeximus.cli", *args], cwd=d, env=env,
                              capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)

    assert run("--receipts", "--path", "s.json", "remember", "x", "--key", "k", "--object", "1").returncode == 0
    assert run("--path", "s.json", "anchor", "--out", "head.json").returncode == 0
    assert run("witness", "keygen", "--out", "w.key", "--allowlist", "wl.txt").returncode == 0
    assert run("witness", "cosign", "head.json", "--store-id", "s", "--key", "w.key",
               "--out", "sig.json").returncode == 0

    no_allowlist = run("witness", "verify", "head.json", "--cosig", "sig.json")
    assert no_allowlist.returncode == 2, no_allowlist.stdout + no_allowlist.stderr

    zero = run("witness", "verify", "head.json", "--cosig", "sig.json",
               "--witnesses-file", "wl.txt", "--threshold", "0")
    assert zero.returncode == 2, zero.stdout + zero.stderr

    # the control: the same command with a real allowlist and threshold 1 passes
    good = run("witness", "verify", "head.json", "--cosig", "sig.json",
               "--witnesses-file", "wl.txt", "--threshold", "1")
    assert good.returncode == 0 and "VERDICT: PASS" in good.stdout, good.stdout + good.stderr


def test_overwriting_a_witness_secret_is_refused(tmp_path):
    """Replacing a witness key silently invalidates every co-signature it ever made -- the old ones then
    read as forgeries rather than as history."""
    env = {**os.environ, "PYTHONPATH": ROOT, "PYTHONIOENCODING": "utf-8"}
    d = str(tmp_path)
    args = [sys.executable, "-m", "inspeximus.cli", "witness", "keygen", "--out", "w.key"]
    assert subprocess.run(args, cwd=d, env=env, capture_output=True, text=True).returncode == 0
    before = open(os.path.join(d, "w.key"), encoding="utf-8").read()
    again = subprocess.run(args, cwd=d, env=env, capture_output=True, text=True)
    assert again.returncode == 2, again.stdout + again.stderr
    assert open(os.path.join(d, "w.key"), encoding="utf-8").read() == before, "the secret was overwritten"


def test_the_example_script_runs_end_to_end():
    """examples/12_split_view_detection.py asserts every control itself; a non-zero exit is a failed
    control, not a crash."""
    r = subprocess.run([sys.executable, os.path.join(ROOT, "examples", "12_split_view_detection.py")],
                       cwd=ROOT, capture_output=True, text=True, timeout=300,
                       env={**os.environ, "PYTHONPATH": ROOT, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stdout + r.stderr
    for marker in ("3-of-3 co-signed: ok=True", "tampered anchor", "refused: 3",
                   "can the fork reach 2-of-3? ok=False", "fork=True", "covers_history=False"):
        assert marker in r.stdout, f"the example stopped demonstrating {marker!r}:\n{r.stdout}"
