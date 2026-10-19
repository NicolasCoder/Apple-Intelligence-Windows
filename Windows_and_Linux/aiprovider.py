"""
AI Provider Architecture for Writing Tools
--------------------------------------------

This module handles different AI model providers (Gemini, OpenAI-compatible, Ollama) and manages their interactions
with the main application. It uses an abstract base class pattern for provider implementations.
"""

import base64
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from abc import ABC, abstractmethod
from typing import List

# External libraries
from google import genai
from google.genai import types as genai_types
from ollama import Client as OllamaClient
from openai import OpenAI
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtWidgets import QVBoxLayout
from ui.UIUtils import colorMode

# Obfuscation prefix to identify encrypted API keys
_OBFUSCATION_PREFIX = "enc:"
_XOR_KEY = 0x5A  # Simple XOR key for obfuscation


def obfuscate_api_key(key: str) -> str:
    """
    Obfuscate an API key using XOR + Base64 encoding.
    Returns the obfuscated string with 'enc:' prefix.
    """
    if not key or key.startswith(_OBFUSCATION_PREFIX):
        return key  # Already obfuscated or empty
    xored = bytes([b ^ _XOR_KEY for b in key.encode('utf-8')])
    return _OBFUSCATION_PREFIX + base64.b64encode(xored).decode('ascii')


def deobfuscate_api_key(obfuscated: str) -> str:
    """
    Deobfuscate an API key that was obfuscated with obfuscate_api_key().
    If the key doesn't have the 'enc:' prefix, returns it as-is (plaintext).
    """
    if not obfuscated or not obfuscated.startswith(_OBFUSCATION_PREFIX):
        return obfuscated  # Not obfuscated, return as-is
    encoded = obfuscated[len(_OBFUSCATION_PREFIX):]
    xored = base64.b64decode(encoded)
    return bytes([b ^ _XOR_KEY for b in xored]).decode('utf-8')


class AIProviderSetting(ABC):
    """
    Abstract base class for a provider setting (e.g., API key, model selection).
    """
    def __init__(self, name: str, display_name: str = None, default_value: str = None, description: str = None):
        self.name = name
        self.display_name = display_name if display_name else name
        self.default_value = default_value if default_value else ""
        self.description = description if description else ""

    @abstractmethod
    def render_to_layout(self, layout: QVBoxLayout):
        """Render the setting widget(s) into the provided layout."""
        pass

    @abstractmethod
    def set_value(self, value):
        """Set the internal value from configuration."""
        pass

    @abstractmethod
    def get_value(self):
        """Return the current value from the widget."""
        pass


class TextSetting(AIProviderSetting):
    """
    A text-based setting (for API keys, URLs, etc.).
    """
    def __init__(self, name: str, display_name: str = None, default_value: str = None, description: str = None):
        super().__init__(name, display_name, default_value, description)
        self.internal_value = default_value
        self.input = None

    def render_to_layout(self, layout: QVBoxLayout):
        row_layout = QtWidgets.QVBoxLayout()
        row_layout.setSpacing(4)
        label = QtWidgets.QLabel(self.display_name)
        label.setStyleSheet(f"""
            font-size: 13px;
            font-weight: 600;
            color: {'#ebebf5' if colorMode=='dark' else '#1d1d1f'};
        """)
        row_layout.addWidget(label)

        self.input = QtWidgets.QLineEdit(self.internal_value)
        input_bg = "rgba(255, 255, 255, 0.08)" if colorMode == "dark" else "rgba(0, 0, 0, 0.05)"
        input_border = "rgba(255, 255, 255, 0.16)" if colorMode == "dark" else "rgba(0, 0, 0, 0.14)"
        input_fg = "#f5f5f7" if colorMode == "dark" else "#1d1d1f"
        self.input.setStyleSheet(f"""
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
        """)
        self.input.setPlaceholderText(self.description)
        row_layout.addWidget(self.input)
        layout.addLayout(row_layout)

    def set_value(self, value):
        self.internal_value = value

    def get_value(self):
        return self.input.text() if self.input else self.internal_value


