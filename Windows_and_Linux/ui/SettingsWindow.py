import os
import sys

from aiprovider import AIProvider
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QHBoxLayout, QRadioButton, QScrollArea

from ui.AutostartManager import AutostartManager
from ui.Motion import GlowButton, fade_in
from ui.UIUtils import UIUtils, colorMode

_ = lambda x: x

PROVIDER_DISPLAY_TO_INTERNAL = [
    ("🏠 Ollama (IA Local Gratuita — Sem API Key)", "Ollama (For Experts)"),
    ("✨ Google Gemini (Nuvem — API Key Gratuita)", "Gemini (Recommended)"),
    ("🌐 OpenAI / Groq / OpenRouter / LM Studio", "OpenAI Compatible (For Experts)"),
]

WHISPER_MODEL_OPTIONS = [
    ("⚡ tiny — Ultra leve (~75 MB) • Ideal para PC fraco / CPU", "tiny"),
    ("🚀 base — Leve e rápido (~145 MB) • PC modesto / CPU", "base"),
    ("⚖️ small — Equilibrado (~460 MB) • GPU básica ou CPU boa", "small"),
    ("🎯 medium — Alta precisão (~1.5 GB) • GPU intermediária", "medium"),
    ("🏆 large-v3 — Máxima precisão (~3 GB) • GPU NVIDIA RTX", "large-v3"),
]

WHISPER_DEVICE_OPTIONS = [
    ("🔄 Automático (GPU CUDA float16 se disponível, senão CPU int8)", "auto"),
    ("🟢 GPU NVIDIA — CUDA float16 (Mais rápido em placas RTX/GTX)", "cuda_float16"),
    ("🟡 GPU NVIDIA — CUDA int8_float16 (Economiza VRAM)", "cuda_int8"),
    ("💻 Processador — CPU int8 (Para PCs sem placa NVIDIA)", "cpu_int8"),
]


