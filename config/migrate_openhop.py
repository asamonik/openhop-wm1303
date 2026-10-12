"""One-time filesystem migration. Call check before downtime, apply after stop.

No service, package, or hardware operations happen here. Old virtual environments
are retained for recovery; installers recreate them at the canonical location.
"""

import argparse
import filecmp
from importlib.metadata import distributions
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile


class MigrationError(RuntimeError):
    pass


def present(path):
    return path.exists() or path.is_symlink()


def check_tree(source, target, *, canonical_wins=False):
    """Reject ambiguous trees and SQLite families before changing either side."""
    if source.is_symlink():
        if source.resolve() == target.resolve() and target.is_dir():
            return
        raise MigrationError(f"Unexpected migration symlink: {source}")
    if target.is_symlink():
        if target.resolve() == source.resolve() and source.is_dir():
            return
        raise MigrationError(f"Unexpected migration symlink: {target}")
    for path in (source, target):
        if present(path) and not path.is_dir():
            raise MigrationError(f"Expected directory: {path}")
    if not source.exists() or not target.exists():
        return
    # Never graft a WAL/SHM file from one database onto another database, even
    # when the two main .db files happen to have identical bytes.
    def database_names(directory):
        return {re.sub(r"-(?:wal|shm|journal)$", "", entry.name)
                for entry in directory.iterdir() if re.search(r"\.(?:db|sqlite|sqlite3)(?:-(?:wal|shm|journal))?$", entry.name)}

    for name in database_names(source) & database_names(target):
        db = source / name
        for suffix in ("", "-wal", "-shm", "-journal"):
            a, b = Path(str(db) + suffix), target / (db.name + suffix)
            if present(a) != present(b) or (present(a) and
                    (a.is_symlink() or b.is_symlink() or not filecmp.cmp(a, b, shallow=False))):
                raise MigrationError(f"Conflicting SQLite histories: {db} and {target / db.name}")
    for source_item in source.iterdir():
        target_item = target / source_item.name
        if not present(target_item):
            continue
        if source_item.is_dir() and not source_item.is_symlink() and target_item.is_dir() and not target_item.is_symlink():
            check_tree(source_item, target_item, canonical_wins=canonical_wins)
        elif canonical_wins:
            # Config/log collisions are retained in the archived legacy tree.
            continue
        elif source_item.is_symlink() and target_item.is_symlink() and os.readlink(source_item) == os.readlink(target_item):
            continue
        elif (source_item.is_file() and target_item.is_file() and
              not source_item.is_symlink() and not target_item.is_symlink() and
              filecmp.cmp(source_item, target_item, shallow=False)):
            continue
        else:
            raise MigrationError(f"Conflicting migration paths: {source_item} and {target_item}")


def archive(path):
    """Keep originals beside the new tree; never replace an earlier backup."""
    destination = path.with_name(path.name + ".pre-openhop")
    index = 1
    while present(destination):
        destination = path.with_name(path.name + f".pre-openhop.{index}")
        index += 1
    path.rename(destination)
    print(f"Recovery copy: {destination}")
    return destination


def copy_missing(source, target):
    for item in source.iterdir():
        dest = target / item.name
        if not present(dest):
            if item.is_symlink():
                dest.symlink_to(os.readlink(item))
            elif item.is_dir():
                shutil.copytree(item, dest, symlinks=True, copy_function=copy_owned)
                shutil.copystat(item, dest)
                os.chown(dest, item.stat().st_uid, item.stat().st_gid)
            else:
                copy_owned(item, dest)
        elif item.is_dir() and dest.is_dir() and not item.is_symlink() and not dest.is_symlink():
            copy_missing(item, dest)


def copy_owned(source, target):
    source, target = Path(source), Path(target)
    fd, temporary = tempfile.mkstemp(prefix=".openhop-copy-", dir=target.parent)
    os.close(fd)
    try:
        shutil.copy2(source, temporary)
        info = source.stat()
        os.chown(temporary, info.st_uid, info.st_gid)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target


def move_tree(source, target, *, canonical_wins=False):
    check_tree(source, target, canonical_wins=canonical_wins)
    if source.is_symlink():
        source.unlink()  # Already points to the canonical tree.
        return
    if not source.exists():
        return
    if target.is_symlink():
        target.unlink()  # Canonical alias to the legacy tree.
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        source.rename(target)
    elif not any(target.iterdir()):
        target.rmdir()
        source.rename(target)
    else:
        # Keep the entire source until all copies succeed. A failed copy is
        # retryable and never deletes the original identity/database/history.
        copy_missing(source, target)
        archive(source)
    print(f"Migrated {source} -> {target}")


