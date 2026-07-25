# Simulates a typical new Windows Hermes user installing this repo from scratch.
# Runs under Windows PowerShell or PowerShell 7 (pwsh) on Linux CI.
#
# Typical user command this approximates:
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -Yes
#
# In CI / cloud agents we skip live Hermes/Voicebox/model downloads and validate
# the Windows launcher + plugin/bridge/config landing in a fresh HERMES_DIR.
#
# Usage:
#   pwsh -NoProfile -File .\test_windows_scratch.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\test_windows_scratch.ps1

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot

function Assert-True($cond, $msg) {
    if (-not $cond) { throw "ASSERT FAIL: $msg" }
    Write-Host "  OK  $msg" -ForegroundColor Green
}

Write-Host "=== Windows scratch install simulation ===" -ForegroundColor Cyan
Write-Host "Repo: $RepoRoot"
Write-Host "Host: $([System.Environment]::OSVersion.VersionString)"
Write-Host "PS:   $($PSVersionTable.PSVersion)"

# Fresh user home: empty Hermes directory (as if first-time install)
$ScratchRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("hermes-voicebox-scratch-" + [guid]::NewGuid().ToString("N"))
$HermesDir = Join-Path $ScratchRoot ".hermes"
New-Item -ItemType Directory -Force -Path $ScratchRoot | Out-Null
Assert-True (-not (Test-Path $HermesDir)) "Hermes dir does not exist yet (from-scratch)"

Write-Host ""
Write-Host "Step 1: Print config snippet via Windows launcher" -ForegroundColor Cyan
& "$RepoRoot\install.ps1" -HermesDir $HermesDir -PrintSnippet
Assert-True ($LASTEXITCODE -eq 0) "install.ps1 -PrintSnippet exit 0"

Write-Host ""
Write-Host "Step 2: From-scratch plugin install (skip live upstream downloads)" -ForegroundColor Cyan
Write-Host "        (Approximates a user who will start Hermes/Voicebox separately,"
Write-Host "         or already has them — validates the Windows entrypoint path.)"
& "$RepoRoot\install.ps1" -HermesDir $HermesDir -Yes -SkipPrereqs
Assert-True ($LASTEXITCODE -eq 0) "install.ps1 -Yes -SkipPrereqs exit 0"

$plugin = Join-Path $HermesDir (Join-Path "desktop-plugins" (Join-Path "voice-switcher" "plugin.js"))
$bridge = Join-Path $HermesDir (Join-Path "scripts" "voicebox_tts.py")
$snippet = Join-Path $HermesDir "voicebox-provider.snippet.yaml"
$config = Join-Path $HermesDir "config.yaml"

Assert-True (Test-Path $plugin) "plugin.js installed"
Assert-True (Test-Path $bridge) "voicebox_tts.py installed"
Assert-True (Test-Path $snippet) "snippet written"
Assert-True (Test-Path $config) "config.yaml created"

$cfgText = Get-Content -Raw $config
Assert-True ($cfgText -match "provider:\s*voicebox") "config contains voicebox provider"
Assert-True ($cfgText -match "type:\s*command") "config contains type: command"
Assert-True ($cfgText -match "BEGIN hermes-voicebox") "config contains merge markers"
$bridgeLeaf = [System.IO.Path]::GetFileName($bridge)
Assert-True ($cfgText -match [regex]::Escape($bridgeLeaf)) "config references voicebox_tts.py"

Write-Host ""
Write-Host "Step 3: Re-run installer (idempotent upgrade path)" -ForegroundColor Cyan
& "$RepoRoot\install.ps1" -HermesDir $HermesDir -Yes -SkipPrereqs
Assert-True ($LASTEXITCODE -eq 0) "reinstall exit 0"
$cfgText2 = Get-Content -Raw $config
$beginCount = ([regex]::Matches($cfgText2, "BEGIN hermes-voicebox")).Count
Assert-True ($beginCount -eq 1) "marked block not duplicated on reinstall ($beginCount)"

Write-Host ""
Write-Host "Step 4: Simulated 'full -Yes' argument wiring (dry parse only)" -ForegroundColor Cyan
# Ensure the typical user flags are accepted by the launcher without executing heavy provision.
& "$RepoRoot\install.ps1" -HermesDir $HermesDir -Yes -SkipHermes -SkipVoicebox -SkipModels -PreferDesktop -PrintSnippet
Assert-True ($LASTEXITCODE -eq 0) "typical full-flag set accepted"

Write-Host ""
Write-Host "=== Scratch simulation PASSED ===" -ForegroundColor Green
Write-Host "Artifacts under: $ScratchRoot"
Write-Host ""
Write-Host "On a real Windows PC, a brand-new Hermes user would typically run:"
Write-Host '  powershell -ExecutionPolicy Bypass -File .\install.ps1 -Yes'
Write-Host "That also provisions Hermes/Voicebox/models (multi-GB). Then restart Hermes Desktop."

# Keep artifacts for inspection unless CLEANUP=1
if ($env:CLEANUP -eq "1") {
    Remove-Item -Recurse -Force $ScratchRoot
    Write-Host "Cleaned $ScratchRoot"
} else {
    Write-Host "Set CLEANUP=1 to auto-delete scratch dir."
}
