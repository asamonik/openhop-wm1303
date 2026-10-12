"""Prepare the repeater's runtime files without following links in /tmp."""

import grp
import os
from pathlib import Path
import pwd
import stat
import sys


RUNTIME_FILES = (
    "openhop_spectral_results.json",
    "openhop_wm1303_bridge_conf.json",
    "openhop_cad_config.json",
    "openhop_channel_e_bridge_conf.json",
)


def prepare_runtime_file(path: Path, uid: int, gid: int) -> None:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        # O_EXCL refuses a file/link created between the two open calls.
        fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o664)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError(f"Runtime path must be a regular file without hard links: {path}")
        if info.st_uid not in (0, uid):
            raise ValueError(f"Runtime file belongs to a different user: {path}")
        os.fchown(fd, uid, gid)
        os.fchmod(fd, 0o664)
    finally:
        os.close(fd)


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Usage: prepare_runtime_files.py <user> <group>")
    uid = pwd.getpwnam(sys.argv[1]).pw_uid
    gid = grp.getgrnam(sys.argv[2]).gr_gid
    for filename in RUNTIME_FILES:
        prepare_runtime_file(Path("/tmp") / filename, uid, gid)
        # Radio services have stopped and the HAL is rebuilt before this call.
        # Old IPC snapshots are disposable; persistent state lives under /var.
        for suffix in ("", ".tmp"):
            legacy = Path("/tmp") / (filename.replace("openhop_", "pymc_", 1) + suffix)
            try:
                info = legacy.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid in (0, uid):
                legacy.unlink()


if __name__ == "__main__":
    main()
