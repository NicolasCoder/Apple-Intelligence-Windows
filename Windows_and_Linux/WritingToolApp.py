import gettext
import json
import logging
import os
import signal
import sys
import threading
import time

import darkdetect
import pyperclip
import ui.AboutWindow
import ui.CustomPopupWindow
import ui.OnboardingWindow
import ui.ResponseWindow
import ui.SettingsWindow
from ui.WhatsAppToastWindow import WhatsAppToastWindow
from aiprovider import GeminiProvider, OllamaProvider, OpenAICompatibleProvider, obfuscate_api_key
from audio_transcriber import AudioTranscriber
from whatsapp_watcher import WhatsAppOggWatcher
from pynput import keyboard as pykeyboard
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import QLocale, Signal, Slot
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox
from update_checker import UpdateChecker

_ = gettext.gettext


class _SelectedTextHolder:
    """
    Carries the result of an async clipboard capture from `_show_popup` to
    `process_option_thread`. The capture thread sets `text` and signals
    `ready` once done.
    """
    __slots__ = ("text", "ready")

    def __init__(self):
        self.text = ""
        self.ready = threading.Event()


class WritingToolApp(QtWidgets.QApplication):
    """
    The main application class for Writing Tools.
    """
    output_ready_signal = Signal(str)
    show_message_signal = Signal(str, str)  # a signal for showing message boxes
    hotkey_triggered_signal = Signal()
    followup_response_signal = Signal(str)


    def __init__(self, argv):
        super().__init__(argv)
        self.setQuitOnLastWindowClosed(False)
        self.current_response_window = None
        logging.debug('Initializing WritingToolApp')
        self.output_ready_signal.connect(self.replace_text)
        self.show_message_signal.connect(self.show_message_box)
        self.hotkey_triggered_signal.connect(self.on_hotkey_pressed)
        self.config = None
        self.config_path = None
        self.load_config()

        # Run any pending config migrations in a single pass (single restart).
        self._migrate_config()

        self.options = None
        self.options_path = None
        self.load_options()
        self.onboarding_window = None
        self.popup_window = None
        self.tray_icon = None
        self.tray_menu = None
        self.settings_window = None
        self.about_window = None
        self.registered_hotkey = None
        self.output_queue = ""
        self.last_replace = 0
        self.hotkey_listener = None
        self.paused = False
        self.toggle_action = None

        # Holder for the user's selected text.
        self.current_text_holder = None
        self._capture_lock = threading.Lock()

        # Initialize AudioTranscriber (sounddevice + NVIDIA Nemotron 3.5 Streaming / Faster-Whisper)
        whisper_model = (self.config or {}).get('whisper_model', 'nemotron-3.5-streaming')
        whisper_device = (self.config or {}).get('whisper_device', 'auto')
        live_streaming = (self.config or {}).get('live_streaming_preview', True)
        saved_mic_device = (self.config or {}).get('microphone_device', None)
        self.audio_transcriber = AudioTranscriber(
            self,
            default_model_size=whisper_model,
            device_index=saved_mic_device,
            whisper_device=whisper_device,
            live_streaming_enabled=live_streaming
        )
        self.audio_transcriber.status_changed.connect(self._on_audio_status_changed)
        self.audio_transcriber.audio_level_signal.connect(self._on_audio_level)
        self.audio_transcriber.audio_spectrum_signal.connect(self._on_audio_spectrum)
        self.audio_transcriber.partial_transcript_signal.connect(self._on_partial_transcript)
        self.audio_transcriber.transcription_finished.connect(self._on_transcription_finished)
        self.audio_transcriber.transcription_error.connect(self._on_transcription_error)
        # Preload ASR model on CUDA in background so first transcription is fast
        self.audio_transcriber.preload_model_async()

        self._ = gettext.gettext

        # Initialize the ctrl+c hotkey listener
        self.ctrl_c_timer = None
        self.setup_ctrl_c_listener()

        # Setup available AI providers
        self.providers = [GeminiProvider(self), OpenAICompatibleProvider(self), OllamaProvider(self)]

        if not self.config:
            logging.debug('No config found, creating default config and showing onboarding')
            self.config = {
                'shortcut': 'ctrl+space',
                'theme': 'gradient',
                'streaming': False,
                'provider': 'Gemini (Recommended)',
                'providers': {
                    'Gemini (Recommended)': {
                        'api_key': '',
                        'model_name': 'gemini-flash-latest'
                    },
                    'OpenAI Compatible (For Experts)': {
                        'api_key': '',
                        'api_base': 'https://api.openai.com/v1',
                        'api_organisation': '',
                        'api_project': '',
                        'api_model': 'gpt-4o-mini'
                    },
                    'Ollama (For Experts)': {
                        'api_base': 'http://localhost:11434',
                        'api_model': 'llama3.1:8b',
                        'keep_alive': '5'
                    }
                },
                'is_config_file_updated_for_v8': True,
                'is_config_file_updated_for_v9': True
            }
            self.save_config(self.config)
            self.show_onboarding()

        logging.debug('Setting up provider, hotkey and tray icon')
        provider_name = self.config.get('provider', 'Gemini (Recommended)')
        self.current_provider = next((provider for provider in self.providers if provider.provider_name == provider_name), None)
        if not self.current_provider:
            logging.warning(f'Provider {provider_name} not found. Using default provider.')
            self.current_provider = self.providers[0]

        self.current_provider.load_config(self.config.get("providers", {}).get(provider_name, {}))

        self.create_tray_icon()
        self.register_hotkey()

        try:
            lang = self.config['locale']
        except KeyError:
            lang = None
        self.change_language(lang)

        # Pre-warm the Chromium/Electron popup window in background so Ctrl+Space opens in <10ms
        try:
            self.popup_window = ui.CustomPopupWindow.CustomPopupWindow(
                self,
                initial_mode="audio",
                auto_start_recording=False
            )
            self.popup_window.hide()
        except Exception as e:
            logging.warning(f"Popup pre-warm notice: {e}")

        # Pre-warm the bottom-right WhatsApp .ogg Toast Window & Multi-Browser Download Watcher
        try:
            self.whatsapp_toast = WhatsAppToastWindow(self)
            self.whatsapp_toast.hide()
        except Exception as e:
            logging.warning(f"WhatsApp toast pre-warm notice: {e}")
            self.whatsapp_toast = None

        wa_enabled = (self.config or {}).get('whatsapp_ogg_watcher', True)
        wa_extra_dirs = (self.config or {}).get('whatsapp_extra_dirs', [])
        self.whatsapp_watcher = WhatsAppOggWatcher(
            self,
            enabled=wa_enabled,
            extra_dirs=wa_extra_dirs
        )
        self.whatsapp_watcher.ogg_detected.connect(self._on_whatsapp_ogg_detected)
        self.update_tray_menu()

        # Initialize update checker
        self.update_checker = UpdateChecker(self)
        self.update_checker.check_updates_async()

        self.recent_triggers = []  # Track recent hotkey triggers
        self.TRIGGER_WINDOW = 1.5  # Time window in seconds
        self.MAX_TRIGGERS = 3  # Max allowed triggers in window

    def setup_translations(self, lang=None):
        if not lang:
            lang = QLocale.system().name().split('_')[0]

        try:
            translation = gettext.translation(
                'messages',
                localedir=os.path.join(os.path.dirname(__file__), 'locales'),
                languages=[lang]
            )
        except FileNotFoundError:
            translation = gettext.NullTranslations()

        translation.install()
        # Update the translation function for all UI components.
        self._ = translation.gettext
        ui.AboutWindow._ = self._
        ui.SettingsWindow._ = self._
        ui.ResponseWindow._ = self._
        ui.OnboardingWindow._ = self._
        ui.CustomPopupWindow._ = self._

    def retranslate_ui(self):
        self.update_tray_menu()

    def change_language(self, lang):
        self.setup_translations(lang)
        self.retranslate_ui()

        # Update all other windows
        for widget in QApplication.topLevelWidgets():
            if widget != self and hasattr(widget, 'retranslate_ui'):
                widget.retranslate_ui()

    def check_trigger_spam(self):
        """
        Check if hotkey is being triggered too frequently (3+ times in 1.5 seconds).
        Returns True if spam is detected.
        """
        current_time = time.time()
        
        # Add current trigger
        self.recent_triggers.append(current_time)
        
        # Remove old triggers outside the window
        self.recent_triggers = [t for t in self.recent_triggers 
                            if current_time - t <= self.TRIGGER_WINDOW]
        
        # Check if we have too many triggers in the window
        return len(self.recent_triggers) >= self.MAX_TRIGGERS

    def load_config(self):
        """
        Load the configuration file.
        """
        self.config_path = os.path.join(os.path.dirname(sys.argv[0]), 'config.json')
        logging.debug(f'Loading config from {self.config_path}')
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
                logging.debug('Config loaded successfully')
        else:
            logging.debug('Config file not found')
            self.config = None

    def _migrate_config(self):
        """
        One-shot config migration. Catches any user up to the current schema
        (v9) regardless of where they started — v7, v8, or already current —
        in a single pass with at most one restart.

        Each version is gated on its own `is_config_file_updated_for_v{N}`
        flag, so re-running this is a no-op once everything's caught up.
        Bump CURRENT_CONFIG_VERSION and add a `# v{N}` block when adding a
        new migration step.

        v8 (introduced 2025):
          • Google removed Gemini 2.0 from the free API → bump model.
          • Obfuscate plaintext Gemini API keys (defeats Ctrl+F scanning).
          The custom-model input field didn't exist pre-v8, so any pre-v8
          model value is safe to overwrite — there's nothing custom to
          preserve.

        v9 (introduced 2026):
          • Google deprecated the Gemma 3 family. Migrate every v8 user on a
            now-deprecated preset to the new default (`gemini-flash-latest`)
            so they immediately land on the fast experience. They can opt
            into the unlimited-but-slow Gemma 4 options from the dropdown
            if they hit the 20/day Flash quota.
            Preserve custom model values (which DID exist by then) so a user
            who picked, say, `gemini-3.1-pro-preview` doesn't get reset.
          • SDK migration to `google-genai` is code-side only; nothing to
            do in config.
        """
        CURRENT_CONFIG_VERSION = 9
        # Default for new installs and migrating users.
        NEW_DEFAULT_MODEL = 'gemini-flash-latest'
        # v8 -> v9 model mapping. Every retired preset is bumped to the new
        # default so users get the fast Flash-tier experience by default.
        V8_TO_V9_MAP = {
            'gemma-3-27b-it':           NEW_DEFAULT_MODEL,
            'gemma-3-4b-it':            NEW_DEFAULT_MODEL,
            'gemini-flash-lite-latest': NEW_DEFAULT_MODEL,
            # 'gemini-flash-latest' itself is already current — no entry needed.
        }

        # New user (no config yet) — onboarding will create a fresh, current
        # config; nothing to migrate.
        if not self.config:
            logging.debug('No config to migrate (new user)')
            return

        needs_v8 = not self.config.get('is_config_file_updated_for_v8', False)
        needs_v9 = not self.config.get('is_config_file_updated_for_v9', False)

        if not needs_v8 and not needs_v9:
            logging.debug('Config already up-to-date, no migration needed')
            return

        logging.info(f'Running config migration (needs_v8={needs_v8}, needs_v9={needs_v9})...')

        config_changed = False
        gemini_config = (
            self.config.get('providers', {}).get('Gemini (Recommended)')
        )

        if gemini_config is not None:
            old_model = gemini_config.get('model_name', '')

            # v8: pre-v8 users didn't have a custom-model field, so we can
            # bump unconditionally. We skip the historical "v8 default of
            # gemma-3-27b-it" intermediate stop and jump straight to the
            # current default.
            if needs_v8:
                if old_model != NEW_DEFAULT_MODEL:
                    gemini_config['model_name'] = NEW_DEFAULT_MODEL
                    logging.info(f'[v8] Bumped Gemini model "{old_model}" -> "{NEW_DEFAULT_MODEL}"')
                    config_changed = True

                # Obfuscate the API key. The helper is idempotent — already-
                # obfuscated keys (with the `enc:` prefix) pass through
                # unchanged.
                api_key = gemini_config.get('api_key', '')
                if api_key:
                    new_key = obfuscate_api_key(api_key)
                    if new_key != api_key:
                        gemini_config['api_key'] = new_key
                        logging.info('[v8] Obfuscated plaintext Gemini API key')
                        config_changed = True

            # v9: only runs for users coming from v8. Preserve tier choice via
            # the V8_TO_V9_MAP so an unlimited-tier user doesn't get silently
            # downgraded to a daily-quota model. Custom values (anything not
            # in the map) are left alone.
            elif needs_v9:
                new_model = V8_TO_V9_MAP.get(old_model)
                if new_model is not None and new_model != old_model:
                    gemini_config['model_name'] = new_model
                    logging.info(f'[v9] Bumped Gemini model "{old_model}" -> "{new_model}"')
                    config_changed = True

        # Stamp every version flag up to current so we never re-run on
        # subsequent startups, even if no fields actually needed changing
        # (e.g., a v8 user who'd already picked a custom non-deprecated model).
        for n in range(8, CURRENT_CONFIG_VERSION + 1):
            self.config[f'is_config_file_updated_for_v{n}'] = True

        self.save_config(self.config)
        logging.info('Config migration complete')

        # Single restart popup, regardless of how many versions we jumped.
        if config_changed:
            # Use QMessageBox directly — signals aren't connected yet at this
            # point in __init__.
            QMessageBox.information(
                None,
                'Writing Tools Updated',
                'Writing Tools has just completed an internal update '
                '(your config.json was migrated to the current format).\n\n'
                'Please restart Writing Tools.'
            )
            sys.exit(0)

    def load_options(self):
        """
        Load the options file.
        """
        self.options_path = os.path.join(os.path.dirname(sys.argv[0]), 'options.json')
        logging.debug(f'Loading options from {self.options_path}')
        if os.path.exists(self.options_path):
            with open(self.options_path, 'r', encoding='utf-8') as f:
                self.options = json.load(f)
                logging.debug('Options loaded successfully')
        else:
            logging.debug('Options file not found')
            self.options = None

    def save_config(self, config):
        """
        Save the configuration file.
        """
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
            logging.debug('Config saved successfully')
        self.config = config

    def show_onboarding(self):
        """
        Show the onboarding window for first-time users.
        """
        logging.debug('Showing onboarding window')
        self.onboarding_window = ui.OnboardingWindow.OnboardingWindow(self)
        self.onboarding_window.close_signal.connect(self.exit_app)
        self.onboarding_window.show()

    @staticmethod
    def _to_pynput_hotkey(hotkey_str):
        """
        Convert a user-facing hotkey string ('ctrl+j', 'ctrl+alt+space') to
        pynput's `<ctrl>+j` / `<ctrl>+<alt>+<space>` format. Single-char keys
        stay as-is; multi-char keys (modifiers, named keys) get wrapped in <>.
        """
        return '+'.join(
            f'{t}' if len(t) <= 1 else f'<{t}>'
            for t in hotkey_str.split('+')
        )

    @staticmethod
    def _attach_thread_to_default_desktop():
        """Ensure the current thread is attached to the interactive WinSta0\\Default desktop."""
        if sys.platform != 'win32':
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hdefault = user32.OpenDesktopW('Default', 0, True, 0x01FF)
            if hdefault:
                user32.SetThreadDesktop(hdefault)
        except Exception as e:
            logging.debug(f'SetThreadDesktop notice: {e}')

    @staticmethod
    def _parse_win32_hotkey(hotkey_str: str):
        """
        Parse a shortcut string like 'ctrl+space' or 'ctrl+shift+space' into
        (win32_modifiers, virtual_key_code).
        """
        MOD_ALT = 0x0001
        MOD_CONTROL = 0x0002
        MOD_SHIFT = 0x0004
        MOD_WIN = 0x0008
        MOD_NOREPEAT = 0x4000

        VK_MAP = {
            'space': 0x20,
            'enter': 0x0D,
            'return': 0x0D,
            'tab': 0x09,
            'esc': 0x1B,
            'escape': 0x1B,
            'backspace': 0x08,
            'delete': 0x2E,
            'insert': 0x2D,
            'home': 0x24,
            'end': 0x23,
            'pageup': 0x21,
            'pagedown': 0x22,
            'up': 0x26,
            'down': 0x28,
            'left': 0x25,
            'right': 0x27,
        }
        for i in range(1, 25):
            VK_MAP[f'f{i}'] = 0x70 + (i - 1)

        mods = MOD_NOREPEAT
        vk = 0
        parts = [p.strip().lower().strip('<>') for p in (hotkey_str or '').split('+') if p.strip()]
        for p in parts:
            if p in ('ctrl', 'control', 'ctrl_l', 'ctrl_r'):
                mods |= MOD_CONTROL
            elif p in ('shift', 'shift_l', 'shift_r'):
                mods |= MOD_SHIFT
            elif p in ('alt', 'alt_l', 'alt_r', 'alt_gr'):
                mods |= MOD_ALT
            elif p in ('win', 'cmd', 'super'):
                mods |= MOD_WIN
            elif p in VK_MAP:
                vk = VK_MAP[p]
            elif len(p) == 1 and p.isalnum():
                vk = ord(p.upper())
        return mods, vk

    def start_hotkey_listener(self):
        """
        Register global hotkeys using native Win32 `RegisterHotKey` on the
        interactive Default desktop (reliable across all Windows apps and IDEs),
        with a pynput fallback for non-Windows platforms.
        """
        try:
            # Stop previous listeners if any
            if getattr(self, '_win32_hotkey_thread_id', None):
                try:
                    import ctypes
                    ctypes.windll.user32.PostThreadMessageW(self._win32_hotkey_thread_id, 0x0012, 0, 0)  # WM_QUIT
                except Exception:
                    pass
                self._win32_hotkey_thread_id = None

            if self.hotkey_listener is not None:
                try:
                    self.hotkey_listener.stop()
                except Exception:
                    pass
                self.hotkey_listener = None

            orig_shortcut = self.config.get('shortcut', 'ctrl+space')
            self.registered_hotkey = orig_shortcut

            if sys.platform == 'win32':
                import ctypes
                import ctypes.wintypes

                bindings = []  # list of (hotkey_id, raw_str, mods, vk, callback)

                def on_global_activate():
                    if self.paused:
                        return
                    logging.info(f'Global hotkey triggered ({orig_shortcut})')
                    self.hotkey_triggered_signal.emit()

                g_mods, g_vk = self._parse_win32_hotkey(orig_shortcut)
                if g_vk:
                    bindings.append((1, orig_shortcut, g_mods, g_vk, on_global_activate))

                next_id = 2
                if self.options:
                    for button_name, button_cfg in self.options.items():
                        if button_name == 'Custom':
                            continue
                        raw = (button_cfg.get('hotkey') or '').strip()
                        if not raw:
                            continue
                        b_mods, b_vk = self._parse_win32_hotkey(raw)
                        if b_vk:
                            bindings.append((next_id, raw, b_mods, b_vk, self._make_button_hotkey_callback(button_name)))
                            next_id += 1

                def _win32_hotkey_loop():
                    self._attach_thread_to_default_desktop()
                    user32 = ctypes.windll.user32
                    kernel32 = ctypes.windll.kernel32
                    self._win32_hotkey_thread_id = kernel32.GetCurrentThreadId()

                    cb_map = {}
                    for hid, raw_str, mods, vk, cb in bindings:
                        ok = user32.RegisterHotKey(None, hid, mods, vk)
                        if ok:
                            cb_map[hid] = cb
                            logging.info(f'Win32 RegisterHotKey registered: "{raw_str}" (id={hid})')
                        else:
                            err = ctypes.GetLastError()
                            logging.warning(f'Win32 RegisterHotKey failed for "{raw_str}" (err={err})')

                    msg = ctypes.wintypes.MSG()
                    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                        if msg.message == 0x0312:  # WM_HOTKEY
                            hid = int(msg.wParam)
                            if hid in cb_map:
                                try:
                                    cb_map[hid]()
                                except Exception as e:
                                    logging.error(f'Error in hotkey callback: {e}')
                        user32.TranslateMessage(ctypes.byref(msg))
                        user32.DispatchMessageW(ctypes.byref(msg))

                    for hid in cb_map:
                        user32.UnregisterHotKey(None, hid)

                t = threading.Thread(target=_win32_hotkey_loop, daemon=True)
                t.start()
                return

            # Non-Windows fallback using pynput GlobalHotKeys
            hotkey_map = {}
            global_parsed = self._to_pynput_hotkey(orig_shortcut)
            pykeyboard.HotKey.parse(global_parsed)

            def on_global_activate_pynput():
                if self.paused:
                    return
                logging.debug('triggered global hotkey')
                self.hotkey_triggered_signal.emit()

            hotkey_map[global_parsed] = on_global_activate_pynput
            if self.options:
                for button_name, button_cfg in self.options.items():
                    if button_name == 'Custom':
                        continue
                    raw = (button_cfg.get('hotkey') or '').strip()
                    if not raw:
                        continue
                    try:
                        parsed = self._to_pynput_hotkey(raw)
                        pykeyboard.HotKey.parse(parsed)
                        if parsed not in hotkey_map:
                            hotkey_map[parsed] = self._make_button_hotkey_callback(button_name)
                    except Exception:
                        continue

            if hotkey_map:
                self.hotkey_listener = pykeyboard.GlobalHotKeys(hotkey_map)
                self.hotkey_listener.start()
        except Exception as e:
            logging.error(f'Failed to register hotkey listener: {e}', exc_info=True)

    def _make_button_hotkey_callback(self, button_name):
        """
        Build a callback that fires `button_name` directly, bypassing the
        popup. Closes over `button_name` so we can register many distinct
        callbacks in a single GlobalHotKeys map.

        The actual fire is bounced through the Qt event loop because pynput
        invokes callbacks on its listener thread; popup/clipboard work needs
        to happen on the main thread.
        """
        def callback():
            if self.paused:
                return
            logging.debug(f'Direct hotkey fired for button "{button_name}"')
            # Match the global hotkey's behaviour: cancel any in-flight
            # request so a new fire doesn't pile up on top of a previous one.
            if self.current_provider:
                self.current_provider.cancel()
                self.output_queue = ""
            # noinspection PyTypeChecker
            QtCore.QMetaObject.invokeMethod(
                self,
                "_fire_button_directly",
                QtCore.Qt.ConnectionType.QueuedConnection,
                QtCore.Q_ARG(str, button_name)
            )
        return callback

    @Slot(str)
    def _fire_button_directly(self, button_name):
        """
        Run a button's option without showing the popup — invoked by a
        per-button direct hotkey. Mirrors the relevant parts of
        `_show_popup`: set up the async clipboard capture, then hand off
        to the worker thread (`process_option`) which waits on the holder
        and dispatches the AI call.
        """
        logging.debug(f'Firing button "{button_name}" directly')

        # If the popup is currently visible (e.g., user opened it then
        # pressed a button hotkey), close it so it doesn't compete.
        if self.popup_window is not None and self.popup_window.isVisible():
            self.popup_window.close_popup()

        # Sanity check: button must still exist in options. Could be stale
        # if options.json was edited externally between registration and
        # fire — skip gracefully rather than crash.
        if not self.options or button_name not in self.options:
            logging.warning(f'Button "{button_name}" no longer exists; ignoring hotkey')
            return

        self.current_text_holder = _SelectedTextHolder()
        self._fire_ctrl_c_and_capture_async(self.current_text_holder)

        # Same worker path as a popup-button click. process_option_thread
        # waits on the holder, surfaces "Please select text…" if empty,
        # and routes window-mode options through the response window.
        self.process_option(button_name)

    def register_hotkey(self):
        """
        Register the global hotkey for activating Writing Tools.
        """
        logging.debug('Registering hotkey')
        self.start_hotkey_listener()
        logging.debug('Hotkey registered')

    def on_hotkey_pressed(self):
        """
        Handle the hotkey press event.
        """
        logging.debug('Hotkey pressed')
        
        # Check for spam triggers
        if self.check_trigger_spam():
            logging.warning('Hotkey spam detected - quitting application')
            self.exit_app()
            return
            
        # Original hotkey handling continues...
        if self.current_provider:
            logging.debug("Cancelling current provider's request")
            self.current_provider.cancel()
            self.output_queue = ""

        # noinspection PyTypeChecker
        QtCore.QMetaObject.invokeMethod(self, "_show_popup", QtCore.Qt.ConnectionType.QueuedConnection)

    @Slot()
    def _show_popup(self):
        """
        Show the popup window when the global hotkey fires.
        - If the popup is ALREADY visible and recording audio, pressing Ctrl+Space
          finishes the recording and starts transcription immediately!
        - Otherwise:
          1. Inject Ctrl+C while focus is still on the user's active window and
             capture selected text (up to 0.22s fast check).
          2. If text IS selected -> open popup in 'Modo Texto'.
          3. If NO text is selected -> open popup in 'Modo Áudio' and AUTOMATICALLY
             start recording the microphone (Space to finish & transcribe).
        """
        logging.debug('Showing popup window')

        # If popup is already visible and actively recording, finish recording & transcribe!
        if (
            self.popup_window is not None
            and self.popup_window.isVisible()
            and hasattr(self, 'audio_transcriber')
            and self.audio_transcriber.is_recording
        ):
            logging.info('Global hotkey pressed while recording -> finishing recording & transcribing!')
            self.popup_window.finish_recording_and_transcribe()
            return

        # Fire Ctrl+C *before* showing the popup so focus is still on the target app
        self.current_text_holder = _SelectedTextHolder()
        self._fire_ctrl_c_and_capture_async(self.current_text_holder)

        # Wait up to 0.22s so we immediately know whether text was selected
        self.current_text_holder.ready.wait(timeout=0.22)
        captured_text = (self.current_text_holder.text or '').strip()
        initial_mode = "text" if captured_text else "audio"
        logging.debug(f'Popup initial_mode={initial_mode} (captured len={len(captured_text)})')

        try:
            if self.popup_window is None:
                self.popup_window = ui.CustomPopupWindow.CustomPopupWindow(
                    self,
                    initial_mode=initial_mode,
                    auto_start_recording=(initial_mode == "audio")
                )
            else:
                self.popup_window.open_in_mode(
                    initial_mode,
                    auto_record=(initial_mode == "audio")
                )

            # Set the window icon
            icon_path = os.path.join(os.path.dirname(sys.argv[0]), 'icons', 'app_icon.png')
            if os.path.exists(icon_path):
                self.setWindowIcon(QtGui.QIcon(icon_path))

            # Get the screen containing the cursor
            cursor_pos = QCursor.pos()
            screen = QGuiApplication.screenAt(cursor_pos)
            if screen is None:
                screen = QGuiApplication.primaryScreen()
            screen_geometry = screen.geometry()

            popup_width = self.popup_window.width()
            popup_height = self.popup_window.height()
            x = cursor_pos.x()
            y = cursor_pos.y() + 18
            if x + popup_width > screen_geometry.right():
                x = screen_geometry.right() - popup_width
            if y + popup_height > screen_geometry.bottom():
                y = cursor_pos.y() - popup_height - 10
            self.popup_window.move(x, y)

            self.popup_window.show()
            self.popup_window.force_foreground_focus()
            logging.debug(f'Popup window shown at ({x}, {y}) in mode={initial_mode}')
        except Exception as e:
            logging.error(f'Error showing popup window: {e}', exc_info=True)

    @Slot(str)
    def _switch_popup_to_text_mode_if_late_capture(self, captured_text: str):
        """If clipboard took >220ms to populate, cancel auto-record and switch popup to Text Mode."""
        if (
            self.popup_window is not None
            and self.popup_window.isVisible()
            and self.popup_window.current_mode == "audio"
            and not self.popup_window.is_busy_processing
            and (time.time() - getattr(self.popup_window, '_opened_ts', 0.0) < 0.85)
        ):
            logging.debug('Late clipboard capture arrived; switching popup to text mode')
            if hasattr(self, 'audio_transcriber') and self.audio_transcriber.is_recording:
                self.popup_window.record_timer.stop()
                self.audio_transcriber.cancel_recording()
            self.popup_window.set_mode("text")

    def _fire_ctrl_c_and_capture_async(self, holder):
        """
        Inject Ctrl+C now (while focus is still on the user's source app),
        then poll the clipboard in a background thread.
        """
        self._attach_thread_to_default_desktop()
        try:
            clipboard_backup = pyperclip.paste()
        except Exception:
            clipboard_backup = ''

        self.clear_clipboard()

        try:
            if sys.platform == 'win32':
                import ctypes
                user32 = ctypes.windll.user32
                KEYEVENTF_KEYUP = 0x0002
                VK_CONTROL = 0x11
                VK_SHIFT = 0x10
                VK_MENU = 0x12
                VK_SPACE = 0x20
                VK_C = 0x43
                # Release Space/Shift/Alt if still logically held from the hotkey chord,
                # then press Ctrl+C cleanly.
                user32.keybd_event(VK_SPACE, 0, KEYEVENTF_KEYUP, 0)
                user32.keybd_event(VK_SHIFT, 0, KEYEVENTF_KEYUP, 0)
                user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
                user32.keybd_event(VK_CONTROL, 0, 0, 0)
                user32.keybd_event(VK_C, 0, 0, 0)
                user32.keybd_event(VK_C, 0, KEYEVENTF_KEYUP, 0)
                user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
            else:
                kbrd = pykeyboard.Controller()
                kbrd.press(pykeyboard.Key.ctrl.value)
                kbrd.press('c')
                kbrd.release('c')
                kbrd.release(pykeyboard.Key.ctrl.value)
        except Exception as e:
            logging.error(f'Error simulating Ctrl+C: {e}')

        def _poll_clipboard():
            self._attach_thread_to_default_desktop()
            with self._capture_lock:
                text = ''
                try:
                    time.sleep(0.04)
                    deadline = time.time() + 0.65
                    while time.time() < deadline:
                        try:
                            text = pyperclip.paste() or ''
                        except Exception as e:
                            logging.error(f'Error reading clipboard during poll: {e}')
                            text = ''
                        if text:
                            break
                        time.sleep(0.03)
                    holder.text = text
                    logging.debug(f'Captured selected text (len={len(text)})')
                    if text.strip():
                        QtCore.QMetaObject.invokeMethod(
                            self,
                            "_switch_popup_to_text_mode_if_late_capture",
                            QtCore.Qt.ConnectionType.QueuedConnection,
                            QtCore.Q_ARG(str, text)
                        )
                finally:
                    try:
                        pyperclip.copy(clipboard_backup)
                    except Exception as e:
                        logging.error(f'Error restoring clipboard: {e}')
                    holder.ready.set()

        threading.Thread(target=_poll_clipboard, daemon=True).start()

    # ─── AUDIO MODE: FASTER-WHISPER (CUDA FLOAT16) -> LLM -> CTRL+V ──────────

    def process_audio_file(self, file_path: str, delete_after: bool = False):
        """
        Trigger asynchronous faster-whisper transcription on CUDA float16.
        When finished, `_on_transcription_finished` sends the text to the
        configured LLM for punctuation/grammar correction, closes the popup,
        and pastes (Ctrl+V) into the active text field.
        """
        self.audio_transcriber.transcribe_file_async(file_path, delete_after=delete_after, language="pt")

    @Slot(str)
    def _on_audio_status_changed(self, status_text: str):
        st_lower = (status_text or "").lower()
        if "gravando" in st_lower:
            self.set_tray_state("recording", status_text)
        elif "transcrevendo" in st_lower or "carregando" in st_lower or "refinando" in st_lower:
            self.set_tray_state("processing", status_text)
        if self.popup_window is not None and self.popup_window.isVisible():
            self.popup_window.update_audio_status(status_text)

    @Slot(float)
    def _on_audio_level(self, rms: float):
        if self.popup_window is not None and self.popup_window.isVisible():
            self.popup_window.update_audio_level(rms)

    @Slot(dict)
    def _on_audio_spectrum(self, spec: dict):
        if self.popup_window is not None and self.popup_window.isVisible():
            if hasattr(self.popup_window, "update_audio_spectrum"):
                self.popup_window.update_audio_spectrum(spec)

    @Slot(str)
    def _on_partial_transcript(self, partial_text: str):
        if self.popup_window is not None and self.popup_window.isVisible():
            if hasattr(self.popup_window, "update_partial_transcript"):
                self.popup_window.update_partial_transcript(partial_text)

    @Slot(str)
    def _on_transcription_error(self, error_msg: str):
        logging.error(f"Audio transcription error: {error_msg}")
        self.set_tray_state("idle")
        if self.popup_window is not None and self.popup_window.isVisible():
            self.popup_window._reset_audio_ui()
            self.popup_window.update_audio_status(f"⚠️ {error_msg}")

    @Slot(str)
    def _on_transcription_finished(self, raw_transcript: str):
        """
        Called when faster-whisper finishes transcribing the audio.
        Passes the transcribed text through the configured LLM in WritingTools
        (if audio_llm_refinement is enabled) to fix punctuation and grammar in PT-BR,
        then closes the popup and pastes (Ctrl+V) into the active text field.
        """
        refine_enabled = (self.config or {}).get('audio_llm_refinement', True)
        if not refine_enabled:
            self._finish_audio_and_paste(raw_transcript.strip())
            return

        self.set_tray_state("processing", "Refinando pontuação e gramática na IA...")
        if self.popup_window is not None and self.popup_window.isVisible():
            self.popup_window.update_audio_status("Refinando pontuação e gramática na IA...")

        def _llm_refine_and_paste():
            refined_text = raw_transcript.strip()
            try:
                if self.current_provider:
                    system_instruction = (
                        "Você é um assistente especialista em revisão de transcrições de áudio em Português do Brasil (PT-BR).\n"
                        "Sua tarefa é corrigir a pontuação, acentuação, capitalização e erros gramaticais ou de transcrição fonética do texto ditado "
                        "(ex: se o contexto falar de 'ícone ali embaixo' e o áudio transcrever 'sistema de rei / de três', corrija para 'sistema de tray'), "
                        "eliminando vícios de fala óbvios (como 'ééé', 'hmm'), mas mantendo fielmente o sentido, o vocabulário e o tom original de quem falou.\n"
                        "Retorne APENAS o texto final corrigido e pontuado, sem nenhum comentário, sem aspas extras e sem introduções."
                    )
                    prompt = f"Corrija a pontuação e a gramática desta transcrição de áudio:\n\n{raw_transcript}"
                    llm_result = self.current_provider.get_response(
                        system_instruction,
                        prompt,
                        return_response=True
                    )
                    if (
                        llm_result
                        and llm_result.strip()
                        and "ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST" not in llm_result
                        and not llm_result.startswith("An error occurred")
                    ):
                        refined_text = llm_result.strip()
            except Exception as e:
                logging.error(f"LLM refinement failed, falling back to raw Whisper transcript: {e}")

            QtCore.QMetaObject.invokeMethod(
                self,
                "_finish_audio_and_paste",
                QtCore.Qt.ConnectionType.QueuedConnection,
                QtCore.Q_ARG(str, refined_text)
            )

        threading.Thread(target=_llm_refine_and_paste, daemon=True).start()

    @Slot(str)
    def _finish_audio_and_paste(self, final_text: str):
        """
        Hide the floating popup window (returning OS focus to the active text
        field) and paste the final text via Ctrl+V.
        """
        logging.info(f"Audio pipeline complete. Pasting text (len={len(final_text)})")
        self.set_tray_state("idle")
        if self.popup_window is not None:
            self.popup_window.is_busy_processing = False
            self.popup_window.is_working_text = False
            self.popup_window.hide()

        def _delayed_paste():
            # Wait briefly so Windows restores focus to the underlying text field
            time.sleep(0.18)
            self.output_queue = ""
            self.output_ready_signal.emit(final_text)

        threading.Thread(target=_delayed_paste, daemon=True).start()

    @Slot(str)
    def _on_whatsapp_ogg_detected(self, file_path: str):
        """
        Automatically triggered when a new `.ogg` audio file is downloaded in any browser.
        Opens the bottom-right Apple Intelligence corner toast in 'Transcribing' mode,
        runs Faster-Whisper + LLM punctuation refinement in the background, and then
        triggers the iridescent particle burst + fast AI word-by-word reveal!
        """
        if self.paused or not (self.config or {}).get('whatsapp_ogg_watcher', True):
            return

        logging.info(f"Auto-transcribing newly downloaded WhatsApp .ogg file: {file_path}")
        self.set_tray_state("processing", f"Transcrevendo WhatsApp: {os.path.basename(file_path)}")

        if self.whatsapp_toast is None:
            self.whatsapp_toast = WhatsAppToastWindow(self)
        self.whatsapp_toast.show_transcribing(file_path)

        def _ogg_worker():
            try:
                raw_text = self.audio_transcriber.transcribe_file_sync(file_path, language="pt")
                if not raw_text:
                    QtCore.QMetaObject.invokeMethod(
                        self.whatsapp_toast,
                        "show_result",
                        QtCore.Qt.ConnectionType.QueuedConnection,
                        QtCore.Q_ARG(str, "⚠️ Não foi possível detectar fala neste áudio .ogg.")
                    )
                    return

                final_text = raw_text.strip()
                refine_enabled = (self.config or {}).get('audio_llm_refinement', True)
                if refine_enabled and self.current_provider:
                    QtCore.QMetaObject.invokeMethod(
                        self.whatsapp_toast,
                        "update_status",
                        QtCore.Qt.ConnectionType.QueuedConnection,
                        QtCore.Q_ARG(str, "Refinando pontuação na IA...")
                    )
                    try:
                        system_instruction = (
                            "Você é um assistente especialista em revisão de transcrições de áudios do WhatsApp em Português do Brasil (PT-BR).\n"
                            "Sua tarefa é corrigir a pontuação, acentuação, capitalização e erros gramaticais ou de transcrição fonética do áudio, "
                            "eliminando vícios de fala óbvios (como 'ééé', 'hmm'), mas mantendo 100% o sentido, as gírias e o tom natural de quem mandou o áudio.\n"
                            "Retorne APENAS o texto final corrigido e pontuado, sem nenhum comentário, sem aspas extras e sem introduções."
                        )
                        prompt = f"Corrija a pontuação e a gramática desta transcrição de áudio do WhatsApp:\n\n{raw_text}"
                        llm_res = self.current_provider.get_response(
                            system_instruction,
                            prompt,
                            return_response=True
                        )
                        if (
                            llm_res
                            and llm_res.strip()
                            and "ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST" not in llm_res
                            and not llm_res.startswith("An error occurred")
                        ):
                            final_text = llm_res.strip()
                    except Exception as llm_err:
                        logging.warning(f"WhatsApp .ogg LLM refinement fallback: {llm_err}")

                QtCore.QMetaObject.invokeMethod(
                    self.whatsapp_toast,
                    "show_result",
                    QtCore.Qt.ConnectionType.QueuedConnection,
                    QtCore.Q_ARG(str, final_text)
                )
            except Exception as e:
                logging.error(f"Error transcribing WhatsApp .ogg file: {e}", exc_info=True)
                QtCore.QMetaObject.invokeMethod(
                    self.whatsapp_toast,
                    "show_result",
                    QtCore.Qt.ConnectionType.QueuedConnection,
                    QtCore.Q_ARG(str, f"⚠️ Erro ao transcrever áudio: {e}")
                )
            finally:
                QtCore.QMetaObject.invokeMethod(
                    self,
                    "set_tray_state",
                    QtCore.Qt.ConnectionType.QueuedConnection,
                    QtCore.Q_ARG(str, "idle"),
                    QtCore.Q_ARG(str, "")
                )

        threading.Thread(target=_ogg_worker, daemon=True).start()

    @staticmethod
    def clear_clipboard():
        """
        Clear the system clipboard.
        """
        try:
            pyperclip.copy('')
        except Exception as e:
            logging.error(f'Error clearing clipboard: {e}')

    def process_option(self, option, custom_change=None):
        """
        Spawn a worker thread that waits for the asynchronous clipboard
        capture and then runs the chosen option. Kept as a thin wrapper so
        the popup's click handler returns immediately and the GUI thread
        is never blocked on the clipboard read.
        """
        logging.debug(f'Processing option: {option}')

        # Drop any stale ref so a previous run's late-arriving response can't
        # land in a now-irrelevant window. The new window (if any) is created
        # by the worker via `_setup_response_window` once the text is in.
        if hasattr(self, 'current_response_window'):
            delattr(self, 'current_response_window')

        threading.Thread(
            target=self.process_option_thread,
            args=(option, custom_change),
            daemon=True
        ).start()

    @Slot(str, str)
    def _setup_response_window(self, option, selected_text):
        """
        Open the response window and seed its chat history. Called from
        `process_option_thread` via `BlockingQueuedConnection` so the
        worker can rely on the window existing before it dispatches the
        AI request.
        """
        self.current_response_window = self.show_response_window(option, selected_text)
        self.current_response_window.chat_history = [
            {
                "role": "user",
                "content": f"Original text to {option.lower()}:\n\n{selected_text}"
            }
        ]

    def process_option_thread(self, option, custom_change=None):
        """
        Worker: wait for the background clipboard capture to land, then
        either open a response window (for window-mode options) or set up
        for inline replacement, and finally run the AI request.
        """
        logging.debug(f'Starting processing thread for option: {option}')

        # Typically near-instant since the user took time to read the popup
        # and click. The 3s ceiling is a safety net for genuinely sluggish
        # systems; if the 2s polling deadline in the capture thread tripped
        # first, the event is already set and this returns immediately.
        holder = self.current_text_holder
        if holder is None or not holder.ready.wait(timeout=3.0):
            logging.warning('Timed out waiting for selected text capture')
        selected_text = (holder.text if holder else '') or ''

        if not selected_text.strip():
            # The chat-mode fallback that used to fire here was removed when
            # popup show became instant — we no longer have a way to detect
            # "user wants to chat" vs "capture failed", so we pick the safer
            # interpretation and surface the error.
            self.show_message_signal.emit('Error', 'Please select text to use this option.')
            return

        if self.options[option]['open_in_window']:
            QtCore.QMetaObject.invokeMethod(
                self,
                '_setup_response_window',
                QtCore.Qt.ConnectionType.BlockingQueuedConnection,
                QtCore.Q_ARG(str, option),
                QtCore.Q_ARG(str, selected_text)
            )

        try:
            selected_prompt = self.options.get(option, ('', ''))
            prompt_prefix = selected_prompt['prefix']
            system_instruction = selected_prompt['instruction']
            text_for_llm = selected_text
            if option == 'Corrigir PT-BR':
                import re
                # 1. Strip accidental top-row keyboard digits bumped inside, before, or after words
                #    (e.g., "12qual" -> "qual", "melho0r" -> "melhor", "eu8" -> "eu") while preserving standalone numbers like "100%"
                text_for_llm = re.sub(r'(?<=[a-zA-ZÀ-ÿ])[0-9]+|[0-9]+(?=[a-zA-ZÀ-ÿ])', '', text_for_llm)
                # 2. Fix punctuation and common PT-BR fast-typing shortcuts/transpositions
                text_for_llm = text_for_llm.replace('en>?', 'né?').replace('en?', 'né?')
                _ptbr_fast_map = [
                    (r'\bentai\b', 'então'),
                    (r'\bnoc aso\b', 'no caso'),
                    (r'\bd aprada\b', 'da parada'),
                    (r'\boe stiloera\b', 'o estilera'),
                    (r'\bgastoq\b', 'gasto que'),
                    (r'\bvcx\b', 'você'),
                    (r'\bdboa\b', 'de boa'),
                    (r'\bminah\b', 'minha'),
                    (r'\btesrtar\b', 'testar'),
                    (r'\bceritnho\b', 'certinho'),
                    (r'\begmam\b', 'Gemma'),
                    (r'\bamndsar\b', 'mandar'),
                    (r'\bamdnar\b', 'mandar'),
                    (r'\bpsosuvel\b', 'possível'),
                    (r'\bpadroa\b', 'padrão'),
                    (r'\bduplciar\b', 'duplicar'),
                    (r'\btetxo\b', 'texto'),
                    (r'\betxtod\b', 'texto'),
                    (r'\bqualqwuer\b', 'qualquer'),
                    (r'\bqualwuerr\b', 'qualquer'),
                    (r'\bqazuqleur\b', 'qualquer'),
                    (r'\brpa\b', 'pra'),
                    (r'\bess ecaso\b', 'esse caso'),
                    (r'\bevr\b', 'ver'),
                    (r'\bafzer\b', 'fazer'),
                    (r'\bbvora\b', 'bora'),
                ]
                for pat, repl in _ptbr_fast_map:
                    text_for_llm = re.sub(pat, repl, text_for_llm, flags=re.IGNORECASE)
            if option == 'Custom':
                prompt = f"{prompt_prefix}Described change: {custom_change}\n\nText: {text_for_llm}"
            else:
                prompt = f"{prompt_prefix}{text_for_llm}"

            self.output_queue = ""

            QtCore.QMetaObject.invokeMethod(
                self,
                "set_tray_state",
                QtCore.Qt.ConnectionType.QueuedConnection,
                QtCore.Q_ARG(str, "processing"),
                QtCore.Q_ARG(str, f"Processando: {option}...")
            )

            logging.debug(f'Getting response from provider for option: {option}')

            if self.options[option]['open_in_window']:
                logging.debug('Getting response for window display')
                response = self.current_provider.get_response(system_instruction, prompt, return_response=True)
                logging.debug(f'Got response of length: {len(response) if response else 0}')

                if hasattr(self, 'current_response_window'):
                    # noinspection PyTypeChecker
                    QtCore.QMetaObject.invokeMethod(
                        self.current_response_window,
                        'set_text',
                        QtCore.Qt.ConnectionType.QueuedConnection,
                        QtCore.Q_ARG(str, response)
                    )
                    logging.debug('Invoked set_text on response window')
            else:
                logging.debug('Getting response for direct replacement')
                self.current_provider.get_response(system_instruction, prompt)
                logging.debug('Response processed')

        except Exception as e:
            logging.error(f'An error occurred: {e}', exc_info=True)

            if "Resource has been exhausted" in str(e):
                self.show_message_signal.emit('Error - Rate Limit Hit', 'Whoops! You\'ve hit the per-minute rate limit of the Gemini API. Please try again in a few moments.\n\nIf this happens often, simply switch to a Gemini model with a higher usage limit in Settings.')
            else:
                self.show_message_signal.emit('Error', f'An error occurred: {e}')
        finally:
            QtCore.QMetaObject.invokeMethod(
                self,
                "set_tray_state",
                QtCore.Qt.ConnectionType.QueuedConnection,
                QtCore.Q_ARG(str, "idle"),
                QtCore.Q_ARG(str, "")
            )

    def _close_working_popup(self):
        """If the popup is showing the orb (text mode working state), hide it and let focus return."""
        popup = self.popup_window
        if popup is not None and getattr(popup, 'is_working_text', False):
            popup.is_busy_processing = False
            popup.is_working_text = False
            popup.hide()
            QApplication.processEvents()
            time.sleep(0.18)  # let Windows hand focus back to the target text field

    @Slot(str, str)
    def show_message_box(self, title, message):
        """
        Show a message box with the given title and message.
        """
        self._close_working_popup()
        QMessageBox.warning(None, title, message)

    def show_response_window(self, option, text):
        """
        Show the response in a new window instead of pasting it.
        """
        response_window = ui.ResponseWindow.ResponseWindow(self, f"{option} Result")
        response_window.selected_text = text  # Store the text for regeneration
        response_window.show()
        return response_window

    def replace_text(self, new_text):
        """
        Replaces the text by pasting in the LLM generated text. With "Key Points" and "Summary", invokes a window with the output instead.
        """
        error_message = 'ERROR_TEXT_INCOMPATIBLE_WITH_REQUEST'

        # Confirm new_text exists and is a string
        if new_text and isinstance(new_text, str):
            self.output_queue += new_text
            current_output = self.output_queue.strip()  # Strip whitespace for comparison

            # If the new text is the error message, show a message box
            if current_output == error_message:
                self.show_message_signal.emit('Error', 'The text is incompatible with the requested change.')
                return

            # Check if we're building up to the error message (to prevent partial pasting)
            if len(current_output) <= len(error_message):
                clean_current = ''.join(current_output.split())
                clean_error = ''.join(error_message.split())
                if clean_current == clean_error[:len(clean_current)]:
                    return

            logging.debug('Processing output text')
            try:
                # For Summary and Key Points, show in response window
                if getattr(self, 'current_response_window', None) is not None:
                    self.current_response_window.append_text(new_text)
                    
                    # If this is the initial response, add it to chat history
                    if len(self.current_response_window.chat_history) == 1:  # Only original text exists
                        self.current_response_window.chat_history.append({
                            "role": "assistant",
                            "content": self.output_queue.rstrip('\n')
                        })
                else:
                    # For other options, use clipboard-based replacement on the Default desktop
                    self._close_working_popup()
                    self._attach_thread_to_default_desktop()
                    clipboard_backup = pyperclip.paste()
                    cleaned_text = self.output_queue.rstrip('\n')
                    pyperclip.copy(cleaned_text)

                    if sys.platform == 'win32':
                        import ctypes
                        user32 = ctypes.windll.user32
                        KEYEVENTF_KEYUP = 0x0002
                        VK_CONTROL = 0x11
                        VK_V = 0x56
                        user32.keybd_event(VK_CONTROL, 0, 0, 0)
                        user32.keybd_event(VK_V, 0, 0, 0)
                        user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
                        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
                    else:
                        kbrd = pykeyboard.Controller()
                        kbrd.press(pykeyboard.Key.ctrl.value)
                        kbrd.press('v')
                        kbrd.release('v')
                        kbrd.release(pykeyboard.Key.ctrl.value)

                    time.sleep(0.2)
                    pyperclip.copy(clipboard_backup)

                if getattr(self, 'current_response_window', None) is None:
                    self.output_queue = ""

            except Exception as e:
                logging.error(f'Error processing output: {e}')
        else:
            logging.debug('No new text to process')

    @staticmethod
    def _render_orb_mic_pixmap(size: int = 128, state: str = "idle") -> QtGui.QPixmap:
        """
        Render the Apple Intelligence 3D Glowing Orb with a crisp White Studio Microphone
        in the center and a live status badge dot in the bottom-right corner.
        States:
          - 'idle'       : Iridescent Violet/Blue/Pink Orb + White Mic + Emerald Green Active Badge
          - 'recording'  : Glowing Crimson/Orange Orb + White Mic + Red Recording Badge
          - 'processing' : Electric Cyan/Purple Thinking Orb + White Mic + Cyan AI Badge
          - 'paused'     : Dimmed Slate Orb + White Mic + Amber Pause Badge
        """
        pix = QtGui.QPixmap(size, size)
        pix.fill(QtCore.Qt.GlobalColor.transparent)

        p = QtGui.QPainter(pix)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)

        s = float(size)
        cx, cy = s * 0.5, s * 0.5
        r = s * 0.43

        # 1. Outer Aura Halo
        halo = QtGui.QRadialGradient(cx, cy, s * 0.50)
        if state == "recording":
            halo.setColorAt(0.0, QtGui.QColor(255, 59, 48, 175))
            halo.setColorAt(0.72, QtGui.QColor(255, 45, 85, 85))
            halo.setColorAt(1.0, QtGui.QColor(255, 45, 85, 0))
        elif state == "processing":
            halo.setColorAt(0.0, QtGui.QColor(0, 212, 255, 185))
            halo.setColorAt(0.72, QtGui.QColor(168, 85, 247, 90))
            halo.setColorAt(1.0, QtGui.QColor(168, 85, 247, 0))
        elif state == "paused":
            halo.setColorAt(0.0, QtGui.QColor(120, 120, 135, 90))
            halo.setColorAt(1.0, QtGui.QColor(120, 120, 135, 0))
        else:
            halo.setColorAt(0.0, QtGui.QColor(139, 92, 246, 170))
            halo.setColorAt(0.68, QtGui.QColor(10, 132, 255, 85))
            halo.setColorAt(1.0, QtGui.QColor(236, 72, 153, 0))

        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QtCore.QPointF(cx, cy), s * 0.49, s * 0.49)

        # 2. Main 3D Orb Sphere (Apple Intelligence Iridescent Mesh)
        sphere = QtGui.QRadialGradient(cx - r * 0.28, cy - r * 0.30, r * 1.38)
        if state == "recording":
            sphere.setColorAt(0.0, QtGui.QColor(255, 138, 101))
            sphere.setColorAt(0.38, QtGui.QColor(255, 45, 85))
            sphere.setColorAt(0.78, QtGui.QColor(190, 18, 60))
            sphere.setColorAt(1.0, QtGui.QColor(88, 12, 36))
        elif state == "processing":
            sphere.setColorAt(0.0, QtGui.QColor(103, 232, 249))
            sphere.setColorAt(0.36, QtGui.QColor(14, 165, 233))
            sphere.setColorAt(0.75, QtGui.QColor(139, 92, 246))
            sphere.setColorAt(1.0, QtGui.QColor(46, 16, 101))
        elif state == "paused":
            sphere.setColorAt(0.0, QtGui.QColor(148, 163, 184))
            sphere.setColorAt(0.55, QtGui.QColor(71, 85, 105))
            sphere.setColorAt(1.0, QtGui.QColor(30, 41, 59))
        else:
            # Signature Apple Intelligence Violet -> Electric Blue -> Magenta -> Deep Cosmic Core
            sphere.setColorAt(0.0, QtGui.QColor(96, 165, 250))
            sphere.setColorAt(0.34, QtGui.QColor(139, 92, 246))
            sphere.setColorAt(0.68, QtGui.QColor(219, 39, 119))
            sphere.setColorAt(1.0, QtGui.QColor(30, 18, 68))

        p.setBrush(sphere)
        p.drawEllipse(QtCore.QPointF(cx, cy), r, r)

        # 3. Iridescent Rim Light & Top-Left Glass Specular Glint
        rim_pen = QtGui.QPen(QtGui.QColor(255, 255, 255, 115), max(1.2, s * 0.022))
        p.setPen(rim_pen)
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.drawEllipse(QtCore.QPointF(cx, cy), r - s * 0.01, r - s * 0.01)

        glint = QtGui.QRadialGradient(cx - r * 0.32, cy - r * 0.36, r * 0.55)
        glint.setColorAt(0.0, QtGui.QColor(255, 255, 255, 130))
        glint.setColorAt(0.5, QtGui.QColor(255, 255, 255, 35))
        glint.setColorAt(1.0, QtGui.QColor(255, 255, 255, 0))
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(glint)
        p.drawEllipse(QtCore.QPointF(cx - r * 0.22, cy - r * 0.25), r * 0.52, r * 0.36)

        # 4. Crisp White Studio Microphone in the Center of the Orb
        mic_color = QtGui.QColor(255, 255, 255, 250 if state != "paused" else 185)
        p.setBrush(mic_color)
        p.setPen(QtCore.Qt.PenStyle.NoPen)

        # Microphone capsule (rounded pill)
        cap_w = s * 0.175
        cap_h = s * 0.265
        cap_rect = QtCore.QRectF(cx - cap_w * 0.5, cy - s * 0.19, cap_w, cap_h)
        p.drawRoundedRect(cap_rect, cap_w * 0.5, cap_w * 0.5)

        # Microphone U-cradle arc + stem + base
        stroke_w = max(1.8, s * 0.046)
        mic_pen = QtGui.QPen(mic_color, stroke_w, QtCore.Qt.PenStyle.SolidLine, QtCore.Qt.PenCapStyle.RoundCap)
        p.setPen(mic_pen)
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)

        arc_w = s * 0.31
        arc_h = s * 0.25
        arc_rect = QtCore.QRectF(cx - arc_w * 0.5, cy - s * 0.125, arc_w, arc_h)
        # Draw bottom half of ellipse (180 deg to 360 deg in 1/16th degrees)
        p.drawArc(arc_rect, 180 * 16, 180 * 16)

        # Vertical stand stem & horizontal foot
        stem_top = cy + s * 0.125
        stem_bot = cy + s * 0.205
        p.drawLine(QtCore.QPointF(cx, stem_top), QtCore.QPointF(cx, stem_bot))
        foot_half = s * 0.085
        p.drawLine(QtCore.QPointF(cx - foot_half, stem_bot), QtCore.QPointF(cx + foot_half, stem_bot))

        # 5. Live Status Badge Dot (Bottom-Right Corner) so user always sees if it's active
        badge_r = s * 0.135
        bx = cx + r * 0.68
        by = cy + r * 0.68

        # Dark cutout ring around badge for contrast on any taskbar
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor(15, 15, 22, 235))
        p.drawEllipse(QtCore.QPointF(bx, by), badge_r * 1.28, badge_r * 1.28)

        if state == "recording":
            badge_col = QtGui.QColor("#ff3b30")  # Bright Red (Recording)
        elif state == "processing":
            badge_col = QtGui.QColor("#00d4ff")  # Electric Cyan (AI Thinking)
        elif state == "paused":
            badge_col = QtGui.QColor("#ff9f0a")  # Amber (Paused)
        else:
            badge_col = QtGui.QColor("#30d158")  # Apple Emerald Green (Active & Ready)

        p.setBrush(badge_col)
        p.drawEllipse(QtCore.QPointF(bx, by), badge_r, badge_r)

        p.end()
        return pix

    @classmethod
    def _build_orb_mic_tray_icon(cls, state: str = "idle") -> QtGui.QIcon:
        icon = QtGui.QIcon()
        for sz in (32, 48, 64, 128):
            icon.addPixmap(cls._render_orb_mic_pixmap(sz, state))
        return icon

    @staticmethod
    def _promote_windows_tray_icon():
        """
        On Windows 11, promote our system tray icon in `HKCU\\Control Panel\\NotifyIconSettings`
        (`IsPromoted = 1`) so it appears directly on the taskbar next to the clock instead
        of being hidden inside the '^' overflow popup.
        """
        if not sys.platform.startswith("win32"):
            return
        try:
            import winreg
            base_path = r"Control Panel\NotifyIconSettings"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base_path, 0, winreg.KEY_READ) as parent:
                num_subkeys, _, _ = winreg.QueryInfoKey(parent)
                for i in range(num_subkeys):
                    sub_name = winreg.EnumKey(parent, i)
                    sub_path = f"{base_path}\\{sub_name}"
                    try:
                        with winreg.OpenKey(
                            winreg.HKEY_CURRENT_USER,
                            sub_path,
                            0,
                            winreg.KEY_READ | winreg.KEY_WRITE
                        ) as sk:
                            exe_path, _ = winreg.QueryValueEx(sk, "ExecutablePath")
                            exe_lower = (exe_path or "").lower()
                            if "python" in exe_lower or "writing" in exe_lower:
                                winreg.SetValueEx(sk, "IsPromoted", 0, winreg.REG_DWORD, 1)
                    except WindowsError:
                        continue
        except Exception as e:
            logging.debug(f"NotifyIconSettings promote check: {e}")

    @Slot(str, str)
    def set_tray_state(self, state: str = "idle", custom_status: str = ""):
        """
        Update the System Tray Orb+Mic icon and tooltip to reflect the live state:
        'idle' (🟢 Ativo), 'recording' (🔴 Gravando), 'processing' (🟣 Processando IA), or 'paused' (🟠 Pausado).
        """
        if not self.tray_icon:
            return
        if self.paused and state == "idle":
            state = "paused"

        self._tray_state = state
        self.tray_icon.setIcon(self._build_orb_mic_tray_icon(state))

        shortcut_str = (self.config or {}).get("shortcut", "ctrl+space").upper()
        if state == "recording":
            tip = f"🔴 Gravando Microfone... (Aperte Espaço para transcrever)\n{custom_status}".strip()
        elif state == "processing":
            tip = f"🟣 Apple Intelligence — {custom_status or 'Processando na IA...'}"
        elif state == "paused":
            tip = "🟠 Apple Intelligence — Pausado (Clique com botão direito para retomar)"
        else:
            tip = f"🟢 Apple Intelligence — Ativo e Pronto ({shortcut_str})"

        self.tray_icon.setToolTip(tip)
        if hasattr(self, "_tray_status_action") and self._tray_status_action:
            status_label = {
                "recording": "🔴 Status: Gravando Microfone...",
                "processing": "🟣 Status: Processando na IA...",
                "paused": "🟠 Status: Pausado",
                "idle": f"🟢 Status: Ativo ({shortcut_str})",
            }.get(state, f"🟢 Status: Ativo ({shortcut_str})")
            self._tray_status_action.setText(status_label)

    def _on_tray_icon_activated(self, reason):
        """Left-clicking or double-clicking the tray Orb icon opens the Apple Intelligence popup."""
        if reason in (
            QtWidgets.QSystemTrayIcon.ActivationReason.Trigger,
            QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._show_popup()

    def create_tray_icon(self):
        """
        Create the system tray icon (Apple Intelligence Glowing Orb + Microphone in center)
        and promote it to the visible Windows taskbar area.
        """
        if self.tray_icon:
            logging.debug('Tray icon already exists')
            return

        logging.debug('Creating Apple Intelligence Orb+Mic system tray icon')
        # Also save updated high-res Orb+Mic icon to icons/app_icon.png so windows & shortcuts match
        try:
            icons_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icons')
            os.makedirs(icons_dir, exist_ok=True)
            orb_png_path = os.path.join(icons_dir, 'app_icon.png')
            self._render_orb_mic_pixmap(256, "idle").save(orb_png_path, "PNG")
        except Exception as e:
            logging.debug(f"Could not save updated app_icon.png: {e}")

        orb_icon = self._build_orb_mic_tray_icon("paused" if self.paused else "idle")
        self.setWindowIcon(orb_icon)
        self.tray_icon = QtWidgets.QSystemTrayIcon(orb_icon, self)
        self.tray_menu = QtWidgets.QMenu()
        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.activated.connect(self._on_tray_icon_activated)

        self.update_tray_menu()
        self.tray_icon.show()
        self.set_tray_state("paused" if self.paused else "idle")

        # Promote icon in Windows 11 taskbar after registration
        QtCore.QTimer.singleShot(800, self._promote_windows_tray_icon)
        logging.debug('Tray icon displayed and promoted')

    def update_tray_menu(self):
        """
        Update the system tray context menu with Apple dark/light styling and status indicator.
        """
        self.tray_menu.clear()
        self.apply_dark_mode_styles(self.tray_menu)

        shortcut_str = (self.config or {}).get("shortcut", "ctrl+space").upper()

        # Live status header item
        self._tray_status_action = self.tray_menu.addAction(
            "🟠 Status: Pausado" if self.paused else f"🟢 Status: Ativo ({shortcut_str})"
        )
        self._tray_status_action.setEnabled(False)
        self.tray_menu.addSeparator()

        # Open Apple Intelligence Popup
        open_action = self.tray_menu.addAction(f"🎙️ Abrir Apple Intelligence ({shortcut_str})")
        open_action.triggered.connect(self._show_popup)

        # WhatsApp .ogg Auto-Transcribe Toggle
        wa_on = (self.config or {}).get("whatsapp_ogg_watcher", True)
        self._wa_toggle_action = self.tray_menu.addAction(
            "🟢 Auto-Transcrever WhatsApp (.ogg): Ativo"
            if wa_on else "⚪ Auto-Transcrever WhatsApp (.ogg): Desativado"
        )
        self._wa_toggle_action.triggered.connect(self.toggle_whatsapp_watcher)

        # Settings menu item
        settings_action = self.tray_menu.addAction("⚙️ Configurações")
        settings_action.triggered.connect(self.show_settings)

        # Pause/Resume toggle action
        self.toggle_action = self.tray_menu.addAction(
            "▶️ Retomar Atalhos" if self.paused else "⏸️ Pausar Atalhos"
        )
        self.toggle_action.triggered.connect(self.toggle_paused)

        # About menu item
        about_action = self.tray_menu.addAction("ℹ️ Sobre")
        about_action.triggered.connect(self.show_about)

        self.tray_menu.addSeparator()

        # Exit menu item
        exit_action = self.tray_menu.addAction("❌ Sair")
        exit_action.triggered.connect(self.exit_app)

    def toggle_whatsapp_watcher(self):
        """Toggle automatic WhatsApp .ogg download transcription on/off."""
        cur = (self.config or {}).get("whatsapp_ogg_watcher", True)
        new_val = not cur
        if self.config is not None:
            self.config["whatsapp_ogg_watcher"] = new_val
            self.save_config(self.config)
        if hasattr(self, "whatsapp_watcher") and self.whatsapp_watcher:
            self.whatsapp_watcher.set_enabled(new_val)
        self.update_tray_menu()

    def toggle_paused(self):
        """Toggle the paused state of the application and update tray icon badge."""
        logging.debug('Toggle paused state')
        self.paused = not self.paused
        self.toggle_action.setText("▶️ Retomar Atalhos" if self.paused else "⏸️ Pausar Atalhos")
        self.set_tray_state("paused" if self.paused else "idle")
        logging.debug('App is paused' if self.paused else 'App is resumed')

    @staticmethod
    def apply_dark_mode_styles(menu):
        """
        Apply Apple-style dark/light glass menu stylesheet to the system tray menu.
        """
        is_dark_mode = darkdetect.isDark()
        if is_dark_mode:
            menu.setStyleSheet("""
                QMenu {
                    background-color: #1c1c22;
                    color: #f5f5f7;
                    border: 1px solid rgba(255, 255, 255, 0.16);
                    border-radius: 10px;
                    padding: 6px;
                    font-family: 'Segoe UI Variable Display', 'Segoe UI', sans-serif;
                    font-size: 12.5px;
                }
                QMenu::item {
                    padding: 6px 14px;
                    border-radius: 6px;
                }
                QMenu::item:selected {
                    background-color: #0a84ff;
                    color: #ffffff;
                }
                QMenu::item:disabled {
                    color: #30d158;
                    font-weight: 700;
                }
                QMenu::separator {
                    height: 1px;
                    background: rgba(255, 255, 255, 0.12);
                    margin: 4px 8px;
                }
            """)
        else:
            menu.setStyleSheet("""
                QMenu {
                    background-color: #f5f5f7;
                    color: #1d1d1f;
                    border: 1px solid rgba(0, 0, 0, 0.12);
                    border-radius: 10px;
                    padding: 6px;
                    font-family: 'Segoe UI Variable Display', 'Segoe UI', sans-serif;
                    font-size: 12.5px;
                }
                QMenu::item {
                    padding: 6px 14px;
                    border-radius: 6px;
                }
                QMenu::item:selected {
                    background-color: #007aff;
                    color: #ffffff;
                }
                QMenu::item:disabled {
                    color: #248a3d;
                    font-weight: 700;
                }
                QMenu::separator {
                    height: 1px;
                    background: rgba(0, 0, 0, 0.10);
                    margin: 4px 8px;
                }
            """)


    """
    The function below (process_followup_question) processes follow-up questions in the chat interface for Summary, Key Points, and Table operations.

    This method handles the complex interaction between the UI, chat history, and AI providers:

    1. Chat History Management:
    - Maintains a list of all messages (original text, summary, follow-ups)
    - Properly formats roles (user/assistant) for each message
    - Preserves conversation context across multiple questions (until the Window is closed)

    2. Provider-Specific Handling:
    a) Gemini:
        - Converts internal roles to Gemini's user/model format
        - Uses chat sessions with proper history formatting
        - Maintains context through chat.send_message()
    
    b) OpenAI-compatible:
        - Uses standard OpenAI message array format
        - Includes system instruction and full conversation history
        - Properly maps internal roles to OpenAI roles

    3. Flow:
    a) User asks follow-up question
    b) Question is added to chat history
    c) Full history is formatted for the current provider
    d) Response is generated while maintaining context
    e) Response is displayed in chat UI
    f) New response is added to history for future context

    4. Threading:
    - Runs in a separate thread to prevent UI freezing
    - Uses signals to safely update UI from background thread
    - Handles errors too

    Args:
        response_window: The ResponseWindow instance managing the chat UI
        question: The follow-up question from the user

    This implementation is a bit convoluted, but it allows us to manage chat history & model roles across both providers! :3
    """

    def process_followup_question(self, response_window, question):
        """
        Process a follow-up question in the chat window.
        """
        logging.debug(f'Processing follow-up question: {question}')
        
        def process_thread():
            logging.debug('Starting follow-up processing thread')
            try:
                if not response_window.chat_history:
                    logging.error("No chat history found")
                    self.show_message_signal.emit('Error', 'Chat history not found')
                    return

                # Add current question to chat history
                response_window.chat_history.append({
                    "role": "user",
                    "content": question
                })
                
                # Get chat history
                history = response_window.chat_history.copy()
                
                # System instruction based on original option
                system_instruction = "You are a helpful AI assistant. Provide clear and direct responses, maintaining the same format and style as your previous responses. If appropriate, use Markdown formatting to make your response more readable."
                
                logging.debug('Sending request to AI provider')
                
                # Format conversation differently based on provider
                if isinstance(self.current_provider, GeminiProvider):
                    # Gemini takes the system instruction via its config object,
                    # not as an in-history message. We pass the raw chat history
                    # (user/assistant turns); GeminiProvider handles role mapping
                    # and drops any "system" entries internally.
                    response_text = self.current_provider.get_response(
                        system_instruction,
                        history,
                        return_response=True
                    )

                elif isinstance(self.current_provider, OllamaProvider):  #
                    # For Ollama, prepare messages with system instruction and history
                    messages = [{"role": "system", "content": system_instruction}]

                    for msg in history:
                        messages.append({
                            "role": msg["role"],
                            "content": msg["content"]
                        })

                    # Get response from Ollama
                    response_text = self.current_provider.get_response(
                        system_instruction,
                        messages,
                        return_response=True
                    )

                else:
                    # For OpenAI/compatible providers, prepare messages array, add system message
                    messages = [{"role": "system", "content": system_instruction}]

                    # Add history messages (including latest question)
                    for msg in history:
                        # Convert 'assistant' role to 'assistant' for OpenAI
                        role = "assistant" if msg["role"] == "assistant" else "user"
                        messages.append({"role": role, "content": msg["content"]})
                    
                    # Get response by passing the full messages array
                    response_text = self.current_provider.get_response(
                        system_instruction,
                        messages,  # Pass messages array directly
                        return_response=True
                    )

                logging.debug(f'Got response of length: {len(response_text)}')
                
                # Add response to chat history
                response_window.chat_history.append({
                    "role": "assistant",
                    "content": response_text
                })
                
                # Emit response via signal
                self.followup_response_signal.emit(response_text)

            except Exception as e:
                logging.error(f'Error processing follow-up question: {e}', exc_info=True)

                if "Resource has been exhausted" in str(e):
                    self.show_message_signal.emit('Error - Rate Limit Hit', 'Whoops! You\'ve hit the per-minute rate limit of the Gemini API. Please try again in a few moments.\n\nIf this happens often, simply switch to a Gemini model with a higher usage limit in Settings.')
                    self.followup_response_signal.emit("Sorry, an error occurred while processing your question.")
                else:
                    self.show_message_signal.emit('Error', f'An error occurred: {e}')
                    self.followup_response_signal.emit("Sorry, an error occurred while processing your question.")

        # Start the thread
        threading.Thread(target=process_thread, daemon=True).start()

    def show_settings(self, providers_only=False):
        """
        Show the settings window and force it to the foreground on Windows.
        """
        logging.info('Showing settings window')
        try:
            if self.settings_window is not None and self.settings_window.isVisible():
                self.settings_window.close()
            self.settings_window = ui.SettingsWindow.SettingsWindow(self, providers_only=bool(providers_only))
            self.settings_window.retranslate_ui()
            self.settings_window.showNormal()
            self.settings_window.show()
            self.settings_window.raise_()
            self.settings_window.activateWindow()
            if sys.platform == "win32":
                import ctypes
                user32 = ctypes.windll.user32
                hwnd = int(self.settings_window.winId())
                user32.ShowWindow(hwnd, 5)  # SW_SHOW
                user32.SetForegroundWindow(hwnd)
        except Exception as e:
            logging.error(f"Error opening SettingsWindow: {e}", exc_info=True)


    def show_about(self):
        """
        Show the about window.
        """
        logging.debug('Showing about window')
        if not self.about_window:
            self.about_window = ui.AboutWindow.AboutWindow()
        self.about_window.show()

    def setup_ctrl_c_listener(self):
        """
        Listener for Ctrl+C to exit the app.
        """
        signal.signal(signal.SIGINT, lambda signum, frame: self.handle_sigint(signum, frame))
        # This empty timer is needed to make sure that the sigint handler gets checked inside the main loop:
        # without it, the sigint handle would trigger only when an event is triggered, either by a hotkey combination
        # or by another GUI event like spawning a new window. With this we trigger it every 100ms with an empy lambda
        # so that the signal handler gets checked regularly.
        self.ctrl_c_timer = QtCore.QTimer()
        self.ctrl_c_timer.start(100)
        self.ctrl_c_timer.timeout.connect(lambda: None)
    def handle_sigint(self, signum, frame):
        """
        Handle the SIGINT signal (Ctrl+C) to exit the app gracefully.
        """
        logging.info("Received SIGINT. Exiting...")
        self.exit_app()

    def exit_app(self):
        """
        Exit the application.
        """
        logging.debug('Stopping the listener')
        if self.hotkey_listener is not None:
            self.hotkey_listener.stop()
        logging.debug('Exiting application')
        self.quit()
