[CmdletBinding()]
param(
    [string]$EnvironmentPath = (Join-Path $env:USERPROFILE "mineru-env"),

    [switch]$ForceReinstall,

    [switch]$DownloadModels,

    [ValidateSet("auto", "huggingface", "modelscope")]
    [string]$ModelSource = "auto"
)

$ErrorActionPreference = "Stop"
$utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8WithoutBom
$OutputEncoding = $utf8WithoutBom
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$PinnedUvVersion = "0.11.32"
$PinnedMinerUVersion = "3.4.5"
$UvDownloadBaseUrl = "https://github.com/astral-sh/uv/releases/download/$PinnedUvVersion"
$UvAssetHashes = @{
    "x86_64-pc-windows-msvc"  = "ACFDE570451CFDB8689FA159A138EE805BA4E241C466432750302C86254B0984"
    "aarch64-pc-windows-msvc" = "A7427EA0440BB826B6716D1837FF3D173B8E7D496CB09EE8F456B4E023A2FDCD"
}
$managedUvDirectory = Join-Path $env:LOCALAPPDATA "mineru-skill\uv"
$lockFilePath = Join-Path (Split-Path -Parent $PSScriptRoot) "requirements.lock"

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Normalize-ComparablePath([string]$Path) {
    return [IO.Path]::GetFullPath($Path).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
}

function Assert-SafeEnvironmentPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw "EnvironmentPath cannot be empty."
    }

    $fullPath = [IO.Path]::GetFullPath($Path)
    $comparisonPath = Normalize-ComparablePath $fullPath
    $forbidden = @(
        [IO.Path]::GetPathRoot($fullPath),
        $env:USERPROFILE,
        $env:SystemRoot,
        $env:ProgramFiles,
        ${env:ProgramFiles(x86)},
        $env:ProgramData
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }

    foreach ($forbiddenPath in $forbidden) {
        if (
            $comparisonPath.Equals(
                (Normalize-ComparablePath $forbiddenPath),
                [StringComparison]::OrdinalIgnoreCase
            )
        ) {
            throw "Refusing unsafe EnvironmentPath: $fullPath"
        }
    }

    if (Test-Path -LiteralPath $fullPath -PathType Leaf) {
        throw "EnvironmentPath points to a file, not a directory: $fullPath"
    }
    return $fullPath
}

function Test-CompatiblePython([string]$Command, [string[]]$PrefixArguments) {
    try {
        $code = "import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] < (3, 13) else 1)"
        & $Command @PrefixArguments -c $code 2>$null
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
}

function Find-CompatiblePython {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        foreach ($version in @("-3.11", "-3.12", "-3.10")) {
            if (Test-CompatiblePython $py.Source @($version)) {
                return @{ Command = $py.Source; Arguments = @($version) }
            }
        }
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python -and (Test-CompatiblePython $python.Source @())) {
        return @{ Command = $python.Source; Arguments = @() }
    }
    return $null
}

function Get-UvAssetKey {
    $arch = $env:PROCESSOR_ARCHITEW6432
    if (-not $arch) {
        $arch = $env:PROCESSOR_ARCHITECTURE
    }
    switch ($arch) {
        "AMD64" { return "x86_64-pc-windows-msvc" }
        "ARM64" { return "aarch64-pc-windows-msvc" }
        default { throw "Unsupported processor architecture for the pinned uv asset: $arch" }
    }
}

function Test-PinnedUvVersion([string]$UvExe) {
    try {
        $output = & $UvExe --version 2>$null
        if ($LASTEXITCODE -ne 0) {
            return $false
        }
        $line = (($output | Select-Object -Last 1).ToString())
        return $line.StartsWith("uv $PinnedUvVersion", [StringComparison]::Ordinal)
    }
    catch {
        return $false
    }
}

function Find-Uv {
    $managedUv = Join-Path $managedUvDirectory "uv.exe"
    if (Test-Path -LiteralPath $managedUv -PathType Leaf) {
        if (Test-PinnedUvVersion $managedUv) {
            return $managedUv
        }
        Write-Warning "The managed uv is not the pinned version; reinstalling it."
    }

    $pathUv = Get-Command uv -ErrorAction SilentlyContinue
    if ($pathUv) {
        if (Test-PinnedUvVersion $pathUv.Source) {
            return $pathUv.Source
        }
        Write-Warning (
            "uv found on PATH is not the pinned version {0}; " +
            "using the hash-verified managed copy instead." -f $PinnedUvVersion
        )
    }

    $legacyUv = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
    if (Test-Path -LiteralPath $legacyUv -PathType Leaf) {
        if (Test-PinnedUvVersion $legacyUv) {
            return $legacyUv
        }
    }
    return $null
}

