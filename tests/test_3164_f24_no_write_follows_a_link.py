"""3.16.4, AUDIT-A F-24: a file inspeximus writes beside a store or in a project is never written through a link.

A repository controls `.inspeximus/`, and git stores symbolic links. `open(path, "w")` follows one and truncates the
file it names: measured on WSL with the re-stamp run, a 64-byte user file became 648 bytes. The same pattern wrote the
archive run's log and state file, `secrets_notice.json`, `nudge.json`, the store's `.cusum.json`, `.irrev.json` and
`.embedid`, the archive log, the partitions registry, the action ledger and its salt, the mem0 import sidecar and the
update-check cache. Each case below plants a link at that name and checks the link's target afterwards.

POSIX: a symbolic link to a file of the user's (the reported attack). Windows: a symbolic link needs a privilege this
machine's account does not hold, so the case uses a junction to a directory of the user's, which needs none; `is_link`
must see it, and nothing in the directory may change."""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from inspeximus import _safewrite  # noqa: E402
from inspeximus import claude_code as cc  # noqa: E402
from inspeximus.core import Inspeximus  # noqa: E402

VICTIM = '{"precious": "a file of the user\'s, written by the user"}\n'


@pytest.fixture
def env(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keyhome"))
    monkeypatch.setenv("INSPEXIMUS_NO_UPDATE_CHECK", "1")
    (tmp_path / "keyhome" / "inspeximus").mkdir(parents=True)
    proj = tmp_path / "repo"
    (proj / ".git").mkdir(parents=True)
    (proj / ".inspeximus").mkdir()
    return proj


def plant(name, tmp_path):
    """A link at `name` to something of the user's. Returns a function that says whether the target is untouched."""
    name = str(name)
    if os.name == "nt":
        victim = tmp_path / "user_dir"
        victim.mkdir(exist_ok=True)
        (victim / "keep.txt").write_text(VICTIM)
        r = subprocess.run(["cmd", "/c", "mklink", "/J", name, str(victim)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        assert r.returncode == 0, r.stderr
        return lambda: sorted(os.listdir(victim)) == ["keep.txt"] and (victim / "keep.txt").read_text() == VICTIM \
            and _safewrite.is_link(name)
    victim = tmp_path / "user_file.json"
    victim.write_text(VICTIM)
    os.symlink(str(victim), name)
    return lambda: victim.read_text() == VICTIM


def _store(proj):
    m = cc._store(str(proj))
    m.remember("a note about the release", key="n1")
    m.flush()
    return m


def test_the_helper_sees_a_link_and_a_junction(tmp_path):
    f = tmp_path / "plain.json"
    f.write_text("{}")
    assert not _safewrite.is_link(f) and not _safewrite.is_link(tmp_path / "absent")
    intact = plant(tmp_path / "linked.json", tmp_path)
    assert _safewrite.is_link(tmp_path / "linked.json")
    with pytest.raises(_safewrite.LinkRefused):
        _safewrite.write_atomic(tmp_path / "linked.json", "{}")
    with pytest.raises(_safewrite.LinkRefused):
        _safewrite.open_for_write(tmp_path / "linked.json")
    assert intact()


def test_control_the_helper_writes_a_plain_file_and_leaves_no_temporary(tmp_path):
    p = tmp_path / "state.json"
    _safewrite.write_atomic(p, '{"a": 1}')
    _safewrite.write_atomic(p, b'{"a": 2}')
    with _safewrite.open_for_write(tmp_path / "run.log") as fh:
        fh.write("ok")
    assert json.loads(p.read_text()) == {"a": 2} and (tmp_path / "run.log").read_text() == "ok"
    assert sorted(os.listdir(tmp_path)) == ["run.log", "state.json"]


@pytest.mark.skipif(os.name == "nt", reason="the reported attack is a symbolic link, which needs POSIX here")
def test_control_a_plain_open_follows_the_link(tmp_path):
    """The fixture reproduces the defect: what 3.16.3 did, open(path, "w"), truncates the link's target."""
    intact = plant(tmp_path / "linked.json", tmp_path)
    with open(tmp_path / "linked.json", "w") as fh:
        fh.write("x")
    assert not intact()


def test_the_secrets_notice_is_not_written_through_a_link(env, tmp_path):
    m = _store(env)
    intact = plant(env / ".inspeximus" / "secrets_notice.json", tmp_path)
    cc._secrets_notice(str(env), m)
    assert intact()


def test_the_nudge_counter_is_not_written_through_a_link(env, tmp_path):
    _store(env)
    intact = plant(env / ".inspeximus" / "nudge.json", tmp_path)
    cc._bump_writes(str(env))
    assert intact()


@pytest.mark.parametrize("name", ["archive-auto.log", "archive-auto.json"])
def test_the_archive_runs_log_and_state_are_not_written_through_a_link(env, tmp_path, monkeypatch, name):
    (tmp_path / "keyhome" / "inspeximus" / "config.json").write_text(json.dumps(
        {"archive": {"auto": True, "trigger_mb": 0.0001}}))
    m = _store(env)
    for i in range(40):
        m.remember("ran: command number %d with some output" % i, key="cmd:%d" % i, tags=["bash"], mtype="episodic")
    m.flush()
    intact = plant(str(m.path) + "." + name, tmp_path)
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: type("P", (), {"pid": 1})())
    cc.maybe_archive_in_background(str(env))
    if name == "archive-auto.json":
        cc._mark_archive_run_done(str(m.path), True)
    assert intact()
    # 3.17.0: the run's state and log are not beside the store at all. They are in the key home, where the run wrote them.
    assert os.path.exists(cc._archive_state_path(str(m.path), ".json" if name.endswith("json") else ".log"))


@pytest.mark.parametrize("sidecar", ["cusum.json", "irrev.json"])
def test_the_store_sidecars_are_not_written_through_a_link(env, tmp_path, sidecar):
    m = _store(env)
    intact = plant(str(m.path) + "." + sidecar, tmp_path)
    m._irrev, m._cusum = {}, {}                       # what the first spend and the first drift check load
    try:
        (m._save_cusum if sidecar == "cusum.json" else m._save_budget)()
    except OSError:
        pass                                          # a refused budget write fails closed, as any write error does
    assert intact()


def test_the_embed_recipe_sidecar_is_not_written_through_a_link(env, tmp_path):
    p = env / ".inspeximus" / "vectors.json"
    m = Inspeximus(str(p))
    intact = plant(str(p) + ".embedid", tmp_path)
    m._persist_vectors, m.embed_id = True, "nomic-embed-text|test"
    m.remember("a note with a recipe", key="n1")
    with pytest.raises(OSError):                      # a refused sidecar is reported by flush, as any sidecar error
        m.flush()
    assert intact()


def test_the_archive_log_is_not_written_through_a_link(env, tmp_path):
    from inspeximus import archive
    m = _store(env)
    intact = plant(str(archive.log_path(m.path)), tmp_path)
    with pytest.raises(OSError):
        archive._write_log(m, [])
    assert intact()


def test_the_partitions_registry_is_not_written_through_a_link(env, tmp_path):
    from inspeximus.partitions import Partitions
    m = _store(env)
    reg = env / ".inspeximus" / "partitions.json"
    intact = plant(reg, tmp_path)
    with pytest.raises(OSError):
        Partitions(m, path=str(reg))._save()
    assert intact()


@pytest.mark.parametrize("which", ["ledger", "salt"])
def test_the_action_ledger_and_its_salt_are_not_written_through_a_link(env, tmp_path, which):
    from inspeximus.actions import ActionLedger
    path = env / ".inspeximus" / "actions.json"
    led = ActionLedger(path=str(path))
    intact = plant(path if which == "ledger" else led.salt_path, tmp_path)
    # The salt is read first: through a link it reads the user's file as a salt and fails before any write.
    with pytest.raises((OSError, ValueError)):
        led._save() if which == "ledger" else led._salt_bytes(create=True)
    assert intact()


def test_the_import_sidecar_is_not_written_through_a_link(env, tmp_path):
    from inspeximus import migrate
    m = _store(env)
    intact = plant(migrate.sidecar_path(m), tmp_path)
    with pytest.raises(OSError):
        migrate._write_sidecar(m, {"abc"})
    assert intact()


def test_the_update_cache_is_not_written_through_a_link(env, tmp_path, monkeypatch):
    import urllib.request
    from inspeximus import _update
    monkeypatch.delenv("INSPEXIMUS_NO_UPDATE_CHECK")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    intact = plant(env / ".inspeximus" / ".update_check.json", tmp_path)
    _update.check_for_update("0.0.1", cache_dir=str(env / ".inspeximus"))
    assert intact()


@pytest.mark.skipif(os.name == "nt", reason="a dangling symbolic link needs POSIX here")
@pytest.mark.parametrize("which", ["salt", "notice"])
def test_a_dangling_link_does_not_create_a_file_where_it_points(env, tmp_path, which):
    """A link to a name that does not exist yet: `open(path, "w")` creates the file the link names, at a path the
    repository chose. The ledger's salt checks `exists()`, which is False for a dangling link, and then wrote."""
    target = tmp_path / "outside" / "planted.txt"
    target.parent.mkdir()
    if which == "salt":
        from inspeximus.actions import ActionLedger
        led = ActionLedger(path=str(env / ".inspeximus" / "actions.json"))
        os.symlink(str(target), str(led.salt_path))
        with pytest.raises(OSError):
            led._salt_bytes(create=True)
    else:
        m = _store(env)
        os.symlink(str(target), str(env / ".inspeximus" / "secrets_notice.json"))
        cc._secrets_notice(str(env), m)
    assert not target.exists(), "a file was created where the repository's link pointed"
