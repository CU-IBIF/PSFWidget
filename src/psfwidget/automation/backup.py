"""Database backups, deployment checks, and process priority (AUT-07, NFR-11).

The results database lives on the acquisition PC (NFR-11); backups are its
copy on the network. Each backup is a consistent SQLite copy made while the
database may be open, verified by opening it and checking its integrity, and
written with a ``.sha256`` file so later corruption of the copy is detectable.
Only backups this function wrote (same name pattern) are removed by retention.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from psfwidget.io._common import sha256_of
from psfwidget.tracking.store import ResultsDatabase

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
NICE_INCREMENT = 10
NETWORK_FILESYSTEMS = frozenset(
    {"nfs", "nfs4", "cifs", "smbfs", "smb3", "afpfs", "sshfs", "fuse.sshfs", "davfs", "9p"}
)


class BackupError(RuntimeError):
    """Raised when a backup cannot be made or does not verify."""


@dataclass(frozen=True)
class Backup:
    """A verified backup file."""

    path: Path
    sha256: str
    removed: tuple[Path, ...]
    """Older backups removed by retention."""


def backup_name(database: Path, when: datetime) -> str:
    """Return the file name of a backup of ``database`` made at ``when``."""
    return f"{database.stem}_{when:%Y%m%dT%H%M%SZ}.sqlite"


def verify(path: Path) -> str:
    """Check that a backup opens and passes SQLite's integrity check; return its SHA-256.

    Raises
    ------
    BackupError
        If the copy is damaged or its recorded hash does not match.
    """
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        (result,) = connection.execute("PRAGMA integrity_check").fetchone()
    finally:
        connection.close()
    if result != "ok":
        raise BackupError(f"{path.name} failed the integrity check: {result}")
    digest = sha256_of(path)
    recorded = path.with_name(path.name + ".sha256")
    if recorded.exists() and recorded.read_text("utf-8").split()[0] != digest:
        raise BackupError(f"{path.name} does not match its recorded SHA-256")
    return digest


def backup_database(database: Path, folder: Path, keep: int, now: datetime) -> Backup:
    """Copy the database to ``folder``, verify the copy, and apply retention.

    Raises
    ------
    BackupError
        If the database is missing, or the copy does not verify (the copy is
        then removed so a damaged file is never mistaken for a backup).
    """
    if not database.is_file():
        raise BackupError(f"no database at {database} to back up")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / backup_name(database, now)
    temporary = target.with_suffix(".partial")
    temporary.unlink(missing_ok=True)
    with ResultsDatabase(database) as db:
        db.backup(temporary)
    try:
        digest = verify(temporary)
    except BackupError:
        temporary.unlink(missing_ok=True)
        raise
    temporary.replace(target)
    target.with_name(target.name + ".sha256").write_text(
        f"{digest}  {target.name}\n", encoding="utf-8"
    )
    pattern = f"{database.stem}_????????T??????Z.sqlite"
    existing = sorted(folder.glob(pattern), key=lambda p: p.name)
    removed = []
    for old in existing[: max(len(existing) - keep, 0)]:
        old.unlink()
        old.with_name(old.name + ".sha256").unlink(missing_ok=True)
        removed.append(old)
    return Backup(target, digest, tuple(removed))


def is_network_path(path: Path) -> bool:
    """Whether ``path`` is on a network share (UNC path, network drive, or NFS/SMB mount)."""
    text = str(path)
    if text.startswith(("\\\\", "//")):
        return True
    resolved = path.resolve() if path.exists() else path.parent.resolve()
    if sys.platform == "win32":  # pragma: no cover - Windows only
        import ctypes

        drive = os.path.splitdrive(str(resolved))[0]
        if drive:
            drive_remote = 4
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            return bool(kernel32.GetDriveTypeW(drive + "\\") == drive_remote)
        return False
    mounts = Path("/proc/mounts")
    if not mounts.exists():  # macOS: no /proc; network volumes are under /Volumes
        return False
    best, kind = "", ""
    for line in mounts.read_text("utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) >= 3 and str(resolved).startswith(parts[1]) and len(parts[1]) > len(best):
            best, kind = parts[1], parts[2]
    return kind in NETWORK_FILESYSTEMS


def lower_priority() -> bool:
    """Run the rest of this process at reduced priority; return whether it worked (NFR-11)."""
    try:
        if sys.platform == "win32":  # pragma: no cover - Windows only
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            return bool(
                kernel32.SetPriorityClass(
                    kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS
                )
            )
        os.nice(NICE_INCREMENT)
        return True
    except OSError:
        return False