function Install-PinnedUv {
    $assetKey = Get-UvAssetKey
    $expectedHash = $UvAssetHashes[$assetKey]
    if (-not $expectedHash) {
        throw "No recorded SHA-256 for uv asset '$assetKey'."
    }
    $assetName = "uv-$assetKey.zip"
    $assetUrl = "$UvDownloadBaseUrl/$assetName"
    $temporaryDirectory = Join-Path (
        [IO.Path]::GetTempPath()
    ) ("mineru-uv-" + [guid]::NewGuid().ToString("N"))
    $zipPath = Join-Path $temporaryDirectory $assetName
    $extractPath = Join-Path $temporaryDirectory "extract"

    New-Item -ItemType Directory -Path $temporaryDirectory | Out-Null
    try {
        Write-Host "Downloading pinned uv $PinnedUvVersion ($assetKey) from $assetUrl"
        Invoke-WebRequest -UseBasicParsing -Uri $assetUrl -OutFile $zipPath
        $actualHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash
        if (-not $actualHash.Equals($expectedHash, [StringComparison]::OrdinalIgnoreCase)) {
            throw (
                "uv asset hash mismatch: expected {0}, got {1}. " +
                "Refusing to extract or run this download." -f $expectedHash, $actualHash
            )
        }
        Write-Host "Verified uv asset SHA-256: $expectedHash"
        Expand-Archive -LiteralPath $zipPath -DestinationPath $extractPath
        $uvBinary = Get-ChildItem -LiteralPath $extractPath -Recurse -Filter "uv.exe" |
            Select-Object -First 1
        if (-not $uvBinary) {
            throw "uv.exe was not found inside the verified archive."
        }
        New-Item -ItemType Directory -Path $managedUvDirectory -Force | Out-Null
        Copy-Item -LiteralPath $uvBinary.FullName -Destination (
            Join-Path $managedUvDirectory "uv.exe"
        ) -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporaryDirectory) {
            Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force
        }
    }
    return (Join-Path $managedUvDirectory "uv.exe")
}

function Get-InstalledMinerUVersion([string]$PythonExe) {
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        return $null
    }
    try {
        $output = & $PythonExe -c "import importlib.metadata as m; print(m.version('mineru'))" 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        return (($output | Select-Object -Last 1).ToString().Trim())
    }
    catch {
        return $null
    }
}

function Test-ExactMinerUVersion([string]$VersionText) {
    if ([string]::IsNullOrWhiteSpace($VersionText)) {
        return $false
    }
    return $VersionText.Trim() -eq $PinnedMinerUVersion
}

