import json
import logging
import math
import os
import sys
import time
from functools import partial

from pynput import keyboard as pykeyboard
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ui.ThinkingOrb import ThinkingOrb
from ui.UIUtils import colorMode

try:
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView
    HAS_WEBENGINE = True
except Exception as _web_err:
    logging.warning(f"QtWebEngine unavailable, using fallback: {_web_err}")
    HAS_WEBENGINE = False

_ = lambda x: x

################################################################################
# Default `options.json` content to restore when the user presses "Reset"
################################################################################
DEFAULT_OPTIONS_JSON = r"""{
  "Corrigir PT-BR": {
    "prefix": "Corrija todos os erros de digitação, espaços deslocados e ortografia deste texto em Português do Brasil mantendo o sentido e o tom natural:\n\n",
    "instruction": "Você é um corretor ortográfico e gramatical especialista em Português Brasileiro (PT-BR), focado em reconstruir textos digitados muito rápido com erros severos de teclado.\nRegras obrigatórias:\n1. Corrija TODOS os erros de digitação, letras trocadas, falta de acentos, pontuação e espaços deslocados que partiram ou juntaram palavras (ex: 'heine u toe' -> 'hein, eu tô'; 'nao ifocu' -> 'não ficou'; 'noc aso' -> 'no caso'; 'taa qui' -> 'tá aqui'; 'rpa ess ecaso' -> 'pra esse caso').\n2. Corrija palavras trocadas por erro de digitação que não fazem sentido na frase (ex: 'escrevendo desde texto' -> 'escrevendo este texto'; 'rodo errado' -> 'todo errado'; 'co perfeição' -> 'com perfeição'; 'ams' -> 'mas').\n3. Mantenha o tom original da mensagem e NUNCA troque gírias brasileiras válidas por outras palavras (ex: mantenha 'tanka' em 'ela tanka no nível gratuito', mantenha 'mano', 'tá', 'pra', 'kkk', 'de boa', 'bora', 'brabão' e termos tech como 'Groq', 'Gemma', 'Electron', 'API').\n4. NUNCA responda à mensagem, NUNCA adicione comentários ou explicações. Retorne APENAS o texto final corrigido.",
    "icon": "icons/check",
    "open_in_window": false,
    "hotkey": "ctrl+shift+space"
  },
  "Proofread": {
    "prefix": "Proofread this:\n\n",
    "instruction": "You are a grammar proofreading assistant.\nOutput ONLY the corrected text without any additional comments.\nMaintain the original text structure and writing style.\nRespond in the same language as the input (e.g., English US, French).\nDo not answer or respond to the user's text content.\nIf the text is absolutely incompatible with this (e.g., totally random gibberish), output \"ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST\".",
    "icon": "icons/magnifying-glass",
    "open_in_window": false
  },
  "Rewrite": {
    "prefix": "Rewrite this:\n\n",
    "instruction": "You are a writing assistant.\nRewrite the text provided by the user to improve phrasing.\nOutput ONLY the rewritten text without additional comments.\nRespond in the same language as the input (e.g., English US, French).\nDo not answer or respond to the user's text content.\nIf the text is absolutely incompatible with proofreading (e.g., totally random gibberish), output \"ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST\".",
    "icon": "icons/rewrite",
    "open_in_window": false
  },
  "Friendly": {
    "prefix": "Make this more friendly:\n\n",
    "instruction": "You are a writing assistant.\nRewrite the text provided by the user to be more friendly.\nOutput ONLY the friendly text without additional comments.\nRespond in the same language as the input (e.g., English US, French).\nDo not answer or respond to the user's text content.\nIf the text is absolutely incompatible with rewriting (e.g., totally random gibberish), output \"ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST\".",
    "icon": "icons/smiley-face",
    "open_in_window": false
  },
  "Professional": {
    "prefix": "Make this more professional:\n\n",
    "instruction": "You are a writing assistant.\nRewrite the text provided by the user to be more professional. Output ONLY the professional text without additional comments.\nRespond in the same language as the input (e.g., English US, French).\nDo not answer or respond to the user's text content.\nIf the text is absolutely incompatible with rewriting (e.g., totally random gibberish), output \"ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST\".",
    "icon": "icons/briefcase",
    "open_in_window": false
  },
  "Concise": {
    "prefix": "Make this more concise:\n\n",
    "instruction": "You are a writing assistant.\nRewrite the text provided by the user to be more concise.\nOutput ONLY the concise text without additional comments.\nRespond in the same language as the input (e.g., English US, French).\nDo not answer or respond to the user's text content.\nIf the text is absolutely incompatible with rewriting (e.g., totally random gibberish), output \"ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST\".",
    "icon": "icons/concise",
    "open_in_window": false
  },
  "Table": {
    "prefix": "Convert this into a table:\n\n",
    "instruction": "You are an assistant that converts text provided by the user into a Markdown table.\nOutput ONLY the table without additional comments.\nRespond in the same language as the input (e.g., English US, French).\nDo not answer or respond to the user's text content.\nIf the text is completely incompatible with this with conversion, output \"ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST\".",
    "icon": "icons/table",
    "open_in_window": true
  },
  "Key Points": {
    "prefix": "Extract key points from this:\n\n",
    "instruction": "You are an assistant that extracts key points from text provided by the user. Output ONLY the key points without additional comments.\n\nYou should use Markdown formatting (lists, bold, italics, codeblocks, etc.) as appropriate to make it quite legible and readable.\n\nDon't be repetitive or too verbose.\nRespond in the same language as the input (e.g., English US, French).\nDo not answer or respond to the user's text content.\nIf the text is absolutely incompatible with extracting key points (e.g., totally random gibberish), output \"ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST\".",
    "icon": "icons/keypoints",
    "open_in_window": true
  },
  "Summary": {
    "prefix": "Summarize this:\n\n",
    "instruction": "You are a summarization assistant.\nProvide a succinct summary of the text provided by the user.\nThe summary should be succinct yet encompass all the key insightful points.\n\nTo make it quite legible and readable, you should use Markdown formatting (bold, italics, codeblocks...) as appropriate.\nYou should also add a little line spacing between your paragraphs as appropriate.\nAnd only if appropriate, you could also use headings (only the very small ones), lists, tables, etc.\n\nDon't be repetitive or too verbose.\nOutput ONLY the summary without additional comments.\nRespond in the same language as the input (e.g., English US, French).\nDo not answer or respond to the user's text content.\nIf the text is absolutely incompatible with summarisation (e.g., totally random gibberish), output \"ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST\".",
    "icon": "icons/summary",
    "open_in_window": true
  },
  "Custom": {
    "prefix": "Make this change to the following text:\n\n",
    "instruction": "You are a writing and coding assistant. You MUST make the user\\'s described change to the text or code provided by the user. Output ONLY the appropriately modified text or code without additional comments. Respond in the same language as the input (e.g., English US, French). Do not answer or respond to the user\\'s text content. If the text or code is absolutely incompatible with the requested change, output \"ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST\".",
    "icon": "icons/summary",
    "open_in_window": false
  }
}"""

