"""AUDIT-A round 2 on v3.16.3: every command inspeximus writes or spawns as `python -m inspeximus.<module>` runs with
the project directory as its working directory, and `python -m` puts that directory first on sys.path. A repository
with an `inspeximus/` directory (or inspeximus.py) therefore runs ITS code as the hook. Measured with the plugin's own
command `uvx --from inspeximus==3.16.3 python -m inspeximus.claude_code` in a directory holding
`inspeximus/__init__.py`: the file ran (marker written). With `python -P -m` or `python -I -m` it did not.
Fix: add `-I` (isolated: no cwd, no PYTHON* variables, no user site) or `-P` (3.11 and later) to each command, or call
a console script. Fails on v3.16.3 for each command below."""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus.install as ins  # noqa: E402
from inspeximus import claude_code as cc  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # the repository, from tests/
SAFE = re.compile(r"\s-(I|P)\s|\s-[A-Za-z]*I[A-Za-z]*\s")


def _safe(cmd: str) -> bool:
    """Builder, 3.16.4 (F-22): a command that runs an inspeximus module is safe when it carries -E and either
    `-P -m` or the sys.path shim of inspeximus._launch. -I alone is no longer written: it drops the user site."""
    from inspeximus import _launch
    c = " " + cmd.replace("\\", "/") + " "
    return " -E " in c and (" -P -m inspeximus." in c or _launch.SHIM in cmd.replace('\\"', '"'))


def _runs_inspeximus(cmd: str) -> bool:
    return "inspeximus.claude_code" in cmd or "inspeximus.mcp_server" in cmd


def test_the_hook_command_the_installer_writes_ignores_the_working_directory():
    for kind, exe in (("python", sys.executable), ("uvx", "uvx")):
        cmd = ins.hook_command(kind, exe)
        assert _runs_inspeximus(cmd) and _safe(cmd), f"{kind}: {cmd}"


def test_the_mcp_server_launch_for_a_python_runtime_ignores_the_working_directory():
    command, args = ins._server_launch("python", sys.executable)
    assert "-m" in args and ("-I" in args[:args.index("-m")] or "-P" in args[:args.index("-m")]), (command, args)


def test_the_plugin_hooks_file_ignores_the_working_directory():
    path = os.path.join(ROOT, "hooks", "hooks.json")
    cmds = []

    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get("command"), str):
                cmds.append(o["command"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(json.load(open(path, encoding="utf-8")))
    assert cmds and all(_runs_inspeximus(c) for c in cmds), "control: the file holds the hook commands"
    assert all(_safe(c) for c in cmds), [c for c in cmds if not _safe(c)]


def test_the_detached_archive_run_ignores_its_working_directory(tmp_path, monkeypatch):
    for k in [k for k in os.environ if k.startswith("INSPEXIMUS_")]:
        monkeypatch.delenv(k)
    monkeypatch.setenv("INSPEXIMUS_KEY_HOME", str(tmp_path / "keyhome"))
    (tmp_path / "keyhome" / "inspeximus").mkdir(parents=True)
    (tmp_path / "keyhome" / "inspeximus" / "config.json").write_text(json.dumps(
        {"archive": {"auto": True, "trigger_mb": 0.0001}, "stores": {"links": [os.path.realpath(str(tmp_path / "store"))]}}))
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    monkeypatch.setenv("INSPEXIMUS_CODING_STORE", str(store_dir))
    (store_dir / "coding_memory.json").write_bytes(b"x" * 4096)
    repo = tmp_path / "repo"
    (repo / "inspeximus").mkdir(parents=True)
    marker = tmp_path / "SHADOW-RAN"
    (repo / "inspeximus" / "__init__.py").write_text("open(%r, 'w').write('ran')" % str(marker))
    spawned = []
    real_popen = subprocess.Popen
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: spawned.append((argv, kw)) or type("P", (), {"pid": 1})())
    assert cc.maybe_archive_in_background(str(repo)) == "started"
    argv, kw = spawned[0]
    # Builder, 3.16.4: the spawn is `python -E -c <shim that names the package this process imported>`: -E keeps
    # every PYTHON* variable out, the shim keeps the working directory out, and the user site stays (F-22).
    # a `-m` would import whatever inspeximus the interpreter has installed, not the one that started the run.
    assert "-E" in argv[1:argv.index("-c")] and "-I" not in argv, argv
    # And run it for real, in the repository, with PYTHONPATH pointing at it: the repository's package must not run.
    env = dict(os.environ, PYTHONPATH=str(repo))
    r = real_popen(argv, cwd=str(repo), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    r.communicate(timeout=120)
    assert not marker.exists(), "the repository's inspeximus/__init__.py ran as the archive run"


def test_control_without_isolation_the_repositorys_package_does_run(tmp_path):
    """The fixture still reproduces the defect: plain `python -m inspeximus.claude_code` in that directory runs it."""
    repo = tmp_path / "repo"
    (repo / "inspeximus").mkdir(parents=True)
    marker = tmp_path / "SHADOW-RAN"
    (repo / "inspeximus" / "__init__.py").write_text("open(%r, 'w').write('ran')" % str(marker))
    subprocess.run([sys.executable, "-m", "inspeximus.claude_code", "--help"], cwd=str(repo),
                   capture_output=True, timeout=120)
    assert marker.exists(), "control: python -m in a directory with inspeximus/ should import it"


def test_the_isolated_hook_command_does_not_run_the_repositorys_package(tmp_path):
    repo = tmp_path / "repo"
    (repo / "inspeximus").mkdir(parents=True)
    marker = tmp_path / "SHADOW-RAN"
    (repo / "inspeximus" / "__init__.py").write_text("open(%r, 'w').write('ran')" % str(marker))
    from inspeximus import _launch
    subprocess.run([sys.executable] + _launch.module_args("inspeximus.claude_code", sys.version_info),
                   input=b"{}", cwd=str(repo),
                   capture_output=True, timeout=120)
    assert not marker.exists(), "the isolated launch ran the repository's inspeximus/"
