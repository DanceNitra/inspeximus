"""`verify_bundle` said PASS on forged content and never mentioned that it had not looked.

The bundle is content-free by design -- it carries hashes, never text -- so checks 1-7 are structurally
blind to what the store serves today. That is defensible. What was not defensible is the OUTPUT: an
auditor holding a substituted store ran the documented command, read `VERDICT: PASS`, and nothing in the
result said the one question they came to ask had gone unasked. `bind_content` existed, in a separate
function nobody is routed to.

So the omission is now stated in the verdict itself (`limits`, `summary.content_checked`), and
`store_items=` folds the content check in. Measured before writing this: verify_bundle returned ok=True on
a store whose text had been replaced after export -- in every published version.
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest  # noqa: E402

from inspeximus import Inspeximus  # noqa: E402
from inspeximus.audit_bundle import build_bundle, verify_bundle  # noqa: E402


@pytest.fixture
def store_and_bundle():
    d = tempfile.mkdtemp()
    store = Inspeximus(path=os.path.join(d, "m.json"), receipts=True)
    rid = store.remember("Revenue is 100M", mtype="semantic")
    store.remember("Headcount is 40", mtype="semantic")
    store.flush()
    return store, build_bundle(store), rid, d


def test_a_forged_record_now_fails_the_verdict(store_and_bundle):
    """THE finding. Before this, ok stayed True and the text was served as if witnessed."""
    store, bundle, rid, _ = store_and_bundle
    assert verify_bundle(bundle, store_items=list(store.items))["ok"] is True

    next(x for x in store._items if x["id"] == rid)["text"] = "Revenue is 900M"
    store._save(force=True)

    res = verify_bundle(bundle, store_items=list(store.items))
    assert res["ok"] is False, "a record that no longer matches its first receipt must fail the verdict"
    assert any("no longer match" in p for p in res["problems"]), res["problems"]


def test_without_the_store_the_verdict_says_it_did_not_look(store_and_bundle):
    """The chain really did verify, so `ok` stays True -- but silence about scope is what misled. The
    caller must be able to see the gap without having read the docstring."""
    store, bundle, rid, _ = store_and_bundle
    next(x for x in store._items if x["id"] == rid)["text"] = "Revenue is 900M"
    store._save(force=True)

    res = verify_bundle(bundle)
    assert res["ok"] is True, "the chain is intact; claiming otherwise would false-alarm on every bundle"
    assert res["summary"]["content_checked"] is False
    assert any("CONTENT NOT CHECKED" in lim for lim in res["limits"]), res["limits"]


def test_ordinary_growth_is_a_note_and_not_a_failure(store_and_bundle):
    """A bundle is a snapshot, not a lease. The naive version of this check called every later write a
    problem -- the same false alarm as comparing raw anchor tips, which looked like detection and fired on
    normal operation."""
    store, bundle, _, _ = store_and_bundle
    store.remember("Revenue is 120M in Q3", mtype="semantic")
    store.flush()

    res = verify_bundle(bundle, store_items=list(store.items))
    assert res["ok"] is True, "writing after the export is not tampering"
    assert res["summary"]["content_checked"] is True
    assert any("covered by no receipt" in lim for lim in res["limits"]), res["limits"]


def test_the_limits_are_reported_even_on_a_clean_pass(store_and_bundle):
    """A limit is not an error report; it is the scope of the verdict, and it belongs on the happy path
    or it will only ever be read by people already suspicious."""
    store, bundle, _, _ = store_and_bundle
    res = verify_bundle(bundle, store_items=list(store.items))
    assert res["ok"] is True
    assert any("NOT OPERATOR-ADVERSARIAL" in lim for lim in res["limits"]), res["limits"]


def test_the_cli_prints_the_scope_next_to_the_verdict(store_and_bundle):
    """The CLI is where most people meet this. A PASS whose scope is only in the docstring is the same
    silent assurance in a different place."""
    store, bundle, _, d = store_and_bundle
    bpath = os.path.join(d, "bundle.json")
    with open(bpath, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh)

    r = subprocess.run([sys.executable, "-m", "inspeximus.audit_bundle", "verify", bpath],
                       capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(
                           os.path.abspath(__file__))),
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "content NOT checked" in r.stdout, r.stdout
    assert "CONTENT NOT CHECKED" in r.stdout, "the note must appear, not only the summary word"


def test_the_cli_store_flag_catches_the_forgery(store_and_bundle):
    store, bundle, rid, d = store_and_bundle
    bpath = os.path.join(d, "bundle.json")
    with open(bpath, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh)
    next(x for x in store._items if x["id"] == rid)["text"] = "Revenue is 900M"
    store._save(force=True)

    r = subprocess.run([sys.executable, "-m", "inspeximus.audit_bundle", "verify", bpath,
                        "--store", store.path],
                       capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(
                           os.path.abspath(__file__))),
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert r.returncode == 1, f"a forged store must exit non-zero so it can gate CI:\n{r.stdout}"
    assert "FAIL" in r.stdout and "no longer match" in r.stdout, r.stdout


# ── the same fix has to reach the CLI people actually use ───────────────────────────────────────────
def _inspeximus(*args, **kw):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return subprocess.run([sys.executable, "-m", "inspeximus.cli", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=root,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": root}, **kw)


def test_the_headline_cli_also_states_the_scope(store_and_bundle):
    """`inspeximus audit-verify` is the command the README documents -- a second entry point into the same
    verifier. Fixing only `python -m inspeximus.audit_bundle` would have left the documented one silent,
    which is the class this repository keeps meeting: the fix lands where the report pointed and survives
    one call site over."""
    store, bundle, _, d = store_and_bundle
    bpath = os.path.join(d, "b.json")
    with open(bpath, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh)

    r = _inspeximus("audit-verify", bpath)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "content NOT checked" in r.stdout and "CONTENT NOT CHECKED" in r.stdout, r.stdout


def test_the_headline_cli_binds_content_when_given_the_store(store_and_bundle):
    store, bundle, rid, d = store_and_bundle
    bpath = os.path.join(d, "b.json")
    with open(bpath, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh)
    next(x for x in store._items if x["id"] == rid)["text"] = "Revenue is 900M"
    store._save(force=True)

    r = _inspeximus("audit-verify", bpath, "--store", store.path)
    assert r.returncode == 1, r.stdout
    assert "no longer match" in r.stdout, r.stdout


def test_a_mistyped_store_path_is_refused_not_created(store_and_bundle):
    """THE trap. Opening a store creates it, so a typo would hand the auditor a PASS over an empty store
    they had just made -- exactly the erasure-certificate defect (valid:True while the absence proof
    pointed at a path that was not there) in a second place."""
    store, bundle, _, d = store_and_bundle
    bpath = os.path.join(d, "b.json")
    with open(bpath, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh)
    typo = os.path.join(d, "m-typo.json")

    r = _inspeximus("audit-verify", bpath, "--store", typo)
    assert r.returncode == 1, f"a missing store must not verify clean:\n{r.stdout}"
    assert "does not exist" in r.stdout, r.stdout
    assert not os.path.exists(typo), "verifying must not create the store it was pointed at"


def test_the_json_output_carries_the_scope_too(store_and_bundle):
    """Machine consumers are the ones most likely to read `ok` and nothing else."""
    store, bundle, _, d = store_and_bundle
    bpath = os.path.join(d, "b.json")
    with open(bpath, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh)

    r = _inspeximus("--json", "audit-verify", bpath)
    payload = json.loads(r.stdout)
    assert payload["summary"]["content_checked"] is False
    assert any("CONTENT NOT CHECKED" in lim for lim in payload["limits"]), payload["limits"]


# ── zero comparisons is not a clean content check ───────────────────────────────────────────────────
def test_an_audit_that_compared_nothing_is_not_a_pass(store_and_bundle):
    """The hole in yesterday's fix. `checked` counted RECEIPTS, not comparisons, and `ok` was
    `not mismatched` -- so a store where nothing matched by id produced zero re-hashes, ok=True, and
    "content checked, PASS" printed beside the verdict. Hand the auditor the wrong store, or re-mint the
    ids while rewriting the text, and the strongest possible check-that-cannot-fail results: an audit that
    compared nothing and said so in the affirmative."""
    _, bundle, _, _ = store_and_bundle
    res = verify_bundle(bundle, store_items=[])
    assert res["ok"] is False, "an audit with zero comparisons must not read as a pass"
    assert any("no content was compared" in p for p in res["problems"]), res["problems"]


def test_the_affirmative_line_counts_comparisons_not_receipts(store_and_bundle):
    store, bundle, _, _ = store_and_bundle
    res = verify_bundle(bundle, store_items=list(store.items))
    assert res["ok"] is True
    line = next(c for c in res["checks"] if "binds to the receipts" in c)
    assert " of " in line, f"the line must state comparisons OF receipted records: {line}"

    from inspeximus.audit_bundle import bind_content
    b = bind_content(bundle, list(store.items))
    assert b["checked"] == b["receipted"] == 2
    assert bind_content(bundle, [])["checked"] == 0, "nothing was re-hashed, so nothing was checked"


def test_re_minted_ids_do_not_launder_rewritten_text(store_and_bundle):
    """The attack the zero-comparison hole enabled: keep the chain, replace every record with a rewritten
    one under a fresh id. Every original lands in `orphaned`, nothing is compared."""
    store, bundle, _, _ = store_and_bundle
    forged = [dict(r, id="re" + r["id"][2:], text="Revenue is 900M") for r in store.items]
    res = verify_bundle(bundle, store_items=forged)
    assert res["ok"] is False, res


def test_a_truncated_orphan_list_says_it_was_truncated(store_and_bundle):
    """Twenty substituted records used to print five NOTE lines and nothing about the other fifteen."""
    _, bundle, _, _ = store_and_bundle
    import copy
    big = copy.deepcopy(bundle)
    for i in range(20):
        big["write_chain"].append(dict(big["write_chain"][0], memory_id=f"ghost{i}", seq=100 + i))
    res = verify_bundle(big, store_items=[])
    assert any("more record(s) in the chain are absent" in lim for lim in res["limits"]), res["limits"]