class DropdownSetting(AIProviderSetting):
    """
    A dropdown setting (e.g., for selecting a model).

    Optionally supports a "Custom" option that reveals a text input for arbitrary values.
    When allow_custom=True, users can select "Custom" from the dropdown and enter any value.
    If the loaded config value doesn't match any preset option, "Custom" is auto-selected.
    """
    _CUSTOM_SENTINEL = "__custom__"

    def __init__(self, name: str, display_name: str = None, default_value: str = None,
                 description: str = None, options: list = None, allow_custom: bool = False,
                 custom_placeholder: str = "Digite o nome do modelo customizado"):
        super().__init__(name, display_name, default_value, description)
        self.options = options if options else []
        self.internal_value = default_value
        self.dropdown = None
        self.allow_custom = allow_custom
        self.custom_placeholder = custom_placeholder
        self.custom_input = None
        self.custom_input_container = None

    def render_to_layout(self, layout: QVBoxLayout):
        row_layout = QtWidgets.QVBoxLayout()
        row_layout.setSpacing(4)
        label = QtWidgets.QLabel(self.display_name)
        label.setStyleSheet(f"""
            font-size: 13px;
            font-weight: 600;
            color: {'#ebebf5' if colorMode=='dark' else '#1d1d1f'};
        """)
        row_layout.addWidget(label)

        self.dropdown = QtWidgets.QComboBox()
        self.dropdown.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.PointingHandCursor))
        combo_bg = "rgba(255, 255, 255, 0.08)" if colorMode == "dark" else "rgba(0, 0, 0, 0.05)"
        combo_border = "rgba(255, 255, 255, 0.16)" if colorMode == "dark" else "rgba(0, 0, 0, 0.14)"
        combo_fg = "#f5f5f7" if colorMode == "dark" else "#1d1d1f"
        popup_bg = "#2c2c2e" if colorMode == "dark" else "#ffffff"
        self.dropdown.setStyleSheet(f"""
            QComboBox {{
                font-size: 13px;
                padding: 6px 10px;
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
        """)

        # Add preset options
        for option, value in self.options:
            self.dropdown.addItem(option, value)

        # Add "Custom" option if enabled
        if self.allow_custom:
            self.dropdown.addItem("🔧 Outro modelo (digitar manualmente)...", self._CUSTOM_SENTINEL)

        # Set initial selection based on internal_value
        index = self.dropdown.findData(self.internal_value)
        if index != -1:
            self.dropdown.setCurrentIndex(index)
        elif self.allow_custom and self.internal_value:
            custom_index = self.dropdown.findData(self._CUSTOM_SENTINEL)
            if custom_index != -1:
                self.dropdown.setCurrentIndex(custom_index)

        row_layout.addWidget(self.dropdown)
        layout.addLayout(row_layout)

        # Create custom input row if allow_custom is enabled
        if self.allow_custom:
            self.custom_input_container = QtWidgets.QWidget()
            custom_row_layout = QtWidgets.QHBoxLayout(self.custom_input_container)
            custom_row_layout.setContentsMargins(0, 4, 0, 0)

            self.custom_input = QtWidgets.QLineEdit()
            self.custom_input.setPlaceholderText(self.custom_placeholder)
            self.custom_input.setStyleSheet(f"""
                QLineEdit {{
                    font-size: 13px;
                    padding: 7px 10px;
                    background-color: {combo_bg};
                    color: {combo_fg};
                    border: 1px solid {combo_border};
                    border-radius: 9px;
                }}
                QLineEdit:focus {{
                    border: 1px solid #0a84ff;
                }}
            """)

            if self.dropdown.currentData() == self._CUSTOM_SENTINEL and self.internal_value:
                self.custom_input.setText(self.internal_value)

            custom_row_layout.addWidget(self.custom_input)
            layout.addWidget(self.custom_input_container)

            self.dropdown.currentIndexChanged.connect(self._on_dropdown_changed)
            self._update_custom_input_visibility()

    def _on_dropdown_changed(self):
        self._update_custom_input_visibility()

    def _update_custom_input_visibility(self):
        if self.custom_input_container:
            is_custom = self.dropdown.currentData() == self._CUSTOM_SENTINEL
            self.custom_input_container.setVisible(is_custom)
            if is_custom and self.custom_input:
                self.custom_input.setFocus()

    def set_value(self, value):
        self.internal_value = value

    def get_value(self):
        if not self.dropdown:
            return self.internal_value
        if self.allow_custom and self.dropdown.currentData() == self._CUSTOM_SENTINEL:
            return self.custom_input.text().strip()
        return self.dropdown.currentData()


