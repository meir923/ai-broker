# Run by double-click START.vbs
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location -LiteralPath $Root

function Find-Python {
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @{ Kind = "python" }
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        return @{ Kind = "py3" }
    }
    return $null
}

$p = Find-Python
if (-not $p) {
    Write-Host "Python not found. Install from https://www.python.org/downloads/ (enable Add to PATH)." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

Write-Host "Installing AI Broker (web)..." -ForegroundColor Cyan
if ($p.Kind -eq "python") {
    python -m pip install -e ".[web]" --quiet
} else {
    py -3 -m pip install -e ".[web]" --quiet
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip install failed." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

if ($p.Kind -eq "python") {
    $runLine = "python -m aibroker.cli web --auto-port"
} else {
    $runLine = "py -3 -m aibroker.cli web --auto-port"
}

# Server stays in this CMD window; browser opens from Python after bind (correct port)
$batch = "@echo off`r`ntitle AI Broker Server`r`ncd /d `"$Root`"`r`necho Free port is printed below. Close with Ctrl+C`r`necho.`r`n$runLine`r`npause`r`n"
$batPath = Join-Path $env:TEMP "aibroker_run_web.bat"
$utf8bom = New-Object System.Text.UTF8Encoding $true
[System.IO.File]::WriteAllText($batPath, $batch, $utf8bom)

Start-Process cmd.exe -ArgumentList @("/k", "`"$batPath`"")

Write-Host ""
Write-Host "A browser tab opens from the server (correct port). Keep the CMD window open." -ForegroundColor Green
Read-Host "Press Enter to close this helper window"
