"""AUDIT-B 3.16.3: the archive policy. A size check on the prompt path starts one detached `--maintain`.

The hook's cost is the hot file: 6.39 s at 71,772 rows, 1.33 s at 16,384, and rows return at about a
thousand captures a day. `--archive` was manual. The policy is off by default (it moves records); when on, the
prompt path pays one config read and one `stat`, and a detached run archives and stamps under the store's lock.
Also here: `file` is an archivable class, and a prompt hook that runs during `--apply` returns a normal answer.
"""
import json
import os
import subprocess
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
    monkeypatch.setenv("PYTHONPATH", ROOT)          # the detached run is a new process: it must import THIS tree


def _project(tmp_path, monkeypatch, n_cmd=40, n_file=10, age_days=40, config=None):
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    st = proj / ".inspeximus"
    st.mkdir()
    if config is not None:
        (st / "config.json").write_text(json.dumps(config), encoding="utf-8")
    p = str(st / "coding_memory.json")
    m = Inspeximus(p)
    now = time.time()
    ids = []
    with pytest.MonkeyPatch.context() as mp:                  # restored on exit, whatever happens inside
        for i in range(n_cmd + n_file):
            mp.setattr(core.time, "time", lambda i=i: now - age_days * DAY + i)
            if i < n_cmd:
                ids.append(m.remember(f"ran: export number {i} of the batch", key=f"cmd:{i:04d}", tags=["bash"],
                                      mtype="episodic"))
            else:
                ids.append(m.remember(f"state of file number {i}", key=f"file:src/f{i}.py", tags=["file"],
                                      mtype="episodic"))
    m.flush()
    return str(proj), p, ids


def _all_ids(p):
    seg = {i for s in archive.listed_segments(p).values() for i in s["ids"]}
    return {r["id"] for r in Inspeximus(p)._items} | seg, seg


def _env_for(tmp_path):
    e = {k: v for k, v in os.environ.items() if not k.startswith("INSPEXIMUS_") and k != "PYTHONPATH"}
    e.update(PYTHONPATH=ROOT, INSPEXIMUS_KEY_HOME=os.environ["INSPEXIMUS_KEY_HOME"], INSPEXIMUS_NO_UPDATE_CHECK="1",
             HOME=str(tmp_path), USERPROFILE=str(tmp_path), APPDATA=str(tmp_path))
    return e


def _wait_for(pred, seconds=90):
    end = time.time() + seconds
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.5)
    return False


# ── the policy ───────────────────────────────────────────────────────────────

def test_the_policy_is_off_by_default_and_reads_config_and_env(tmp_path, monkeypatch):
    proj, p, _ = _project(tmp_path, monkeypatch, n_cmd=2, n_file=0)
    assert cc.archive_policy(proj)["auto"] is False
    assert cc.maybe_archive_in_background(proj) == "off"
    cfg = tmp_path / "proj" / ".inspeximus" / "config.json"
    cfg.write_text(json.dumps({"archive": {"auto": True, "trigger_mb": 12, "older_than_days": 3,
                                            "classes": ["cmd", "file"]}}), encoding="utf-8")
    pol = cc.archive_policy(proj)
    assert pol["auto"] and pol["trigger_mb"] == 12.0 and pol["older_than_days"] == 3.0
    assert pol["classes"] == ["cmd", "file"] and pol["min_interval_s"] == 3600.0
    monkeypatch.setenv("INSPEXIMUS_ARCHIVE_AUTO", "0")
    assert cc.archive_policy(proj)["auto"] is False


@pytest.mark.parametrize("bad", [{"auto": "yes"}, {"trigger_mb": "big"}, {"trigger_mb": -1}, {"classes": []},
                                 {"classes": [3]}, {"older_than_days": True}, {"allow_git_tracked": 1}])
def test_a_value_of_the_wrong_type_falls_back_to_the_default(tmp_path, monkeypatch, bad):
    proj, p, _ = _project(tmp_path, monkeypatch, n_cmd=1, n_file=0, config={"archive": bad})
    assert cc.archive_policy(proj) == cc.AUTO_ARCHIVE_DEFAULTS


def test_a_small_store_or_a_missing_one_starts_nothing(tmp_path, monkeypatch):
    proj, p, _ = _project(tmp_path, monkeypatch, config={"archive": {"auto": True, "trigger_mb": 500}})
    assert cc.maybe_archive_in_background(proj) == "small"
    os.remove(p)
    assert cc.maybe_archive_in_background(proj) == "missing"


def test_a_malformed_config_never_raises(tmp_path, monkeypatch):
    proj, p, _ = _project(tmp_path, monkeypatch, n_cmd=1, n_file=0)
    (tmp_path / "proj" / ".inspeximus" / "config.json").write_text("{not json", encoding="utf-8")
    assert cc.maybe_archive_in_background(proj) == "off"


# ── the trigger and the detached run ──────────────────────────────────────────