def replace_text(path, replacements):
    if not path.is_file():
        return
    if path.is_symlink():
        raise MigrationError(f"Configuration symlink needs manual migration: {path}")
    original = path.read_text()
    updated = original
    for old, new in replacements:
        updated = re.sub(re.escape(old) + r"(?=$|[/\s\"'\],}:])", lambda _: new, updated)
    if updated == original:
        return
    backup = path.with_name(path.name + ".pre-openhop")
    if not present(backup):
        copy_owned(path, backup)
    info = path.stat()
    fd, temporary = tempfile.mkstemp(prefix=".openhop-migrate-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchown(stream.fileno(), info.st_uid, info.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode))
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Migration:
    def __init__(self, root=Path("/"), home=Path("/home/pi")):
        self.root = Path(root)
        self.account_home = Path(home)
        self.home = self.path(str(home))
        self.old_base = self.path("/opt/pymc_repeater")
        self.base = self.path("/opt/openhop_repeater")
        self.pairs = [(self.old_base, self.base, False)]
        for prefix, canonical_wins in (("/etc", True), ("/var/lib", False), ("/var/log", True)):
            self.pairs.append((self.path(prefix + "/pymc_repeater"),
                               self.path(prefix + "/openhop_repeater"), canonical_wins))
        self.pairs.append((self.home / ".config/pymc_repeater", self.home / ".config/openhop_repeater", False))
        self.pairs.append((self.path("/usr/local/lib/pymc-wm1303"), self.path("/usr/local/lib/openhop-wm1303"), True))
        self.pairs.append((self.path("/etc/systemd/system/pymc-repeater.service.d"),
                           self.path("/etc/systemd/system/openhop-repeater.service.d"), False))

    def path(self, absolute):
        return self.root / str(absolute).lstrip("/")

    def check(self):
        for source, target, canonical_wins in self.pairs:
            check_tree(source, target, canonical_wins=canonical_wins)
        for base in (self.old_base, self.base):
            for old, new in (("pyMC_core", "openhop_core"), ("pyMC_Repeater", "openhop_repeater")):
                check_tree(base / "repos" / old, base / "repos" / new)
        # A tree split between old and new roots must also agree after renaming
        # its checkout directories. Otherwise it could combine unrelated Git DBs.
        for old, new in (("pyMC_core", "openhop_core"), ("pyMC_Repeater", "openhop_repeater")):
            check_tree(self.old_base / "repos" / old, self.base / "repos" / new)
            check_tree(self.old_base / "repos" / new, self.base / "repos" / old)
        for directory in (self.path("/etc/pymc_repeater"), self.path("/etc/openhop_repeater"),
                          self.home / ".config/pymc_repeater", self.home / ".config/openhop_repeater"):
            if directory.is_dir():
                for path in directory.rglob("*"):
                    if path.is_symlink():
                        raise MigrationError(f"Configuration symlink needs manual migration: {path}")

    def apply(self):
        self.check()
        moved_code = present(self.old_base)
        moved_code |= any(present(base / "repos" / old) for base in (self.old_base, self.base)
                          for old in ("pyMC_core", "pyMC_Repeater"))
        # Write before any rename so a power loss cannot make a relocated venv
        # look healthy merely because its python symlink still runs.
        marker_base = self.old_base if self.old_base.is_dir() and not self.old_base.is_symlink() else self.base
        marker = marker_base / ".rebuild-venv"
        if moved_code:
            marker.touch()
        for source, target, canonical_wins in self.pairs:
            move_tree(source, target, canonical_wins=canonical_wins)
        for old, new in (("pyMC_core", "openhop_core"), ("pyMC_Repeater", "openhop_repeater")):
            move_tree(self.base / "repos" / old, self.base / "repos" / new)
        if (self.base / ".rebuild-venv").exists() and (self.base / "venv").exists():
            inventory = self.base / ".pre-openhop-packages.json"
            if not inventory.exists():
                packages = []
                for site in (self.base / "venv/lib").glob("python*/site-packages"):
                    for dist in distributions(path=[str(site)]):
                        name, version = dist.metadata["Name"], dist.version
                        if name and version:
                            packages.append({"name": name, "version": version,
                                             "direct_url": json.loads(dist.read_text("direct_url.json") or "null")})
                inventory.write_text(json.dumps(packages, indent=2) + "\n")
            archive(self.base / "venv")
        replacements = [
            ("/opt/pymc_repeater/repos/pyMC_core", "/opt/openhop_repeater/repos/openhop_core"),
            ("/opt/pymc_repeater/repos/pyMC_Repeater", "/opt/openhop_repeater/repos/openhop_repeater"),
            ("/opt/openhop_repeater/repos/pyMC_core", "/opt/openhop_repeater/repos/openhop_core"),
            ("/opt/openhop_repeater/repos/pyMC_Repeater", "/opt/openhop_repeater/repos/openhop_repeater"),
            ("/opt/pymc_repeater", "/opt/openhop_repeater"),
            ("/etc/pymc_repeater", "/etc/openhop_repeater"),
            ("/var/lib/pymc_repeater", "/var/lib/openhop_repeater"),
            ("/var/log/pymc_repeater", "/var/log/openhop_repeater"),
            (str(self.account_home / ".config/pymc_repeater"), str(self.account_home / ".config/openhop_repeater")),
            ("~/.config/pymc_repeater", "~/.config/openhop_repeater"),
        ]
        for directory in (self.path("/etc/openhop_repeater"), self.home / ".config/openhop_repeater"):
            if directory.exists():
                for path in directory.rglob("*"):
                    if path.suffix in (".yaml", ".yml", ".json"):
                        replace_text(path, replacements)
        # Retain administrator service overrides and update only their paths.
        for unit in ("openhop-repeater", "openhop-plugin-manager"):
            for path in self.path(f"/etc/systemd/system/{unit}.service.d").glob("*.conf"):
                replace_text(path, replacements)
        # Keep the old service definitions for recovery before the installer
        # replaces/removes them. Never overwrite the first recovery copy.
        for unit in ("pymc-repeater", "openhop-repeater", "openhop-plugin-manager"):
            path = self.path(f"/etc/systemd/system/{unit}.service")
            backup = path.with_name(path.name + ".pre-openhop")
            if path.is_file() and not path.is_symlink() and not present(backup):
                copy_owned(path, backup)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("check", "apply"))
    parser.add_argument("--home", type=Path, required=True)
    args = parser.parse_args()
    try:
        migration = Migration(home=args.home)
        getattr(migration, args.mode)()
    except (OSError, MigrationError) as exc:
        parser.exit(1, f"OpenHop migration stopped: {exc}\nOriginals/recovery copies have been retained.\n")


if __name__ == "__main__":
    main()
