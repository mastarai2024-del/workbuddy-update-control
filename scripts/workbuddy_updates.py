#!/usr/bin/env python3
"""Inspect and reversibly block the verified macOS WorkBuddy updater. No network."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile

KNOWN_UPDATER = "73e624fefade28999e081ce3c0d635dd3474eb82bd5485dfa52495b7ac1c4af3"
BUNDLE = "com.tencent.workbuddy.mac"
MARKER = "workbuddy-update-control: downloads disabled\n"
LEGACY_MARKER = "Automatic WorkBuddy update downloads disabled by user request on 2026-10-28.\n"
VERSION = re.compile(r"\d+\.\d+\.\d+(?:\.\d+)*\Z")
KEY = "startupForceAutoUpdate"
IMMUTABLE = 2  # macOS UF_IMMUTABLE


class Refused(RuntimeError):
    pass


def regular_path(path):
    """Reject links at every existing component, including the final target."""
    path = Path(os.path.abspath(path))
    for part in [*reversed(path.parents), path]:
        if part.is_symlink():
            raise Refused(f"Symlink is outside the supported layout: {part}")
    return path


def read_json(path):
    regular_path(path)
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise Refused(f"Expected JSON object: {path}")
    return value


def atomic_json(path, value, mode=0o600):
    regular_path(path)
    if path.exists():
        mode = stat.S_IMODE(path.stat().st_mode)
    fd, tmp = tempfile.mkstemp(prefix=".update-control-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def asar_member(path, name):
    with path.open("rb") as stream:
        prefix = stream.read(16)
        if len(prefix) != 16:
            raise Refused("Invalid ASAR header")
        _, header_size, _, json_size = struct.unpack("<4I", prefix)
        if not 0 < json_size <= header_size <= 64 * 1024 * 1024:
            raise Refused("Invalid ASAR header size")
        entry = json.loads(stream.read(json_size))
        for component in name.split("/"):
            entry = entry["files"][component]
        if entry.get("unpacked") or entry["size"] > 1024 * 1024:
            raise Refused("Unsupported ASAR member")
        stream.seek(8 + header_size + int(entry["offset"]))
        content = stream.read(entry["size"])
        if len(content) != entry["size"]:
            raise Refused("Truncated ASAR member")
        return content


def running_pids(app):
    output = subprocess.check_output(["ps", "-axo", "pid=,comm="], text=True)
    prefix = str(app) + "/Contents/"
    result = []
    for line in output.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[1].startswith(prefix):
            result.append(int(parts[0]))
    return result


def require_stopped(app):
    pids = running_pids(app)
    if pids:
        raise Refused(f"Quit WorkBuddy before modifying files; active PIDs: {pids}")


def guard_state(path):
    regular_path(path)
    if not path.exists():
        return "missing"
    if path.is_dir():
        return "directory"
    if path.is_file() and path.stat().st_size < 1024 and path.read_text() in (MARKER, LEGACY_MARKER):
        return "guard"
    raise Refused(f"Unknown file occupies download path: {path}")


class Installation:
    def __init__(self, app, home, config_dir=None):
        self.app = regular_path(app)
        self.home = regular_path(home)
        self.config_dir = regular_path(config_dir or self.home / ".workbuddy")
        self.config = regular_path(self.config_dir / "app/app-config.json")
        self.cache = regular_path(self.home / "Library/Caches" / (BUNDLE + ".BundleMigration"))
        self.downloads = regular_path(self.cache / "downloads")

    def inspect(self):
        info_path = regular_path(self.app / "Contents/Info.plist")
        with info_path.open("rb") as stream:
            info = plistlib.load(stream)
        updater = asar_member(regular_path(self.app / "Contents/Resources/app.asar"),
                              "main/update-service.darwin.js")
        digest = hashlib.sha256(updater).hexdigest()
        supported = (info.get("CFBundleIdentifier") == BUNDLE
                     and info.get("CFBundleShortVersionString") == "5.9.2"
                     and digest == KNOWN_UPDATER)
        config = read_json(self.config)
        state = guard_state(self.downloads)
        pending = self.cache / "pending-update.json"
        lock = self.cache / "install-in-progress.json"
        return {"app": str(self.app), "version": info.get("CFBundleShortVersionString"),
                "bundleId": info.get("CFBundleIdentifier"), "updaterSha256": digest,
                "supportedLayout": supported, "config": str(self.config),
                "startupForceAutoUpdate": config.get(KEY), "downloadPath": str(self.downloads),
                "downloadPathState": state,
                "downloadGuardImmutable": state == "guard" and bool(self.downloads.stat().st_flags & IMMUTABLE),
                "pendingUpdateExists": pending.exists(), "installLockExists": lock.exists(),
                "activePids": running_pids(self.app)}

    def require_supported(self):
        result = self.inspect()
        if not result["supportedLayout"]:
            raise Refused("Unknown version or updater hash. Inspect the owning source before adding support.")
        return result

    def deletion_plan(self):
        paths = []
        pending = self.cache / "pending-update.json"
        lock = self.cache / "install-in-progress.json"
        metadata = [read_json(p) for p in (pending, lock) if p.exists()]
        versions = {item.get("version") for item in metadata}
        if len(versions) > 1:
            raise Refused("Pending update and installer lock disagree")
        version = next(iter(versions), None)
        if metadata:
            if not isinstance(version, str) or not VERSION.fullmatch(version):
                raise Refused("Invalid pending version")
            expected = {"zipPath": self.downloads / f"WorkBuddy-{version}.zip",
                        "extractedAppPath": self.cache / "extracted" / version / "WorkBuddy.app",
                        "stagedAppPath": self.app.parent / f".{self.app.name}.installing.{version}"}
            for item in metadata:
                if item.get("phase") not in (None, "prepared"):
                    raise Refused("An installer may be active; preserve files and investigate")
                for key, path in expected.items():
                    if item.get(key) != str(path):
                        raise Refused(f"Unexpected {key}; preserve files and investigate")
                    regular_path(path)
            paths += [expected["zipPath"], Path(str(expected["zipPath"]) + ".tmp"),
                      Path(str(expected["zipPath"]) + ".tmp.etag"),
                      self.cache / "extracted" / version, expected["stagedAppPath"], pending, lock]
        if guard_state(self.downloads) == "directory":
            allowed = {str(path) for path in paths if path.parent == self.downloads}
            if any(str(child) not in allowed for child in self.downloads.iterdir()):
                raise Refused("Unaccounted files in downloads; preserve them and investigate")
        for path in paths:
            regular_path(path)
        return [p for p in paths if p.exists()]


def disable(installation, backup_root, apply=False):
    before = installation.require_supported()
    require_stopped(installation.app)
    paths = installation.deletion_plan()
    plan = {"startupForceAutoUpdate": False, "remove": [str(p) for p in paths],
            "guardPath": str(installation.downloads), "applied": False}
    if not apply:
        return plan
    if (before["startupForceAutoUpdate"] is False and before["downloadGuardImmutable"] and not paths):
        return {**plan, "applied": True, "alreadyDisabled": True}
    backup_root = regular_path(backup_root)
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = Path(tempfile.mkdtemp(prefix="workbuddy-updates-", dir=backup_root))
    config = read_json(installation.config)
    snapshot = {"schema": 1, "config": str(installation.config), "app": str(installation.app),
                "home": str(installation.home), "configDir": str(installation.config_dir),
                "settingWasPresent": KEY in config, "settingValue": config.get(KEY),
                "downloadPathState": before["downloadPathState"], "remove": plan["remove"],
                "createdAt": dt.datetime.now(dt.timezone.utc).isoformat(), "phase": "prepared"}
    snapshot_path = backup / "before.json"
    atomic_json(snapshot_path, snapshot)
    try:
        require_stopped(installation.app)
        config[KEY] = False
        atomic_json(installation.config, config)
        snapshot["phase"] = "setting-disabled"
        atomic_json(snapshot_path, snapshot)
        for path in paths:
            regular_path(path)
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        snapshot["phase"] = "packages-removed"
        atomic_json(snapshot_path, snapshot)
        state = guard_state(installation.downloads)
        if state == "directory":
            installation.downloads.rmdir()
        if state != "guard":
            installation.cache.mkdir(parents=True, exist_ok=True)
            with installation.downloads.open("x") as stream:
                stream.write(MARKER)
            installation.downloads.chmod(0o400)
        os.chflags(installation.downloads, installation.downloads.stat().st_flags | IMMUTABLE)
        snapshot["phase"] = "complete"
        atomic_json(snapshot_path, snapshot)
    except Exception as exc:
        raise Refused(f"Partially applied; recovery snapshot: {snapshot_path}; {exc}") from exc
    return {**plan, "applied": True, "backup": str(backup), "state": installation.inspect()}


def restore(installation, backup, apply=False):
    require_stopped(installation.app)
    snapshot = read_json(regular_path(backup) / "before.json")
    expected = {"schema": 1, "app": str(installation.app), "home": str(installation.home),
                "config": str(installation.config), "configDir": str(installation.config_dir)}
    if any(snapshot.get(k) != v for k, v in expected.items()):
        raise Refused("Backup belongs to another installation or unsupported schema")
    state = guard_state(installation.downloads)
    config = read_json(installation.config)
    if KEY in config and config[KEY] is not False:
        raise Refused("Setting changed after disable; review before restoring")
    original = snapshot.get("downloadPathState")
    if original not in ("missing", "directory", "guard"):
        raise Refused("Invalid original download path state")
    if state == "directory" and any(installation.downloads.iterdir()):
        raise Refused("Download directory contains new files; preserve them")
    result = {"applied": False, "backup": str(backup), "deletedPackagesRestored": False}
    if not apply:
        return result
    if state == "guard" and original != "guard":
        os.chflags(installation.downloads, installation.downloads.stat().st_flags & ~IMMUTABLE)
        installation.downloads.unlink()
    if original == "directory":
        installation.downloads.mkdir(parents=True, exist_ok=True)
    if snapshot["settingWasPresent"]:
        config[KEY] = snapshot["settingValue"]
    else:
        config.pop(KEY, None)
    atomic_json(installation.config, config)
    return {**result, "applied": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["inspect", "disable", "verify", "restore"])
    parser.add_argument("--app", type=Path, default=Path("/Applications/WorkBuddy.app"))
    parser.add_argument("--config-dir", type=Path, help="Actual WorkBuddy config root, if customized")
    parser.add_argument("--backup-root", type=Path)
    parser.add_argument("--backup", type=Path, help="Snapshot directory used by restore")
    parser.add_argument("--apply", action="store_true", help="Perform disable/restore; otherwise preview only")
    args = parser.parse_args(argv)
    try:
        if sys.platform != "darwin":
            raise Refused("macOS only; other platforms require a different updater investigation")
        home = Path.home().resolve()
        installation = Installation(args.app, home, args.config_dir)
        if args.command == "disable":
            root = args.backup_root or home / "Library/Application Support/workbuddy-update-control/backups"
            result = disable(installation, root, args.apply)
        elif args.command == "restore":
            if args.backup is None:
                raise Refused("restore requires --backup")
            result = restore(installation, args.backup, args.apply)
        else:
            result = installation.inspect()
            if args.command == "verify":
                result["fileChecksPassed"] = (result["supportedLayout"]
                    and result["startupForceAutoUpdate"] is False and result["downloadGuardImmutable"]
                    and not result["pendingUpdateExists"] and not result["installLockExists"])
                result["runtimeVerification"] = "Restart and inspect fresh updater logs and UI separately."
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if args.command != "verify" or result["fileChecksPassed"] else 1
    except (Refused, OSError, ValueError, KeyError) as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
