"""Directories a test creates outside tmp_path, and the only way the suite removes them again (3.22).

LIMIT: the replaced-directory check compares st_ino, which holds on NTFS only. ext4 handed the same inode number
back at once (WSL, 2026-10-21), so a caller on another file system cannot rely on it to spot a replacement.

Several Windows tests exercise the Git Bash misreading of a path: `/c/Users/<you>` read by Windows becomes
`C:\\c\\Users\\<you>`, so the code under test, or the test itself, writes under the C:\\c mirror. That mirror is
OUTSIDE tmp_path, on a drive that can hold a person's data: C:\\c exists on the owner's machine. Until 3.22 four
tests each cleaned it up their own way, and three of those could delete what they did not make (an rmtree of
a path that already existed, or of an ancestor another test was filling, or the removal of someone else's
empty directory).

The rules, in one place:
- `make_owned_dirs(path, root)` creates `path` and its missing ancestors itself and records only what it
  created, as (path, file id). A directory that existed, or that someone else made first, is never recorded.
- `remove_owned(owned, root)` removes the deepest recorded directory with its contents (the test writes there),
  then each recorded ancestor only while it is empty, and stops at the first that is not empty or whose file
  id changed.
- Both refuse any path that is not `root` or under it, before they touch anything.
"""
import os
import shutil


def mirror_root(path):
    """The C:\\c-style mirror root for `path`'s drive: `C:\\c` for `C:\\Users\\...`."""
    drive = os.path.splitdrive(os.path.abspath(str(path)))[0]
    if not drive:
        raise ValueError("%r has no drive letter, so it has no Git Bash mirror" % (path,))
    return drive + "\\" + drive[0].lower()


def mirror_of(path, root=None):
    """Where Windows puts `path` when it receives the Git Bash form unconverted: <root><rest of path>.

    `root` defaults to the real mirror root of `path`'s drive; tests of this module pass a fake one."""
    rest = os.path.splitdrive(os.path.abspath(str(path)))[1]
    return os.path.join(root, rest.lstrip("\\/")) if root is not None else mirror_root(path) + rest


def _under(path, root):
    p, r = os.path.normcase(os.path.abspath(str(path))), os.path.normcase(os.path.abspath(str(root)))
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


def _require_under(path, root):
    if not _under(path, root):
        raise ValueError("%r is not under the mirror root %r; refusing to create or remove it" % (path, root))


def make_owned_dirs(path, root):
    """Create `path` and each missing ancestor. Returns what THIS call created, deepest first, as (path, id)."""
    p = os.path.abspath(str(path))
    _require_under(p, root)
    missing = []
    while not os.path.exists(p):
        missing.append(p)
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    owned = []
    for d in reversed(missing):
        _require_under(d, root)
        try:
            os.mkdir(d)
        except FileExistsError:
            continue                                 # made by someone else between the check and here: theirs
        owned.append((d, os.stat(d).st_ino))
    return owned[::-1]


def remove_owned(owned, root):
    """Undo make_owned_dirs: the leaf with its contents, then each ancestor while it is empty."""
    for d, _ in owned:
        _require_under(d, root)                      # checked for every entry before anything is removed
    for n, (d, ino) in enumerate(owned):
        try:
            if os.stat(d).st_ino != ino:
                return                               # replaced under the same path: someone else's now
        except OSError:
            continue
        if n == 0:
            shutil.rmtree(d, ignore_errors=True)
            continue
        try:
            os.rmdir(d)
        except OSError:
            return                                   # not empty: something else lives here, so stop climbing