def test_a_big_store_starts_one_detached_run_and_no_second(tmp_path, monkeypatch):
    proj, p, ids = _project(tmp_path, monkeypatch, config={"archive": {"auto": True, "trigger_mb": 0.0001, "allow_git_tracked": True}})
    monkeypatch.chdir(proj)
    assert cc.maybe_archive_in_background(proj) == "started"
    assert cc.maybe_archive_in_background(proj) == "recent", "two prompts in a row start one run"
    state = json.load(open(p + ".archive-auto.json", encoding="utf-8"))
    assert state["last_attempt"] > 0 and state["policy"]["classes"] == ["cmd"]
    assert _wait_for(lambda: archive.listed_segments(p)), "the detached run archived nothing within 90 s"
    time.sleep(1.5)
    everything, in_segments = _all_ids(p)
    assert everything == set(ids), "a record is in neither the hot store nor a segment"
    assert len(in_segments) == 40, "every old cmd capture moved, and no file capture did"


def test_the_detached_run_stamps_the_rows_that_have_no_verdict(tmp_path, monkeypatch):
    proj, p, ids = _project(tmp_path, monkeypatch, n_cmd=6, n_file=6, age_days=1,
                            config={"archive": {"auto": True, "trigger_mb": 0.0001, "allow_git_tracked": True}})
    m = Inspeximus(p)
    for r in m._items:
        (r.get("meta") or {}).pop("read_guards", None)
        m._touched.add(r["id"])
    m._save(force=True)
    monkeypatch.chdir(proj)
    assert cc.maybe_archive_in_background(proj) == "started"
    log = p + ".archive-auto.log"
    assert _wait_for(lambda: os.path.exists(log) and '"stamp_guards"' in open(log, encoding="utf-8").read())
    time.sleep(1.0)
    assert Inspeximus(p).stamp_read_guards(dry_run=True)["to_stamp"] == 0


# ── `file` is archivable ───────────────────────────────────────────────────────

def test_the_file_class_moves_file_captures_and_nothing_else(tmp_path, monkeypatch):
    proj, p, ids = _project(tmp_path, monkeypatch)
    monkeypatch.chdir(proj)
    r = archive.apply(Inspeximus(p), 7, ("file",), allow_git_tracked=True)
    assert r["applied"], r
    everything, in_segments = _all_ids(p)
    assert everything == set(ids) and len(in_segments) == 10
    hot_keys = {str(x.get("key")) for x in Inspeximus(p)._items}
    assert not any(k.startswith("file:") for k in hot_keys) and sum(k.startswith("cmd:") for k in hot_keys) == 40


def test_the_default_classes_still_do_not_include_file(tmp_path, monkeypatch):
    proj, p, ids = _project(tmp_path, monkeypatch)
    monkeypatch.chdir(proj)
    cc.archive_store(proj, older_than=7, apply=True, allow_git_tracked=True)
    hot_keys = {str(x.get("key")) for x in Inspeximus(p)._items}
    assert sum(k.startswith("file:") for k in hot_keys) == 10


# ── a prompt hook during --apply ───────────────────────────────────────────────

def test_a_prompt_hook_that_runs_during_apply_answers_normally_and_loses_nothing(tmp_path, monkeypatch):
    proj, p, ids = _project(tmp_path, monkeypatch, n_cmd=600, n_file=0)
    env = _env_for(tmp_path)
    ev = json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "which export number ran last",
                     "cwd": proj.replace("\\", "/"), "session_id": "t"})
    worker = subprocess.Popen([sys.executable, "-m", "inspeximus.claude_code", "--archive", "--older-than", "7",
                               "--apply", "--allow-git-tracked"], cwd=proj, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, encoding="utf-8")
    overlapped = 0
    answers = []
    while True:
        busy = worker.poll() is None
        h = subprocess.run([sys.executable, "-m", "inspeximus.claude_code"], input=ev, cwd=proj, env=env,
                           capture_output=True, text=True, encoding="utf-8", timeout=120)
        assert h.returncode == 0 and "Traceback" not in h.stderr, h.stderr[-400:]
        answers.append(h.stdout)
        overlapped += busy
        if not busy or len(answers) >= 40:
            break
    out, err = worker.communicate(timeout=180)
    assert worker.returncode == 0, err[-400:]
    assert overlapped >= 1, "control: no hook ran while --apply was working"
    everything, in_segments = _all_ids(p)
    assert everything == set(ids) and len(in_segments) == 600
    assert all(a == "" or a.lstrip().startswith("{") for a in answers), "a hook printed something that is not JSON"


# ── an erasure reaches an archived `file:` row ──────────────────────────────────

def test_an_erasure_reaches_file_rows_that_were_archived(tmp_path, monkeypatch):
    proj, p, ids = _project(tmp_path, monkeypatch, n_cmd=0, n_file=0)
    m = Inspeximus(p)
    now = time.time()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(core.time, "time", lambda: now - 40 * DAY)
        mine = m.remember("state of the payroll file", key="file:hr/payroll.py", tags=["file"], mtype="episodic",
                          source={"doc": "hr/alice"})
        other = m.remember("state of another file", key="file:src/other.py", tags=["file"], mtype="episodic")
    m.flush()
    monkeypatch.chdir(proj)
    assert archive.apply(Inspeximus(p), 7, ("file",), allow_git_tracked=True)["applied"]
    assert {mine, other} <= {i for s in archive.listed_segments(p).values() for i in s["ids"]}, "control: archived"
    r = Inspeximus(p).forget_subject("hr/alice", request_id="dsar-file")
    assert mine in r["ids"] and other not in r["ids"], r
    left = {i for s in archive.listed_segments(p).values() for i in s["ids"]}
    assert mine not in left and other in left, "the erased file row left the segment, the other stayed"
