"""AUDIT-B 3.16.3 on AUDIT-A F-5 (fixed in 3.16.2, faff546d): a long-lived handle that opened before the
background archive ran writes after it, and the archived rows stay out of the hot store.

Why this test belongs to the policy. Before the F-5 fix a handle that had loaded the store re-added every row the
archive moved on its next save (the merge brings back rows missing on disk unless they are tombstoned, and a move
leaves no tombstone). A manual `--archive` made that a rare accident; a size-triggered run starts by itself, so
every MCP server that was already open would re-inflate the store on its next write. The handle below is the MCP
server's shape: opened once, kept, written to later.
"""
import json
import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import inspeximus.claude_code as cc  # noqa: E402
import inspeximus.core as core  # noqa: E402
from inspeximus import archive  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

DAY = 86400.0


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path_factory):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path_factory.mktemp("key-home")))
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("PYTHONPATH", ROOT)



def _user_config(config):
    path = cc.user_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(config if isinstance(config, str) else json.dumps(config))

def _project(tmp_path, n=60, config=None):
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    st = proj / ".inspeximus"
    st.mkdir()
    if config is not None:                                    # the USER's config: a repository's is ignored (F-9)
        _user_config(config)
    p = str(st / "coding_memory.json")
    m = Inspeximus(p)
    now = time.time()
    ids = []
    with pytest.MonkeyPatch.context() as mp:                  # restored on exit, whatever happens inside
        for i in range(n):
            mp.setattr(core.time, "time", lambda i=i: now - 40 * DAY + i)
            ids.append(m.remember(f"ran: export number {i} of the batch", key=f"cmd:{i:04d}", tags=["bash"],
                                  mtype="episodic"))
    m.flush()
    return str(proj), p, ids


def _state(p):
    seg = {i for s in archive.listed_segments(p).values() for i in s["ids"]}
    hot = {r["id"] for r in Inspeximus(p)._items}
    return hot, seg


def test_a_handle_opened_before_the_maintain_run_does_not_write_the_moved_rows_back(tmp_path):
    proj, p, ids = _project(tmp_path)
    mcp_handle = Inspeximus(p)                       # the MCP server: opened once, kept
    assert len(mcp_handle._items) == 60
    out = cc.maintain_store(proj, older_than=7, classes=("cmd",), allow_git_tracked=True)
    assert out["archive"].get("applied"), out
    hot, seg = _state(p)
    assert seg == set(ids) and not hot, "control: the run moved every old capture"
    note = mcp_handle.remember("a decision recorded through the long-lived handle", key="decision::later")
    mcp_handle.flush()
    hot, seg = _state(p)
    assert not (hot & seg), "the handle wrote archived rows back into the hot store"
    assert hot == {note} and seg == set(ids), "a row is missing, doubled or misplaced"


def test_the_same_through_the_detached_run_the_policy_starts(tmp_path, monkeypatch):
    proj, p, ids = _project(tmp_path, config={"archive": {"auto": True, "trigger_mb": 0.0001,
                                                          "allow_git_tracked": True}})
    mcp_handle = Inspeximus(p)
    monkeypatch.chdir(proj)
    assert cc.maybe_archive_in_background(proj) == "started"
    end = time.time() + 90
    while time.time() < end and not archive.listed_segments(p):
        time.sleep(0.5)
    assert archive.listed_segments(p), "the detached run archived nothing within 90 s"
    log = cc._archive_state_path(p, ".log")
    while time.time() < end and not (os.path.exists(log) and '"stamp_guards"' in open(log, encoding="utf-8").read()):
        time.sleep(0.5)
    time.sleep(1.0)
    note = mcp_handle.remember("written after the background run", key="decision::after")
    mcp_handle.flush()
    hot, seg = _state(p)
    assert not (hot & seg), "the handle wrote archived rows back into the hot store"
    assert hot == {note} and seg == set(ids)
