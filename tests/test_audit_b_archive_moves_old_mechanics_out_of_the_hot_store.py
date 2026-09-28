"""AUDIT-B B-25: old captured mechanics move out of the hot store into monthly segments, and nothing is lost.

On a copy of a 71,772-record project store, 89.5 % of the rows were the hook's `ran: ...` captures and every
prompt paid for all of them: 9.3 s per UserPromptSubmit. Archiving the captures older than 7 days through
`--archive --apply` moved 46,086 rows into three segments (3.4, 16.2 and 20.3 MB), and the hook printed
byte-identical output on five of five prompts and on SessionStart, in about 2 s instead of 6.

What is required here:
  * only named classes move, only past the cutoff, and a record that a kept record refers to stays;
  * the dry run writes nothing; segments split by month and by size; the log is a verifiable chain;
  * `recall(include_archive=True)` returns what recall returned before the move, in the same order, and a
    pooled read can never write an archived row back into the hot file;
  * the hook's stdout is byte-identical before and after, for UserPromptSubmit and SessionStart;
  * an interrupted run is finished, never duplicated; segments in a git work tree need the flag;
  * stores this step cannot yet account for (receipts, encryption, the JSON pin) are refused.
"""
import hashlib
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import inspeximus.core as core  # noqa: E402
from inspeximus import archive  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

DAY = 86400.0
T0 = 1790000000.0            # the frozen "now"


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)


def _write(path, rows, monkeypatch):
    """rows: (age_days, text, kwargs). Written in order, each at T0 - age plus its index in seconds."""
    m = Inspeximus(str(path))
    ids = []
    for i, (age, text, kw) in enumerate(rows):
        monkeypatch.setattr(core.time, "time", lambda age=age, i=i: T0 - age * DAY + i)
        ids.append(m.remember(text, **kw))
    m.flush()
    monkeypatch.setattr(core.time, "time", lambda: T0)
    return ids


def _cmd(n):
    return {"key": f"cmd:{hashlib.sha1(str(n).encode()).hexdigest()[:10]}", "tags": ["bash"], "mtype": "episodic"}


def _mixed_rows():
    rows = []
    for i in range(40):
        rows.append((40 if i % 2 == 0 else 2, f"ran: git status in repo number {i} with flag alpha", _cmd(i)))
    rows.append((60, "we decided the deploy freezes on fridays", {"tags": ["decision"]}))
    rows.append((60, "src/app.py :: current state -> print(1)",
                 {"key": "file:src/app.py", "tags": ["file", "edit"], "mtype": "semantic"}))
    rows.append((60, "SESSION DIGEST 3 -- what changed", {"key": "session:3:digest", "tags": ["session-digest"]}))
    rows.append((60, "SESSION 3 OPEN", {"key": "session:3:open", "tags": ["session-boundary"]}))
    return rows


def _files(d):
    return sorted(os.listdir(d))


def test_the_dry_run_writes_nothing(tmp_path, monkeypatch):
    p = tmp_path / "coding_memory.json"
    _write(p, _mixed_rows(), monkeypatch)
    before = (_files(tmp_path), p.read_bytes())
    r = archive.plan(Inspeximus(str(p)), 7, now=T0)
    assert r["moving"] == 20 and r["hot_rows_after"] == r["hot_rows_before"] - 20
    assert (_files(tmp_path), p.read_bytes()) == before


def test_only_named_classes_move_and_only_past_the_cutoff(tmp_path, monkeypatch):
    p = tmp_path / "coding_memory.json"
    ids = _write(p, _mixed_rows(), monkeypatch)
    archive.apply(Inspeximus(str(p)), 7, now=T0)
    hot = {r["id"] for r in Inspeximus(str(p))._items}
    old_cmd = {ids[i] for i in range(40) if i % 2 == 0}
    assert not (old_cmd & hot), "every capture older than the window left the hot store"
    assert {ids[i] for i in range(40) if i % 2} <= hot, "captures inside the window stay"
    assert set(ids[40:]) <= hot, "the decision, the file state, the digest and the boundary never move"
    moved = {i for seg in archive.listed_segments(p).values() for i in seg["ids"]}
    assert moved == old_cmd


def test_a_capture_that_a_kept_record_refers_to_stays(tmp_path, monkeypatch):
    p = tmp_path / "coding_memory.json"
    ids = _write(p, _mixed_rows(), monkeypatch)
    m = Inspeximus(str(p))
    note = m.remember("a note that links an old capture", tags=["note"])
    next(r for r in m._items if r["id"] == note)["links"] = [ids[0]]
    m._dirty = True
    m.flush()
    r = archive.apply(Inspeximus(str(p)), 7, now=T0)
    assert r["held_back_by_reference"] == 1
    assert ids[0] in {x["id"] for x in Inspeximus(str(p))._items}


