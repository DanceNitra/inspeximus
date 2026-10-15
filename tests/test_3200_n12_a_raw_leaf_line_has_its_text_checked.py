"""3.20, AUDIT-A N-12 (Low-Medium, repository tooling): a line that carries its leaf as leaf_b64 has its text checked.

THE DEFECT. After N-2, verify.py tied a line with no `entry` but a `leaf_b64` by hashing the raw leaf, and only counted
it. So a line rewritten to drop its `entry` and carry the real leaf bytes (public) as `leaf_b64`, beside a substituted
text under the real digest in payloads.json, passed: "checked: 4 entries are the leaves the tree hashed", one text
fewer checked, exit 0 (AUDIT-A, dr/ss5/f7_verify.py "leaf_b64 line", on d0e5b2ed).

WHAT IS PINNED HERE. A raw leaf that decodes to a JSON object is read as the entry, and its payload is checked, so the
substituted text fails. A raw leaf that is not a record is reported as naming no text to check.
"""
import base64
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


def _verify(site):
    r = subprocess.run([sys.executable, "verify.py"], cwd=str(site), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _rows(site):
    return [json.loads(ln) for ln in (site / "log.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]


def _write_rows(site, rows):
    with open(str(site / "log.jsonl"), "w", encoding="utf-8", newline="\n") as fh:      # newline= needs 3.10 on Path
        fh.write("".join(json.dumps(r) + "\n" for r in rows))


def _as_raw_leaf(site, index):
    rows = _rows(site)
    leaf = (site / "entries" / ("%d.leaf.json" % index)).read_bytes()
    entry = rows[index].pop("entry")
    rows[index]["leaf_b64"] = base64.b64encode(leaf).decode("ascii")
    _write_rows(site, rows)
    return entry


def _publish_texts(site, n):
    """payloads.json as the seed tool writes it: every entry's text, looked up by its digest."""
    texts = ["entry %d" % i for i in range(n)]
    (site / "payloads.json").write_text(json.dumps(
        {"by_digest": {hashlib.sha256(t.encode("utf-8")).hexdigest(): t for t in texts}}), encoding="utf-8")


def _substitute_text(site, entry, text):
    path = site / "payloads.json"
    payloads = json.loads(path.read_text(encoding="utf-8"))
    assert entry["payload_sha256"] in payloads["by_digest"], "CONTROL: the site publishes this entry's text"
    payloads["by_digest"][entry["payload_sha256"]] = text
    path.write_text(json.dumps(payloads), encoding="utf-8")


def test_control_a_raw_leaf_line_with_its_real_text_verifies(tmp_path):
    site, _ = _site(tmp_path, 3)
    _publish_texts(site, 3)
    _as_raw_leaf(site, 2)
    code, out = _verify(site)
    assert code == 0 and "checked: 4 entries are the leaves the tree hashed." in out, out
    assert "checked: 3 published entry texts hash to what the log recorded." in out, out


def test_a_raw_leaf_line_beside_a_substituted_text_fails(tmp_path):
    site, _ = _site(tmp_path, 3)
    _publish_texts(site, 3)
    entry = _as_raw_leaf(site, 2)
    _substitute_text(site, entry, "a text nobody registered")
    code, out = _verify(site)
    assert code == 1 and "does not hash to what the log recorded" in out, out
