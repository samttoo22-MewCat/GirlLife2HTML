$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

function Test-BuilderPython([string]$Path, [string[]]$Prefix = @()) {
    try {
        & $Path @Prefix -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>$null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Find-BuilderPython {
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher -and (Test-BuilderPython $launcher.Source @('-3'))) {
        return [pscustomobject]@{ Path = $launcher.Source; Prefix = @('-3') }
    }

    foreach ($name in @('python.exe', 'python3.exe')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command -and (Test-BuilderPython $command.Source)) {
            return [pscustomobject]@{ Path = $command.Source; Prefix = @() }
        }
    }
    return $null
}

$python = Find-BuilderPython
if (-not $python) {
    Write-Host 'Python 3.10 or newer is required. No compatible Python was found.'
    $answer = Read-Host 'Download and install the official Python Windows installer from python.org for this user? [y/N]'
    if ($answer -notmatch '^(?i:y|yes)$') {
        Write-Host 'Cancelled. Python was not downloaded or installed.'
        exit 1
    }

    Write-Host 'Finding the latest stable 64-bit installer on python.org...'
    $page = Invoke-WebRequest -Uri 'https://www.python.org/downloads/windows/' -UseBasicParsing
    $stable = ($page.Content -split '(?is)<h2[^>]*>\s*Pre-releases')[0]
    $match = [regex]::Match(
        $stable,
        'https://www\.python\.org/ftp/python/(?<version>\d+\.\d+\.\d+)/python-\k<version>-amd64\.exe'
    )
    if (-not $match.Success) {
        throw 'Could not find a stable 64-bit installer on the official Python downloads page.'
    }

    $version = $match.Groups['version'].Value
    $installerUrl = $match.Value
    $installer = Join-Path $env:TEMP "python-$version-amd64.exe"
    Write-Host "Downloading $installerUrl"
    Invoke-WebRequest -Uri $installerUrl -OutFile $installer

    $signature = Get-AuthenticodeSignature -FilePath $installer
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
        throw 'The installer signature is invalid or is not from the Python Software Foundation; refusing to run it.'
    }

    Write-Host 'The installer is signed by the Python Software Foundation. Installing for the current Windows user...'
    $install = Start-Process -FilePath $installer -ArgumentList @('/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_test=0') -Wait -PassThru
    Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    if ($install.ExitCode -ne 0) {
        throw "Python installer exited with code $($install.ExitCode)."
    }

    $python = Find-BuilderPython
    if (-not $python) {
        $pythonDir = Join-Path $env:LOCALAPPDATA ("Programs\Python\Python" + ($version -replace '\.', ''))
        $pythonExe = Join-Path $pythonDir 'python.exe'
        if (Test-BuilderPython $pythonExe) {
            $python = [pscustomobject]@{ Path = $pythonExe; Prefix = @() }
        }
    }
    if (-not $python) {
        throw 'Python was installed, but this session could not find it. Close and reopen the terminal, then run the builder again.'
    }
}

Write-Host "Using Python: $($python.Path)"
$builderArguments = @($python.Prefix) + @('build_girllife_html.py')
& $python.Path @builderArguments
exit $LASTEXITCODE
