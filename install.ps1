# Windows launcher for the cross-platform Hermes Voicebox installer.
# Bootstraps Python if missing, then runs install.py (prereqs + plugin).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 -Yes
#   .\install.ps1 -Yes
#   .\install.ps1 -SkipPrereqs
#   .\install.ps1 -SkipPrereqs -AllProfiles
#   .\install.ps1 -SkipPrereqs -Profile work
#
# Encoding: ASCII / UTF-8 with BOM for Windows PowerShell 5.1 compatibility.
[CmdletBinding()]
param(
    [string]$HermesDir = $env:HERMES_DIR,
    [string]$BaseUrl = $(if ($env:VOICEBOX_BASE_URL) { $env:VOICEBOX_BASE_URL } else { "http://127.0.0.1:17493" }),
    [string]$ModelProfile = "plugin",
    [string]$Profile = "",
    [switch]$AllProfiles,
    [switch]$NoConfig,
    [switch]$ForceConfig,
    [switch]$PrintSnippet,
    [switch]$Yes,
    [switch]$SkipPrereqs,
    [switch]$SkipHermes,
    [switch]$SkipVoicebox,
    [switch]$SkipModels,
    [switch]$PreferDocker,
    [switch]$PreferDesktop
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

function Test-Python {
    param([string]$File, [string[]]$PrefixArgs)
    try {
        $allArgs = @()
        if ($PrefixArgs) { $allArgs += $PrefixArgs }
        $allArgs += @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)")
        & $File @allArgs | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Find-Python {
    $commands = @(
        @{ File = "py"; Args = @("-3") },
        @{ File = "python"; Args = @() },
        @{ File = "python3"; Args = @() }
    )
    foreach ($cand in $commands) {
        $cmd = Get-Command $cand.File -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        if (Test-Python -File $cand.File -PrefixArgs $cand.Args) {
            return @{ File = $cand.File; Args = $cand.Args }
        }
    }
    return $null
}

function Install-PythonBootstrap {
    Write-Host "Python 3.10+ not found - attempting install via winget..." -ForegroundColor Yellow
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        Write-Error "winget not found. Install Python from https://www.python.org/downloads/ (check Add python.exe to PATH), then re-run."
        exit 1
    }
    & winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    # Refresh PATH in this session from machine + user env
    $machinePath = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machinePath;$userPath"
}

$py = Find-Python
if (-not $py) {
    Install-PythonBootstrap
    $py = Find-Python
}
if (-not $py) {
    Write-Error "Python 3.10+ is still not on PATH. Close this window, open a new PowerShell, and re-run install.ps1."
    exit 1
}

# Join-Path so this launcher works on Windows PowerShell and pwsh-on-Linux CI.
$InstallPy = Join-Path $ScriptDir "install.py"
$installArgs = @($InstallPy, "--base-url", $BaseUrl, "--model-profile", $ModelProfile)
if ($HermesDir) { $installArgs += @("--hermes-dir", $HermesDir) }
if ($NoConfig) { $installArgs += "--no-config" }
if ($ForceConfig) { $installArgs += "--force-config" }
if ($PrintSnippet) { $installArgs += "--print-snippet" }
if ($Yes) { $installArgs += "--yes" }
if ($SkipPrereqs) { $installArgs += "--skip-prereqs" }
if ($SkipHermes) { $installArgs += "--skip-hermes" }
if ($SkipVoicebox) { $installArgs += "--skip-voicebox" }
if ($SkipModels) { $installArgs += "--skip-models" }
if ($PreferDocker) { $installArgs += "--prefer-docker" }
if ($PreferDesktop) { $installArgs += "--prefer-desktop" }
if ($AllProfiles) { $installArgs += "--all-profiles" }
if ($Profile) { $installArgs += @("--profile", $Profile) }

Write-Host "Using: $($py.File) $($py.Args -join ' ')" -ForegroundColor Cyan
& $py.File @($py.Args + $installArgs)
exit $LASTEXITCODE
