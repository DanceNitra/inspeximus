"""3.20, AUDIT-A N-2 (Low-Medium, repository tooling): deleting a leaf file does not get an edited log past verify.py.

THE DEFECT. The F7 fix tied each log.jsonl line to the tree through entries/<n>.leaf.json, and a line with no leaf file
was reported as "not checked" with exit 0. So the F7 edit (a record and its payload digest changed on the line) passed
again once the edited entry's leaf file was deleted as well (AUDIT-A, dr/ss5/f7_verify.py, on dcce3660).

WHAT IS PINNED HERE. The leaf is the canonical JSON of the entry, so the verifier re-encodes the record on the line and
requires SHA-256(0x00 || it) to equal leaf_hash. The edit fails with or without the leaf files. A line with no record
and no raw leaf cannot be tied, and the run fails unless --allow-untied is given.
"""
import hashlib
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
pytest.importorskip("cryptography")
from test_a_log_a_stranger_can_check_without_us import _site  # noqa: E402


def _verify(site, *args):
    r = subprocess.run([sys.executable, "verify.py"] + list(args), cwd=str(site), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _rewrite_line(site, index, fn):
    path = site / "log.jsonl"
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    fn(rows[index])
    with open(str(path), "w", encoding="utf-8", newline="\n") as fh:      # newline= needs 3.10 on Path
        fh.write("".join(json.dumps(r) + "\n" for r in rows))


def _drop_leaf_files(site, indexes=None):
    entries = site / "entries"
    for name in os.listdir(str(entries)):
        if name.endswith(".leaf.json") and (indexes is None or int(name.split(".")[0]) in indexes):
            os.remove(str(entries / name))


def test_control_an_honest_log_with_every_leaf_file_deleted_verifies(tmp_path):
    site, _ = _site(tmp_path, 3)
    _drop_leaf_files(site)
    assert not [n for n in os.listdir(str(site / "entries")) if n.endswith(".leaf.json")], "CONTROL: files removed"
    code, out = _verify(site)
    assert code == 0 and "checked: 4 entries are the leaves the tree hashed." in out, out


def test_an_edited_record_with_its_leaf_file_deleted_fails(tmp_path):
    site, _ = _site(tmp_path, 3)
    _rewrite_line(site, 2, lambda r: r["entry"].update(subject="s:forged"))
    _drop_leaf_files(site, {2})
    code, out = _verify(site)
    assert code == 1 and "entry 2: the record in log.jsonl is not the leaf the tree hashed" in out, out


def test_an_edited_payload_digest_with_every_leaf_file_deleted_fails(tmp_path):
    site, _ = _site(tmp_path, 3)
    forged = hashlib.sha256(b"forged").hexdigest()
    _rewrite_line(site, 2, lambda r: r["entry"].update(payload_sha256=forged))
    (site / "payloads.json").write_text(json.dumps({"by_digest": {forged: "forged"}}), encoding="utf-8")
    _drop_leaf_files(site)
    code, out = _verify(site)
    assert code == 1 and "not the leaf the tree hashed" in out, out


def test_a_line_with_no_record_and_no_leaf_fails_unless_allowed(tmp_path):
    site, _ = _site(tmp_path, 3)
    _rewrite_line(site, 3, lambda r: r.pop("entry"))
    code, out = _verify(site)
    assert code == 1 and "1 entries carry no record and no leaf" in out, out
    code, out = _verify(site, "--allow-untied")
    assert code == 0 and "not checked: 1 entries carry no record and no leaf" in out, out
    assert "checked: 3 entries are the leaves the tree hashed." in out, out