try {
    Write-Step "Checking this computer"
    if ([IO.Path]::DirectorySeparatorChar -ne '\') {
        throw "This installer currently supports Windows only."
    }

    $EnvironmentPath = Assert-SafeEnvironmentPath $EnvironmentPath
    $pythonExe = Join-Path $EnvironmentPath "Scripts\python.exe"
    $mineruExe = Join-Path $EnvironmentPath "Scripts\mineru.exe"
    $modelsDownloader = Join-Path $EnvironmentPath "Scripts\mineru-models-download.exe"
    Write-Host "PowerShell: $($PSVersionTable.PSVersion)"
    Write-Host "Environment: $EnvironmentPath"

    $root = [IO.Path]::GetPathRoot($EnvironmentPath)
    $driveName = $root.TrimEnd('\').TrimEnd(':')
    $drive = Get-PSDrive -Name $driveName -ErrorAction SilentlyContinue
    if ($drive) {
        Write-Host ("Free disk space on {0}: {1:N1} GB" -f $root, ($drive.Free / 1GB))
        if ($drive.Free -lt 20GB) {
            Write-Warning (
                "Less than 20 GB is free. Current MinerU documentation recommends " +
                "20 GB for local deployment; package, model, and cache sizes can change."
            )
        }
    }

    $nvidia = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if ($nvidia) {
        & $nvidia.Source --query-gpu=name,memory.total --format=csv,noheader
    }
    else {
        Write-Warning "No NVIDIA GPU was detected. The pipeline backend can use CPU but may be slow."
    }

    $installedVersion = Get-InstalledMinerUVersion $pythonExe
    $environmentReady = (
        (Test-Path -LiteralPath $mineruExe -PathType Leaf) -and
        (Test-ExactMinerUVersion $installedVersion) -and
        (Test-CompatiblePython $pythonExe @())
    )
    $backupPath = $null

    if ($ForceReinstall -and (Test-Path -LiteralPath $EnvironmentPath -PathType Container)) {
        $backupPath = "$EnvironmentPath.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        if (Test-Path -LiteralPath $backupPath) {
            $backupPath = "$backupPath-$([guid]::NewGuid().ToString('N').Substring(0, 6))"
        }
        Write-Step "Moving the existing environment to a recoverable backup"
        Move-Item -LiteralPath $EnvironmentPath -Destination $backupPath
        Write-Host "Backup: $backupPath"
        $environmentReady = $false
        $installedVersion = $null
    }
    elseif ((Test-Path -LiteralPath $EnvironmentPath -PathType Container) -and -not $environmentReady) {
        $hasEntries = Get-ChildItem -LiteralPath $EnvironmentPath -Force | Select-Object -First 1
        if ($hasEntries) {
            $found = if ($installedVersion) { "MinerU $installedVersion" } else { "an incomplete environment" }
            throw (
                "Found $found at $EnvironmentPath, but this skill requires exactly MinerU " +
                "$PinnedMinerUVersion on Python 3.10-3.12. Review the path, then rerun with " +
                "-ForceReinstall to preserve it as a backup."
            )
        }
    }

    if ($environmentReady) {
        Write-Step "Exact MinerU version is already installed"
        Write-Host "MinerU $installedVersion"
    }
    else {
        $uv = Find-Uv
        if (-not $uv) {
            Write-Step "Installing pinned uv $PinnedUvVersion without modifying the user PATH"
            $uv = Install-PinnedUv
        }
        if (-not (Test-PinnedUvVersion $uv)) {
            throw "uv.exe is not the pinned version $PinnedUvVersion; refusing to continue."
        }

        $compatible = Find-CompatiblePython
        Write-Step "Creating an isolated Python environment"
        if ($compatible) {
            $pythonPath = & $compatible.Command @($compatible.Arguments) -c "import sys; print(sys.executable)"
            if ($LASTEXITCODE -ne 0) { throw "Compatible Python detection failed." }
            & $uv venv $EnvironmentPath --python $pythonPath
        }
        else {
            Write-Host "No compatible Python 3.10-3.12 was found. Installing isolated Python 3.11."
            & $uv python install 3.11
            if ($LASTEXITCODE -ne 0) { throw "Python 3.11 installation failed." }
            & $uv venv $EnvironmentPath --python 3.11
        }
        if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed." }

        Write-Step "Installing MinerU from the hashed dependency lock"
        if (-not (Test-Path -LiteralPath $lockFilePath -PathType Leaf)) {
            throw "The bundled requirements.lock is missing: $lockFilePath"
        }
        $env:UV_HTTP_TIMEOUT = "120"
        & $uv pip install --python $pythonExe --require-hashes -r $lockFilePath
        if ($LASTEXITCODE -ne 0) { throw "MinerU installation from the lock file failed." }
    }

    Write-Step "Verifying MinerU"
    $installedVersion = Get-InstalledMinerUVersion $pythonExe
    if (-not (Test-ExactMinerUVersion $installedVersion)) {
        throw "Expected exactly MinerU $PinnedMinerUVersion after installation, found '$installedVersion'."
    }
    & $mineruExe --version
    if ($LASTEXITCODE -ne 0) { throw "mineru.exe verification failed." }

    $marker = [ordered]@{
        managed_by = "mineru-pdf-to-markdown"
        mineru_requirement = "mineru[pipeline]==$PinnedMinerUVersion"
        verified_version = $installedVersion
        verified_at = (Get-Date).ToString("o")
    } | ConvertTo-Json
    Set-Content -LiteralPath (Join-Path $EnvironmentPath ".mineru-skill-owner.json") -Value $marker -Encoding UTF8

    if ($DownloadModels) {
        if (-not (Test-Path -LiteralPath $modelsDownloader -PathType Leaf)) {
            throw "mineru-models-download.exe was not found after installation."
        }
        Write-Step "Downloading the pipeline models from $ModelSource"
        & $modelsDownloader -s $ModelSource -m pipeline
        if ($LASTEXITCODE -ne 0) { throw "Pipeline model download failed." }
    }

    Write-Host "`nMinerU is ready." -ForegroundColor Green
    Write-Host "Environment: $EnvironmentPath"
    Write-Host "MinerU: $installedVersion"
    if ($backupPath) {
        Write-Host "Previous environment backup: $backupPath"
    }
    if (-not $DownloadModels) {
        Write-Host "Models were not pre-downloaded. Obtain approval before the first model download."
    }
}
catch {
    [Console]::Error.WriteLine("ERROR: $($_.Exception.Message)")
    exit 1
}