def test_segments_split_by_month_and_by_size_and_hold_the_rows_verbatim(tmp_path, monkeypatch):
    p = tmp_path / "coding_memory.json"
    rows = [(age, f"ran: build step {i} for month {age}", _cmd(i)) for i, age in
            enumerate([40] * 12 + [75] * 12)]
    _write(p, rows, monkeypatch)
    m = Inspeximus(str(p))
    before = {r["id"]: json.dumps(dict(r), sort_keys=True, default=str) for r in m._items}
    r = archive.apply(m, 7, now=T0, cap_bytes=1500)
    names = [w["file"] for w in r["written"]]
    months = {n.split(".archive-")[1][:7] for n in names}
    assert len(months) == 2 and len(names) > 2, names
    from inspeximus import sqlite_store as rows_mod
    seen = {}
    for n in names:
        for rec in rows_mod.load(tmp_path / n):
            seen[rec["id"]] = json.dumps(rec, sort_keys=True, default=str)
    assert seen == before, "every row is in exactly one segment, byte for byte"


def test_the_log_is_a_chain_that_notices_an_edit(tmp_path, monkeypatch):
    p = tmp_path / "coding_memory.json"
    _write(p, _mixed_rows(), monkeypatch)
    archive.apply(Inspeximus(str(p)), 7, now=T0, cap_bytes=600)
    entries = archive.read_log(p)
    assert len(entries) > 1 and archive.verify_log(entries)[0]
    entries[0]["count"] += 1
    assert not archive.verify_log(entries)[0]


def test_recall_with_the_archive_returns_what_recall_returned_before(tmp_path, monkeypatch):
    """A capture held back in the hot store (a note links it) ties with archived captures. Recall orders
    the tie by recency, so the pooled read returns the order the store gave before the move. Rows that
    tie AND share one `ts` fall back to list position, which the move does not keep (see pooled())."""
    p = tmp_path / "coding_memory.json"
    ids = _write(p, _mixed_rows(), monkeypatch)
    m = Inspeximus(str(p))
    note = m.remember("a note that links one old capture", tags=["note"])
    next(r for r in m._items if r["id"] == note)["links"] = [ids[20]]
    m._dirty = True
    m.flush()
    q = "git status repo flag alpha"
    before = [(h["id"], h["text"]) for h in Inspeximus(str(p)).recall(q, k=45)]
    assert archive.apply(Inspeximus(str(p)), 7, now=T0)["held_back_by_reference"] == 1
    hot_only = [h["id"] for h in Inspeximus(str(p)).recall(q, k=45)]
    after = [(h["id"], h["text"]) for h in Inspeximus(str(p)).recall(q, k=45, include_archive=True)]
    assert after == before
    assert hot_only != [i for i, _ in before], "the control: without the archive, recall sees less"


def test_a_pooled_read_never_writes_an_archived_row_into_the_hot_file(tmp_path, monkeypatch):
    p = tmp_path / "coding_memory.json"
    _write(p, _mixed_rows(), monkeypatch)
    archive.apply(Inspeximus(str(p)), 7, now=T0)
    n = len(Inspeximus(str(p))._items)
    m = Inspeximus(str(p))
    m.recall("git status repo flag alpha", k=20, include_archive=True, reinforce=True)
    m.flush()
    assert len(Inspeximus(str(p))._items) == n and len(m._items) == n


def test_a_save_inside_a_pooled_read_writes_no_archived_row(tmp_path, monkeypatch):
    p = tmp_path / "coding_memory.json"
    _write(p, _mixed_rows(), monkeypatch)
    archive.apply(Inspeximus(str(p)), 7, now=T0)
    n = len(Inspeximus(str(p))._items)
    m = Inspeximus(str(p))
    with archive.pooled(m):
        assert len(m._items) == 44, "the control: the pool holds the archived rows"
        m._dirty = True
        m._save(force=True)
    assert len(Inspeximus(str(p))._items) == n


def test_an_interrupted_run_is_finished_not_copied_twice(tmp_path, monkeypatch):
    p = tmp_path / "coding_memory.json"
    _write(p, _mixed_rows(), monkeypatch)
    m = Inspeximus(str(p))
    # the crash comes after the segment and the log, at the hot store's save (this handle only)
    monkeypatch.setattr(m, "_save", lambda force=False: (_ for _ in ()).throw(SystemExit("crash")))
    with pytest.raises(SystemExit):
        archive.apply(m, 7, now=T0)
    assert len(Inspeximus(str(p))._items) == 44, "the crash left the rows in the hot store"
    r = archive.apply(Inspeximus(str(p)), 7, now=T0)
    assert r["applied"] and r["written"] == [], "no new segment: the logged one is finished"
    assert len(Inspeximus(str(p))._items) == 24
    assert len(archive.listed_segments(p)) == 1


def test_a_segment_written_before_a_crash_is_reused(tmp_path, monkeypatch):
    p = tmp_path / "coding_memory.json"
    _write(p, _mixed_rows(), monkeypatch)
    monkeypatch.setattr(archive, "_write_log", lambda *a, **k: (_ for _ in ()).throw(SystemExit("crash")))
    with pytest.raises(SystemExit):
        archive.apply(Inspeximus(str(p)), 7, now=T0)
    monkeypatch.undo()
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setattr(core.time, "time", lambda: T0)
    segs = [f for f in _files(tmp_path) if ".archive-" in f]
    assert len(segs) == 1 and not archive.read_log(p)
    r = archive.apply(Inspeximus(str(p)), 7, now=T0)
    assert [w["file"] for w in r["written"]] == segs


