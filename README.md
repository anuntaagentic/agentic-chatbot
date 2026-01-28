# Agentic Windows Helper (Concept MVP)

This is a concept Windows 11 chatbot app that runs diagnostics, proposes fixes, and only executes fixes after confirmation.

## Features
- Split view UI (chat + diagnostics/actions log).
- Multi-agent flow: diagnosis, fix planning, execution.
- Command allowlist/denylist gate.
- Logs stored in `%LOCALAPPDATA%\\AgenticChatbot\\logs\\YYYY-MM-DD.log`.
- Optional AutoGen + Groq integration for smarter summaries.

## Setup
1) Create/activate a Python environment.
2) Install dependencies:

```bash
pip install -r requirements.txt
```

3) Run:

```bash
python -m app.main
```

## Build executable (Windows)
Use PyInstaller to create a single EXE:

```powershell
.\build_exe.ps1
```

The output will be in `dist\AgenticWindowsHelper.exe`.

## Groq (optional)
Set environment variables before launching:

- `GROQ_API_KEY`: your Groq API key
- `GROQ_MODEL`: optional (default: `llama3-70b-8192`)
- `GROQ_BASE_URL`: optional (default: `https://api.groq.com/openai/v1`)

## Knowledge base + web hints
Generate vectors once:

```bash
python -m app.build_vectors
```

This writes `tech_support_dataset.vectors.pkl`. The app will load vectors from this file on startup.
Optional web hints use DuckDuckGo instant answers. Disable by setting:

```
setx ENABLE_WEB_SEARCH 0
```

## Safety gates
Allowlist and denylist live here:
- `config/allowlist.json`
- `config/denylist.json`

Adjust these to control which commands are permitted.