class AIProvider(ABC):
    """
    Abstract base class for AI providers.
    """
    def __init__(self, app, provider_name: str, settings: List[AIProviderSetting],
                 description: str = "An unfinished AI provider!",
                 logo: str = "generic",
                 button_text: str = "Go to URL",
                 button_action: callable = None):
        self.provider_name = provider_name
        self.settings = settings
        self.app = app
        self.description = description if description else "An unfinished AI provider!"
        self.logo = logo
        self.button_text = button_text
        self.button_action = button_action

    @abstractmethod
    def get_response(self, system_instruction: str, prompt: str) -> str:
        pass

    def load_config(self, config: dict):
        for setting in self.settings:
            if setting.name in config:
                setattr(self, setting.name, config[setting.name])
                setting.set_value(config[setting.name])
            else:
                setattr(self, setting.name, setting.default_value)
                setting.set_value(setting.default_value)
        self.after_load()

    def save_config(self):
        config = {}
        for setting in self.settings:
            config[setting.name] = setting.get_value()
        self.app.config["providers"][self.provider_name] = config
        self.app.save_config(self.app.config)

    @abstractmethod
    def after_load(self):
        pass

    @abstractmethod
    def before_load(self):
        pass

    @abstractmethod
    def cancel(self):
        pass


class GeminiProvider(AIProvider):
    """
    Provider for Google's Gemini API (using the unified `google-genai` SDK).
    """
    _SAFETY_SETTINGS = [
        genai_types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
        genai_types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
        genai_types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
        genai_types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
    ]

    def __init__(self, app):
        self.close_requested = False
        self.client = None

        settings = [
            TextSetting(name="api_key", display_name="Chave de API (Gemini API Key)", description="Cole sua chave de API do Google AI Studio aqui"),
            DropdownSetting(
                name="model_name",
                display_name="Modelo do Gemini",
                default_value="gemini-flash-latest",
                description="Selecione o modelo Gemini",
                options=[
                    ("⭐ Gemini Flash Latest (muito rápido | 20 usos grátis/dia)", "gemini-flash-latest"),
                    ("Gemma 4 31B (uso gratuito ilimitado)", "gemma-4-31b-it"),
                    ("Gemma 4 26B A4B (uso gratuito ilimitado)", "gemma-4-26b-a4b-it"),
                ],
                allow_custom=True,
                custom_placeholder="Ex: gemini-2.5-flash"
            )
        ]
        super().__init__(app, "Gemini (Recommended)", settings,
            "• API do Google Gemini na nuvem (requer chave de API gratuita do Google AI Studio).\n"
            "• Caso prefira usar IA 100% local e sem chave de API, escolha 'Ollama (Local / Grátis)' acima!",
            "gemini",
            "Obter API Key Gratuita (Google AI Studio)",
            lambda: webbrowser.open("https://aistudio.google.com/app/apikey"))

    def _build_config(self, system_instruction: str) -> "genai_types.GenerateContentConfig":
        is_gemma = "gemma" in (self.model_name or "").lower()
        kwargs = {
            "system_instruction": system_instruction,
            "safety_settings": self._SAFETY_SETTINGS,
            "max_output_tokens": 1000,
        }
        if not is_gemma:
            kwargs["thinking_config"] = genai_types.ThinkingConfig(thinking_level="minimal")
        return genai_types.GenerateContentConfig(**kwargs)

    @staticmethod
    def _messages_to_contents(messages: list) -> list:
        contents = []
        for m in messages:
            role = m.get("role")
            if role == "system":
                continue
            text = m.get("content", "")
            gemini_role = "model" if role == "assistant" else "user"
            contents.append(genai_types.Content(role=gemini_role, parts=[genai_types.Part(text=text)]))
        return contents

    def get_response(self, system_instruction: str, prompt, return_response: bool = False) -> str:
        self.close_requested = False

        if not self.client:
            logging.warning("Gemini API key is not configured yet.")
            if not return_response:
                self.app.show_message_signal.emit(
                    "Chave de API Necessária (ou use IA Local)",
                    "Você selecionou o provedor Gemini, mas ainda não configurou uma API Key.\n\n"
                    "💡 Dica: Clique no ícone de engrenagem (⚙️) no canto superior direito da janela flutuante "
                    "para mudar para 'Ollama (For Experts)' (IA Local Gratuita) ou colar sua chave do Gemini!"
                )
            return ""

        try:
            contents = self._messages_to_contents(prompt) if isinstance(prompt, list) else prompt

            response = self.client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=self._build_config(system_instruction),
            )

            response_text = (response.text or "").rstrip('\n')

            if not return_response and not getattr(self.app, 'current_response_window', None):
                self.app.output_ready_signal.emit(response_text)
                self.app.replace_text(True)
                return ""
            return response_text
        except Exception as e:
            logging.error(f"Error processing Gemini response: {e}")
            if not return_response:
                self.app.show_message_signal.emit("Erro no Gemini", f"Ocorreu um erro ao consultar o Gemini: {e}")
            return ""
        finally:
            self.close_requested = False

    def load_config(self, config: dict):
        if 'api_key' in config:
            config = config.copy()
            config['api_key'] = deobfuscate_api_key(config['api_key'])
        super().load_config(config)

    def save_config(self):
        config = {}
        for setting in self.settings:
            value = setting.get_value()
            if setting.name == 'api_key':
                value = obfuscate_api_key(value)
            config[setting.name] = value
        self.app.config["providers"][self.provider_name] = config
        self.app.save_config(self.app.config)

    def after_load(self):
        if self.api_key and self.api_key.strip():
            self.client = genai.Client(api_key=self.api_key.strip())
        else:
            self.client = None

    def before_load(self):
        self.client = None

    def cancel(self):
        self.close_requested = True


