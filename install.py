"""
Apple Intelligence Writing Tools (Windows & Linux) — 1-Click Installer
----------------------------------------------------------------------
- Installs all required Python dependencies (PySide6, faster-whisper, sounddevice, openai, etc.)
- Auto-detects NVIDIA GPU (CUDA) vs CPU and installs CUDA runtime libraries if an NVIDIA GPU is present
- Initializes `Windows_and_Linux/config.json` from `config.example.json` (preserving any existing config)
- Registers Writing Tools to start automatically with Windows on boot (silent background tray daemon)
- Launches Writing Tools in the background immediately after installation
"""

import json
import os
import shutil
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(REPO_ROOT, "Windows_and_Linux")
REQ_FILE = os.path.join(APP_DIR, "requirements.txt")
CONFIG_FILE = os.path.join(APP_DIR, "config.json")
CONFIG_EXAMPLE = os.path.join(APP_DIR, "config.example.json")


def has_nvidia_gpu() -> bool:
    """Return True if an NVIDIA GPU is detected via nvidia-smi."""
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi and sys.platform.startswith("win"):
        candidate = r"C:\Windows\System32\nvidia-smi.exe"
        if os.path.exists(candidate):
            nvidia_smi = candidate
    if not nvidia_smi:
        return False
    try:
        res = subprocess.run([nvidia_smi, "-L"], capture_output=True, text=True, timeout=5)
        return res.returncode == 0 and "GPU" in (res.stdout or "")
    except Exception:
        return False


def install_dependencies(gpu_detected: bool):
    print("\n[1/4] Instalando dependencias Python do Writing Tools...")
    subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "pip"], check=False)
    subprocess.run([sys.executable, "-m", "pip", "install", "-r", REQ_FILE], check=True)

    if gpu_detected:
        print("\n[2/4] Placa NVIDIA detectada! Instalando bibliotecas CUDA (cublas/cudnn) para Faster-Whisper...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "nvidia-cublas-cu12", "nvidia-cudnn-cu12"],
            check=False
        )
    else:
        print("\n[2/4] Modo CPU detectado (sem GPU NVIDIA dedicada). Configurando Faster-Whisper leve (CPU int8)...")


def setup_config(gpu_detected: bool):
    print("\n[3/4] Verificando arquivo de configuracao (config.json)...")
    if not os.path.exists(CONFIG_FILE):
        if os.path.exists(CONFIG_EXAMPLE):
            shutil.copyfile(CONFIG_EXAMPLE, CONFIG_FILE)
        else:
            default_cfg = {
                "shortcut": "ctrl+space",
                "theme": "gradient",
                "streaming": False,
                "provider": "Ollama (For Experts)",
                "whisper_model": "nemotron-3.5-streaming" if gpu_detected else "base",
                "whisper_device": "auto" if gpu_detected else "cpu_int8",
                "live_streaming_preview": True,
                "audio_llm_refinement": True,
                "providers": {
                    "Gemini (Recommended)": {"api_key": "", "model_name": "gemini-flash-latest"},
                    "OpenAI Compatible (For Experts)": {
                        "api_key": "",
                        "api_base": "https://api.groq.com/openai/v1",
                        "api_organisation": "",
                        "api_project": "",
                        "api_model": "openai/gpt-oss-120b"
                    },
                    "Ollama (For Experts)": {
                        "api_base": "http://localhost:11434",
                        "api_model": "qwen2.5:7b",
                        "keep_alive": "15"
                    }
                },
                "is_config_file_updated_for_v8": True,
                "is_config_file_updated_for_v9": True
            }
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(default_cfg, f, indent=4, ensure_ascii=False)

        if not gpu_detected:
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                cfg["whisper_model"] = "base"
                cfg["whisper_device"] = "cpu_int8"
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=4, ensure_ascii=False)
            except Exception:
                pass
        print("  -> config.json criado com sucesso!")
    else:
        print("  -> config.json existente preservado!")


def setup_autostart_and_launch(launch_now: bool = True):
    print("\n[4/4] Configurando inicializacao automatica com o sistema (Autostart)...")
    sys.path.insert(0, APP_DIR)
    from ui.AutostartManager import AutostartManager

    ok = AutostartManager.set_autostart(True)
    if ok:
        print("  -> Autostart ativado! O Writing Tools iniciara automaticamente junto com o Windows.")
    else:
        print("  -> Aviso: Nao foi possivel registrar o Autostart automaticamente.")

    if launch_now:
        print("\nIniciando o Writing Tools em segundo plano...")
        if sys.platform.startswith("win"):
            vbs_path = AutostartManager.ensure_silent_launcher()
            subprocess.Popen(["wscript.exe", "//B", "//Nologo", vbs_path], cwd=APP_DIR)
        else:
            subprocess.Popen([sys.executable, os.path.join(APP_DIR, "main.py")], cwd=APP_DIR)
        print("\n==================================================================")
        print(" INSTALACAO CONCLUIDA COM SUCESSO!")
        print(" - Atalho Global (Audio / Texto): Ctrl + Space")
        print(" - Atalho Direto Corrigir PT-BR : Ctrl + Shift + Space")
        print(" - O icone ja esta ativo na bandeja do sistema ( perto do relogio ).")
        print("==================================================================\n")


def main():
    launch_now = "--no-launch" not in sys.argv
    gpu = has_nvidia_gpu()
    install_dependencies(gpu)
    setup_config(gpu)
    setup_autostart_and_launch(launch_now=launch_now)


if __name__ == "__main__":
    main()

