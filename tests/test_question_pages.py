"""The pages under docs/questions/ are executed here, and what they say the example prints is compared
line for line with what it does print.

Each page answers one question a developer searches for, with one ```python example followed by a
```text block holding its output. A page whose example no longer prints what the page says it prints
states something false, so the example runs in an empty directory, the way a reader runs it, and its
stdout must equal the documented block exactly. There are no wildcards. The examples print only values
that are the same on every run (counts, verdicts, text), which is why they name record ids instead of
printing them.

The pages were commissioned with three more rules, and the ones a program can check are checked here:

  * the title is the question;
  * there is a limits section;
  * a number in the prose is one the example itself produces (it appears in the code or the output), or
    a legal citation. A number with no source on the page is the kind of claim these pages must not make.

Each check has a control below that feeds it a page it must reject, because a check that cannot fail
has measured nothing.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PAGES_DIR = ROOT / "docs" / "questions"

#: Named, not globbed. A glob over a directory that was renamed or emptied runs zero cases and passes.
PAGES = [
    "delete-a-users-data-and-prove-it.md",
    "prove-what-the-agent-knew-at-a-given-time.md",
    "why-a-corrected-fact-comes-back.md",
    "eu-ai-act-article-12-logs.md",
    "detect-edits-to-the-memory-file.md",
]

LIMITS_HEADING = "## Limits: what this does not do"

_PY = re.compile(r"^```python\n(.*?)^```", re.S | re.M)
_OUT = re.compile(r"^```text\n(.*?)^```", re.S | re.M)
_FENCE = re.compile(r"^```.*?^```", re.S | re.M)

#: Legal citations are references, not measurements: "Article 12(3)(d)", "Art. 26(5)",
#: "Regulation (EU) 2024/1689", "point 1(a)", "paragraph 2".
_CITATION = re.compile(
    r"\b(?:Articles?|Art\.)\s*\d+(?:\(\d+\))*(?:\s*\([a-z]\))*"
    r"|Regulation \(EU\) \d{4}/\d+"
    r"|\b[Pp]oints?\s+\d+\s*(?:\([a-z]\))?"
    r"|\b[Pp]aragraphs?\s+\d+(?:\s*\([a-z]\))?")


def _read(name: str) -> str:
    return (PAGES_DIR / name).read_text(encoding="utf-8")


def _example(text: str) -> tuple[str, str]:
    """The page's one example and its one documented output. More than one of either is refused:
    the harness would have to guess which output belongs to which code."""
    code, out = _PY.findall(text), _OUT.findall(text)
    assert len(code) == 1, f"expected exactly one ```python block, found {len(code)}"
    assert len(out) == 1, f"expected exactly one ```text output block, found {len(out)}"
    assert text.index(code[0]) < text.index(out[0]), "the output block must follow the example"
    return code[0], out[0]


def _prose(text: str) -> str:
    """The page without code, output, quotations of legal text, HTML comments, link targets and
    list numbering: what is left is what the page itself asserts."""
    text = _FENCE.sub("", text)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith(">"))
    text = re.sub(r"\]\([^)]*\)", "]", text)
    text = re.sub(r"^\s*\d+\.\s", "", text, flags=re.M)
    return _CITATION.sub("", text)


def _unsourced_numbers(text: str) -> list[str]:
    code, out = _example(text)
    return sorted({n for n in re.findall(r"\d+", _prose(text)) if n not in code and n not in out})


def _run(code: str, work: Path) -> subprocess.CompletedProcess:
    """Run the example as a reader would: a fresh directory, this checkout on the path, and none of our
    INSPEXIMUS_* variables. The config home is redirected into the temp directory as well, because a
    store with receipts writes its head file there and a test must not write into the real one."""
    script = work / "example.py"
    script.write_text(code, encoding="utf-8")
    home = work / "config"
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_")}
    env.update({"PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8", "HOME": str(home),
                "XDG_CONFIG_HOME": str(home), "APPDATA": str(home), "INSPEXIMUS_KEY_HOME": str(home)})
    return subprocess.run([sys.executable, "-X", "utf8", str(script)], cwd=work, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=300)


def _mismatch(documented: str, printed: str) -> str:
    want = [ln.rstrip() for ln in documented.rstrip("\n").splitlines()]
    got = [ln.rstrip() for ln in printed.rstrip("\n").splitlines()]
    if want == got:
        return ""
    return "\n".join(difflib.unified_diff(want, got, "documented", "printed", lineterm=""))


def test_the_page_list_is_the_directory():
    """A page added to the directory without being added here would never run."""
    assert PAGES_DIR.is_dir(), f"{PAGES_DIR} is missing"
    on_disk = sorted(p.name for p in PAGES_DIR.glob("*.md"))
    assert on_disk == sorted(PAGES), f"pages on disk {on_disk} differ from the tested list {sorted(PAGES)}"


@pytest.mark.parametrize("name", PAGES)
def test_the_example_prints_what_the_page_says(name, tmp_path):
    code, documented = _example(_read(name))
    if "new_receipt_keypair" in code:
        pytest.importorskip("cryptography", reason=f"{name} signs, which needs inspeximus[crypto]")
    r = _run(code, tmp_path)
    assert r.returncode == 0, f"{name}: the example fails when run:\n{r.stdout}\n{r.stderr}"
    diff = _mismatch(documented, r.stdout)
    assert not diff, f"{name}: the documented output is not what the example prints:\n{diff}"


@pytest.mark.parametrize("name", PAGES)
def test_the_title_is_the_question_and_there_is_a_limits_section(name):
    text = _read(name)
    title = text.splitlines()[0]
    assert title.startswith("# ") and title.endswith("?"), f"{name}: title is not a question: {title!r}"
    assert "## Short answer" in text, f"{name}: no short answer section"
    assert LIMITS_HEADING in text, f"{name}: no {LIMITS_HEADING!r} section"
    limits = text.split(LIMITS_HEADING, 1)[1].split("\n## ", 1)[0]
    assert limits.count("\n- ") >= 2, f"{name}: the limits section lists fewer than two limits"


@pytest.mark.parametrize("name", PAGES)
def test_every_number_in_the_prose_comes_from_the_example(name):
    assert _unsourced_numbers(_read(name)) == [], (
        f"{name}: these numbers appear in the prose but not in the example or its output")


def test_the_ai_act_page_says_what_inspeximus_is_not():
    """Asked for in plain words, so checked in plain words."""
    assert "inspeximus is a component, not a compliance guarantee" in _read("eu-ai-act-article-12-logs.md")


# ── controls: each check above must be able to fail ────────────────────────────────────────────────
_CONTROL = """# Is this a control?

