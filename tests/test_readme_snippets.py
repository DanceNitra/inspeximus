"""The README's runnable claims, executed verbatim.

The first screen tells a reader to run three things and states what they will see. A command in a README
that does not work is worse than no README: it is the first thing someone tries, and the only impression
they get. These run them and assert the stated outcome, so the copy cannot drift away from the code.

Not a style check on the prose -- these are the CLAIMS: exit codes, verdicts, and which way each answer
goes.
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import Inspeximus
from inspeximus.audit_bundle import bind_content, build_bundle

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README = os.path.join(ROOT, "README.md")
SECRET = "alice@example.com"


def _readme():
    with open(README, encoding="utf-8") as fh:
        return fh.read()


def test_snippet_1_cli_residue_exits_nonzero_and_names_the_kinds():
    """`inspeximus residue ... → exit 1` is a claim the README makes in a comment. It is also what makes
    the command usable as a CI or DSAR gate, so it is the part that must not drift."""
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "trace.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"pii": SECRET}))
    con = sqlite3.connect(os.path.join(d, "v.sqlite"))
    con.execute("CREATE TABLE t(x TEXT)")
    con.execute("INSERT INTO t VALUES(?)", (SECRET,))
    con.commit()
    con.close()

    r = subprocess.run([sys.executable, "-m", "inspeximus.cli", "residue", "--root", d,
                        "--value", SECRET], capture_output=True, text=True)
    assert r.returncode == 1, f"the README promises a non-zero exit on residue: {r.stdout}"
    assert "PLAIN" in r.stdout and "LIVE" in r.stdout, r.stdout
    assert SECRET not in r.stdout, "and it must not print the value it was asked to hunt"


def test_snippet_1_exits_zero_when_clean():
    """The other half: a gate that always fails is not a gate."""
    r = subprocess.run([sys.executable, "-m", "inspeximus.cli", "residue",
                        "--root", tempfile.mkdtemp(), "--value", SECRET],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_snippet_2_forget_with_residue_check():
    d = tempfile.mkdtemp()
    store = Inspeximus(path=os.path.join(d, "m.json"), receipts=True)
    rid = store.remember(f"Alice contact is {SECRET}")
    store.flush()
    with open(os.path.join(d, "leftover.log"), "w", encoding="utf-8") as fh:
        fh.write(f"Alice contact is {SECRET}")

    res = store.forget(ids=[rid], request_id="DSAR-1", verify_residue_in=d)
    assert res["residue"]["ok"] is False, "the README says False if it survived anywhere under that root"


def test_snippet_3_bind_content_and_explain_growth():
    store = Inspeximus(path=os.path.join(tempfile.mkdtemp(), "m.json"), receipts=True)
    rid = store.remember("Revenue is 100M", mtype="semantic")
    store.flush()
    prior_anchor = store.anchor()
    witnessed = build_bundle(store)

    assert bind_content(witnessed, list(store.items))["ok"] is True
    next(x for x in store._items if x["id"] == rid)["text"] = "Revenue is 900M"
    store._save(force=True)
    assert bind_content(witnessed, list(store.items))["ok"] is False, \
        "the README says False if the content no longer matches"

    store.remember("one")
    store.remember("two")
    assert store.explain_growth(prior_anchor, writes=2)["ok"] is True


def test_the_readme_still_documents_these_commands():
    """If a capability is removed, the copy must not survive it -- that is how a README starts lying."""
    text = _readme()
    for claim in ("inspeximus residue", "verify_residue_in", "bind_content", "explain_growth"):
        assert claim in text, f"the README no longer mentions {claim}"


def test_the_readme_version_matches_the_package():
    import inspeximus
    assert f"v{inspeximus.__version__}" in _readme(), \
        "the README footer version drifted from the package version"


# ── every fenced example is a promise that the code is code ─────────────────────────────────────────
def _md_files():
    docs = os.path.join(ROOT, "docs")
    files = [README]
    if os.path.isdir(docs):
        files += [os.path.join(docs, f) for f in sorted(os.listdir(docs)) if f.endswith(".md")]
    return files


def test_every_fenced_python_block_parses():
    """A ```python block says "this is code". One in the README was not: `witnesses=[...pubkeys...]` is a
    syntax error, so anyone who copied that example got a traceback instead of a witness pool. Parsing is
    the floor -- it does not prove the example is correct, only that it is Python."""
    import ast
    import re

    failures = []
    for path in _md_files():
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        for m in re.finditer(r"```python\n(.*?)```", text, re.S):
            try:
                ast.parse(m.group(1))
            except SyntaxError as e:
                line = text[:m.start()].count("\n") + 1
                failures.append(f"{os.path.basename(path)}:{line} -> {e.msg}")
    assert not failures, "fenced python that does not parse: " + "; ".join(failures)


def test_every_documented_cli_invocation_names_a_real_subcommand():
    """Global flags are allowed before the subcommand (`inspeximus --receipts remember ...`), which is why
    a naive check false-alarmed on it; prose lines that merely begin with the word are skipped."""
    import re

    with open(os.path.join(ROOT, "inspeximus", "cli.py"), encoding="utf-8") as fh:
        cli_src = fh.read()
    subs = set(re.findall(r'sub\.add_parser\(\s*"([\w-]+)"', cli_src))
    assert subs, "no subcommands found; the extraction, not the docs, is broken"

    bad = []
    for path in _md_files():
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        # Match EVERY fence and read its language tag, then keep the shell-ish ones. A pattern that only
        # matched ```bash did not recognise ```python as an opener, so that block's CLOSING fence became
        # an opener and swallowed the prose after it -- the scanner manufacturing its own finding, which
        # is exactly the failure this file exists to prevent.
        for block in re.finditer(r"```([a-zA-Z]*)\n(.*?)```", text, re.S):
            if block.group(1).lower() not in ("", "bash", "sh", "console", "shell", "text"):
                continue
            for line in block.group(2).splitlines():
                m = re.match(r"\s*(?:\$\s*)?inspeximus((?:\s+--[\w-]+(?:[= ]\S+)?)*)\s+([\w-]+)\b", line)
                if m and m.group(2) not in subs:
                    bad.append(f"{os.path.basename(path)}: {line.strip()[:60]}")
    assert not bad, "documented CLI commands with no such subcommand: " + "; ".join(bad)
