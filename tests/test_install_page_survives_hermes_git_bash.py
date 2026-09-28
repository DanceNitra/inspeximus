"""3.15.3: the install page's Git Bash lines work in the Git Bash Hermes Agent runs, which does not convert paths.

Found on 2026-09-28 in the friend-flow re-test on a second machine (Hermes Agent on a 9B local model, Git
Bash, HOME=/c/Users/<you>). The calls below are Hermes' own, byte for byte from its state.db (session
20260928_053936_688d38):

  1. python -m venv "$HOME/.inspeximus/venv"      exit 0, and the venv landed under C:\\c\\Users\\<you>
  2. a whole line wrapped in one more pair of quotes  exit 127: bash read the line as one command name
  7. inspeximus install --all ...                   a bare `inspeximus` ran an older copy in Hermes' own venv

Windows reads the unconverted `/c/Users/...` as `C:\\c\\Users\\...`. The page now passes `$USERPROFILE`,
the Windows form, and names every program by its full path.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(REPO, "docs", "install", "index.md")
sys.path.insert(0, os.path.join(REPO, "tools"))

HERMES_CALL_1 = 'python -m venv "$HOME/.inspeximus/venv"'
HERMES_CALL_2 = '"\\"$HOME/.inspeximus/venv/Scripts/python.exe\\" -m pip install -U \\"inspeximus[mcp]\\""'
HERMES_CALL_6 = 'python -m pip install -U "inspeximus[mcp]"'
HERMES_CALL_7 = "inspeximus install --all --rules yes --hermes-provider yes"


def _git_bash():
    if os.name != "nt":
        return None
    try:
        import run_install_page
        return run_install_page.git_bash()
    except SystemExit:
        return None


BASH = _git_bash()
windows_git_bash = pytest.mark.skipif(BASH is None, reason="needs Git for Windows' bash on Windows")


def _page_gitbash_lines():
    import run_install_page
    return [ln for ln in run_install_page.page_block("gitbash", "yes").splitlines() if ln.strip()]


def _run(cmd, home, convert, cwd):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("INSPEXIMUS_")}
    # As in Hermes' Git Bash: HOME in the POSIX form (/c/Users/<you>), USERPROFILE in the Windows form.
    drive, rest = os.path.splitdrive(os.path.abspath(str(home)))
    env.update(HOME="/" + drive[0].lower() + rest.replace("\\", "/"), USERPROFILE=str(home))
    if not convert:                                  # Hermes Agent's Git Bash
        env.update(MSYS_NO_PATHCONV="1", MSYS2_ARG_CONV_EXCL="*")
    return subprocess.run([BASH, "-c", cmd], env=env, cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)


def _mirror(path):
    """Where Windows puts `path` when it receives the Git Bash form unconverted: C:\\c\\<rest>."""
    drive, rest = os.path.splitdrive(os.path.abspath(str(path)))
    return drive + "\\" + drive[0].lower() + rest


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    mirror = _mirror(h)
    top = mirror
    while not os.path.exists(os.path.dirname(top)) and os.path.dirname(top) != top:
        top = os.path.dirname(top)
    existed = os.path.exists(top)
    yield h
    if os.path.isdir(mirror):                        # the defect writes OUTSIDE tmp_path; remove only ours
        shutil.rmtree(mirror, ignore_errors=True)
    if not existed and os.path.isdir(top):
        for d, _, _ in sorted(os.walk(top), key=lambda t: -len(t[0])):
            try:
                os.rmdir(d)
            except OSError:
                pass


def _venv_python(root):
    return os.path.join(str(root), ".inspeximus", "venv", "Scripts", "python.exe")


@windows_git_bash
def test_hermes_call_1_lands_under_c_when_bash_does_not_convert(home, tmp_path):
    """The control: this environment reproduces the defect, or the page test below would prove nothing."""
    r = _run(HERMES_CALL_1, home, convert=False, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert os.path.exists(_venv_python(_mirror(home))), "Hermes' call 1 no longer lands under C:\\c"
    assert not os.path.exists(_venv_python(home))


@windows_git_bash
@pytest.mark.parametrize("convert", [False, True], ids=["hermes-no-conversion", "git-bash-default"])
def test_the_page_line_creates_the_venv_in_the_home(home, tmp_path, convert):
    first = _page_gitbash_lines()[0]
    assert "$USERPROFILE" in first and "$HOME" not in first, first
    r = _run(first, home, convert=convert, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    assert os.path.exists(_venv_python(home)), (r.stdout, r.stderr)
    assert not os.path.exists(_venv_python(_mirror(home)))


@windows_git_bash
def test_a_line_wrapped_in_one_more_pair_of_quotes_is_one_command_name(home, tmp_path):
    r = _run(HERMES_CALL_2, home, convert=False, cwd=tmp_path)
    assert r.returncode == 127 and "No such file or directory" in r.stderr, (r.returncode, r.stderr)


def _code_lines():
    text = open(PAGE, encoding="utf-8").read()
    for block in re.findall(r"```(?:bash|powershell)\n(.*?)```", text, re.S):
        for ln in block.splitlines():
            if ln.strip():
                yield ln.strip()


def _runs_a_bare_program(line):
    """True when a line runs pip or the inspeximus installer without naming the venv's program."""
    if not re.search(r"\bpip install\b|\binstall --all\b", line):
        return False
    first = line.lstrip("& ").split('" ')[0] if line.lstrip("& ").startswith('"') else line.split()[0]
    return not re.search(r"\.inspeximus[\\/]venv[\\/](Scripts|bin)[\\/]", first)


def test_every_page_line_names_the_venvs_program_by_path():
    lines = list(_code_lines())
    assert any("install --all" in ln for ln in lines)
    assert [ln for ln in lines if _runs_a_bare_program(ln)] == []
    for bare in (HERMES_CALL_6, HERMES_CALL_7):                 # control: the checker sees Hermes' calls
        assert _runs_a_bare_program(bare), bare


def test_no_gitbash_line_nests_quotes():
    for ln in _page_gitbash_lines():
        assert '\\"' not in ln and not ln.startswith('"\\'), ln
