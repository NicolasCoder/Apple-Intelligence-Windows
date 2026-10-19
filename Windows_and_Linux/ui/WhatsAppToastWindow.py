"""
Apple Intelligence Corner Toast Popup for Automatic WhatsApp .ogg Transcriptions
--------------------------------------------------------------------------------
Appears smoothly in the bottom-right corner of the primary screen (just above the
Windows taskbar clock) whenever a new `.ogg` audio file is downloaded in any browser.
Shows a 3D Thinking Orb while Faster-Whisper + Groq transcribe the audio, then
triggers an iridescent particle burst + fast AI word-by-word reveal with 1-click Copy/Paste.
"""

import json
import logging
import os
import sys
import pyperclip
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QUrl

try:
    from PySide6.QtWebEngineCore import QWebEnginePage
    from PySide6.QtWebEngineWidgets import QWebEngineView
    HAS_WEBENGINE = True
except Exception:
    HAS_WEBENGINE = False


class _ToastBridgePage(QWebEnginePage):
    ipc_received = QtCore.Signal(dict)

    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
        if message and message.startswith("WT_TOAST_IPC:"):
            try:
                data = json.loads(message[len("WT_TOAST_IPC:"):])
                self.ipc_received.emit(data)
            except Exception as e:
                logging.error(f"Toast IPC parse error: {e}")


class WhatsAppToastWindow(QtWidgets.QWidget):
    """
    Bottom-right floating glass notification card for WhatsApp `.ogg` transcriptions.
    """
    TOAST_WIDTH = 400
    TOAST_HEIGHT_TRANSCRIBING = 132
    TOAST_HEIGHT_RESULT = 215

    def __init__(self, app):
        super().__init__(None)
        self.app = app
        self._page_ready = False
        self._pending_js: list[str] = []
        self._current_text = ""
        self._current_filename = ""

        self._auto_close_timer = QtCore.QTimer(self)
        self._auto_close_timer.setSingleShot(True)
        self._auto_close_timer.timeout.connect(self.hide)

        self.init_ui()

    def init_ui(self):
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setWindowTitle("WhatsApp Audio AI")
        self.setFixedSize(self.TOAST_WIDTH, self.TOAST_HEIGHT_TRANSCRIBING)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if HAS_WEBENGINE:
            self.web_view = QWebEngineView(self)
            self.web_page = _ToastBridgePage(self.web_view)
            self.web_page.ipc_received.connect(self._on_ipc)
            self.web_view.setPage(self.web_page)
            self.web_view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.web_view.setStyleSheet("background: transparent; border: none;")
            self.web_page.setBackgroundColor(Qt.GlobalColor.transparent)
            self.web_view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

            base_root = getattr(
                sys,
                "_MEIPASS",
                os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
            )
            html_path = os.path.abspath(os.path.join(base_root, "electron_ui", "toast.html"))
            if not os.path.exists(html_path):
                html_path = os.path.abspath(
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "electron_ui", "toast.html")
                )
            self.web_view.loadFinished.connect(self._on_load_finished)
            self.web_view.load(QUrl.fromLocalFile(html_path))
            layout.addWidget(self.web_view)

    def _run_js(self, code: str):
        if not HAS_WEBENGINE:
            return
        if self._page_ready:
            self.web_page.runJavaScript(code)
        else:
            self._pending_js.append(code)

    def _on_load_finished(self, ok: bool):
        self._page_ready = True
        while self._pending_js:
            js = self._pending_js.pop(0)
            self.web_page.runJavaScript(js)

    def _position_bottom_right(self, height: int):
        """Position the toast card neatly in the bottom-right corner of the primary screen."""
        screen = QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        x = avail.right() - self.TOAST_WIDTH - 16
        y = avail.bottom() - height - 16
        self.setFixedSize(self.TOAST_WIDTH, height)
        self.move(x, y)

    @QtCore.Slot(str)
    def show_transcribing(self, file_path: str):
        """Show the corner toast immediately in 'Transcribing...' state with the 3D Thinking Orb."""
        self._auto_close_timer.stop()
        self._current_filename = os.path.basename(file_path)
        self._current_text = ""

        self._position_bottom_right(self.TOAST_HEIGHT_TRANSCRIBING)
        fn_json = json.dumps(self._current_filename, ensure_ascii=False)
        self._run_js(f"window.startTranscribing({fn_json}, 'Transcrevendo áudio do WhatsApp...');")
        self.show()
        self.raise_()

    @QtCore.Slot(str)
    def update_status(self, status_text: str):
        st_json = json.dumps(status_text, ensure_ascii=False)
        self._run_js(f"window.updateStatus({st_json});")

    @QtCore.Slot(str)
    def show_result(self, text: str):
        """
        Expand the corner toast and trigger the Apple Intelligence particle burst +
        fast AI word-by-word reveal! Also copies the text to clipboard for convenience.
        """
        self._current_text = (text or "").strip()
        try:
            if self._current_text:
                pyperclip.copy(self._current_text)
        except Exception:
            pass

        self._position_bottom_right(self.TOAST_HEIGHT_RESULT)
        txt_json = json.dumps(self._current_text, ensure_ascii=False)
        fn_json = json.dumps(self._current_filename, ensure_ascii=False)
        self._run_js(f"window.showTranscribedResult({txt_json}, {fn_json});")
        self.show()
        self.raise_()

        # Auto-dismiss after 22 seconds unless closed earlier
        self._auto_close_timer.start(22000)

    def _on_ipc(self, data: dict):
        action = data.get("action")
        if action == "close":
            self._auto_close_timer.stop()
            self.hide()
        elif action == "copy":
            if self._current_text:
                try:
                    pyperclip.copy(self._current_text)
                except Exception:
                    pass
        elif action == "paste":
            self._auto_close_timer.stop()
            self.hide()
            if self._current_text:
                self.app.output_queue = ""
                self.app.output_ready_signal.emit(self._current_text)
