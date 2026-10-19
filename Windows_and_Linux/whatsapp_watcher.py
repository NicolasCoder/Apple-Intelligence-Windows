"""
Multi-Browser WhatsApp .ogg Download Watcher
--------------------------------------------
Automatically discovers download directories across all installed browsers on Windows & Linux
(Chrome, Chrome Beta/Canary, Edge, Brave, Opera/Opera GX, Vivaldi, Arc, Firefox, Zen Browser,
Floorp, Waterfox, LibreWolf, etc.), as well as OS Shell Downloads/Desktop folders and all
mounted drives' `Downloads` folders (`C:\\Downloads`, `D:\\Downloads`, `F:\\Downloads`, etc.).

Watches for newly downloaded `.ogg` audio files in real time and triggers automatic
Faster-Whisper transcription + Apple Intelligence corner toast popup.
"""

import glob
import json
import logging
import os
import re
import string
import sys
import threading
import time
from PySide6 import QtCore


class WhatsAppOggWatcher(QtCore.QObject):
    """
    Watches all browser download directories for newly downloaded `.ogg` files
    and emits `ogg_detected(file_path)` once the download is complete.
    """
    ogg_detected = QtCore.Signal(str)

    def __init__(self, parent=None, enabled: bool = True, extra_dirs: list | None = None):
        super().__init__(parent)
        self.enabled = enabled
        self.extra_dirs = list(extra_dirs or [])
        self._watched_dirs: set[str] = set()
        self._seen_files: dict[str, tuple[float, int]] = {}
        self._in_progress: set[str] = set()
        self._lock = threading.Lock()
        self._start_ts = time.time()
        self._last_browser_pref_scan = 0.0

        self._fs_watcher = QtCore.QFileSystemWatcher(self)
        self._fs_watcher.directoryChanged.connect(self._on_directory_changed)

        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.setInterval(1200)
        self._poll_timer.timeout.connect(self._poll_tick)

        self.refresh_directories(initial=True)
        if self.enabled:
            self._poll_timer.start()

    @staticmethod
    def discover_all_download_dirs(extra_dirs: list | None = None) -> list[str]:
        """
        Discover all download and save-file directories configured across:
        1. Windows Registry User Shell Folders (Downloads, Desktop, Documents)
        2. All mounted drives' `<Drive>:\\Downloads` and `<Drive>:\\Download`
        3. All Chromium-based browsers' `Preferences` (`download.default_directory` & `savefile.default_directory`)
        4. All Firefox / Zen / Gecko browsers' `prefs.js` (`browser.download.dir` & `browser.download.lastDir`)
        5. Standard `~/Downloads` and `~/Desktop`
        6. Any user-configured `extra_dirs`
        """
        found: set[str] = set()

        def _add_dir(path_str: str | None):
            if not path_str:
                return
            try:
                expanded = os.path.abspath(os.path.expandvars(os.path.expanduser(path_str.strip())))
                if os.path.isdir(expanded):
                    # Avoid watching root drives like C:\ directly
                    if len(expanded.rstrip("\\/")) > 3:
                        found.add(expanded)
            except Exception:
                pass

        # 1. Standard user folders
        for std in ("~/Downloads", "~/Download", "~/Desktop", "~/Área de Trabalho"):
            _add_dir(std)

        # 2. Windows Registry Shell Folders + Mounted Drives' Downloads folders
        if sys.platform.startswith("win"):
            try:
                import winreg
                reg_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_path) as k:
                    for val_name in (
                        "{374DE290-123F-4565-9164-39C4925E467B}",  # Downloads
                        "{7D83EE9B-2244-4E70-B1F5-5393042AF1E4}",  # Downloads alt GUID
                        "Desktop",
                    ):
                        try:
                            val, _ = winreg.QueryValueEx(k, val_name)
                            _add_dir(val)
                        except Exception:
                            pass
            except Exception:
                pass

            # Check <Drive>:\Downloads on all mounted drives (C:, D:, E:, F:, etc.)
            for letter in string.ascii_uppercase:
                drive_root = f"{letter}:\\"
                if os.path.exists(drive_root):
                    for sub in ("Downloads", "Download", "WhatsApp", "Audios WhatsApp"):
                        cand = os.path.join(drive_root, sub)
                        if os.path.isdir(cand):
                            _add_dir(cand)

        # 3. Chromium-based browsers (Chrome, Chrome Beta, Edge, Brave, Opera, Opera GX, Vivaldi, Arc, etc.)
        local_app = os.environ.get("LOCALAPPDATA", "")
        roaming_app = os.environ.get("APPDATA", "")
        pref_patterns = []
        if local_app:
            pref_patterns.extend([
                os.path.join(local_app, "*", "*", "User Data", "*", "Preferences"),
                os.path.join(local_app, "*", "User Data", "*", "Preferences"),
            ])
        if roaming_app:
            pref_patterns.extend([
                os.path.join(roaming_app, "Opera Software", "*", "Preferences"),
                os.path.join(roaming_app, "*", "User Data", "*", "Preferences"),
                os.path.join(roaming_app, "*", "*", "User Data", "*", "Preferences"),
            ])
        if not sys.platform.startswith("win"):
            cfg_home = os.path.expanduser("~/.config")
            pref_patterns.extend([
                os.path.join(cfg_home, "*", "*", "Preferences"),
                os.path.join(cfg_home, "*", "Default", "Preferences"),
            ])

        for pat in pref_patterns:
            for pref_file in glob.glob(pat):
                try:
                    with open(pref_file, "r", encoding="utf-8", errors="ignore") as f:
                        data = json.load(f)
                    for section in ("download", "savefile"):
                        sec_obj = data.get(section)
                        if isinstance(sec_obj, dict):
                            _add_dir(sec_obj.get("default_directory"))
                except Exception:
                    continue

        # 4. Firefox / Zen Browser / Floorp / Waterfox / LibreWolf (Gecko prefs.js)
        gecko_patterns = []
        if roaming_app:
            gecko_patterns.extend([
                os.path.join(roaming_app, "*", "Profiles", "*", "prefs.js"),
                os.path.join(roaming_app, "*", "*", "Profiles", "*", "prefs.js"),
            ])
        else:
            gecko_patterns.extend([
                os.path.expanduser("~/.mozilla/firefox/*/prefs.js"),
                os.path.expanduser("~/.zen/*/prefs.js"),
            ])

        pref_regex = re.compile(
            r'user_pref\("(?:browser\.download\.dir|browser\.download\.lastDir)",\s*"([^"]+)"\)'
        )
        for pat in gecko_patterns:
            for prefs_js in glob.glob(pat):
                try:
                    with open(prefs_js, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    for match in pref_regex.findall(content):
                        cleaned = match.replace("\\\\", "\\")
                        _add_dir(cleaned)
                except Exception:
                    continue

        # 5. User-supplied extra directories
        for extra in (extra_dirs or []):
            _add_dir(extra)

        return sorted(found)

    def set_enabled(self, enabled: bool):
        self.enabled = bool(enabled)
        if self.enabled:
            self._start_ts = time.time()
            self.refresh_directories(initial=True)
            if not self._poll_timer.isActive():
                self._poll_timer.start()
            logging.info(f"WhatsApp .ogg Watcher ENABLED. Watching {len(self._watched_dirs)} directories.")
        else:
            self._poll_timer.stop()
            logging.info("WhatsApp .ogg Watcher DISABLED.")

    def get_watched_directories(self) -> list[str]:
        return sorted(self._watched_dirs)

    def refresh_directories(self, initial: bool = False):
        """Refresh the list of browser download directories and snapshot existing .ogg files if initial."""
        discovered = set(self.discover_all_download_dirs(self.extra_dirs))
        new_dirs = discovered - self._watched_dirs
        if new_dirs:
            for d in new_dirs:
                try:
                    self._fs_watcher.addPath(d)
                except Exception:
                    pass
                # Snapshot existing .ogg files when first adding a directory so old files don't fire
                if initial:
                    self._snapshot_existing_oggs(d)
            self._watched_dirs.update(new_dirs)
            logging.info(f"WhatsAppOggWatcher added directories: {sorted(new_dirs)}")
        elif initial:
            for d in self._watched_dirs:
                self._snapshot_existing_oggs(d)
        self._last_browser_pref_scan = time.time()

    def _snapshot_existing_oggs(self, directory: str):
        """Record existing .ogg files so only newly downloaded ones trigger transcription."""
        try:
            for entry in os.scandir(directory):
                if entry.is_file() and entry.name.lower().endswith(".ogg"):
                    try:
                        st = entry.stat()
                        norm = os.path.normcase(os.path.abspath(entry.path))
                        self._seen_files[norm] = (st.st_mtime, st.st_size)
                    except Exception:
                        pass
        except Exception:
            pass

    @QtCore.Slot(str)
    def _on_directory_changed(self, directory: str):
        if not self.enabled:
            return
        self._scan_directory(directory)

    def _poll_tick(self):
        if not self.enabled:
            return
        # Every 8 seconds, re-check browser Preferences/prefs.js in case user saved to a new folder
        if time.time() - self._last_browser_pref_scan > 8.0:
            self.refresh_directories(initial=False)

        for d in list(self._watched_dirs):
            self._scan_directory(d)

    def _scan_directory(self, directory: str):
        if not os.path.isdir(directory):
            return
        try:
            for entry in os.scandir(directory):
                if entry.is_file() and entry.name.lower().endswith(".ogg"):
                    self._check_candidate_ogg(entry.path)
        except Exception as e:
            logging.debug(f"Scan error in {directory}: {e}")

    def _check_candidate_ogg(self, file_path: str):
        norm = os.path.normcase(os.path.abspath(file_path))
        try:
            st = os.stat(file_path)
        except Exception:
            return

        # Must be non-empty and created/modified recently (after watcher started - 2s)
        if st.st_size < 400:
            return

        with self._lock:
            if norm in self._in_progress:
                return
            prev = self._seen_files.get(norm)
            if prev is not None:
                prev_mtime, prev_size = prev
                if abs(st.st_mtime - prev_mtime) < 0.5 and st.st_size == prev_size:
                    return
            else:
                # If never seen before, check if it's an old file from before the watcher started
                if st.st_mtime < (self._start_ts - 3.0) and st.st_ctime < (self._start_ts - 3.0):
                    self._seen_files[norm] = (st.st_mtime, st.st_size)
                    return

            self._in_progress.add(norm)

        # Verify in a short background thread that the browser has finished writing the file
        threading.Thread(
            target=self._verify_and_emit,
            args=(file_path, norm),
            daemon=True
        ).start()

    def _verify_and_emit(self, file_path: str, norm: str):
        try:
            # Wait up to 4 seconds for file size to stabilize and file to be unlocked
            prev_size = -1
            stable_count = 0
            for _ in range(16):
                if not os.path.exists(file_path):
                    return
                try:
                    st = os.stat(file_path)
                    cur_size = st.st_size
                except Exception:
                    time.sleep(0.25)
                    continue

                if cur_size > 400 and cur_size == prev_size:
                    # Also check we can open the file for reading (not locked exclusively by browser)
                    try:
                        with open(file_path, "rb") as f:
                            header = f.read(4)
                        if header == b"OggS":
                            stable_count += 1
                            if stable_count >= 1:
                                with self._lock:
                                    self._seen_files[norm] = (st.st_mtime, cur_size)
                                logging.info(f"New WhatsApp .ogg download ready: {file_path} ({cur_size} bytes)")
                                self.ogg_detected.emit(os.path.abspath(file_path))
                                return
                    except Exception:
                        pass
                else:
                    stable_count = 0
                prev_size = cur_size
                time.sleep(0.25)
        finally:
            with self._lock:
                self._in_progress.discard(norm)
