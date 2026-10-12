"""Exercise device migration on disposable roots, never on installed services."""

import base64
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "overlay/openhop_repeater"))
sys.path.append(str(ROOT / "tests/fixtures/upstream"))
sys.path.insert(0, str(ROOT / "overlay/openhop_core/src"))


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "config" / f"{name}.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


migration = module("migrate_openhop")
packages = module("restore_venv_packages")


class DeviceMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.job = migration.Migration(self.root, Path("/home/operator"))

    def put(self, path, content="data"):
        target = self.root / path.lstrip("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return target

    def test_fresh_install_and_repeat_are_noops(self):
        self.job.check()
        self.job.apply()
        self.job.apply()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_moves_checkouts_preserves_local_changes_and_retires_venv(self):
        self.put("/opt/pymc_repeater/repos/pyMC_core/.git/config", "origin and stash metadata")
        self.put("/opt/pymc_repeater/repos/pyMC_Repeater/local.txt", "untracked edits")
        self.put("/opt/pymc_repeater/venv/bin/custom-tool", "#!/opt/pymc_repeater/venv/bin/python")
        self.put("/opt/pymc_repeater/venv/lib/python3.12/site-packages/plugin_dep-1.0.dist-info/METADATA",
                 "Metadata-Version: 2.1\nName: plugin-dep\nVersion: 1.0\n")
        self.job.check()
        self.assertFalse(self.job.base.exists())
        self.job.apply()
        self.assertFalse(self.job.old_base.exists())
        self.assertEqual((self.job.base / "repos/openhop_core/.git/config").read_text(), "origin and stash metadata")
        self.assertEqual((self.job.base / "repos/openhop_repeater/local.txt").read_text(), "untracked edits")
        self.assertFalse((self.job.base / "venv").exists())
        self.assertTrue((self.job.base / "venv.pre-openhop/bin/custom-tool").exists())
        self.assertTrue((self.job.base / ".rebuild-venv").exists())
        inventory = json.loads((self.job.base / ".pre-openhop-packages.json").read_text())
        self.assertEqual(inventory[0]["name"], "plugin-dep")
        self.job.apply()
        self.assertFalse((self.job.base / "venv.pre-openhop.1").exists())

    def test_retry_after_power_loss_finishes_venv_rebuild(self):
        self.put("/opt/openhop_repeater/.rebuild-venv", "")
        self.put("/opt/openhop_repeater/venv/bin/python", "relocated interpreter")
        self.job.apply()
        self.assertFalse((self.job.base / "venv").exists())
        self.assertTrue((self.job.base / "venv.pre-openhop/bin/python").exists())

    def test_repeated_upgrade_keeps_new_venv(self):
        self.put("/opt/openhop_repeater/venv/bin/python", "canonical interpreter")
        self.put("/opt/openhop_repeater/repos/openhop_core/local.txt", "local edits")
        self.job.apply()
        self.assertEqual((self.job.base / "venv/bin/python").read_text(), "canonical interpreter")

    def test_rewrites_known_paths_preserves_identity_secrets_comments_and_permissions(self):
        original = """# keep comment
storage:
  storage_dir: /var/lib/pymc_repeater
identity:
  path: '/etc/pymc_repeater/identity.key'
plugin_path: /opt/pymc_repeater/repos/pyMC_Repeater/plugins/custom
user_key: /home/operator/.config/pymc_repeater/identity.key
tilde_key: ~/.config/pymc_repeater/identity.key
custom_path: /opt/pymc_repeater_other/keep
jwt_secret: fixture-secret
"""
        config = self.put("/etc/pymc_repeater/config.yaml", original)
        config.chmod(0o600)
        key = self.put("/etc/pymc_repeater/identity.key", "existing-identity")
        key.chmod(0o600)
        self.job.apply()
        canonical = self.root / "etc/openhop_repeater"
        self.assertEqual((canonical / "identity.key").read_text(), "existing-identity")
        self.assertEqual((canonical / "config.yaml.pre-openhop").read_text(), original)
        result = (canonical / "config.yaml").read_text()
        for text in ("# keep comment", "jwt_secret: fixture-secret", "/etc/openhop_repeater/identity.key",
                     "/var/lib/openhop_repeater", "/opt/openhop_repeater/repos/openhop_repeater/plugins/custom",
                     "/home/operator/.config/openhop_repeater/identity.key", "/opt/pymc_repeater_other/keep"):
            self.assertIn(text, result)
        self.assertIn("~/.config/openhop_repeater/identity.key", result)
        self.assertEqual(stat.S_IMODE((canonical / "config.yaml").stat().st_mode), 0o600)
        self.job.apply()
        self.assertEqual((canonical / "config.yaml.pre-openhop").read_text(), original)

    def test_canonical_config_wins_legacy_conflicts_are_archived(self):
        self.put("/etc/pymc_repeater/config.yaml", "node: old")
        self.put("/etc/pymc_repeater/identity.key", "same-identity")
        self.put("/etc/openhop_repeater/config.yaml", "node: current")
        self.job.apply()
        self.assertEqual((self.root / "etc/openhop_repeater/config.yaml").read_text(), "node: current")
        self.assertEqual((self.root / "etc/openhop_repeater/identity.key").read_text(), "same-identity")
        self.assertEqual((self.root / "etc/pymc_repeater.pre-openhop/config.yaml").read_text(), "node: old")
        self.assertFalse((self.root / "etc/pymc_repeater").exists())

    def test_missing_nested_configuration_and_modes_survive_merge(self):
        nested = self.put("/etc/pymc_repeater/plugins/custom/settings.yaml", "enabled: true")
        nested.chmod(0o600)
        self.put("/etc/openhop_repeater/config.yaml", "node: current")
        self.job.apply()
        copied = self.root / "etc/openhop_repeater/plugins/custom/settings.yaml"
        self.assertEqual(copied.read_text(), "enabled: true")
        self.assertEqual(stat.S_IMODE(copied.stat().st_mode), 0o600)

    def test_sqlite_committed_wal_survives_migration(self):
        db = self.put("/var/lib/pymc_repeater/repeater.db", "")
        subprocess.run([sys.executable, "-c", """
import os, sqlite3, sys
c = sqlite3.connect(sys.argv[1])
c.execute('PRAGMA journal_mode=WAL')
c.execute('CREATE TABLE history(value TEXT)')
c.execute("INSERT INTO history VALUES ('committed-in-wal')")
c.commit()
os._exit(0)
""", str(db)], check=True)
        self.assertTrue(Path(str(db) + "-wal").exists())
        self.job.apply()
        with sqlite3.connect(self.root / "var/lib/openhop_repeater/repeater.db") as conn:
            self.assertEqual(conn.execute("SELECT value FROM history").fetchone(), ("committed-in-wal",))

    def test_conflicting_database_blocks_every_move(self):
        self.put("/opt/pymc_repeater/repos/pyMC_core/local.txt", "keep")
        legacy = self.put("/var/lib/pymc_repeater/repeater.db", "old history")
        current = self.put("/var/lib/openhop_repeater/repeater.db", "different history")
        with self.assertRaisesRegex(migration.MigrationError, "Conflicting SQLite"):
            self.job.apply()
        self.assertEqual(legacy.read_text(), "old history")
        self.assertEqual(current.read_text(), "different history")
        self.assertFalse(self.job.base.exists())
        self.assertFalse((self.job.old_base / ".rebuild-venv").exists())

    def test_matching_database_with_unmatched_wal_is_rejected(self):
        self.put("/var/lib/pymc_repeater/repeater.db", "same main file")
        self.put("/var/lib/openhop_repeater/repeater.db", "same main file")
        self.put("/var/lib/pymc_repeater/repeater.db-wal", "different committed data")
        with self.assertRaisesRegex(migration.MigrationError, "Conflicting SQLite"):
            self.job.check()

    def test_orphaned_wal_cannot_be_attached_to_migrating_database(self):
        self.put("/var/lib/pymc_repeater/repeater.db", "source database")
        self.put("/var/lib/openhop_repeater/repeater.db-wal", "unrelated journal")
        with self.assertRaisesRegex(migration.MigrationError, "Conflicting SQLite"):
            self.job.check()

    def test_database_in_config_tree_also_protects_its_journals(self):
        self.put("/etc/pymc_repeater/plugins/state.sqlite3", "source database")
        self.put("/etc/openhop_repeater/plugins/state.sqlite3-wal", "unrelated journal")
        with self.assertRaisesRegex(migration.MigrationError, "Conflicting SQLite"):
            self.job.check()

    def test_conflicting_checkouts_are_not_combined(self):
        self.put("/opt/pymc_repeater/repos/pyMC_core/.git/config", "origin A")
        self.put("/opt/openhop_repeater/repos/openhop_core/.git/config", "origin B")
        with self.assertRaisesRegex(migration.MigrationError, "Conflicting migration"):
            self.job.check()

    def test_aliases_in_both_directions_resolve_to_real_canonical_directory(self):
        for canonical_alias in (False, True):
            with self.subTest(canonical_alias=canonical_alias), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                old, new = root / "old", root / "new"
                real, alias = (old, new) if canonical_alias else (new, old)
                real.mkdir()
                (real / "identity.key").write_text("keep")
                alias.symlink_to(real, target_is_directory=True)
                migration.move_tree(old, new)
                self.assertFalse(migration.present(old))
                self.assertFalse(new.is_symlink())
                self.assertEqual((new / "identity.key").read_text(), "keep")

    def test_failed_merge_retains_source_and_is_retryable(self):
        self.put("/etc/pymc_repeater/identity.key", "keep")
        self.put("/etc/openhop_repeater/config.yaml", "node: current")
        with patch.object(migration.shutil, "copy2", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.job.apply()
        self.assertEqual((self.root / "etc/pymc_repeater/identity.key").read_text(), "keep")
        self.assertFalse((self.root / "etc/openhop_repeater/identity.key").exists())
        self.job.apply()
        self.assertEqual((self.root / "etc/openhop_repeater/identity.key").read_text(), "keep")

    def test_preserves_service_overrides_and_updater(self):
        self.put("/etc/systemd/system/pymc-repeater.service.d/custom.conf",
                 "[Service]\nEnvironment=PYTHONPATH=/opt/pymc_repeater/repos/pyMC_Repeater:/opt/pymc_repeater/repos/pyMC_core/src\n")
        self.put("/etc/systemd/system/pymc-repeater.service", "old-unit")
        self.put("/usr/local/lib/pymc-wm1303/updater.conf", "custom-updater-config")
        self.job.apply()
        override = self.root / "etc/systemd/system/openhop-repeater.service.d/custom.conf"
        self.assertIn("/opt/openhop_repeater/repos/openhop_core/src", override.read_text())
        self.assertIn("/opt/openhop_repeater/repos/openhop_repeater:", override.read_text())
        self.assertTrue(override.with_name(override.name + ".pre-openhop").exists())
        self.assertEqual((self.root / "etc/systemd/system/pymc-repeater.service.pre-openhop").read_text(), "old-unit")
        self.assertEqual((self.root / "usr/local/lib/openhop-wm1303/updater.conf").read_text(), "custom-updater-config")

    def test_configuration_symlink_rejected_before_moving_installation(self):
        self.put("/opt/pymc_repeater/repos/pyMC_core/local.txt", "keep")
        target = self.put("/custom/config.yaml", "node: custom")
        source = self.put("/etc/pymc_repeater/config.yaml", "")
        source.unlink()
        source.symlink_to(target)
        with self.assertRaisesRegex(migration.MigrationError, "Configuration symlink"):
            self.job.check()
        self.assertTrue(self.job.old_base.exists())

    def test_identity_symlink_does_not_silently_break_during_migration(self):
        target = self.put("/etc/pymc_repeater/real.key", "preserve identity")
        (target.parent / "identity.key").symlink_to(target)
        with self.assertRaisesRegex(migration.MigrationError, "Configuration symlink"):
            self.job.check()
        self.assertEqual((target.parent / "identity.key").read_text(), "preserve identity")


class MigrationContracts(unittest.TestCase):
    def test_fresh_install_creates_required_runtime_directories(self):
        import pwd
        import grp
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = (ROOT / "install.sh").read_text()
            start = source.index('mkdir -p "${REPO_DIR}" "${CONFIG_DIR}"')
            block = source[start:source.index('# Phase 4: Clone Repositories', start)]
            paths = dict(INSTALL_BASE=str(root / "opt/openhop_repeater"),
                         REPO_DIR=str(root / "opt/openhop_repeater/repos"),
                         CONFIG_DIR=str(root / "etc/openhop_repeater"),
                         PKTFWD_DIR=str(root / "home/operator/wm1303_pf"),
                         LOG_DIR=str(root / "var/log/openhop_repeater"),
                         DATA_DIR=str(root / "var/lib/openhop_repeater"),
                         PI_HOME=str(root / "home/operator"))
            subprocess.run(["bash", "-eu", "-c", 'step() { :; }; ok() { :; };\n' + block], check=True,
                           env=dict(os.environ, **paths, SCRIPT_DIR=str(ROOT),
                                    PI_USER=pwd.getpwuid(os.getuid()).pw_name,
                                    PI_GROUP=grp.getgrgid(os.getgid()).gr_name), capture_output=True)
            for path in paths.values():
                self.assertTrue(Path(path).is_dir(), path)

    def test_restoring_extras_preserves_current_openhop_versions(self):
        inventory = [{"name": "openhop_core", "version": "1.0"},
                     {"name": "pymc-repeater", "version": "1.0"},
                     {"name": "cherrypy", "version": "17.0"},
                     {"name": "plugin-dep", "version": "2.3"},
                     {"name": "local-plugin", "version": "1.0", "direct_url":
                      {"url": "file:///opt/pymc_repeater/my-plugin", "dir_info": {"editable": True}}}]
        self.assertEqual(list(packages.missing_requirements(inventory, {"cherrypy"})),
                         [["plugin-dep==2.3"], ["-e", "/opt/openhop_repeater/my-plugin"]])

    def test_native_binary_must_use_both_canonical_ipc_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / "lora_pkt_fwd"
            for contents, valid in ((b"/tmp/pymc_cad_config.json\0/tmp/pymc_spectral_results.json", False),
                                    (b"/tmp/openhop_cad_config.json", False),
                                    (b"/tmp/openhop_cad_config.json\0/tmp/openhop_spectral_results.json", True)):
                binary.write_bytes(contents)
                result = subprocess.run(["bash", "-c", 'source "$1"; packet_forwarder_uses_openhop_paths "$2"',
                                         "test", str(ROOT / "config/deploy_overlay.sh"), str(binary)])
                self.assertEqual(result.returncode == 0, valid)

    def test_both_entrypoints_check_then_stop_then_migrate_then_start(self):
        for name in ("install.sh", "upgrade.sh"):
            text = (ROOT / name).read_text()
            check = text.index('migrate_openhop.py" check')
            stop = text.index('systemctl stop "${service_unit}"')
            move = text.index('migrate_openhop.py" apply')
            restore = text.index('restore_venv_packages.py')
            start = text.index('systemctl enable openhop-plugin-manager.service')
            self.assertLess(check, stop, name)
            self.assertLess(stop, move, name)
            self.assertLess(move, restore, name)
            self.assertLess(restore, start, name)
            self.assertIn("for service_unit in openhop-plugin-manager.service", text)
        upgrade = (ROOT / "upgrade.sh").read_text()
        self.assertIn('"$EXISTING_CORE_DIR:$CORE_REVISION"', upgrade)
        self.assertIn('"$EXISTING_REPEATER_DIR:$REPEATER_REVISION"', upgrade)

    def test_user_identity_is_copied_without_regeneration(self):
        from repeater.config import _load_or_create_identity_key
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            legacy = root / "pymc_repeater/identity.key"
            legacy.parent.mkdir()
            key = b"k" * 32
            legacy.write_bytes(base64.b64encode(key))
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": temp}), \
                 patch("repeater.config.resolve_config_path", return_value=root / "missing-system-key"):
                self.assertEqual(_load_or_create_identity_key(), key)
                self.assertEqual(_load_or_create_identity_key(), key)
            self.assertEqual((root / "openhop_repeater/identity.key").read_bytes(), legacy.read_bytes())


if __name__ == "__main__":
    unittest.main()
