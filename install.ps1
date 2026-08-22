# Windows launcher for the Hermes Voicebox integration installer.
#
# This is a thin wrapper around install.py, which performs the FULL install:
#   1. Prerequisite check & provisioning (Python deps, Hermes, Voicebox,
#      TTS models, demo voices)
#   2. Desktop plugin + TTS bridge + Fish provider scripts -> $env:USERPROFILE\.hermes
#   3. Marked TTS block merged into every profile's config.yaml
#   4. Speak-stream hook patches into hermes-agent (chunked read-aloud)
#   5. GPU lifecycle daemon (Scheduled Task) + backend server on :17493
#
# All command-line flags are passed through to install.py:
#   .\install.ps1                          # interactive, provisions everything
#   .\install.ps1 -y                       # non-interactive
#   .\install.ps1 -OneClick                # best-effort, tolerates partial failures
#   .\install.ps1 -SkipPrereqs             # plugin/bridge/config files only
#   .\install.ps1 -AllProfiles -y          # install into every Hermes profile
#
# See: py -3 install.py --help

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "=== Installing Hermes Voicebox Integration & Backend ===" -ForegroundColor Cyan

# Find a Python 3 launcher (py is the standard Windows Python launcher).
$py = $null
foreach ($candidate in @("py", "python")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        if ($candidate -eq "py") {
            # Verify it resolves to Python 3
            & py -3 --version *> $null
            if ($LASTEXITCODE -eq 0) { $py = "py -3"; break }
        } else {
            $py = "python"; break
        }
    }
}
if (-not $py) {
    Write-Host "ERROR: Python 3 not found. Install Python 3.10+ from https://python.org first." -ForegroundColor Red
    exit 1
}

# Map common bash-style flags to argparse equivalents so either style works.
$argList = New-Object System.Collections.Generic.List[string]
foreach ($a in $args) {
    switch -Regex ($a) {
        '^--one-click$'   { $argList.Add('--one-click'); continue }
        '^--skip-prereqs$'{ $argList.Add('--skip-prereqs'); continue }
        '^--all-profiles$'{ $argList.Add('--all-profiles'); continue }
        default           { $argList.Add($a) }
    }
}

Invoke-Expression "$py install.py $($argList -join ' ')"
