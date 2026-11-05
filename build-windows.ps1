[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
if ($env:OS -ne "Windows_NT") {
    throw "Run this build script on Windows to create the Windows executable."
}

$ProjectRoot = (Resolve-Path $PSScriptRoot).Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    $PythonLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($PythonLauncher) {
        & py -3 -m venv (Join-Path $ProjectRoot ".venv")
    } else {
        $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if (-not $PythonCommand) {
            throw "Install Python 3 and rerun this build script."
        }
        & python -m venv (Join-Path $ProjectRoot ".venv")
    }
    if ($LASTEXITCODE -ne 0) { throw "Could not create the Python build environment." }
}

& $Python -m pip install -r (Join-Path $ProjectRoot "requirements-build.txt")
if ($LASTEXITCODE -ne 0) { throw "Could not install the app build dependencies." }

$ReleaseRoot = Join-Path $ProjectRoot "release"
$AppFolder = Join-Path $ReleaseRoot "FacebookMediaDownloader"
if (Test-Path -LiteralPath $AppFolder) {
    $ResolvedAppFolder = (Resolve-Path -LiteralPath $AppFolder).Path
    $ExpectedAppFolder = [System.IO.Path]::GetFullPath($AppFolder)
    if ($ResolvedAppFolder -ne $ExpectedAppFolder -or
        [System.IO.Path]::GetDirectoryName($ResolvedAppFolder) -ne [System.IO.Path]::GetFullPath($ReleaseRoot)) {
        throw "Refusing to replace an unexpected release folder: $ResolvedAppFolder"
    }
    Remove-Item -LiteralPath $ResolvedAppFolder -Recurse -Force
}
New-Item -ItemType Directory -Path $AppFolder -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $ProjectRoot "build\spec") -Force | Out-Null

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --name FacebookMediaDownloader `
    --collect-all playwright `
    --collect-all yt_dlp `
    --distpath $ReleaseRoot `
    --workpath (Join-Path $ProjectRoot "build\pyinstaller") `
    --specpath (Join-Path $ProjectRoot "build\spec") `
    (Join-Path $ProjectRoot "app.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller could not build the Windows app." }

$PreviousBrowsersPath = $env:PLAYWRIGHT_BROWSERS_PATH
try {
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $AppFolder "ms-playwright"
    & $Python -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw "Could not download the Playwright Chromium browser." }
} finally {
    if ($null -eq $PreviousBrowsersPath) {
        Remove-Item Env:PLAYWRIGHT_BROWSERS_PATH -ErrorAction SilentlyContinue
    } else {
        $env:PLAYWRIGHT_BROWSERS_PATH = $PreviousBrowsersPath
    }
}

Write-Host "Windows app created: $AppFolder\FacebookMediaDownloader.exe"
