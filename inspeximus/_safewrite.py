"""Writes beside a store or in a project that never go through a link (3.16.4, AUDIT-A F-24).

A repository controls its `.inspeximus/` directory, and git stores symbolic links. A repository that ships
`.inspeximus/coding_memory.json.archive-auto.log` as a link to a file of the user's made the archive run open it
with `open(path, "w")`, which follows the link and truncates the file it names. Measured on WSL with the same
pattern in the re-stamp run: a 64-byte user file became 648 bytes of a report. The same `open(path, "w")` wrote
the state, notice and nudge files beside the store, and their temporary names (`<file>.tmp.<pid>`) could be
guessed, so a link placed at one was followed too.

Two ways to write, both refusing a link:
  * `write_atomic(path, data)`: the content goes to a new temporary file in the same directory, created with a
    random name and O_EXCL (`tempfile.mkstemp`, which cannot open an existing name, a link included), then
    `os.replace`. A link at `path` is refused before the write and checked again before the replace. A replace
    renames over a link without following it, so the moment between the check and the replace cannot reach the
    link's target either.
  * `open_for_write(path)`: a truncating open for a file another process writes into (a run's log). On POSIX
    `O_NOFOLLOW` makes the open itself fail on a link. On Windows, where there is no such flag, a link or junction
    (a reparse point) is refused before the open, and the opened file must be the file at the path afterwards.

A link is refused with `LinkRefused`, an OSError, so a caller's existing `except OSError` path keeps working. The
user's own config files (settings.json and the like) are written elsewhere and follow links on purpose: a dotfiles
setup links them.
"""
import errno
import os
import stat
import tempfile

#: FILE_ATTRIBUTE_REPARSE_POINT: a Windows symbolic link or junction.
_REPARSE_POINT = 0x400


class LinkRefused(OSError):
    """The path is a symbolic link or a reparse point, and inspeximus does not write through one."""


def is_link(path) -> bool:
    """True when `path` itself is a symbolic link, or on Windows any reparse point (a junction included)."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & _REPARSE_POINT)


def _refuse(path) -> None:
    if is_link(path):
        raise LinkRefused(errno.ELOOP, "inspeximus does not write through a link", str(path))


def write_atomic(path, data, encoding: str = "utf-8") -> None:
    """Replace `path` with `data` (str or bytes) through a new temporary file in the same directory."""
    path = os.fspath(path)
    _refuse(path)
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data if isinstance(data, bytes) else str(data).encode(encoding))
        _refuse(path)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def fresh_file(path) -> str:
    """An empty file at `path`, created exclusively (3.17.0): whatever was at that name, a leftover or a link, is removed first, and
    removing a link removes the link and not its target. The caller then hands the name to a writer that opens it by name, such
    as SQLite, which would otherwise follow a link a repository shipped at that name."""
    path = os.fspath(path)
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), 0o600)
    os.close(fd)
    return path


def copy_file(src, dst) -> None:
    """Copy `src` to `dst` through a new temporary file in the same directory, keeping the times (3.17.0). A link at `dst` is
    refused before the copy and again before the replace, so the copy never lands in the file a link names."""
    import shutil
    src, dst = os.fspath(src), os.fspath(dst)
    _refuse(dst)
    d = os.path.dirname(os.path.abspath(dst))
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(dst) + ".", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "wb") as out, open(src, "rb") as inp:
            shutil.copyfileobj(inp, out, 1 << 20)
        shutil.copystat(src, tmp)
        _refuse(dst)
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def open_for_write(path, encoding: str = "utf-8"):
    """A text file opened for writing at `path`, truncated or created, and never a link's target."""
    path = os.fspath(path)
    _refuse(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        opened, there = os.fstat(fd), os.lstat(path)
        if is_link(path) or (opened.st_ino, opened.st_dev) != (there.st_ino, there.st_dev):
            raise LinkRefused(errno.ELOOP, "the file changed into a link while it was opened", path)
    except BaseException:
        os.close(fd)
        raise
    return os.fdopen(fd, "w", encoding=encoding)
