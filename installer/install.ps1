<#
.SYNOPSIS
    Bootstraps the YouTube Downloader application on Windows.
.DESCRIPTION
    The script installs Python if necessary, creates an isolated virtual
    environment and installs the application's dependencies. A helper script
    named run_app.ps1 is generated to launch the program afterwards.
#>

param(
    [switch]$ForcePythonInstall
)

$ErrorActionPreference = "Stop"

function Resolve-PythonCommand {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return @($python.Source)
    }

    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        return @($pyLauncher.Source, "-3")
    }

    return $null
}

function Install-PythonIfNeeded {
    param(
        [switch]$Force
    )

    $pythonCmd = Resolve-PythonCommand
    if ($pythonCmd -and -not $Force) {
        return $pythonCmd
    }

    Write-Host "Python was not found. Downloading the official installer..."
    $pythonUrl = "https://www.python.org/ftp/python/3.12.3/python-3.12.3-amd64.exe"
    $downloadPath = Join-Path $env:TEMP "python-installer.exe"

    Invoke-WebRequest -Uri $pythonUrl -OutFile $downloadPath
    Write-Host "Running the Python installer (this may take a while)..."
    $arguments = @("/quiet", "InstallAllUsers=1", "PrependPath=1")
    Start-Process -FilePath $downloadPath -ArgumentList $arguments -Wait

    Remove-Item $downloadPath -ErrorAction SilentlyContinue

    $pythonCmd = Resolve-PythonCommand
    if (-not $pythonCmd) {
        throw "Python installation failed. Please install Python 3.8+ manually and rerun this script."
    }

    return $pythonCmd
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$appDir = Join-Path $scriptDir "app"
$venvDir = Join-Path $scriptDir "venv"

$pythonCommand = Install-PythonIfNeeded -Force:$ForcePythonInstall

function Invoke-Python {
    param(
        [string[]]$Arguments
    )

    $command = $pythonCommand[0]
    $extraArgs = @()
    if ($pythonCommand.Length -gt 1) {
        $extraArgs = $pythonCommand[1..($pythonCommand.Length - 1)]
    }

    & $command @extraArgs @Arguments
}

Write-Host "Creating virtual environment..."
Invoke-Python -Arguments @("-m", "venv", $venvDir)

$venvPython = Join-Path $venvDir "Scripts/python.exe"

Write-Host "Upgrading pip..."
& $venvPython -m pip install --upgrade pip

Write-Host "Installing project dependencies..."
& $venvPython -m pip install -r (Join-Path $appDir "requirements.txt")

$launcherPath = Join-Path $scriptDir "run_app.ps1"
$launcherContent = @"
Write-Host "Starting YouTube Downloader..."
& '$venvPython' (Join-Path '$appDir' 'main.py') @args
"@
$launcherContent | Set-Content -Path $launcherPath -Encoding UTF8

Write-Host "Installation complete. Run 'run_app.ps1' to launch the application."