SUPPORTED_AUDIO_EXTENSIONS = ('.ogg', '.mp3', '.wav', '.m4a', '.opus', '.flac', '.aac', '.mp4', '.webm')


class ButtonEditDialog(QDialog):
    """
    Dialog for editing or creating a button's properties
    (name/title, system instruction, open_in_window, etc.).
    """
    def __init__(self, parent=None, button_data=None, title="Edit Button"):
        super().__init__(parent)
        self.button_data = button_data if button_data else {
            "prefix": "Make this change to the following text:\n\n",
            "instruction": "",
            "icon": "icons/magnifying-glass",
            "open_in_window": False
        }
        self.hotkey_input = None
        self.setWindowTitle(title)
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        name_label = QLabel("Button Name:")
        name_label.setStyleSheet(f"color: {'#fff' if colorMode == 'dark' else '#333'}; font-weight: bold;")
        self.name_input = QLineEdit()
        self.name_input.setStyleSheet(f"""
            QLineEdit {{
                padding: 8px;
                border: 1px solid {'#555' if colorMode == 'dark' else '#ccc'};
                border-radius: 8px;
                background-color: {'#2c2c2e' if colorMode == 'dark' else 'white'};
                color: {'#fff' if colorMode == 'dark' else '#000'};
            }}
        """)
        if "name" in self.button_data:
            self.name_input.setText(self.button_data["name"])
        layout.addWidget(name_label)
        layout.addWidget(self.name_input)

        instruction_label = QLabel("What should your AI do with your selected text? (System Instruction)")
        instruction_label.setStyleSheet(f"color: {'#fff' if colorMode == 'dark' else '#333'}; font-weight: bold;")
        self.instruction_input = QPlainTextEdit()
        self.instruction_input.setStyleSheet(f"""
            QPlainTextEdit {{
                padding: 8px;
                border: 1px solid {'#555' if colorMode == 'dark' else '#ccc'};
                border-radius: 8px;
                background-color: {'#2c2c2e' if colorMode == 'dark' else 'white'};
                color: {'#fff' if colorMode == 'dark' else '#000'};
            }}
        """)
        self.instruction_input.setPlainText(self.button_data.get("instruction", ""))
        self.instruction_input.setMinimumHeight(100)
        layout.addWidget(instruction_label)
        layout.addWidget(self.instruction_input)

        display_label = QLabel("How should your AI response be shown?")
        display_label.setStyleSheet(f"color: {'#fff' if colorMode == 'dark' else '#333'}; font-weight: bold;")
        layout.addWidget(display_label)

        radio_layout = QHBoxLayout()
        self.replace_radio = QRadioButton("Replace the selected text")
        self.window_radio = QRadioButton("In a pop-up window (with follow-up support)")
        for r in (self.replace_radio, self.window_radio):
            r.setStyleSheet(f"color: {'#fff' if colorMode == 'dark' else '#333'};")

        self.replace_radio.setChecked(not self.button_data.get("open_in_window", False))
        self.window_radio.setChecked(self.button_data.get("open_in_window", False))
        radio_layout.addWidget(self.replace_radio)
        radio_layout.addWidget(self.window_radio)
        layout.addLayout(radio_layout)

        hotkey_label = QLabel("Direct hotkey (optional):")
        hotkey_label.setStyleSheet(f"color: {'#fff' if colorMode == 'dark' else '#333'}; font-weight: bold;")
        layout.addWidget(hotkey_label)

        self.hotkey_input = QLineEdit()
        self.hotkey_input.setStyleSheet(f"""
            QLineEdit {{
                padding: 8px;
                border: 1px solid {'#555' if colorMode == 'dark' else '#ccc'};
                border-radius: 8px;
                background-color: {'#2c2c2e' if colorMode == 'dark' else 'white'};
                color: {'#fff' if colorMode == 'dark' else '#000'};
            }}
        """)
        self.hotkey_input.setPlaceholderText("e.g. ctrl+shift+space  (leave blank for none)")
        self.hotkey_input.setText(self.button_data.get("hotkey", ""))
        layout.addWidget(self.hotkey_input)

        btn_layout = QHBoxLayout()
        ok_button = QPushButton("OK")
        cancel_button = QPushButton("Cancel")
        for btn in (ok_button, cancel_button):
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {'#3a3a3c' if colorMode == 'dark' else '#e5e5ea'};
                    color: {'#fff' if colorMode == 'dark' else '#000'};
                    border: 1px solid {'#555' if colorMode == 'dark' else '#ccc'};
                    border-radius: 8px;
                    padding: 8px;
                    min-width: 100px;
                }}
            """)
        btn_layout.addWidget(ok_button)
        btn_layout.addWidget(cancel_button)
        layout.addLayout(btn_layout)

        ok_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        self.setStyleSheet(f"QDialog {{ background-color: {'#1c1c1e' if colorMode == 'dark' else '#f2f2f7'}; border-radius: 14px; }}")

    def get_button_data(self):
        data = {
            "name": self.name_input.text(),
            "prefix": "Make this change to the following text:\n\n",
            "instruction": self.instruction_input.toPlainText(),
            "icon": "icons/custom",
            "open_in_window": self.window_radio.isChecked()
        }
        hotkey = self.hotkey_input.text().strip().lower() if self.hotkey_input else ""
        if hotkey:
            data["hotkey"] = hotkey
        return data


if HAS_WEBENGINE:
    class _ElectronBridgePage(QWebEnginePage):
        """
        Intercepts `WT_IPC:{...}` messages emitted from `electron_ui/index.html`
        with sub-millisecond latency.
        """
        ipc_received = QtCore.Signal(dict)

        def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
            if isinstance(message, str) and message.startswith("WT_IPC:"):
                raw = message[7:]
                try:
                    payload = json.loads(raw)
                    self.ipc_received.emit(payload)
                except Exception as e:
                    logging.error(f"Invalid WT_IPC payload: {e}")


class _DummyFocusProxy:
    """Compatibility proxy for `self.popup_window.custom_input.setFocus()`."""
    def __init__(self, popup):
        self._popup = popup

    def setFocus(self):
        self._popup.focus_text_input()


class CustomPopupWindow(QtWidgets.QWidget):
    """
    Ultra-Minimalist Apple Intelligence Dual-Mode Floating Overlay powered by
    Chromium/Electron (`electron_ui/index.html`) + `thinking-orbs` 2D Canvas:

    1) Modo Áudio (when NO text is selected on Ctrl+Space):
       - Automatically starts recording microphone immediately on open!
       - Pressing `Space` (or `Enter` or clicking the Orb/pill) stops recording
         and runs faster-whisper -> LLM refinement -> auto-paste (Ctrl+V).
       - Real-time FFT Pitch & 8-band frequency reactivity on the Orb
         (Grave = deep indigo/violet swell, Médio = Apple Intelligence ribbon,
         Agudo = crystalline cyan/gold star ripples).
    2) Modo Texto (when text IS selected on Ctrl+Space):
       - Frosted glass instruction bar + "Corrigir PT-BR ↵" + minimalist grid.
       - Transitions smoothly to the working Orb state while AI processes.
    """
    AUDIO_SIZE = (296, 224)
    WORKING_SIZE = (296, 214)
    TEXT_WIDTH = 326

    def __init__(self, app, initial_mode="text", auto_start_recording=True):
        super().__init__()
        self.app = app
        self.edit_mode = False
        self.current_mode = initial_mode  # "text" or "audio"
        self.is_busy_processing = False
        self.is_working_text = False
        self._page_ready = False
        self._pending_js = []
        self._record_start_ts = 0.0
        self._space_grace_until = 0.0
        self._last_toggle_ts = 0.0
        self._space_was_down = False
        self._opened_ts = time.time()

        self.custom_input = _DummyFocusProxy(self)
        self.web_view = None

        self.record_timer = QtCore.QTimer(self)
        self.record_timer.setInterval(50)
        self.record_timer.timeout.connect(self._update_recording_timer)

        self.setAcceptDrops(True)
        self.init_ui()
        self.open_in_mode(initial_mode, auto_record=(initial_mode == "audio" and auto_start_recording))

    def init_ui(self):
        self.setWindowFlags(
            QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.Tool
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle("Writing Tools")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if HAS_WEBENGINE:
            self.web_view = QWebEngineView(self)
            self.web_page = _ElectronBridgePage(self.web_view)
            self.web_page.ipc_received.connect(self._on_ipc_message)
            self.web_view.setPage(self.web_page)
            self.web_view.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.web_view.setStyleSheet("background: transparent; border: none;")
            self.web_page.setBackgroundColor(QtCore.Qt.GlobalColor.transparent)
            self.web_view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            # Let parent widget handle file drops
            self.web_view.setAcceptDrops(False)

            base_root = getattr(sys, '_MEIPASS', os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))
            html_path = os.path.abspath(os.path.join(base_root, "electron_ui", "index.html"))
            if not os.path.exists(html_path):
                html_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "electron_ui", "index.html"))
            self.web_view.loadFinished.connect(self._on_web_load_finished)
            self.web_view.load(QUrl.fromLocalFile(html_path))
            layout.addWidget(self.web_view)

        self.installEventFilter(self)

    def _run_js(self, code: str):
        if not HAS_WEBENGINE or self.web_view is None:
            return
        if self._page_ready:
            self.web_page.runJavaScript(code)
        else:
            self._pending_js.append(code)

    def _on_web_load_finished(self, ok: bool):
        self._page_ready = True
        self._sync_full_ui_to_js()
        while self._pending_js:
            js = self._pending_js.pop(0)
            self.web_page.runJavaScript(js)

    def _sync_full_ui_to_js(self):
        is_light = (colorMode != "dark")
        self._run_js(f"window.setTheme({ 'true' if is_light else 'false' });")

        # Sync text options
        opts = list((self.load_options() or {}).keys())
        self._run_js(f"window.populateOptions({json.dumps(opts, ensure_ascii=False)});")

        # Sync microphone devices
        self._sync_mics_to_js()

        # Sync current view mode
        view_mode = "working" if self.is_working_text else self.current_mode
        self._run_js(f"window.setViewMode({json.dumps(view_mode)});")

        # Sync recording/processing state
        is_rec = bool(hasattr(self.app, "audio_transcriber") and self.app.audio_transcriber.is_recording)
        self._run_js(f"window.setOrbState({'true' if is_rec else 'false'}, {'true' if self.is_busy_processing else 'false'});")

    def _sync_mics_to_js(self):
        devices = []
        if hasattr(self.app, "audio_transcriber"):
            devices = self.app.audio_transcriber.list_input_devices()
        saved_idx = (self.app.config or {}).get("microphone_device", None)
        saved_name = (self.app.config or {}).get("microphone_device_name", "")

        selected_idx = None
        mic_items = []
        for dev_idx, dev_label in devices:
            mic_items.append({"index": dev_idx, "label": dev_label})
            if saved_idx is not None and dev_idx == saved_idx:
                selected_idx = dev_idx
            elif selected_idx is None and saved_name and saved_name.lower() in dev_label.lower():
                selected_idx = dev_idx

        if hasattr(self.app, "audio_transcriber"):
            self.app.audio_transcriber.set_device(selected_idx)

        self._run_js(
            f"window.populateMics({json.dumps(mic_items, ensure_ascii=False)}, {json.dumps(selected_idx)});"
        )

    def _compute_text_height(self) -> int:
        opts = [k for k in (self.load_options() or {}).keys() if k not in ("Custom", "Corrigir PT-BR")]
        rows = max(1, math.ceil(len(opts) / 2))
        return min(360, 134 + rows * 36)

    def _resize_for_mode(self, mode: str):
        if mode == "audio":
            w, h = self.AUDIO_SIZE
        elif mode == "working":
            w, h = self.WORKING_SIZE
        else:
            w, h = self.TEXT_WIDTH, self._compute_text_height()
        self.setFixedSize(w, h)
        if self.web_view is not None:
            self.web_view.resize(w, h)

    def open_in_mode(self, mode: str, auto_record: bool = False):
        """
        Prepare and switch the popup into 'audio' or 'text' mode.
        If `mode == 'audio'` and `auto_record=True`, immediately starts microphone recording!
        """
        self._opened_ts = time.time()
        self.is_working_text = False
        self.is_busy_processing = False
        self.current_mode = mode
        self._resize_for_mode(mode)
        self._sync_full_ui_to_js()

        if mode == "audio":
            if auto_record:
                self.start_recording_auto()
            else:
                self._reset_audio_ui()
        else:
            if hasattr(self.app, "audio_transcriber") and self.app.audio_transcriber.is_recording:
                self.record_timer.stop()
                self.app.audio_transcriber.cancel_recording()
            self.focus_text_input()

    def set_mode(self, mode: str):
        """Switch between 'text' and 'audio' mode."""
        if self.is_working_text:
            return
        self.current_mode = mode
        self._resize_for_mode(mode)
        self._run_js(f"window.setViewMode({json.dumps(mode)});")
        if mode == "audio":
            if not self.app.audio_transcriber.is_recording and not self.is_busy_processing:
                self.start_recording_auto()
        else:
            if hasattr(self.app, "audio_transcriber") and self.app.audio_transcriber.is_recording:
                self.record_timer.stop()
                self.app.audio_transcriber.cancel_recording()
                self._run_js("window.setOrbState(false, false);")
            self.focus_text_input()

    def focus_text_input(self):
        if self.web_view is not None:
            self.web_view.setFocus()
            self._run_js("setTimeout(() => { const el = document.getElementById('customInput'); if (el) el.focus(); }, 30);")

    def force_foreground_focus(self):
        """Ensure the popup receives keyboard focus on Windows so pressing Space works hands-free."""
        try:
            self.raise_()
            self.activateWindow()
            if self.web_view is not None:
                self.web_view.setFocus()
            if sys.platform == "win32":
                import ctypes
                user32 = ctypes.windll.user32
                kernel32 = ctypes.windll.kernel32
                hwnd = int(self.winId())
                fg_hwnd = user32.GetForegroundWindow()
                if fg_hwnd and fg_hwnd != hwnd:
                    fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, None)
                    cur_tid = kernel32.GetCurrentThreadId()
                    if fg_tid != cur_tid:
                        user32.AttachThreadInput(fg_tid, cur_tid, True)
                        user32.SetForegroundWindow(hwnd)
                        user32.SetFocus(hwnd)
                        user32.AttachThreadInput(fg_tid, cur_tid, False)
                    else:
                        user32.SetForegroundWindow(hwnd)
        except Exception as e:
            logging.debug(f"force_foreground_focus notice: {e}")

    # ─── IPC DISPATCH FROM ELECTRON/CHROMIUM HTML UI ─────────────────────────

    @QtCore.Slot(dict)
    def _on_ipc_message(self, payload: dict):
        action = payload.get("action", "")
        if action == "close":
            self.close_popup()
        elif action == "start_drag":
            wh = self.windowHandle()
            if wh is not None:
                wh.startSystemMove()
        elif action == "switch_mode":
            new_mode = payload.get("mode", "audio")
            self.set_mode(new_mode)
        elif action == "toggle_record":
            self.toggle_microphone_recording()
        elif action == "space_stop_or_toggle":
            if time.time() < self._space_grace_until:
                return
            self.toggle_microphone_recording()
        elif action == "browse_audio":
            self.browse_audio_file()
        elif action == "select_mic":
            dev_idx = payload.get("index", None)
            dev_label = payload.get("label", "")
            self._on_mic_selected(dev_idx, dev_label)
        elif action == "open_settings":
            self.open_settings_from_popup()
        elif action == "edit_buttons":
            self._open_quick_button_manager()
        elif action == "run_option":
            key = payload.get("key", "Corrigir PT-BR")
            self.on_generic_instruction(key)
        elif action == "run_custom":
            txt = (payload.get("text") or "").strip()
            if txt:
                self._run_text_option("Custom", txt)
            else:
                self.on_quick_pt_br()

    def _on_mic_selected(self, dev_idx, dev_label: str):
        logging.info(f"User selected microphone: {dev_label} (index={dev_idx})")
        was_recording = bool(hasattr(self.app, "audio_transcriber") and self.app.audio_transcriber.is_recording)
        if was_recording:
            self.record_timer.stop()
            self.app.audio_transcriber.cancel_recording()

        if hasattr(self.app, "audio_transcriber"):
            self.app.audio_transcriber.set_device(dev_idx)

        if self.app.config is not None:
            self.app.config["microphone_device"] = dev_idx
            self.app.config["microphone_device_name"] = dev_label
            if hasattr(self.app, "save_config"):
                self.app.save_config(self.app.config)

        self._sync_mics_to_js()
        if was_recording:
            self.start_recording_auto()

    # ─── AUDIO RECORDING (AUTO-START + SPACE TO FINISH) ──────────────────────

    def start_recording_auto(self):
        """Immediately start microphone recording when Audio Mode opens."""
        if self.is_busy_processing:
            return
        transcriber = self.app.audio_transcriber
        if transcriber.is_recording:
            return

        # Grace window so releasing Ctrl+Space does not trigger Space-to-stop
        self._space_grace_until = time.time() + 0.50
        self._last_toggle_ts = time.time()
        self._record_start_ts = time.time()
        self._space_was_down = True

        transcriber.start_recording()
        self._run_js("window.setOrbState(true, false);")
        self._run_js("window.setAudioStatus('Ouvindo...', '0:00');")
        self.record_timer.start()

    def finish_recording_and_transcribe(self):
        """Stop active microphone recording and immediately launch Whisper + LLM pipeline."""
        if self.is_busy_processing:
            return
        transcriber = self.app.audio_transcriber
        if not transcriber.is_recording:
            return
        self._last_toggle_ts = time.time()
        self.record_timer.stop()
        wav_path = transcriber.stop_recording()
        if wav_path:
            self._begin_audio_processing(wav_path, delete_after=True)
        else:
            self._reset_audio_ui()

    def toggle_microphone_recording(self):
        if self.is_busy_processing:
            return
        now = time.time()
        if now - self._last_toggle_ts < 0.32:
            return
        transcriber = self.app.audio_transcriber
        if not transcriber.is_recording:
            self.start_recording_auto()
        else:
            self.finish_recording_and_transcribe()

    def _update_recording_timer(self):
        if not hasattr(self.app, "audio_transcriber") or not self.app.audio_transcriber.is_recording:
            self.record_timer.stop()
            return

        now = time.time()
        elapsed = max(0, int(now - self._record_start_ts))
        mins, secs = divmod(elapsed, 60)
        timer_str = f"{mins}:{secs:02d}"
        self._run_js(f"window.setAudioStatus(null, {json.dumps(timer_str)});")

        # Global Space-key edge detector while recording (works even if OS focus stayed in target app!)
        if sys.platform == "win32" and now >= self._space_grace_until:
            try:
                import ctypes
                user32 = ctypes.windll.user32
                space_down = bool(user32.GetAsyncKeyState(0x20) & 0x8000)
                ctrl_down = bool(user32.GetAsyncKeyState(0x11) & 0x8000)
                if space_down and not ctrl_down and not self._space_was_down:
                    self._space_was_down = True
                    logging.info("Spacebar pressed during recording -> stopping and transcribing!")
                    self.finish_recording_and_transcribe()
                    return
                self._space_was_down = space_down
            except Exception:
                pass

    def browse_audio_file(self):
        if self.is_busy_processing:
            return
        if hasattr(self.app, "audio_transcriber") and self.app.audio_transcriber.is_recording:
            self.record_timer.stop()
            self.app.audio_transcriber.cancel_recording()
            self._run_js("window.setOrbState(false, false);")

        self.is_busy_processing = True
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Selecionar Arquivo de Áudio",
            "",
            "Arquivos de Áudio (*.ogg *.mp3 *.wav *.m4a *.opus *.flac *.aac *.mp4 *.webm);;Todos os Arquivos (*.*)"
        )
        self.is_busy_processing = False
        if file_path and os.path.exists(file_path):
            self._begin_audio_processing(file_path, delete_after=False)

    def _begin_audio_processing(self, file_path: str, delete_after: bool = False):
        self.current_mode = "audio"
        self.is_busy_processing = True
        self._resize_for_mode("audio")
        self._run_js("window.setViewMode('audio');")
        self._run_js("window.setOrbState(false, true);")
        self._run_js("window.setAudioStatus('Transcrevendo...', '');")
        self.app.process_audio_file(file_path, delete_after=delete_after)

    @QtCore.Slot(str)
    def update_audio_status(self, status_text: str):
        short = status_text
        if "Transcrevendo" in status_text:
            short = "Transcrevendo..."
        elif "Refinando" in status_text:
            short = "Refinando com IA..."
        elif "Ouvindo" in status_text or "Gravando" in status_text:
            short = "Ouvindo..."
        self._run_js(f"window.setAudioStatus({json.dumps(short, ensure_ascii=False)}, null);")

    @QtCore.Slot(float)
    def update_audio_level(self, rms: float):
        if self.current_mode == "audio":
            self._run_js(f"window.updateOrbSpectrum({{'rms': {rms:.4f}}});")

    @QtCore.Slot(dict)
    def update_audio_spectrum(self, spec: dict):
        if self.current_mode == "audio" and spec:
            self._run_js(f"window.updateOrbSpectrum({json.dumps(spec)});")

    def _reset_audio_ui(self):
        self.is_busy_processing = False
        self.record_timer.stop()
        self._run_js("window.setOrbState(false, false);")
        self._run_js("window.setAudioStatus('Pronto para ouvir', '0:00');")

    # ─── DRAG & DROP AUDIO FILES ─────────────────────────────────────────────

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                local_path = url.toLocalFile()
                if local_path and local_path.lower().endswith(SUPPORTED_AUDIO_EXTENSIONS):
                    event.acceptProposedAction()
                    self._run_js("document.getElementById('dropOverlay').classList.add('active');")
                    return
        super().dragEnterEvent(event)

    def dragLeaveEvent(self, event: QtGui.QDragLeaveEvent):
        self._run_js("document.getElementById('dropOverlay').classList.remove('active');")
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QtGui.QDropEvent):
        self._run_js("document.getElementById('dropOverlay').classList.remove('active');")
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                local_path = url.toLocalFile()
                if local_path and local_path.lower().endswith(SUPPORTED_AUDIO_EXTENSIONS):
                    event.acceptProposedAction()
                    if hasattr(self.app, "audio_transcriber") and self.app.audio_transcriber.is_recording:
                        self.record_timer.stop()
                        self.app.audio_transcriber.cancel_recording()
                    self._begin_audio_processing(local_path, delete_after=False)
                    return
        super().dropEvent(event)

    # ─── TEXT MODE & WORKING ORB STATE ───────────────────────────────────────

    def enter_working_state(self, title="Corrigindo com IA..."):
        self.is_working_text = True
        self.is_busy_processing = True
        self._resize_for_mode("working")
        self._run_js("window.setViewMode('working');")
        self._run_js("window.setOrbState(false, true);")
        self._run_js(f"window.setAudioStatus({json.dumps(title, ensure_ascii=False)}, '');")
        QtCore.QTimer.singleShot(90000, self._working_timeout)

    def _working_timeout(self):
        if getattr(self, "is_working_text", False) and self.isVisible():
            self.is_busy_processing = False
            self.is_working_text = False
            self.hide()

    def _run_text_option(self, option_key, custom_text=None):
        opts = self.app.options or {}
        in_window = bool(opts.get(option_key, {}).get("open_in_window", False))
        if custom_text is not None:
            self.app.process_option(option_key, custom_text)
        else:
            self.app.process_option(option_key)
        if in_window:
            self.hide()
        else:
            label = "Corrigindo com IA..." if option_key in ("Corrigir PT-BR", "Proofread") else f"{option_key}..."
            self.enter_working_state(label)

    def on_quick_pt_br(self):
        if getattr(self, "is_working_text", False):
            return
        option_key = "Corrigir PT-BR" if "Corrigir PT-BR" in (self.app.options or {}) else "Proofread"
        self._run_text_option(option_key)

    def on_generic_instruction(self, instruction):
        if not getattr(self, "is_working_text", False):
            self._run_text_option(instruction)

    # ─── OPTIONS JSON HELPERS & QUICK BUTTON MANAGER ─────────────────────────

    @staticmethod
    def load_options():
        options_path = os.path.join(os.path.dirname(sys.argv[0]), "options.json")
        if os.path.exists(options_path):
            with open(options_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return json.loads(DEFAULT_OPTIONS_JSON)

    @staticmethod
    def save_options(options):
        options_path = os.path.join(os.path.dirname(sys.argv[0]), "options.json")
        with open(options_path, "w", encoding="utf-8") as f:
            json.dump(options, f, indent=2, ensure_ascii=False)

    def _open_quick_button_manager(self):
        """Allow adding a custom button or restoring defaults via a clean menu."""
        self.edit_mode = True
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1c1c1e;
                color: #f5f5f7;
                border: 1px solid rgba(255,255,255,0.16);
                border-radius: 10px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 14px;
                border-radius: 6px;
            }
            QMenu::item:selected {
                background-color: #0a84ff;
                color: #ffffff;
            }
        """)
        add_act = menu.addAction("+ Adicionar Novo Botão...")
        reset_act = menu.addAction("↺ Restaurar Botões Padrão")
        chosen = menu.exec(QtGui.QCursor.pos())
        if chosen == add_act:
            dialog = ButtonEditDialog(self, title="Adicionar Botão")
            if dialog.exec_():
                bd = dialog.get_button_data()
                if bd.get("name"):
                    data = self.load_options()
                    data[bd["name"]] = {
                        "prefix": bd["prefix"],
                        "instruction": bd["instruction"],
                        "icon": bd["icon"],
                        "open_in_window": bd["open_in_window"],
                    }
                    if bd.get("hotkey"):
                        data[bd["name"]]["hotkey"] = bd["hotkey"]
                    self.save_options(data)
                    self.app.load_options()
                    self.app.register_hotkey()
                    self._sync_full_ui_to_js()
                    self._resize_for_mode(self.current_mode)
        elif chosen == reset_act:
            default_data = json.loads(DEFAULT_OPTIONS_JSON)
            self.save_options(default_data)
            self.app.load_options()
            self.app.register_hotkey()
            self._sync_full_ui_to_js()
            self._resize_for_mode(self.current_mode)
        self.edit_mode = False

    def open_settings_from_popup(self):
        self.close_popup()
        if hasattr(self.app, "show_settings"):
            self.app.show_settings()

    def close_popup(self):
        """Cancel any active recording and hide the pre-warmed window."""
        if hasattr(self.app, "audio_transcriber") and self.app.audio_transcriber.is_recording:
            self.record_timer.stop()
            self.app.audio_transcriber.cancel_recording()
        self.is_busy_processing = False
        self.is_working_text = False
        self.hide()

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Type.WindowDeactivate:
            is_recording = hasattr(self.app, "audio_transcriber") and self.app.audio_transcriber.is_recording
            if not self.edit_mode and not is_recording and not self.is_busy_processing:
                self.hide()
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.close_popup()
        elif event.key() in (QtCore.Qt.Key.Key_Space, QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter) and self.current_mode == "audio":
            if not event.isAutoRepeat() and time.time() >= self._space_grace_until:
                self.toggle_microphone_recording()
        else:
            super().keyPressEvent(event)