def test_segments_inside_a_git_work_tree_need_the_flag(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    p = repo / "coding_memory.json"
    _write(p, _mixed_rows(), monkeypatch)
    with pytest.raises(archive.ArchiveRefused, match="git work tree"):
        archive.apply(Inspeximus(str(p)), 7, now=T0)
    assert not [f for f in _files(repo) if "archive" in f], "the refusal wrote nothing"
    r = archive.apply(Inspeximus(str(p)), 7, now=T0, allow_git_tracked=True)
    assert r["applied"] and "cannot reach git history" in r["git_warning"]
    outside = tmp_path / "plain"
    outside.mkdir()
    q = outside / "coding_memory.json"
    _write(q, _mixed_rows(), monkeypatch)
    r2 = archive.apply(Inspeximus(str(q)), 7, now=T0)
    assert r2["applied"] and r2["git_warning"] is None, "the control: no repository, no flag needed"


def test_stores_this_step_cannot_account_for_are_refused(tmp_path, monkeypatch):
    p = tmp_path / "receipted.json"
    m = Inspeximus(str(p), receipts=True)
    m.remember("ran: ls", key="cmd:abc", tags=["bash"])
    m.flush()
    with pytest.raises(archive.ArchiveRefused, match="receipts"):
        archive.apply(Inspeximus(str(p), receipts=True), 0, now=T0 + DAY)
    monkeypatch.setenv("INSPEXIMUS_STORE_FORMAT", "json")
    q = tmp_path / "pinned.json"
    m = Inspeximus(str(q))
    m.remember("ran: ls", key="cmd:abc", tags=["bash"])
    m.flush()
    with pytest.raises(archive.ArchiveRefused, match="row store"):
        archive.apply(Inspeximus(str(q)), 0, now=T0 + DAY)


def _hook(proj, home, ev):
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_") and k != "PYTHONPATH"}
    env.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(home), INSPEXIMUS_NO_UPDATE_CHECK="1",
               PYTHONPATH=ROOT)
    ev = dict(ev, cwd=str(proj).replace("\\", "/"), session_id="identity")
    return subprocess.run([sys.executable, "-m", "inspeximus.claude_code"],
                          input=json.dumps(ev), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(proj), env=env, timeout=300).stdout


def test_the_hook_prints_the_same_before_and_after_the_archive(tmp_path, monkeypatch):
    """Two copies of one store, one archived: a hook event writes (session boundaries), so running
    before and after on the same store would compare two different stores."""
    import shutil
    projs = {}
    for name in ("full", "archived"):
        proj = tmp_path / name
        (proj / ".git").mkdir(parents=True)
        (proj / ".inspeximus").mkdir()
        (tmp_path / (name + "-home")).mkdir()
        projs[name] = proj
    st = tmp_path / "src.json"
    rows = [(40, f"ran: git log --oneline on old branch {i}", _cmd(i)) for i in range(30)]
    rows += [(1, f"ran: deploy the release branch step {i}", _cmd(100 + i)) for i in range(6)]
    rows += [(50, "we decided the release branch deploys only after CI is green", {"tags": ["decision"]})]
    _write(st, rows, monkeypatch)
    for proj in projs.values():
        shutil.copy(st, proj / ".inspeximus" / "coding_memory.json")
    r = archive.apply(Inspeximus(str(projs["archived"] / ".inspeximus" / "coding_memory.json")), 7, now=T0,
                      allow_git_tracked=True)
    assert r["moving"] == 30
    events = [{"hook_event_name": "UserPromptSubmit", "prompt": "deploy the release branch"},
              {"hook_event_name": "UserPromptSubmit", "prompt": "when does the release branch deploy after green checks"},
              {"hook_event_name": "SessionStart", "source": "startup"}]
    out = {name: [_hook(proj, tmp_path / (name + "-home"), ev) for ev in events] for name, proj in projs.items()}
    assert out["archived"] == out["full"]
    assert all(out["full"]), "the control: the hook printed something to compare"


def test_the_cli_is_a_dry_run_until_apply(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_") and k != "PYTHONPATH"}
    env.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(home), INSPEXIMUS_NO_UPDATE_CHECK="1",
               PYTHONPATH=ROOT)
    st = proj / ".inspeximus"
    st.mkdir()
    m = Inspeximus(str(st / "coding_memory.json"))
    m.remember("ran: ls -la", key="cmd:0000000001", tags=["bash"])
    m.flush()
    run = lambda *a: subprocess.run([sys.executable, "-m", "inspeximus.claude_code", "--archive", *a],
                                    capture_output=True, text=True, cwd=str(proj), env=env, timeout=120)
    dry = run("--older-than", "0")
    assert dry.returncode == 0 and "Dry run" in dry.stdout
    assert not [f for f in os.listdir(st) if "archive" in f]
    assert run().returncode == 2, "no window, no archive"
    done = run("--older-than", "0", "--apply")
    assert done.returncode == 0 and [f for f in os.listdir(st) if ".archive-" in f]
