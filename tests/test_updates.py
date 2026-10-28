import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/workbuddy_updates.py"
spec = importlib.util.spec_from_file_location("updates", SCRIPT)
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


class UpdatesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.app = self.root / "Applications/WorkBuddy.app"
        resources = self.app / "Contents/Resources"
        resources.mkdir(parents=True)
        self.info = self.app / "Contents/Info.plist"
        self.info.write_bytes(plistlib.dumps({"CFBundleIdentifier": u.BUNDLE,
                                              "CFBundleShortVersionString": "5.9.2"}))
        body = b"isolated updater fixture; never executes or connects"
        header = json.dumps({"files": {"main": {"files": {"update-service.darwin.js": {
            "size": len(body), "offset": "0"}}}}}).encode()
        padding = b"\x00" * ((4 - len(header) % 4) % 4)
        self.asar = resources / "app.asar"
        self.asar.write_bytes(struct.pack("<4I", 4, len(header) + len(padding) + 8,
                                        len(header) + len(padding) + 4, len(header)) + header + padding + body)
        self.hash_patch = patch.object(u, "KNOWN_UPDATER", hashlib.sha256(body).hexdigest())
        self.hash_patch.start()
        self.process_patch = patch.object(u, "running_pids", return_value=[])
        self.process_patch.start()
        self.home = self.root / "home"
        self.config = self.home / ".workbuddy/app/app-config.json"
        self.config.parent.mkdir(parents=True)
        self.config.write_text(json.dumps({"theme": "dark", "privateFixture": "stay-local"}))
        self.install = u.Installation(self.app, self.home)
        self.install.downloads.mkdir(parents=True)
        self.backups = self.home / "snapshots"

    def tearDown(self):
        for parent, dirs, files in os.walk(self.root):
            for name in files + dirs:
                path = Path(parent) / name
                if not path.is_symlink() and hasattr(os, "chflags"):
                    os.chflags(path, 0)
        self.process_patch.stop()
        self.hash_patch.stop()
        self.tmp.cleanup()

    def pending(self, phase="prepared"):
        version = "5.9.3.42398134"
        zipped = self.install.downloads / f"WorkBuddy-{version}.zip"
        zipped.write_bytes(b"disposable update fixture")
        extracted = self.install.cache / "extracted" / version / "WorkBuddy.app"
        extracted.mkdir(parents=True)
        staged = self.app.parent / f".{self.app.name}.installing.{version}"
        staged.mkdir()
        data = {"version": version, "zipPath": str(zipped), "extractedAppPath": str(extracted),
                "stagedAppPath": str(staged)}
        (self.install.cache / "pending-update.json").write_text(json.dumps(data))
        (self.install.cache / "install-in-progress.json").write_text(json.dumps({**data, "phase": phase}))
        return zipped, extracted.parent, staged

    def test_inspect_and_preview_leave_files_untouched(self):
        package, _, _ = self.pending()
        before = self.config.read_bytes()
        self.assertTrue(self.install.inspect()["supportedLayout"])
        result = u.disable(self.install, self.backups)
        self.assertFalse(result["applied"])
        self.assertEqual(before, self.config.read_bytes())
        self.assertTrue(package.exists())
        self.assertFalse(self.backups.exists())

    def test_unknown_hash_fails_before_any_write(self):
        with self.asar.open("ab") as stream:
            stream.write(b"unrelated bytes")
        with patch.object(u, "KNOWN_UPDATER", "0" * 64):
            with self.assertRaises(u.Refused):
                u.disable(self.install, self.backups, apply=True)
        self.assertFalse(self.backups.exists())

    def test_unknown_version_fails_before_any_write(self):
        data = plistlib.loads(self.info.read_bytes())
        data["CFBundleShortVersionString"] = "6.0.0"
        self.info.write_bytes(plistlib.dumps(data))
        with self.assertRaises(u.Refused):
            u.disable(self.install, self.backups, apply=True)
        self.assertFalse(self.backups.exists())

    def test_running_app_refused(self):
        with patch.object(u, "running_pids", return_value=[123]):
            with self.assertRaises(u.Refused):
                u.disable(self.install, self.backups, apply=True)
        self.assertFalse(self.backups.exists())

    def test_install_in_progress_preserves_package(self):
        package, _, _ = self.pending("installing")
        with self.assertRaises(u.Refused):
            u.disable(self.install, self.backups, apply=True)
        self.assertTrue(package.exists())

    def test_metadata_cannot_delete_outside_paths(self):
        self.pending()
        outside = self.root / "important.txt"
        outside.write_text("keep")
        pending = self.install.cache / "pending-update.json"
        value = u.read_json(pending)
        value["zipPath"] = str(outside)
        pending.write_text(json.dumps(value))
        with self.assertRaises(u.Refused):
            u.disable(self.install, self.backups, apply=True)
        self.assertEqual(outside.read_text(), "keep")
        self.assertFalse(self.backups.exists())

    def test_unknown_downloads_preserved(self):
        (self.install.downloads / "user-file").write_text("keep")
        with self.assertRaises(u.Refused):
            u.disable(self.install, self.backups, apply=True)
        self.assertFalse(self.backups.exists())

    def test_symlink_download_directory_refused(self):
        self.install.downloads.rmdir()
        outside = self.root / "outside"
        outside.mkdir()
        self.install.downloads.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(u.Refused):
            self.install.inspect()

    def test_malformed_configuration_preserved(self):
        self.config.write_text("{bad")
        with self.assertRaises(ValueError):
            u.disable(self.install, self.backups, apply=True)
        self.assertEqual(self.config.read_text(), "{bad")

    def test_invalid_asar_size_refused(self):
        self.asar.write_bytes(struct.pack("<4I", 4, 4, 4, 200000000))
        with self.assertRaises(u.Refused):
            self.install.inspect()

    @unittest.skipUnless(sys.platform == "darwin", "requires macOS immutable flags")
    def test_disable_blocks_mkdir_then_restore_preserves_new_settings(self):
        targets = self.pending()
        result = u.disable(self.install, self.backups, apply=True)
        backup = Path(result["backup"])
        self.assertTrue(all(not p.exists() for p in targets))
        self.assertFalse(u.read_json(self.config)[u.KEY])
        self.assertTrue(result["state"]["downloadGuardImmutable"])
        # This is the updater's directory precondition, before its package fetch.
        with self.assertRaises(FileExistsError):
            self.install.downloads.mkdir(parents=True, exist_ok=True)
        with self.assertRaises(PermissionError):
            self.install.downloads.unlink()
        snapshot_text = (backup / "before.json").read_text()
        self.assertNotIn("privateFixture", snapshot_text)
        config = u.read_json(self.config)
        config["theme"] = "light"
        u.atomic_json(self.config, config)
        u.restore(self.install, backup, apply=True)
        restored = u.read_json(self.config)
        self.assertNotIn(u.KEY, restored)
        self.assertEqual(restored["theme"], "light")
        self.assertEqual(restored["privateFixture"], "stay-local")
        self.assertTrue(self.install.downloads.is_dir())

    @unittest.skipUnless(sys.platform == "darwin", "requires macOS immutable flags")
    def test_disable_is_idempotent(self):
        u.disable(self.install, self.backups, apply=True)
        result = u.disable(self.install, self.backups, apply=True)
        self.assertTrue(result["alreadyDisabled"])
        self.assertEqual(len(list(self.backups.iterdir())), 1)

    @unittest.skipUnless(sys.platform == "darwin", "requires macOS immutable flags")
    def test_restore_rejects_backup_from_other_install(self):
        result = u.disable(self.install, self.backups, apply=True)
        backup = Path(result["backup"])
        snapshot = u.read_json(backup / "before.json")
        snapshot["config"] = "/outside/app-config.json"
        u.atomic_json(backup / "before.json", snapshot)
        with self.assertRaises(u.Refused):
            u.restore(self.install, backup, apply=True)
        self.assertFalse(u.read_json(self.config)[u.KEY])

    @unittest.skipUnless(sys.platform == "darwin", "requires macOS immutable flags")
    def test_restore_preserves_setting_changed_after_disable(self):
        result = u.disable(self.install, self.backups, apply=True)
        config = u.read_json(self.config)
        config[u.KEY] = True
        u.atomic_json(self.config, config)
        with self.assertRaises(u.Refused):
            u.restore(self.install, Path(result["backup"]), apply=True)
        self.assertTrue(u.read_json(self.config)[u.KEY])


if __name__ == "__main__":
    unittest.main()
