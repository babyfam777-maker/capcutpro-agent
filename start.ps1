# Startar CapCutPro på Windows.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if ($args -contains "-Docker") {
  docker compose up --build
  exit $LASTEXITCODE
}

foreach ($cmd in @("ffmpeg", "ffprobe", "python", "node")) {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
    Write-Host "$cmd saknas. Installera Python 3.11+, Node 22, FFmpeg och Ollama, eller kör .\start.ps1 -Docker"
    exit 1
  }
}

if (-not (Test-Path "worker\.venv")) {
  Write-Host "Installerar worker-paket (första gången)..."
  python -m venv worker\.venv
  & worker\.venv\Scripts\python -m pip install -U pip
  & worker\.venv\Scripts\pip install -r worker\requirements.txt
}

New-Item -ItemType Directory -Force -Path worker\assets\fonts | Out-Null
if (-not (Test-Path worker\assets\fonts\Anton-Regular.ttf)) {
  Invoke-WebRequest -Uri "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf" -OutFile worker\assets\fonts\Anton-Regular.ttf
}
if (-not (Test-Path worker\assets\fonts\Montserrat-ExtraBold.ttf)) {
  Invoke-WebRequest -Uri "https://github.com/JulietaUla/Montserrat/raw/master/fonts/ttf/Montserrat-ExtraBold.ttf" -OutFile worker\assets\fonts\Montserrat-ExtraBold.ttf
}

if (-not $env:OPENAI_BASE_URL) { $env:OPENAI_BASE_URL = "http://127.0.0.1:11434/v1" }
if (-not $env:OPENAI_API_KEY) { $env:OPENAI_API_KEY = "ollama" }
if (-not $env:MODEL) { $env:MODEL = "qwen2.5:3b" }
if (-not $env:WORKER_URL) { $env:WORKER_URL = "http://127.0.0.1:8787" }
if (-not $env:WHISPER_MODEL) { $env:WHISPER_MODEL = "base" }

if ($env:OPENAI_BASE_URL -match "127.0.0.1:11434" -or $env:OPENAI_BASE_URL -match "localhost:11434") {
  try { Invoke-RestMethod http://127.0.0.1:11434/api/tags | Out-Null } catch {
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
      Write-Host "Ollama är inte installerat. Hämta det på https://ollama.com"
      exit 1
    }
    Start-Process ollama -ArgumentList "serve" -WindowStyle Hidden
    Start-Sleep -Seconds 2
  }
  $tags = ""
  try { $tags = (Invoke-RestMethod http://127.0.0.1:11434/api/tags | ConvertTo-Json) } catch { $tags = "" }
  if ($tags -notmatch ($env:MODEL.Split(":")[0])) {
    Write-Host "Laddar ner modellen $($env:MODEL)..."
    ollama pull $env:MODEL
  }
}

if (-not (Test-Path node_modules)) {
  corepack enable
  corepack prepare pnpm@10.18.0 --activate
  pnpm install --frozen-lockfile
}

Write-Host "Startar workern..."
$worker = Start-Process -FilePath "worker\.venv\Scripts\uvicorn.exe" -ArgumentList "app.main:app","--app-dir","worker","--host","127.0.0.1","--port","8787" -PassThru -WindowStyle Hidden
Write-Host "Startar webbsidan. http://127.0.0.1:3000 skrivs ut först när den svarar."
Write-Host "Workern har process-id $($worker.Id)."
node scripts/dev-server.mjs