class SettingsWindow(QtWidgets.QWidget):
    """
    Apple Intelligence-styled Settings window.
    Allows configuring:
    1. General Settings (Global Shortcut, Theme, Start on Boot)
    2. Faster-Whisper Audio Transcription Settings (Model Size, Compute Device/Precision, LLM Refinement toggle)
    3. AI Provider for Text Correction & Audio Refinement (Local Ollama auto-detected, Gemini, or OpenAI-compatible)
    All changes apply immediately on Save without restarting the application.
    """
    close_signal = QtCore.Signal()

    def __init__(self, app, providers_only=False):
        super().__init__()
        self.app = app
        self.current_provider_layout = None
        self.providers_only = providers_only
        self.gradient_radio = None
        self.plain_radio = None
        self.provider_dropdown = None
        self.provider_container = None
        self.autostart_checkbox = None
        self.shortcut_input = None
        self.whisper_model_dropdown = None
        self.whisper_device_dropdown = None
        self.audio_llm_checkbox = None
        self.init_ui()
        self.retranslate_ui()

    def retranslate_ui(self):
        self.setWindowTitle("Configurações — Writing Tools")

    @staticmethod
    def _card_style() -> str:
        bg = "rgba(255, 255, 255, 0.055)" if colorMode == "dark" else "rgba(0, 0, 0, 0.035)"
        border = "rgba(255, 255, 255, 0.13)" if colorMode == "dark" else "rgba(0, 0, 0, 0.10)"
        return f"""
            QWidget#settingsCard {{
                background-color: {bg};
                border: 1px solid {border};
                border-radius: 14px;
            }}
        """

    @staticmethod
    def _combo_style() -> str:
        combo_bg = "rgba(255, 255, 255, 0.08)" if colorMode == "dark" else "rgba(0, 0, 0, 0.05)"
        combo_border = "rgba(255, 255, 255, 0.16)" if colorMode == "dark" else "rgba(0, 0, 0, 0.14)"
        combo_fg = "#f5f5f7" if colorMode == "dark" else "#1d1d1f"
        popup_bg = "#2c2c2e" if colorMode == "dark" else "#ffffff"
        return f"""
            QComboBox {{
                font-size: 13px;
                padding: 7px 10px;
                background-color: {combo_bg};
                color: {combo_fg};
                border: 1px solid {combo_border};
                border-radius: 9px;
            }}
            QComboBox:hover {{
                border: 1px solid #0a84ff;
            }}
            QComboBox::drop-down {{
                border: none;
                width: 24px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {popup_bg};
                color: {combo_fg};
                selection-background-color: #0a84ff;
                selection-color: #ffffff;
                border: 1px solid {combo_border};
                border-radius: 8px;
                padding: 4px;
                outline: none;
            }}
        """

    @staticmethod
    def _input_style() -> str:
        input_bg = "rgba(255, 255, 255, 0.08)" if colorMode == "dark" else "rgba(0, 0, 0, 0.05)"
        input_border = "rgba(255, 255, 255, 0.16)" if colorMode == "dark" else "rgba(0, 0, 0, 0.14)"
        input_fg = "#f5f5f7" if colorMode == "dark" else "#1d1d1f"
        return f"""
            QLineEdit {{
                font-size: 13px;
                padding: 7px 10px;
                background-color: {input_bg};
                color: {input_fg};
                border: 1px solid {input_border};
                border-radius: 9px;
            }}
            QLineEdit:focus {{
                border: 1px solid #0a84ff;
            }}
        """

    def init_provider_ui(self, provider: AIProvider, layout):
        """
        Initialize the user interface for the selected AI provider.
        """
        if self.current_provider_layout:
            self.current_provider_layout.setParent(None)
            UIUtils.clear_layout(self.current_provider_layout)
            self.current_provider_layout.deleteLater()

        # Refresh local Ollama models dynamically when switching to Ollama
        if hasattr(provider, "refresh_detected_models"):
            provider.refresh_detected_models()

        self.current_provider_layout = QtWidgets.QVBoxLayout()
        self.current_provider_layout.setSpacing(10)

        # Provider Header (Logo + Name)
        provider_header_layout = QtWidgets.QHBoxLayout()
        provider_header_layout.setSpacing(10)
        provider_header_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)

        if provider.logo:
            logo_path = os.path.join(os.path.dirname(sys.argv[0]), 'icons', f"provider_{provider.logo}.png")
            if os.path.exists(logo_path):
                targetPixmap = UIUtils.resize_and_round_image(QImage(logo_path), 26, 13)
                logo_label = QtWidgets.QLabel()
                logo_label.setPixmap(targetPixmap)
                logo_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignVCenter)
                provider_header_layout.addWidget(logo_label)

        friendly_title = provider.provider_name
        for disp, internal in PROVIDER_DISPLAY_TO_INTERNAL:
            if internal == provider.provider_name:
                friendly_title = disp
                break

        provider_name_label = QtWidgets.QLabel(friendly_title)
        provider_name_label.setStyleSheet(f"""
            font-size: 15px;
            font-weight: 700;
            color: {'#f5f5f7' if colorMode == 'dark' else '#1d1d1f'};
        """)
        provider_name_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignVCenter)
        provider_header_layout.addWidget(provider_name_label)
        self.current_provider_layout.addLayout(provider_header_layout)

        if provider.description:
            description_label = QtWidgets.QLabel(provider.description)
            description_label.setStyleSheet(f"""
                font-size: 12px;
                color: {'#aeaeb2' if colorMode == 'dark' else '#555555'};
                line-height: 1.35;
            """)
            description_label.setWordWrap(True)
            self.current_provider_layout.addWidget(description_label)

        # Initialize config if needed
        if "providers" not in self.app.config:
            self.app.config["providers"] = {}
        if provider.provider_name not in self.app.config["providers"]:
            self.app.config["providers"][provider.provider_name] = {}

        # Render each setting field
        for setting in provider.settings:
            setting.set_value(
                self.app.config["providers"][provider.provider_name].get(setting.name, setting.default_value)
            )
            setting.render_to_layout(self.current_provider_layout)

        # Action button (e.g., Get API Key or Browse Ollama Library)
        if provider.button_text:
            button = GlowButton(f"🔗  {provider.button_text}", radius=8)
            button.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
            button.setStyleSheet(f"""
                QPushButton {{
                    background-color: {'rgba(10, 132, 255, 0.18)' if colorMode == 'dark' else 'rgba(0, 122, 255, 0.12)'};
                    color: {'#409cff' if colorMode == 'dark' else '#007aff'};
                    padding: 7px 14px;
                    font-size: 12px;
                    font-weight: 600;
                    border: 1px solid {'rgba(10, 132, 255, 0.4)' if colorMode == 'dark' else 'rgba(0, 122, 255, 0.3)'};
                    border-radius: 8px;
                }}
                QPushButton:hover {{
                    background-color: {'rgba(10, 132, 255, 0.30)' if colorMode == 'dark' else 'rgba(0, 122, 255, 0.20)'};
                }}
            """)
            button.clicked.connect(provider.button_action)
            self.current_provider_layout.addWidget(button, alignment=QtCore.Qt.AlignmentFlag.AlignLeft)

        layout.addLayout(self.current_provider_layout)

    def _get_selected_provider_instance(self) -> AIProvider:
        internal_name = self.provider_dropdown.currentData()
        for p in self.app.providers:
            if p.provider_name == internal_name:
                return p
        return self.app.providers[0]

    def init_ui(self):
        """
        Initialize the Apple Intelligence-styled settings window.
        """
        self.setWindowTitle("Configurações — Writing Tools")
        self.setMinimumWidth(610)
        self.setFixedWidth(610)

        UIUtils.setup_window_and_layout(self)
        main_layout = QtWidgets.QVBoxLayout(self.background)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(8)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollArea > QWidget > QWidget {
                background: transparent;
            }
            QScrollBar:vertical {
                background-color: transparent;
                width: 10px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background-color: rgba(128, 128, 128, 0.45);
                min-height: 24px;
                border-radius: 5px;
                margin: 2px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        scroll_content = QtWidgets.QWidget()
        scroll_content.setStyleSheet("background: transparent;")
        content_layout = QtWidgets.QVBoxLayout(scroll_content)
        content_layout.setContentsMargins(24, 22, 24, 16)
        content_layout.setSpacing(14)

        fg_primary = "#f5f5f7" if colorMode == "dark" else "#1d1d1f"
        fg_secondary = "#98989d" if colorMode == "dark" else "#636366"

        # ─── HEADER ──────────────────────────────────────────────────────────
        header_row = QHBoxLayout()
        title_col = QtWidgets.QVBoxLayout()
        title_col.setSpacing(2)
        title_label = QtWidgets.QLabel("⚙️  Configurações")
        title_label.setStyleSheet(f"font-size: 21px; font-weight: 700; color: {fg_primary};")
        subtitle_label = QtWidgets.QLabel("Personalize o modelo do Faster-Whisper, a IA de correção de texto (Local ou Nuvem) e atalhos.")
        subtitle_label.setStyleSheet(f"font-size: 12px; color: {fg_secondary};")
        subtitle_label.setWordWrap(True)
        title_col.addWidget(title_label)
        title_col.addWidget(subtitle_label)
        header_row.addLayout(title_col, 1)
        content_layout.addLayout(header_row)

        # ─── CARD 1: IA DE CORREÇÃO DE TEXTO & REVISÃO (OLLAMA LOCAL / GEMINI / OPENAI) ───
        ai_card = QtWidgets.QWidget()
        ai_card.setObjectName("settingsCard")
        ai_card.setStyleSheet(self._card_style())
        ai_layout = QtWidgets.QVBoxLayout(ai_card)
        ai_layout.setContentsMargins(16, 14, 16, 16)
        ai_layout.setSpacing(10)

        ai_section_title = QtWidgets.QLabel("🧠  Inteligência Artificial (Correção de Texto e Pontuação)")
        ai_section_title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {fg_primary};")
        ai_layout.addWidget(ai_section_title)

        provider_label = QtWidgets.QLabel("Escolha qual IA deseja usar:")
        provider_label.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {fg_primary};")
        ai_layout.addWidget(provider_label)

        self.provider_dropdown = QtWidgets.QComboBox()
        self.provider_dropdown.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        self.provider_dropdown.setStyleSheet(self._combo_style())
        self.provider_dropdown.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)

        current_provider_internal = self.app.config.get('provider', 'Ollama (For Experts)')
        selected_prov_idx = 0
        for idx, (disp_text, internal_key) in enumerate(PROVIDER_DISPLAY_TO_INTERNAL):
            self.provider_dropdown.addItem(disp_text, internal_key)
            if internal_key == current_provider_internal:
                selected_prov_idx = idx

        self.provider_dropdown.setCurrentIndex(selected_prov_idx)
        ai_layout.addWidget(self.provider_dropdown)

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {'rgba(255,255,255,0.1)' if colorMode=='dark' else 'rgba(0,0,0,0.08)'};")
        ai_layout.addWidget(sep)

        self.provider_container = QtWidgets.QVBoxLayout()
        ai_layout.addLayout(self.provider_container)

        self.init_provider_ui(self._get_selected_provider_instance(), self.provider_container)
        self.provider_dropdown.currentIndexChanged.connect(
            lambda: self.init_provider_ui(self._get_selected_provider_instance(), self.provider_container)
        )

        content_layout.addWidget(ai_card)

        # ─── CARD 2: TRANSCRIÇÃO DE ÁUDIO (FASTER-WHISPER) ───────────────────
        whisper_card = QtWidgets.QWidget()
        whisper_card.setObjectName("settingsCard")
        whisper_card.setStyleSheet(self._card_style())
        whisper_layout = QtWidgets.QVBoxLayout(whisper_card)
        whisper_layout.setContentsMargins(16, 14, 16, 16)
        whisper_layout.setSpacing(10)

        w_title = QtWidgets.QLabel("🎙️  Transcrição de Voz (Faster-Whisper Local)")
        w_title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {fg_primary};")
        whisper_layout.addWidget(w_title)

        w_desc = QtWidgets.QLabel(
            "Se o computador não tiver uma placa de vídeo forte, escolha um modelo mais leve (como 'base' ou 'small') "
            "ou mude o processador para CPU."
        )
        w_desc.setWordWrap(True)
        w_desc.setStyleSheet(f"font-size: 12px; color: {fg_secondary};")
        whisper_layout.addWidget(w_desc)

        # Whisper Model Size Dropdown
        w_model_label = QtWidgets.QLabel("Tamanho do Modelo Faster-Whisper:")
        w_model_label.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {fg_primary};")
        whisper_layout.addWidget(w_model_label)

        self.whisper_model_dropdown = QtWidgets.QComboBox()
        self.whisper_model_dropdown.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        self.whisper_model_dropdown.setStyleSheet(self._combo_style())
        saved_w_model = self.app.config.get('whisper_model', 'large-v3')
        w_model_idx = 4  # default large-v3
        for idx, (label_txt, val) in enumerate(WHISPER_MODEL_OPTIONS):
            self.whisper_model_dropdown.addItem(label_txt, val)
            if val == saved_w_model:
                w_model_idx = idx
        self.whisper_model_dropdown.setCurrentIndex(w_model_idx)
        whisper_layout.addWidget(self.whisper_model_dropdown)

        # Whisper Device / Compute Type Dropdown
        w_dev_label = QtWidgets.QLabel("Placa de Vídeo / Dispositivo de Execução:")
        w_dev_label.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {fg_primary};")
        whisper_layout.addWidget(w_dev_label)

        self.whisper_device_dropdown = QtWidgets.QComboBox()
        self.whisper_device_dropdown.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        self.whisper_device_dropdown.setStyleSheet(self._combo_style())
        saved_w_dev = self.app.config.get('whisper_device', 'auto')
        w_dev_idx = 0
        for idx, (label_txt, val) in enumerate(WHISPER_DEVICE_OPTIONS):
            self.whisper_device_dropdown.addItem(label_txt, val)
            if val == saved_w_dev:
                w_dev_idx = idx
        self.whisper_device_dropdown.setCurrentIndex(w_dev_idx)
        whisper_layout.addWidget(self.whisper_device_dropdown)

        # Checkbox: Refine transcribed audio with LLM
        self.audio_llm_checkbox = QtWidgets.QCheckBox(
            "Passar o texto transcrito pela IA para revisar pontuação e gramática antes de colar"
        )
        self.audio_llm_checkbox.setStyleSheet(f"font-size: 12px; color: {fg_primary}; margin-top: 4px;")
        self.audio_llm_checkbox.setChecked(self.app.config.get('audio_llm_refinement', True))
        whisper_layout.addWidget(self.audio_llm_checkbox)

        content_layout.addWidget(whisper_card)

        # ─── CARD 3: ATALHO GLOBAL & APARÊNCIA ───────────────────────────────
        if not self.providers_only:
            gen_card = QtWidgets.QWidget()
            gen_card.setObjectName("settingsCard")
            gen_card.setStyleSheet(self._card_style())
            gen_layout = QtWidgets.QVBoxLayout(gen_card)
            gen_layout.setContentsMargins(16, 14, 16, 16)
            gen_layout.setSpacing(10)

            gen_title = QtWidgets.QLabel("⌨️  Atalho Global e Aparência")
            gen_title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {fg_primary};")
            gen_layout.addWidget(gen_title)

            shortcut_label = QtWidgets.QLabel("Tecla de Atalho Global (para abrir a janela em qualquer lugar):")
            shortcut_label.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {fg_primary};")
            gen_layout.addWidget(shortcut_label)

            self.shortcut_input = QtWidgets.QLineEdit(self.app.config.get('shortcut', 'ctrl+space'))
            self.shortcut_input.setStyleSheet(self._input_style())
            gen_layout.addWidget(self.shortcut_input)

            theme_label = QtWidgets.QLabel("Estilo visual da janela flutuante:")
            theme_label.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {fg_primary};")
            gen_layout.addWidget(theme_label)

            theme_layout = QHBoxLayout()
            self.gradient_radio = QRadioButton("Gradiente Apple Intelligence (Glass)")
            self.plain_radio = QRadioButton("Sólido Minimalista")
            self.gradient_radio.setStyleSheet(f"color: {fg_primary}; font-size: 12px;")
            self.plain_radio.setStyleSheet(f"color: {fg_primary}; font-size: 12px;")
            current_theme = self.app.config.get('theme', 'gradient')
            self.gradient_radio.setChecked(current_theme == 'gradient')
            self.plain_radio.setChecked(current_theme == 'plain')
            theme_layout.addWidget(self.gradient_radio)
            theme_layout.addWidget(self.plain_radio)
            gen_layout.addLayout(theme_layout)

            if AutostartManager.get_startup_path():
                self.autostart_checkbox = QtWidgets.QCheckBox("Iniciar automaticamente junto com o Windows")
                self.autostart_checkbox.setStyleSheet(f"font-size: 12px; color: {fg_primary};")
                self.autostart_checkbox.setChecked(AutostartManager.check_autostart())
                self.autostart_checkbox.stateChanged.connect(self.toggle_autostart)
                gen_layout.addWidget(self.autostart_checkbox)

            content_layout.addWidget(gen_card)

        scroll_area.setWidget(scroll_content)
        main_layout.addWidget(scroll_area)

        # ─── BOTTOM BAR: SAVE & CANCEL BUTTONS ───────────────────────────────
        bottom_container = QtWidgets.QWidget()
        bottom_container.setStyleSheet("background: transparent;")
        bottom_layout = QtWidgets.QHBoxLayout(bottom_container)
        bottom_layout.setContentsMargins(24, 6, 24, 20)
        bottom_layout.setSpacing(10)

        cancel_button = GlowButton("Cancelar", radius=10)
        cancel_button.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        cancel_button.setFixedHeight(40)
        cancel_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {'rgba(255, 255, 255, 0.08)' if colorMode == 'dark' else 'rgba(0, 0, 0, 0.06)'};
                color: {fg_primary};
                padding: 8px 18px;
                font-size: 13px;
                font-weight: 600;
                border: none;
                border-radius: 10px;
            }}
            QPushButton:hover {{
                background-color: {'rgba(255, 255, 255, 0.15)' if colorMode == 'dark' else 'rgba(0, 0, 0, 0.12)'};
            }}
        """)
        cancel_button.clicked.connect(self.close)
        bottom_layout.addWidget(cancel_button)

        save_button = GlowButton("✓  Salvar e Aplicar Agora", radius=10, glow=0.18)
        save_button.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        save_button.setFixedHeight(40)
        save_button.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0a84ff, stop:1 #5e5ce6);
                color: white;
                padding: 8px 22px;
                font-size: 14px;
                font-weight: 700;
                border: none;
                border-radius: 10px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #0070e0, stop:1 #4b49c8);
            }
        """)
        save_button.clicked.connect(self.save_settings)
        bottom_layout.addWidget(save_button, 1)

        main_layout.addWidget(bottom_container)

        screen = QtWidgets.QApplication.primaryScreen().geometry()
        max_height = int(screen.height() * 0.86)
        desired_height = min(750, max_height)
        self.resize(610, desired_height)

    @staticmethod
    def toggle_autostart(state):
        AutostartManager.set_autostart(state == 2)

    def save_settings(self):
        """Save all settings and apply them immediately without needing to restart."""
        self.app.config['locale'] = 'en'

        if not self.providers_only and self.shortcut_input:
            self.app.config['shortcut'] = self.shortcut_input.text().strip() or 'ctrl+space'
            self.app.config['theme'] = 'gradient' if self.gradient_radio.isChecked() else 'plain'
        else:
            self.app.create_tray_icon()

        # Save Whisper settings and apply immediately
        whisper_model = self.whisper_model_dropdown.currentData() if self.whisper_model_dropdown else 'large-v3'
        whisper_device = self.whisper_device_dropdown.currentData() if self.whisper_device_dropdown else 'auto'
        audio_llm = self.audio_llm_checkbox.isChecked() if self.audio_llm_checkbox else True

        self.app.config['whisper_model'] = whisper_model
        self.app.config['whisper_device'] = whisper_device
        self.app.config['audio_llm_refinement'] = audio_llm

        if hasattr(self.app, 'audio_transcriber') and self.app.audio_transcriber:
            self.app.audio_transcriber.configure_whisper(
                model_size=whisper_model,
                whisper_device=whisper_device,
                reload_async=True
            )

        # Save selected AI Provider settings
        self.app.config['streaming'] = False
        chosen_provider_internal = self.provider_dropdown.currentData()
        self.app.config['provider'] = chosen_provider_internal
        self.app.config['is_config_file_updated_for_v8'] = True
        self.app.config['is_config_file_updated_for_v9'] = True

        selected_provider_obj = self._get_selected_provider_instance()
        selected_provider_obj.save_config()

        self.app.current_provider = selected_provider_obj
        self.app.current_provider.load_config(
            self.app.config.get("providers", {}).get(chosen_provider_internal, {})
        )

        self.app.register_hotkey()
        self.providers_only = False
        self.close()

    def showEvent(self, event):
        super().showEvent(event)
        fade_in(self, 240)

    def closeEvent(self, event):
        super().closeEvent(event)