class OpenAICompatibleProvider(AIProvider):
    """
    Provider for OpenAI-compatible APIs (Groq, OpenAI, OpenRouter, LM Studio, vLLM, etc.).
    Includes built-in optimization and automatic multi-model rate-limit fallback for Groq Cloud.
    """
    _GROQ_FALLBACK_CHAIN = [
        "openai/gpt-oss-120b",
        "qwen/qwen3.8-27b",
        "openai/gpt-oss-20b",
    ]

    def __init__(self, app):
        self.close_requested = None
        self.client = None

        settings = [
            TextSetting(name="api_key", display_name="Chave de API (Groq / OpenAI API Key)", description="Ex: gsk_... (Groq) ou sk-... (OpenAI)"),
            TextSetting("api_base", "URL Base da API (API Base URL)", "https://api.groq.com/openai/v1", "Groq: https://api.groq.com/openai/v1 | OpenAI: https://api.openai.com/v1"),
            DropdownSetting(
                name="api_model",
                display_name="Modelo da API (Groq / OpenAI)",
                default_value="openai/gpt-oss-120b",
                description="Selecione o modelo da Groq ou OpenAI",
                options=[
                    ("⚡ Groq: GPT-OSS 120B (Máxima Precisão PT-BR | ~0.4s | 1.000/dia grátis)", "openai/gpt-oss-120b"),
                    ("⚡ Groq: Qwen 3.8 27B (Ultra Rápido ~0.3s | 1.000/dia grátis)", "qwen/qwen3.8-27b"),
                    ("⚡ Groq: GPT-OSS 20B (Leve e Instantâneo ~0.3s | 1.000/dia grátis)", "openai/gpt-oss-20b"),
                    ("OpenAI: GPT-4o Mini", "gpt-4o-mini"),
                ],
                allow_custom=True,
                custom_placeholder="Digite o ID do modelo (ex: openai/gpt-oss-120b)"
            ),
            TextSetting("api_organisation", "Organização (Opcional)", "", "Deixe em branco se não aplicável"),
            TextSetting("api_project", "Projeto (Opcional)", "", "Deixe em branco se não aplicável"),
        ]
        super().__init__(app, "OpenAI Compatible (For Experts)", settings,
            "• Ultra-rápido com Groq Cloud (Gratuito até 3.000 correções/dia somando o fallback automático!) ou OpenAI / OpenRouter / LM Studio.",
            "openai", "Obter API Key Gratuita na Groq", lambda: webbrowser.open("https://console.groq.com/keys"))

    def _call_completion(self, model_id: str, messages: list) -> str:
        is_groq = "groq.com" in (self.api_base or "").lower()
        kwargs = {
            "model": model_id,
            "messages": messages,
            "temperature": 0.15,
            "max_tokens": 800,
            "stream": False,
        }
        if is_groq:
            if model_id.startswith("openai/gpt-oss"):
                kwargs["reasoning_effort"] = "low"
            elif model_id.startswith("qwen/"):
                kwargs["reasoning_effort"] = "none"

        response = self.client.chat.completions.create(**kwargs)
        return (response.choices[0].message.content or "").strip()

    def get_response(self, system_instruction: str, prompt: str | list, return_response: bool = False) -> str:
        self.close_requested = False

        if isinstance(prompt, list):
            messages = prompt
        else:
            messages = [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt}
            ]

        is_groq = "groq.com" in (self.api_base or "").lower()
        models_to_try = [self.api_model]
        if is_groq:
            for fb in self._GROQ_FALLBACK_CHAIN:
                if fb not in models_to_try:
                    models_to_try.append(fb)

        last_err = None
        for model_id in models_to_try:
            try:
                response_text = self._call_completion(model_id, messages)
                if not return_response and not getattr(self.app, 'current_response_window', None):
                    self.app.output_ready_signal.emit(response_text)
                return response_text
            except Exception as e:
                last_err = e
                err_lower = str(e).lower()
                if is_groq and ("rate_limit" in err_lower or "429" in err_lower or "too large" in err_lower or "decommissioned" in err_lower):
                    logging.warning(f"Groq model '{model_id}' hit limit or issue ({e}); falling back to next Groq model...")
                    continue
                break

        error_str = str(last_err)
        logging.error(f"Error while generating content: {error_str}")
        if not return_response:
            if "exceeded" in error_str.lower() or "rate limit" in error_str.lower() or "429" in error_str.lower():
                self.app.show_message_signal.emit(
                    "Limite de Requisições Atingido",
                    "Você atingiu o limite temporário de uso da API. Tente novamente em alguns segundos."
                )
            else:
                self.app.show_message_signal.emit("Erro na API", f"Ocorreu um erro: {error_str}")
        return ""

    def after_load(self):
        self.client = OpenAI(
            api_key=self.api_key.strip() if self.api_key else "local-no-key",
            base_url=(self.api_base or "https://api.groq.com/openai/v1").strip(),
            organization=self.api_organisation.strip() if self.api_organisation else None,
            project=self.api_project.strip() if self.api_project else None
        )

    def before_load(self):
        self.client = None

    def cancel(self):
        self.close_requested = True


