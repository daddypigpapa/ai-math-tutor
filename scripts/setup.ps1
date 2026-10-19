# 최초 1회: 가상환경 만들고 패키지 설치, DB 초기화, 시작 문제 넣기.  (Windows PowerShell 5.1, 프로젝트 폴더에서 실행)
#   .\scripts\setup.ps1 -Learner "아이이름"
param([string]$Learner = "")

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path ".venv")) { python -m venv .venv }
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host ".env 를 만들었다. API 키·PARENT_PIN·디스코드 웹후크를 채워라." }

# KaTeX 로컬 복사본 (인터넷 없이 수식 표시). web\katex 가 비어 있을 때만 받는다.
if (-not (Test-Path "web\katex\katex.min.js")) {
    $tmp = Join-Path $env:TEMP "katex-pack"
    New-Item -ItemType Directory -Force $tmp | Out-Null
    Push-Location $tmp
    npm pack katex@0.16.11 | Out-Null
    tar -xf (Get-ChildItem "katex-*.tgz" | Select-Object -First 1).Name
    Pop-Location
    New-Item -ItemType Directory -Force "web\katex" | Out-Null
    Copy-Item "$tmp\package\dist\katex.min.js", "$tmp\package\dist\katex.min.css" "web\katex\"
    Copy-Item "$tmp\package\dist\fonts" "web\katex\fonts" -Recurse -Force
}

$args2 = @("-m", "pipeline.seed")
if ($Learner) { $args2 += @("--learner", $Learner) }
.\.venv\Scripts\python.exe @args2
Write-Host "완료. 서버 실행: .\scripts\run_server.ps1"