## Short answer

It prints 1 + 1, and the page claims 97 as well.

```python
print(1 + 1)
```

```text
3
```

## Limits: what this does not do

- one
- two
"""


def test_control_a_wrong_documented_output_is_caught(tmp_path):
    code, documented = _example(_CONTROL)
    r = _run(code, tmp_path)
    assert r.returncode == 0 and r.stdout.strip() == "2"
    assert _mismatch(documented, r.stdout), "the comparison accepted a wrong documented output"


def test_control_an_unsourced_number_is_caught():
    assert _unsourced_numbers(_CONTROL) == ["97"]


def test_control_a_citation_is_not_a_number_and_a_bare_number_is():
    assert re.findall(r"\d+", _prose("Article 12(3)(d) and Regulation (EU) 2024/1689, point 1(a)")) == []
    assert re.findall(r"\d+", _prose("a 42 percent drop")) == ["42"]


# ── the limits the pages state, pinned ─────────────────────────────────────────────────────────────
# A limit is a factual claim too. The ones the examples do not already show are executed here, so a
# page cannot keep describing a gap the library has since closed.
@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """A temp working directory and a temp config home, so head files stay out of the real one."""
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    for k in ("HOME", "XDG_CONFIG_HOME", "APPDATA", "INSPEXIMUS_KEY_HOME"):
        monkeypatch.setenv(k, str(tmp_path / "config"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _signed_store(path):
    pytest.importorskip("cryptography")
    from inspeximus import Inspeximus, new_receipt_keypair
    sk, pk = new_receipt_keypair()
    return Inspeximus(path, receipts=True, receipt_key=sk, receipt_pubkey=pk), pk


def test_limit_erasure_absence_is_tied_to_a_store_only_with_store_receipts(isolated):
    """delete-a-users-data-and-prove-it.md: without `store_receipts`, `store_bound` is None and the
    certificate also verifies against a file it was never issued from."""
    from inspeximus import Inspeximus, verify_erasure_certificate
    m, pk = _signed_store("memory.json")
    m.remember("Alice prefers email", source={"doc": "user:alice"})
    m.forget_subject("user:alice", request_id="R1")
    cert = m.erasure_certificate(request_id="R1")
    m.flush()
    other = Inspeximus("other.json", receipts=True)
    other.remember("An unrelated note")
    other.flush()
    unbound = verify_erasure_certificate(cert, store_path="other.json", expected_pubkey=pk)
    assert unbound["valid"] is True and unbound["checks"]["store_bound"] is None


def test_limit_what_it_knew_after_the_recalled_record_is_erased(isolated):
    """prove-what-the-agent-knew-at-a-given-time.md: the id stays in the entry, the text does not."""
    from inspeximus.actions import ActionLedger
    m, pk = _signed_store("memory.json")
    led = ActionLedger(m, actor="a")
    rid = m.remember("Dan likes coffee", source={"doc": "user:dan"})
    m.recall("Dan coffee", k=1)
    led.record("tool:x")
    m.forget_subject("user:dan", request_id="R1")
    knew = led.what_it_knew(0)
    assert knew["memory_state"]["recalled"] == [rid]
    assert [p["found"] for p in knew["recalled_now"]] == [False]


@pytest.mark.parametrize("switch", ["kwarg", "env"])
def test_limit_with_the_echo_guard_off_a_restated_value_becomes_current(isolated, monkeypatch, switch):
    """why-a-corrected-fact-comes-back.md: `echo_guard=False` or `INSPEXIMUS_ECHO_GUARD=0`."""
    from inspeximus import Inspeximus
    if switch == "env":
        monkeypatch.setenv("INSPEXIMUS_ECHO_GUARD", "0")
    m = Inspeximus("m.json", **({"echo_guard": False} if switch == "kwarg" else {}))
    for v in ("db-3", "db-7", "db-3"):
        m.remember(f"The staging database is {v}.internal", key="staging-db")
    assert [h["text"] for h in m.recall("which staging database", k=2)] == [
        "The staging database is db-3.internal"]


def test_limit_a_back_filled_write_is_the_one_retired(isolated):
    """why-a-corrected-fact-comes-back.md: an earlier `valid_from` does not replace a newer value."""
    from inspeximus import Inspeximus
    m = Inspeximus("m.json")
    m.remember("v2", key="k", valid_from="2024-02-01T00:00:00Z")
    m.remember("v1", key="k", valid_from="2024-01-01T00:00:00Z")
    assert [(r["text"], r["status"]) for r in m.history("k")] == [("v1", "superseded"), ("v2", "active")]


def test_limit_without_the_head_file_a_cut_tail_is_not_reported(isolated, monkeypatch):
    """detect-edits-to-the-memory-file.md: `INSPEXIMUS_HEADS=0`, then remove the newest record and its
    receipt. With the head on (the example on that page) the same cut is reported."""
    from inspeximus import Inspeximus
    monkeypatch.setenv("INSPEXIMUS_HEADS", "0")
    m, pk = _signed_store("memory.json")
    m.remember("first", key="a")
    last = m.remember("second", key="b")
    m.flush()
    con = sqlite3.connect("memory.json")
    con.execute("DELETE FROM records WHERE id = ?", (last,))
    con.commit()
    con.close()
    receipts = Path("memory.json.receipts.json")
    receipts.write_text(json.dumps(json.loads(receipts.read_text(encoding="utf-8"))[:-1]), encoding="utf-8")
    reopened = Inspeximus("memory.json", receipts=True, receipt_pubkey=pk)
    assert reopened.verify_writes(expected_pubkey=pk) == (True, [])
