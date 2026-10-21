"""The C:\\c mirror cleanup removes only what the test made, at every call site (3.22).

tests/_owned_dirs.py is the one implementation. Four tests write under the Git Bash mirror of a Windows path,
outside tmp_path, and each used to clean up its own way; three of those could delete what they did not make.
Each site below has a control: the site's 3.22.0 cleanup, copied verbatim, run on the same scenario, and
shown to lose something. Every scenario runs on a FAKE mirror root under tmp_path, never on the real C:\\c,
so these tests run on every platform and touch nothing outside tmp_path.
"""
import os
import shutil

import pytest

import _owned_dirs
import test_a_missing_store_directory_is_refused_everywhere as site_a
import test_audit_a_a_wrong_path_is_not_a_silent_empty_store as site_c
import test_install_upgrade_path as site_b


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "fake-c"
    r.mkdir()
    return r


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "Users" / "you"
    h.mkdir(parents=True)
    return h


# ── the helper ───────────────────────────────────────────────────────────────────────────────────────────
def test_the_helper_refuses_a_path_outside_its_root(tmp_path, root):
    outside = tmp_path / "elsewhere" / "deep"
    with pytest.raises(ValueError):
        _owned_dirs.make_owned_dirs(outside, root)
    assert not (tmp_path / "elsewhere").exists(), "it created a directory outside its root"
    keep = tmp_path / "keep"
    keep.mkdir()
    (keep / "f.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        _owned_dirs.remove_owned([(str(keep / "f.txt"), 0), (str(keep), os.stat(keep).st_ino)], root)
    assert (keep / "f.txt").exists() and keep.is_dir(), "it removed a directory outside its root"


def test_the_helper_removes_everything_it_made_and_nothing_else(root):
    target = root / "Users" / "you" / "home"
    owned = _owned_dirs.make_owned_dirs(target, root)
    assert [d for d, _ in owned] == [str(target), str(target.parent), str(target.parent.parent)]
    (target / "venv.txt").write_text("the test's own output", encoding="utf-8")
    other = root / "Users" / "someone-else"
    other.mkdir()                                    # made after setup, by someone else, and empty
    _owned_dirs.remove_owned(owned, root)
    assert not target.exists() and not target.parent.exists()
    assert other.is_dir() and (root / "Users").is_dir() and root.is_dir()


def test_the_helper_leaves_a_directory_replaced_under_its_path(root):
    # Windows only. Measured 2026-10-21 on WSL ext4: a directory removed and made again under the same path got
    # the same inode number back, so a file id cannot tell it apart there. NTFS file ids are 64 bit and not
    # handed straight back, and every call site of the helper is a Windows test.
    if os.name != "nt":
        pytest.skip("POSIX file systems reuse an inode number at once; the call sites are Windows-only")
    target = root / "Users" / "you" / "home"
    owned = _owned_dirs.make_owned_dirs(target, root)
    shutil.rmtree(str(root / "Users"))               # someone else replaces the tree under the same paths
    target.mkdir(parents=True)
    (target / "theirs.txt").write_text("theirs", encoding="utf-8")
    _owned_dirs.remove_owned(owned, root)
    assert (target / "theirs.txt").read_text(encoding="utf-8") == "theirs"


def test_the_helper_owns_nothing_that_already_existed(root):
    target = root / "Users" / "you" / "home"
    target.mkdir(parents=True)
    (target / "keep.txt").write_text("not ours", encoding="utf-8")
    assert _owned_dirs.make_owned_dirs(target, root) == []
    _owned_dirs.remove_owned([], root)
    assert (target / "keep.txt").read_text(encoding="utf-8") == "not ours"


# ── the install page fixture (tests/test_install_page_survives_hermes_git_bash.py) ──────────────────────
def _install_page_teardown_of_3_22_0(mirror, top, existed):
    if os.path.isdir(mirror):
        shutil.rmtree(mirror, ignore_errors=True)
    if not existed and os.path.isdir(top):
        for d, _, _ in sorted(os.walk(top), key=lambda t: -len(t[0])):
            try:
                os.rmdir(d)
            except OSError:
                pass


def test_install_page_control_the_old_teardown_deleted_a_mirror_that_existed(root):
    """The file-loss control sits on a pre-existing MIRROR path, not on a pre-existing ancestor, on purpose.

    The 3.22.0 teardown removed ancestors with os.rmdir, which refuses a directory that holds a file, so a file in
    an ancestor was never at risk and a control built there would pass against the old code. What it did delete
    was the mirror itself: shutil.rmtree ran on it whether or not it existed before the test. Do not move this
    control back onto an ancestor. The new fixture owns nothing that existed (the helper test above)."""
    target = root / "Users" / "you" / "home"
    target.mkdir(parents=True)
    (target / "keep.txt").write_text("not ours", encoding="utf-8")
    _install_page_teardown_of_3_22_0(str(target), str(target), True)
    assert not (target / "keep.txt").exists()


def test_install_page_control_the_old_teardown_removed_a_foreign_empty_directory(root):
    target = root / "Users" / "you" / "home"
    target.mkdir(parents=True)
    other = root / "Users" / "someone-else"
    other.mkdir()
    _install_page_teardown_of_3_22_0(str(target), str(root / "Users"), False)
    assert not other.exists()


# ── site A: install --check warns about a Git Bash copy of the home ──────────────────────────────────────
def _site_a_cleanup_of_3_22_0(copy):
    top = copy
    while not os.path.exists(os.path.dirname(top)):
        top = os.path.dirname(top)
    os.makedirs(os.path.join(copy, ".inspeximus", "venv"))
    shutil.rmtree(top, ignore_errors=True)


def test_site_a_keeps_a_copy_of_the_home_that_was_already_there(home, root):
    copy = _owned_dirs.mirror_of(home, str(root))
    os.makedirs(copy)
    with open(os.path.join(copy, "keep.txt"), "w", encoding="utf-8") as fh:
        fh.write("not ours")
    owned = site_a.make_home_copy(home, str(root))
    assert os.path.isdir(os.path.join(copy, ".inspeximus", "venv"))
    site_a.remove_home_copy(owned, home, str(root))
    assert not os.path.exists(os.path.join(copy, ".inspeximus")), "the cleanup left what it created"
    assert open(os.path.join(copy, "keep.txt"), encoding="utf-8").read() == "not ours"
    # Control: the 3.22.0 cleanup computed its top as the copy itself here, and removed it whole.
    _site_a_cleanup_of_3_22_0(copy)
    assert not os.path.exists(os.path.join(copy, "keep.txt"))


# ── site B: a Git Bash path read by Windows is refused ───────────────────────────────────────────────────
def _site_b_created_of_3_22_0(misread):
    created = misread
    while not os.path.exists(os.path.dirname(created)):
        created = os.path.dirname(created)
    return None if os.path.exists(misread) else created


def test_site_b_keeps_what_another_test_wrote_beside_the_misread_home(home, root):
    misread = _owned_dirs.mirror_of(home, str(root))
    owned = site_b.make_misread_parent(home, str(root))
    assert os.path.isdir(os.path.dirname(misread)) and not os.path.exists(misread)
    first = os.path.join(str(root), os.path.relpath(misread, str(root)).split(os.sep)[0])
    theirs = os.path.join(first, "another-test", "store.json")
    os.makedirs(os.path.dirname(theirs))
    with open(theirs, "w", encoding="utf-8") as fh:  # another test, on another worker, writing its mirror
        fh.write("theirs")
    site_b.remove_misread_parent(owned, home, str(root))
    assert open(theirs, encoding="utf-8").read() == "theirs"
    assert not os.path.exists(os.path.dirname(misread)), "the cleanup left what it created"
    # Control: the 3.22.0 cleanup chose the highest missing ancestor at setup and removed it whole at the end,
    # with the other test's file in it.
    shutil.rmtree(first)
    created = _site_b_created_of_3_22_0(misread)
    assert created == first
    os.makedirs(os.path.dirname(theirs))
    with open(theirs, "w", encoding="utf-8") as fh:
        fh.write("theirs")
    shutil.rmtree(created, ignore_errors=True)
    assert not os.path.exists(theirs)


# ── site C: a Git Bash path does not open a different empty store ────────────────────────────────────────
def _site_c_cleanup_of_3_22_0(top, existed):
    if not existed and os.path.isdir(top):
        for d, _, _ in sorted(os.walk(top), key=lambda t: -len(t[0])):
            try:
                os.rmdir(d)
            except OSError:
                pass


def test_site_c_keeps_an_empty_directory_someone_else_made(tmp_path, root):
    """Site C could never lose a file: it removed directories with os.rmdir only, which refuses a non-empty one.
    What it did remove was an empty directory it did not make, which on CI is one another test is about to
    fill (the WinError 3 of ci.yml run 39651825248). So this control is about the foreign directory."""
    work = tmp_path / "work"
    work.mkdir()
    mirror = _owned_dirs.mirror_of(work, str(root))
    owned = site_c.make_tmp_mirror(work, str(root))
    first = os.path.join(str(root), os.path.relpath(mirror, str(root)).split(os.sep)[0])
    foreign = os.path.join(first, "someone-else")
    os.mkdir(foreign)
    site_c.remove_tmp_mirror(owned, work, str(root))
    assert os.path.isdir(foreign)
    assert not os.path.exists(mirror), "the cleanup left what it created"
    # Control: the 3.22.0 cleanup walked from the highest missing ancestor and removed every empty directory.
    os.makedirs(mirror)
    _site_c_cleanup_of_3_22_0(first, False)
    assert not os.path.exists(foreign)
