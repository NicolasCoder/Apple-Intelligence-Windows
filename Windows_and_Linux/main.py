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

from WritingToolApp import WritingToolApp  # noqa: E402

# Set up logging to console
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')


def main():
    """
    The main entry point of the application.
    """
    app = WritingToolApp(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
