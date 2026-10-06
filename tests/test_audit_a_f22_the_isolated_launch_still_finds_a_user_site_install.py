"""AUDIT-A F-22 on fix/3164 e08df4da: `python -I` implies `-s`, so an inspeximus installed with `pip install --user`
(the default on Windows Store Python) was not found by the hook or the MCP server. The launch now keeps the user site
and still keeps the working directory and every PYTHON* variable out (inspeximus._launch: -E -P on 3.11 and later,
-E with a sys.path shim otherwise).

Builder's in-tree form of AUDIT-A's test. The user site is built at its DEFAULT location, by pointing APPDATA
(Windows) or HOME (POSIX) at a temp directory, because -E deliberately ignores PYTHONUSERBASE: a project's settings
`env` can set it (AUDIT-A, measured), so it is the attack path and not configuration. AUDIT-A's PYTHONPATH guard is
kept as written. Its F-23 test (uvx's package index) waits for a decision and is not here.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import inspeximus  # noqa: E402
import inspeximus.install as ins  # noqa: E402
from inspeximus import _launch  # noqa: E402


def _user_site_env(tmp_path):
    """A throwaway user base at the default location, with this package copied into its user site."""
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("INSPEXIMUS_", "PYTHON"))}
    env.update(APPDATA=str(home / "AppData" / "Roaming"), LOCALAPPDATA=str(home / "AppData" / "Local"),
               HOME=str(home), USERPROFILE=str(home))
    site = subprocess.run([sys.executable, "-E", "-c", "import site; print(site.getusersitepackages())"], env=env,
                          capture_output=True, text=True).stdout.strip()
    if not site or not os.path.normcase(os.path.abspath(site)).startswith(os.path.normcase(str(home))):
        pytest.skip("this interpreter's user site cannot be moved into a sandbox: %s" % site)
    enabled = subprocess.run([sys.executable, "-E", "-c", "import site; print(site.ENABLE_USER_SITE)"], env=env,
                             capture_output=True, text=True).stdout.strip()
    if enabled != "True":
        pytest.skip("the user site is disabled for this interpreter (a venv): %s" % enabled)
    os.makedirs(site, exist_ok=True)
    shutil.copytree(os.path.dirname(os.path.abspath(inspeximus.__file__)), os.path.join(site, "inspeximus"))
    env["INSPEXIMUS_KEY_HOME"] = str(tmp_path / "keyhome")
    return env


def _run(cmd, env, cwd, stdin):
    return subprocess.run(cmd, shell=True, env=env, cwd=str(cwd), input=stdin, capture_output=True, text=True,
                          timeout=120)


def _ran_from(env, cmd, cwd):
    """Which inspeximus the command imports: its file, printed by a tiny stand-in for the module."""
    return subprocess.run(cmd, shell=True, env=env, cwd=str(cwd), capture_output=True, text=True, timeout=60)


def test_f22_the_installers_python_hook_command_finds_a_user_site_install(tmp_path):
    env = _user_site_env(tmp_path)
    neutral = tmp_path / "work"
    neutral.mkdir()
    event = json.dumps({"hook_event_name": "SessionStart", "cwd": str(neutral)})
    p = _run(ins.hook_command("python", sys.executable), env, neutral, event)
    assert p.returncode == 0, f"exit {p.returncode}: {p.stderr.strip()[-200:]}"


def test_f22_the_shim_form_finds_a_user_site_install(tmp_path, monkeypatch):
    """The form for Python 3.9 and 3.10 (and for uvx and the static plugin file), run on this interpreter."""
    env = _user_site_env(tmp_path)
    neutral = tmp_path / "work"
    neutral.mkdir()
    cmd = '"%s" %s' % (sys.executable, _launch.module_command("inspeximus.claude_code", (3, 10)))
    p = _run(cmd, env, neutral, json.dumps({"hook_event_name": "SessionStart", "cwd": str(neutral)}))
    assert p.returncode == 0, f"exit {p.returncode}: {p.stderr.strip()[-200:]}"


def test_f22_the_mcp_launch_for_a_python_runtime_finds_a_user_site_install(tmp_path):
    env = _user_site_env(tmp_path)
    command, args = ins._server_launch("python", sys.executable)
    probe = [command] + [a for a in args if a not in ("-m", "inspeximus.mcp_server")] + ["-c", "import inspeximus"]
    p = subprocess.run(probe, env=env, cwd=str(tmp_path), capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr.strip()[-200:]


@pytest.mark.parametrize("version", [None, (3, 10), (3, 12)])
def test_control_each_form_still_ignores_a_package_in_the_working_directory(tmp_path, version):
    env = _user_site_env(tmp_path)
    repo = tmp_path / "repo"
    (repo / "inspeximus").mkdir(parents=True)
    (repo / "inspeximus" / "__init__.py").write_text("open(r'%s', 'w').write('ran')\n" % (tmp_path / "PWNED.txt").as_posix())
    (repo / "inspeximus" / "claude_code.py").write_text("")
    if version is None:
        cmd = ins.hook_command("python", sys.executable)
    else:
        cmd = '"%s" %s' % (sys.executable, _launch.module_command("inspeximus.claude_code", version))
    _run(cmd, env, repo, json.dumps({"hook_event_name": "SessionStart", "cwd": str(repo)}))
    assert not (tmp_path / "PWNED.txt").exists(), "the repository's package ran as the hook: %s" % cmd


def test_guard_for_the_f22_fix_the_launch_still_ignores_pythonpath(tmp_path):
    """AUDIT-A's guard, as written: PYTHONPATH from a project's settings must not reach the hook."""
    env = _user_site_env(tmp_path)
    hostile = tmp_path / "hostile"
    (hostile / "inspeximus").mkdir(parents=True)
    (hostile / "inspeximus" / "__init__.py").write_text("open(r'%s', 'w').write('ran')\n" % (tmp_path / "PWNED2.txt").as_posix())
    (hostile / "inspeximus" / "claude_code.py").write_text("")
    env["PYTHONPATH"] = str(hostile)
    neutral = tmp_path / "work"
    neutral.mkdir()
    _run(ins.hook_command("python", sys.executable), env, neutral,
         json.dumps({"hook_event_name": "SessionStart", "cwd": str(neutral)}))
    assert not (tmp_path / "PWNED2.txt").exists(), "PYTHONPATH from the environment made the hook import the repository's package"


def test_control_plain_python_m_does_run_the_repositorys_package(tmp_path):
    repo = tmp_path / "repo"
    (repo / "inspeximus").mkdir(parents=True)
    (repo / "inspeximus" / "__init__.py").write_text("open(r'%s', 'w').write('ran')\n" % (tmp_path / "PWNED3.txt").as_posix())
    (repo / "inspeximus" / "claude_code.py").write_text("")
    subprocess.run([sys.executable, "-m", "inspeximus.claude_code"], cwd=str(repo), input="{}", text=True,
                   capture_output=True, timeout=60)
    assert (tmp_path / "PWNED3.txt").exists(), "control: the fixture no longer reproduces F-15"