class OllamaProvider(AIProvider):
    """
    Provider for connecting to a local Ollama server (100% Free & Offline Local AI).
    Auto-detects locally installed Ollama models and automatically starts `ollama serve`
    in the background if Ollama is installed on the machine but not currently running.
    """
    def __init__(self, app):
        self.close_requested = None
        self.client = None
        self.app = app

        detected_models = self.detect_local_models()
        default_model = detected_models[0][1] if detected_models else "llama3.2:latest"

        self.model_dropdown_setting = DropdownSetting(
            name="api_model",
            display_name="Modelo Local do Ollama",
            default_value=default_model,
            description="Selecione um modelo instalado no seu Ollama ou digite outro",
            options=detected_models,
            allow_custom=True,
            custom_placeholder="Ex: llama3.2:latest, qwen2.5:7b, gemma3:4b"
        )

        settings = [
            self.model_dropdown_setting,
            TextSetting("api_base", "URL do Servidor Ollama", "http://localhost:11434", "Ex: http://localhost:11434"),
            TextSetting("keep_alive", "Manter modelo na memória (minutos)", "10", "Ex: 10 (ou -1 para sempre carregado)")
        ]
        super().__init__(app, "Ollama (For Experts)", settings,
            "• Inteligência Artificial 100% Local, Gratuita e Privada rodando no seu PC via Ollama!\n"
            "• Não precisa de chave de API nem pagar nada. Seus modelos instalados no Ollama aparecem automaticamente abaixo.",
            "ollama", "Baixar / Ver Modelos do Ollama",
            lambda: webbrowser.open("https://ollama.com/library"))

    @staticmethod
    def _find_ollama_executable() -> str | None:
        """Find the `ollama` binary in PATH or standard Windows installation paths."""
        exe = shutil.which("ollama")
        if exe:
            return exe
        if sys.platform == "win32":
            local_app_data = os.environ.get("LOCALAPPDATA", "")
            candidate = os.path.join(local_app_data, "Programs", "Ollama", "ollama.exe")
            if os.path.exists(candidate):
                return candidate
            user_profile = os.environ.get("USERPROFILE", "")
            candidate2 = os.path.join(user_profile, "AppData", "Local", "Programs", "Ollama", "ollama.exe")
            if os.path.exists(candidate2):
                return candidate2
        return None

    @classmethod
    def ensure_ollama_running(cls, api_base: str = "http://localhost:11434") -> bool:
        """
        Check if the Ollama HTTP server is reachable; if not, attempt to launch
        `ollama serve` in the background automatically.
        """
        tags_url = f"{api_base.rstrip('/')}/api/tags"
        try:
            with urllib.request.urlopen(tags_url, timeout=1.2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass

        ollama_exe = cls._find_ollama_executable()
        if not ollama_exe:
            return False

        try:
            logging.info(f"Starting local Ollama server in background: {ollama_exe} serve")
            creationflags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
            subprocess.Popen(
                [ollama_exe, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags
            )
            # Wait up to 3.5s for server to come online
            for _ in range(7):
                time.sleep(0.5)
                try:
                    with urllib.request.urlopen(tags_url, timeout=1.0) as resp:
                        if resp.status == 200:
                            logging.info("Local Ollama server is online.")
                            return True
                except Exception:
                    continue
        except Exception as e:
            logging.warning(f"Could not auto-start ollama serve: {e}")

        return False

    @classmethod
    def detect_local_models(cls, api_base: str = "http://localhost:11434") -> list:
        """
        Return a list of `(display_label, model_name)` for locally installed Ollama models,
        plus popular fallback recommendations.
        """
        found_names = []

        # 1. Try HTTP API (/api/tags)
        try:
            tags_url = f"{api_base.rstrip('/')}/api/tags"
            with urllib.request.urlopen(tags_url, timeout=1.2) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for m in data.get("models", []):
                    name = m.get("name")
                    if name and name not in found_names:
                        found_names.append(name)
        except Exception:
            pass

        # 2. Try `ollama list` CLI if HTTP returned nothing
        if not found_names:
            ollama_exe = cls._find_ollama_executable()
            if ollama_exe:
                try:
                    creationflags = 0x08000000 if sys.platform == "win32" else 0
                    proc = subprocess.run(
                        [ollama_exe, "list"],
                        capture_output=True,
                        text=True,
                        timeout=4,
                        creationflags=creationflags
                    )
                    if proc.returncode == 0 and proc.stdout:
                        lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
                        for ln in lines[1:]:  # Skip header row (NAME ID SIZE MODIFIED)
                            parts = ln.split()
                            if parts:
                                model_id = parts[0].strip()
                                if model_id and model_id not in found_names:
                                    found_names.append(model_id)
                except Exception as e:
                    logging.debug(f"ollama list check failed: {e}")

        options = []
        for name in found_names:
            options.append((f"🟢 {name} (Instalado no seu PC)", name))

        # Add common lightweight suggestions if not already installed
        presets = [
            ("gemma3:4b", "Google Gemma 3 (4B - Rápido e Inteligente)"),
            ("qwen2.5:7b", "Qwen 2.5 (7B - Alta Precisão em Texto)"),
            ("gemma3:1b", "Google Gemma 3 (1B - Ultra Leve ~800MB)"),
            ("llama3.2:latest", "Llama 3.2 (3B - Rápido e Leve)"),
            ("qwen2.5-coder:7b", "Qwen 2.5 Coder 7B"),
            ("llama3.1:8b", "Llama 3.1 (8B)"),
        ]
        for model_key, desc in presets:
            if model_key not in found_names:
                options.append((f"⬇️ {desc} ({model_key})", model_key))

        return options

    def refresh_detected_models(self):
        """Refresh the dropdown options with currently installed local Ollama models."""
        self.model_dropdown_setting.options = self.detect_local_models(
            getattr(self, "api_base", "http://localhost:11434") or "http://localhost:11434"
        )

    def get_response(self, system_instruction: str, prompt: str | list, return_response: bool = False) -> str:
        """
        Send a chat request to the local Ollama server.
        Automatically starts `ollama serve` if needed.
        """
        self.close_requested = False

        base_url = getattr(self, "api_base", "http://localhost:11434") or "http://localhost:11434"
        self.ensure_ollama_running(base_url)

        if isinstance(prompt, list):
            messages = prompt
        else:
            messages = [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt}
            ]

        try:
            if not self.client:
                self.client = OllamaClient(host=base_url)

            response = self.client.chat(
                model=self.api_model,
                messages=messages,
                options={"temperature": 0.25}
            )
            if isinstance(response, dict):
                response_text = response.get('message', {}).get('content', '').strip()
            else:
                response_text = getattr(getattr(response, 'message', None), 'content', '').strip()

            if not return_response and not getattr(self.app, 'current_response_window', None):
                self.app.output_ready_signal.emit(response_text)
            return response_text
        except Exception as e:
            logging.error(f"Error during Ollama chat: {e}")
            if not return_response:
                self.app.show_message_signal.emit(
                    "Erro no Ollama Local",
                    f"Não foi possível conectar ao modelo '{self.api_model}' no Ollama ({base_url}).\n\n"
                    f"Verifique se o Ollama está aberto e se o modelo está instalado (`ollama pull {self.api_model}`).\n\n"
                    f"Detalhes: {e}"
                )
            return ""

    def after_load(self):
        base_url = getattr(self, "api_base", "http://localhost:11434") or "http://localhost:11434"
        self.client = OllamaClient(host=base_url)

    def before_load(self):
        self.client = None

    def cancel(self):
        self.close_requested = True
