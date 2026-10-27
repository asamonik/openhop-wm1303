"""Offline pip recovery checks; run with a Python environment containing pip.

The wheels are generated fixtures. Pip only performs --dry-run validation;
no packages are installed and no network or device services are used.
"""

import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("restore_venv_packages", ROOT / "config/restore_venv_packages.py")
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)


class PackageRecoveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="openhop-package-recovery-")
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        self.wheel = self.path / "maintenance_fixture-1.0-py3-none-any.whl"
        self.write_wheel("original")
        self.digest = hashlib.sha256(self.wheel.read_bytes()).hexdigest()

    def write_wheel(self, value):
        with zipfile.ZipFile(self.wheel, "w") as archive:
            archive.writestr("maintenance_fixture.py", f"value = {value!r}\n")
            archive.writestr("maintenance_fixture-1.0.dist-info/METADATA",
                            "Metadata-Version: 2.1\nName: maintenance-fixture\nVersion: 1.0\n")
            archive.writestr("maintenance_fixture-1.0.dist-info/WHEEL",
                            "Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
            archive.writestr("maintenance_fixture-1.0.dist-info/RECORD", "")

    def dry_run(self, archive_info):
        inventory = [{"name": "maintenance-fixture", "version": "1.0", "direct_url": {
            "url": self.wheel.as_uri(), "archive_info": archive_info,
        }}]
        requirement, = list(restore.missing_requirements(inventory, set()))
        return subprocess.run([
            sys.executable, "-m", "pip", "--disable-pip-version-check", "install",
            "--dry-run", "--ignore-installed", "--no-index", "--no-deps", "--no-cache-dir",
            "--report", str(self.path / "report.json"), *requirement,
        ], capture_output=True, text=True, timeout=30)

    def recorded_hashes(self):
        return ({"hashes": {"sha256": self.digest}}, {"hash": "sha256=" + self.digest},
                {"hashes": {"sha256": self.digest.upper()}})

    def test_original_archive_is_accepted(self):
        for archive in self.recorded_hashes():
            with self.subTest(archive=archive):
                result = self.dry_run(archive)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue((self.path / "report.json").is_file())

    def test_changed_archive_at_same_url_is_rejected(self):
        self.write_wheel("changed after inventory")
        for archive in self.recorded_hashes():
            with self.subTest(archive=archive):
                result = self.dry_run(archive)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("DO NOT MATCH THE HASHES", result.stderr)
                self.assertFalse((self.path / "report.json").exists())


if __name__ == "__main__":
    unittest.main()
