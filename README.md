# ✨ Apple Intelligence Writing Tools & Voice Dictation (Windows & Linux)

Uma suíte minimalista no estilo **macOS Sequoia / Apple Intelligence** para **Windows e Linux**, combinando:
- 🎙️ **Ditado por Voz Instantâneo (Zero-Click)** com **Faster-Whisper Local** (GPU NVIDIA CUDA `float16` ou CPU `int8`) + **Orbe 3D Reativa ao Tom da Voz (FFT Pitch-Reactive)**.
- ✍️ **Correção Inteligente de Digitação em PT-BR (`Corrigir PT-BR`)** ultra-rápida (~0,4s) via **Groq Cloud (Gratuito)**, **Ollama (100% Local)** ou **Google Gemini**.
- 🪟 **Interface Glassmorphism Chromium/Electron** flutuante e leve, iniciando automaticamente junto com o Windows na bandeja do sistema.

---

## 🚀 Instalação em 1 Clique (ou via Vibe Coding)

### Opção A: Usando Vibe Coding (Antigravity, Cursor, Windsurf, Claude Code)
Basta mandar para o seu agente de IA:
> *"Clone o repositório `https://github.com/NicolasCoder/Apple-Intelligence-Windows.git`, execute `python install.py` para instalar as dependências, configurar o Faster-Whisper (GPU/CPU) e ativar a inicialização automática com o Windows."*

### Opção B: Instalação Manual em 1 Clique (Windows)
1. Clone o repositório:
   ```bash
   git clone https://github.com/NicolasCoder/Apple-Intelligence-Windows.git
   cd Apple-Intelligence-Windows
   ```
2. Dê dois cliques em **`install.bat`** (ou rode `python install.py` no terminal).
   - O instalador detecta automaticamente se você tem placa de vídeo **NVIDIA (CUDA)** ou **CPU**, instala tudo, cria o lançador invisível (`start_silent.vbs`), **registra o app para sempre iniciar junto com o Windows** e já abre o ícone na bandeja do sistema!

---

## ⌨️ Como Usar no Dia a Dia

1. **🎙️ Modo Áudio (Sem nenhum texto selecionado)**:
   - Aperte **`Ctrl + Space`** em qualquer lugar do Windows -> a cápsula Apple Intelligence abre **já gravando sua voz** com a Orbe 3D reagindo às notas graves e agudas da sua fala.
   - Aperte **`Espaço`** (ou `Ctrl + Space` novamente) -> para a gravação, transcreve no **Faster-Whisper**, revisa pontuação/gramática na IA e **cola (`Ctrl + V`) automaticamente** onde seu cursor estava!
2. **✍️ Modo Texto (Com texto selecionado)**:
   - Selecione qualquer texto e aperte **`Ctrl + Space`** para abrir o menu Apple Intelligence, **OU** aperte **`Ctrl + Shift + Space`** para acionar o **`Corrigir PT-BR`** direto (sem nem abrir janela) e substituir o texto selecionado em **~0,4s**!

---

## ⚙️ Configurando sua IA (Groq Gratuita, Ollama Local ou Gemini)

Clique no ícone de engrenagem (**`⚙️`**) no topo da janela flutuante (ou clique com o botão direito no ícone da bandeja -> **Settings**):
- **⚡ Groq Cloud (Recomendado — Ultra Rápido ~0,4s e Gratuito)**:
  - Crie uma chave gratuita em [console.groq.com/keys](https://console.groq.com/keys).
  - Em **Configurações**, escolha **`🌐 OpenAI / Groq / OpenRouter / LM Studio`**, cole sua chave `gsk_...` e selecione **`⚡ Groq: GPT-OSS 120B`** (conta com fallback automático entre `gpt-oss-120b`, `qwen3.8-27b` e `gpt-oss-20b`, permitindo até **3.000 correções grátis/dia**!).
- **🏠 Ollama (100% Local e Offline)**:
  - Se tiver o [Ollama](https://ollama.com) instalado, seus modelos locais aparecem automaticamente na lista!
- **✨ Google Gemini**:
  - Suporta chaves gratuitas do [Google AI Studio](https://aistudio.google.com/app/apikey).
