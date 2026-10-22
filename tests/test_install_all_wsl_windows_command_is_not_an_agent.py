"""`install --all` inside WSL does not count a Windows program reached through interop as an installed agent.

Found 2026-10-22 on a clean Ubuntu 24.04 user in WSL with inspeximus 3.22.0 from PyPI: WSL appends the Windows
PATH, so `claude` resolved to /mnt/c/Users/<user>/AppData/Roaming/npm/claude. `install --all --dry-run` then
reported Claude Code as found and planned ~/.claude.json in the Linux home, which that Windows Claude Code
never reads. A real run would have printed ARMED for an agent that was not wired. With the Windows PATH
removed, the same user had no agent at all.

The marker is a Windows drive mount in /proc/mounts (drvfs, or 9p with aname=drvfs), read from the mount
table rather than from a fixed /mnt prefix, because wsl.conf can move the automount root.

Controls: the same command with no drive mount counts as found, so the fixture still reproduces the defect
without the guard; a Linux install of the same command with a drive mount present still counts.
"""
from __future__ import annotations

import pytest

from inspeximus import install_all as A

#: Two lines read from /proc/mounts on Ubuntu 24.04 under WSL 2 on 2026-10-22, verbatim.
DRIVERS = ("drivers /usr/lib/wsl/drivers 9p ro,nosuid,nodev,noatime,aname=drivers;fmask=222;dmask=222,cache=5,"
           "access=client,msize=65536,trans=fd,rfd=8,wfd=8 0 0")
DRIVE_C = ("C:\\134 /mnt/c 9p rw,noatime,aname=drvfs;path=C:\\;uid=1000;gid=1000;symlinkroot=/mnt/,cache=5,"
           "access=client,msize=65536,trans=fd,rfd=6,wfd=6 0 0")
WIN_CLAUDE = "/mnt/c/Users/u/AppData/Roaming/npm/claude"
LINUX_CLAUDE = "/home/u/.local/bin/claude"


@pytest.fixture
def wsl(tmp_path, monkeypatch):
    """detect("claude") with a chosen mount table and a chosen PATH hit, and no install folder at all."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setattr(A, "_install_markers", lambda host: (["claude"], []))

    def setup(lines, hit):
        mounts = tmp_path / "mounts"
        mounts.write_text("\n".join(lines) + "\n", encoding="utf-8")
        monkeypatch.setattr(A, "_PROC_MOUNTS", str(mounts))
        monkeypatch.setattr(A, "_which_safe", lambda cmd: hit if cmd == "claude" else None)
        return A.detect("claude")
    return setup


def test_a_windows_program_on_the_wsl_path_is_not_an_installed_agent(wsl):
    found, why = wsl([DRIVERS, DRIVE_C], WIN_CLAUDE)
    assert found is False
    assert WIN_CLAUDE in why and "Windows" in why


def test_control_without_a_drive_mount_the_same_hit_counts(wsl):
    # The fixture still reproduces the defect: only the drive mount separates this case from the first one.
    assert wsl([DRIVERS], WIN_CLAUDE) == (True, "`claude` on PATH")


def test_control_a_linux_install_still_counts_inside_wsl(wsl):
    assert wsl([DRIVERS, DRIVE_C], LINUX_CLAUDE) == (True, "`claude` on PATH")


def test_the_drive_is_read_from_the_mount_table_not_from_a_fixed_prefix(wsl):
    # wsl.conf [automount] root=/ puts drive C at /c.
    moved = DRIVE_C.replace(" /mnt/c ", " /c ")
    assert wsl([DRIVERS, moved], "/c/Users/u/AppData/Roaming/npm/claude")[0] is False
    assert wsl([DRIVERS, moved], WIN_CLAUDE)[0] is True     # /mnt/c is no drive on this table


def test_a_mount_point_is_matched_on_a_whole_path_segment(wsl):
    assert wsl([DRIVERS, DRIVE_C], "/mnt/cc/bin/claude")[0] is True


def test_no_mount_table_means_no_wsl(wsl, monkeypatch):
    monkeypatch.setattr(A, "_which_safe", lambda cmd: WIN_CLAUDE)
    monkeypatch.setattr(A, "_PROC_MOUNTS", "/nonexistent/proc/mounts")
    assert A.detect("claude") == (True, "`claude` on PATH")
