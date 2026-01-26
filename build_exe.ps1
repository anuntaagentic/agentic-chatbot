$ErrorActionPreference = "Stop"

python -m pip install --upgrade pip
python -m pip install pyinstaller
python -m pip install -r requirements.txt

pyinstaller `
  --noconsole `
  --onefile `
  --name AgenticWindowsHelper `
  --add-data "assets;assets" `
  --add-data "config;config" `
  --collect-all PySide6 `
  app/main.py
