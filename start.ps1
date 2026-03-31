# InfraForge — Start
# Creates venv + installs deps if needed, then launches the server.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvDir = Join-Path $root ".venv"
$python = Join-Path $venvDir "Scripts\python.exe"
$pip = Join-Path $venvDir "Scripts\pip.exe"
$reqs = Join-Path $root "requirements.txt"

# ── Create venv if missing ──────────────────────────────
if (-not (Test-Path $python)) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    python -m venv $venvDir
    if ($LASTEXITCODE -ne 0) { throw "Failed to create venv. Is Python on PATH?" }
    Write-Host "  Done." -ForegroundColor Green
}

# ── Install / update dependencies ───────────────────────
# Compare requirements.txt timestamp against a marker file to skip when unchanged.
$marker = Join-Path $venvDir ".deps-installed"
$needsInstall = -not (Test-Path $marker) -or
    (Get-Item $reqs).LastWriteTime -gt (Get-Item $marker).LastWriteTime

if ($needsInstall) {
    Write-Host "Installing dependencies..." -ForegroundColor Cyan
    & $pip install -r $reqs --quiet
    if ($LASTEXITCODE -ne 0) { throw "pip install failed." }
    New-Item -Path $marker -ItemType File -Force | Out-Null
    Write-Host "  Done." -ForegroundColor Green
} else {
    Write-Host "Dependencies up to date." -ForegroundColor DarkGray
}

# ── Load .env if present ────────────────────────────────
$envFile = Join-Path $root ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([^#][^=]+?)\s*=\s*(.+)$') {
            [System.Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], "Process")
        }
    }
}

# ── Start the server ────────────────────────────────────
$env:PYTHONIOENCODING = "utf-8"
Write-Host "`nStarting InfraForge..." -ForegroundColor Cyan
& $python (Join-Path $root "web_start.py")
