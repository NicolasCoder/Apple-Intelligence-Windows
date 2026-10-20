import ctypes
import logging
import os
import sys

# Ensure working directory is always the application folder (crucial when launched on Windows boot)
_APP_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))
try:
    os.chdir(_APP_DIR)
except Exception:
    pass

# Redirect stdout/stderr when running windowless under pythonw.exe (where sys.stdout/stderr are None)
# so tqdm / huggingface_hub / faster-whisper / transformers never crash on write/flush/encoding.
_LOG_FILE_PATH = os.path.join(_APP_DIR, "app_runtime.log")
try:
    if sys.stdout is None or sys.stderr is None:
        _log_stream = open(_LOG_FILE_PATH, "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = _log_stream
        if sys.stderr is None:
            sys.stderr = _log_stream
    else:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
except Exception:
    pass

# Disable console progress bars in background/GUI mode so downloads never block or crash
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TQDM_DISABLE", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

# Ensure Chromium / QtWebEngine works cleanly when attached to WinSta0\Default
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu-sandbox")


def attach_current_thread_to_default_desktop():
    """
    Ensure the calling thread is attached to the interactive Windows 'Default'
    desktop on WinSta0. Must be called BEFORE any GUI window / hook creation
    on that thread.
    """
    if sys.platform != 'win32':
        return
    try:
        user32 = ctypes.windll.user32
        DESKTOP_ALL = 0x01FF
        hdefault = user32.OpenDesktopW('Default', 0, True, DESKTOP_ALL)
        if hdefault:
            user32.SetThreadDesktop(hdefault)
    except Exception:
        pass


# Attach main thread BEFORE importing PySide6 / WritingToolApp!
attach_current_thread_to_default_desktop()

# Set up logging to both UTF-8 file and stream before importing app modules
try:
    _handlers = [logging.FileHandler(_LOG_FILE_PATH, encoding="utf-8")]
    if sys.stderr is not None:
        _handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=_handlers
    )
except Exception:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

from WritingToolApp import WritingToolApp  # noqa: E402


def main():
    """
    The main entry point of the application.
    """
    app = WritingToolApp(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
