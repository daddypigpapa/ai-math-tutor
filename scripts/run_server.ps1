# 서버 실행 (명세 11절): uvicorn app.main:app --host 0.0.0.0 --port 8000
Set-Location (Split-Path $PSScriptRoot -Parent)
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
