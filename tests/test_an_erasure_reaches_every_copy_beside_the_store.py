"""An erasure reaches the copies our own tools make, and names the ones it cannot vouch for (audit A-12).

`claude_code.merge_store` and `merge_fragments` copy the whole store to `<store>.bak-merge-<time>` before
they merge, and SessionStart itself suggests running them. After `forget_subject` the erased text sat in
that copy and neither the result nor `erasure_certificate` named it. The conversion backup
(`.pre-rows.bak`) already went with the first erasure; the merge backup now goes the same way.

A copy nothing here accounts for (an older version's `.bak-corrupt-...`, `.torn-...`, a user's copy) is
read by `forget` for the values it erased, while it still has them, and named by the certificate, which
does not verify around it. Sidecars are never named.
"""
import glob
import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.claude_code as cc
from inspeximus import Inspeximus, _surface

ERASED = "12 Elm Street"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in list(os.environ):
        if k.startswith("INSPEXIMUS_") and k != "INSPEXIMUS_NO_UPDATE_CHECK":
            monkeypatch.delenv(k, raising=False)


def _alice(path, **kw):
    m = Inspeximus(path, receipts=True, **kw)
    m.remember(f"Alice lives at {ERASED}", key="alice::addr", object="12 Elm", source={"doc": "crm/alice"})
    m.remember("the deploy target is staging", key="deploy", object="staging")
    m.flush()
    return m


def _holders(path):
    return sorted(os.path.basename(p) for p in glob.glob(path + "*")
                  if os.path.isfile(p) and ERASED.encode() in open(p, "rb").read())


def test_an_erasure_reaches_the_backup_merge_store_made(tmp_path):
    proj = str(tmp_path / "proj")
    subprocess.run(["git", "init", "-q", proj], check=True)
    dest = _surface.coding_store_path(proj)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    m = _surface.open_store(dest)
    m.remember(f"Alice lives at {ERASED}", key="alice::addr", object="12 Elm", source={"doc": "crm/alice"})
    m.flush()
    src = str(tmp_path / "old_mcp_store.json")
    o = _surface.open_store(src)
    o.remember("an old MCP decision", key="decision::x", object="x")
    o.flush()
    rep = cc.merge_store(src, cwd=proj, apply=True)
    if not (rep.get("applied") and rep.get("backup") and os.path.exists(rep["backup"])):
        pytest.fail(f"control: merge_store made no backup copy: {rep}")
    m = _surface.open_store(dest)
    out = m.forget_subject("crm/alice")
    m.flush()
    if not out.get("ids"):
        pytest.fail("control: forget_subject erased nothing")
    assert _holders(dest) == [], "the erased text survives in the merge backup"
    cert = m.erasure_certificate()
    assert any(b["state"] == "removed" and b["path"] == rep["backup"] for b in cert["merge_backups"]), cert


def test_an_unknown_copy_holding_the_value_is_named_by_both_reports(tmp_path):
    path = str(tmp_path / "m.json")
    m = _alice(path)
    other = path + ".torn-20260815-185823"
    shutil.copy(path, other)
    if ERASED.encode() not in open(other, "rb").read():
        pytest.fail("control: the copy does not hold the value")
    out = m.forget_subject("crm/alice")
    rs = out["residue_in_store"]
    assert rs["ok"] is False and {"path": os.path.basename(other), "kind": "SIBLING_COPY"} in rs["findings"], rs
    cert = m.erasure_certificate()
    assert cert["self_check"]["verified"] is False
    named = [f for f in cert["siblings_not_reached"] if f["name"] == os.path.basename(other)]
    assert named and named[0]["bytes"] > 0 and named[0]["modified"], cert["siblings_not_reached"]
    assert os.path.exists(other), "a copy the library did not make was deleted; that is the owner's call"


def test_a_copy_without_the_value_is_clean_for_forget_but_still_named(tmp_path):
    path = str(tmp_path / "m.json")
    m = _alice(path)
    with open(path + ".notes", "w") as fh:
        fh.write("nothing about anyone")
    out = m.forget_subject("crm/alice")
    assert out["residue_in_store"]["ok"] is True, out["residue_in_store"]
    cert = m.erasure_certificate()
    assert [f["name"] for f in cert["siblings_not_reached"]] == ["m.json.notes"]
    assert cert["self_check"]["verified"] is False, "a file the certificate cannot read about was vouched for"


def test_the_stores_own_sidecars_are_never_named(tmp_path):
    path = str(tmp_path / "m.json")
    m = _alice(path)
    m.forget_subject("crm/alice")
    for extra in (".embedid", ".salt", ".irrev.json", ".cusum.json", ".partitions.json", ".actions.json",
                  ".actions.json.archive.0001.json", ".actions.json.salt", ".objections.json",
                  ".receipts.json.ab12cd34.tmp", ".actions.json.tmp.4242", ".lock"):
        if not os.path.exists(path + extra):
            open(path + extra, "w").close()
    if not [n for n in os.listdir(tmp_path) if n.startswith("m.json.")]:
        pytest.fail("control: the store has no sidecars, so nothing was classified")
    cert = m.erasure_certificate()
    assert cert["siblings_not_reached"] == [], cert["siblings_not_reached"]
    assert cert["self_check"]["verified"] is True, cert["self_check"]


def test_a_conversion_backup_kept_on_purpose_is_named(tmp_path, monkeypatch):
    path = str(tmp_path / "m.json")
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    _alice(path)
    monkeypatch.delenv("INSPEXIMUS_STORE_FORMAT")
    m = Inspeximus(path, receipts=True)                   # converts to rows, leaving .pre-rows.bak
    if not os.path.exists(path + ".pre-rows.bak"):
        pytest.fail("control: the conversion left no backup")
    monkeypatch.setenv("INSPEXIMUS_KEEP_CONVERSION_BACKUP", "1")
    out = m.forget_subject("crm/alice")
    assert out["residue_in_store"]["ok"] is False, "a kept copy holding the erased value was called clean"
    cert = m.erasure_certificate()
    assert cert["conversion_backup"]["state"] == "kept"
    assert "m.json.pre-rows.bak" in [f["name"] for f in cert["siblings_not_reached"]]
    assert cert["self_check"]["verified"] is False
