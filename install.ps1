# Windows launcher for the cross-platform Hermes Voicebox installer.
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\install.ps1
#   .\install.ps1
#   .\install.ps1 -NoConfig
[CmdletBinding()]
param(
    [string]$HermesDir = $env:HERMES_DIR,
    [switch]$NoConfig,
    [switch]$PrintSnippet
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

function Find-Python {
    $commands = @(
        @{ File = "py"; Args = @("-3") },
        @{ File = "python"; Args = @() },
        @{ File = "python3"; Args = @() }
    )
    foreach ($cand in $commands) {
        $cmd = Get-Command $cand.File -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        try {
            $allArgs = $cand.Args + @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)")
            & $cand.File @allArgs | Out-Null
            if ($LASTEXITCODE -eq 0) {
                return @{ File = $cand.File; Args = $cand.Args }
            }
        } catch {
            continue
        }
    }
    return $null
}

$py = Find-Python
if (-not $py) {
    Write-Error "Python 3.10+ is required but was not found on PATH. Install Python from https://www.python.org/downloads/ and ensure 'Add python.exe to PATH' is checked."
    exit 1
}

$installArgs = @("$ScriptDir\install.py")
if ($HermesDir) {
    $installArgs += @("--hermes-dir", $HermesDir)
}
if ($NoConfig) {
    $installArgs += "--no-config"
}
if ($PrintSnippet) {
    $installArgs += "--print-snippet"
}

Write-Host "Using: $($py.File) $($py.Args -join ' ')" -ForegroundColor Cyan
& $py.File @($py.Args + $installArgs)
exit $LASTEXITCODE
