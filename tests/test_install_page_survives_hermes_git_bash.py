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

#: One worker for the whole file. Every test here writes, or checks for, a venv under the C:\c mirror. Run on two
#: workers, one teardown removed C:\c\Users\runneradmin\AppData while the other test's venv was being created
#: (ci.yml run 39651825248, windows-latest, 2026-10-21: WinError 3). The teardown now removes only what its own
#: fixture created (see _make_owned_dirs), and the group still keeps the tests apart.
pytestmark = pytest.mark.xdist_group("c_drive_mirror")

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


def _make_owned_dirs(path):
    """Create `path` and each missing ancestor. Returns what THIS call created, deepest first, as (path, file id).

    The C:\\c mirror lies OUTSIDE tmp_path, on a drive that can hold someone's data: C:\\c exists on the owner's
    machine. So the teardown may remove only directories this call made, identified by path AND file id, and a
    directory that already existed, or that someone else made in the meantime, is never in the list."""
    missing, p = [], os.path.abspath(str(path))
    while not os.path.exists(p):
        missing.append(p)
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    owned = []
    for d in reversed(missing):
        try:
            os.mkdir(d)
        except FileExistsError:
            continue                                 # made by someone else between the check and here: theirs
        owned.append((d, os.stat(d).st_ino))
    return owned[::-1]


def _remove_owned(owned):
    """Undo _make_owned_dirs. The deepest directory is the one the test writes into, so its contents go with it;
    every ancestor is removed only while it is empty. A directory whose file id changed is someone else's."""
    for n, (d, ino) in enumerate(owned):
        try:
            if os.stat(d).st_ino != ino:
                return
        except OSError:
            continue
        if n == 0:
            shutil.rmtree(d, ignore_errors=True)
            continue
        try:
            os.rmdir(d)
        except OSError:
            return                                   # not empty: something else lives here, so stop climbing


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    owned = _make_owned_dirs(_mirror(h))             # the defect writes OUTSIDE tmp_path, into this mirror
    yield h
    _remove_owned(owned)


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


def test_every_test_in_this_file_runs_in_one_xdist_group(request):
    """Every test collected from this file carries the same xdist_group, so under --dist loadgroup they run one
    after another on one worker.

    This checks the marker, on any platform. It does NOT reproduce the Windows race the marker is for (two
    workers, one fixture teardown removing the C:\\c mirror while another test's venv was being created, ci.yml
    run 39651825248): that needs a runner where C:\\c does not exist, and it cannot be forced here."""
    items = [i for i in request.node.parent.collect() if isinstance(i, pytest.Function)]
    names = {i.originalname for i in items}
    assert {"test_hermes_call_1_lands_under_c_when_bash_does_not_convert",
            "test_the_page_line_creates_the_venv_in_the_home"} <= names, names
    groups = {i.nodeid: [m.args[0] for m in i.iter_markers("xdist_group")] for i in items}
    assert all(g == ["c_drive_mirror"] for g in groups.values()), groups


def _teardown_of_3_22_0(mirror, top, existed):
    """The teardown this file had up to 3.22.0, kept as the control for the tests below."""
    if os.path.isdir(mirror):
        shutil.rmtree(mirror, ignore_errors=True)
    if not existed and os.path.isdir(top):
        for d, _, _ in sorted(os.walk(top), key=lambda t: -len(t[0])):
            try:
                os.rmdir(d)
            except OSError:
                pass


def test_the_teardown_keeps_a_directory_that_existed_before_it(tmp_path):
    """The file-loss control sits on a pre-existing MIRROR path, not on a pre-existing ancestor, on purpose.

    The 3.22.0 teardown removed ancestors with os.rmdir, which refuses a directory that holds a file, so a file in
    an ancestor was never at risk and a control built there would pass against the old code. What it did delete
    was the mirror itself: shutil.rmtree ran on it whether or not it existed before the test. Do not move this
    control back onto an ancestor."""
    # A mirror path that already holds a file, as a reused temp path or the owner's C:\\c can.
    target = tmp_path / "c" / "Users" / "you" / "home"
    target.mkdir(parents=True)
    (target / "keep.txt").write_text("not ours", encoding="utf-8")
    _remove_owned(_make_owned_dirs(target))
    assert (target / "keep.txt").read_text(encoding="utf-8") == "not ours"
    # Control: the 3.22.0 teardown deletes it, so this fixture reproduces the defect.
    _teardown_of_3_22_0(str(target), str(target), True)
    assert not (target / "keep.txt").exists()


def test_the_teardown_keeps_what_someone_else_made_beside_its_own(tmp_path):
    root = tmp_path / "c"
    root.mkdir()
    target = root / "Users" / "you" / "home"
    owned = _make_owned_dirs(target)
    assert [d for d, _ in owned] == [str(target), str(target.parent), str(target.parent.parent)]
    (target / "venv.txt").write_text("the test's own output", encoding="utf-8")
    other = root / "Users" / "someone-else"
    other.mkdir()                                    # made after setup, by someone else, and empty
    _remove_owned(owned)
    assert not target.exists() and not target.parent.exists()
    assert other.is_dir() and (root / "Users").is_dir()
    # Control: the 3.22.0 teardown, which removes every empty directory under the top it found missing.
    target.mkdir(parents=True)
    _teardown_of_3_22_0(str(target), str(root / "Users"), False)
    assert not other.exists()


def test_the_teardown_leaves_a_directory_replaced_under_its_path(tmp_path):
    target = tmp_path / "c" / "Users" / "you" / "home"
    owned = _make_owned_dirs(target)
    shutil.rmtree(str(tmp_path / "c" / "Users"))     # someone else replaces the tree under the same paths
    target.mkdir(parents=True)
    (target / "theirs.txt").write_text("theirs", encoding="utf-8")
    _remove_owned(owned)
    assert (target / "theirs.txt").read_text(encoding="utf-8") == "theirs"
