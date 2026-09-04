[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PdfPath,

    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [string]$EnvironmentPath,

    [string]$MinerUPath,

    [ValidateRange(1, 3600)]
    [int]$HeartbeatSeconds = 15,

    [ValidateRange(0, 10080)]
    [int]$TimeoutMinutes = 0,

    [ValidateSet("auto", "huggingface", "modelscope", "local")]
    [string]$ModelSource = "auto",

    [string]$DiagnosticLogPath,

    [ValidateSet("auto", "txt", "ocr")]
    [string]$Method = "auto",

    [ValidateSet("ch", "ch_server", "korean", "ta", "te", "ka", "th", "el", "arabic", "east_slavic", "cyrillic", "devanagari")]
    [string]$Language,

    [ValidateRange(-1, 2147483647)]
    [int]$StartPage = -1,

    [ValidateRange(-1, 2147483647)]
    [int]$EndPage = -1,

    [switch]$DisableFormula,

    [switch]$DisableTable
)

$ErrorActionPreference = "Stop"
$utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8WithoutBom
$OutputEncoding = $utf8WithoutBom
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonScript = Join-Path $scriptDirectory "convert_pdf.py"
$PinnedMinerUVersion = "3.4.5"

function Test-ExactMinerUVersion([string]$VersionText) {
    if ([string]::IsNullOrWhiteSpace($VersionText)) {
        return $false
    }
    return $VersionText.Trim() -eq $PinnedMinerUVersion
}

function Assert-CompatibleMinerU([string]$PythonExe, [string]$MinerUExe) {
    try {
        $output = & $PythonExe -c "import importlib.metadata as m; print(m.version('mineru'))" 2>$null
        if ($LASTEXITCODE -ne 0) {
            throw "Could not read the installed MinerU version."
        }
        $installedVersion = (($output | Select-Object -Last 1).ToString().Trim())
    }
    catch {
        throw "Could not verify MinerU in the selected environment: $PythonExe"
    }
    if (-not (Test-ExactMinerUVersion $installedVersion)) {
        throw (
            "The selected environment contains MinerU $installedVersion, but this skill " +
            "requires exactly $PinnedMinerUVersion. Run Install-MinerU.ps1 with the same " +
            "-EnvironmentPath and -ForceReinstall."
        )
    }
    return @{
        Python = $PythonExe
        MinerU = $MinerUExe
    }
}

function Resolve-MinerUEnvironment {
    $environmentCandidates = @()
    if ($EnvironmentPath) {
        $environmentCandidates += [IO.Path]::GetFullPath($EnvironmentPath)
    }
    elseif (-not $MinerUPath) {
        $environmentCandidates += (Join-Path $env:USERPROFILE "mineru-env")
        $environmentCandidates += (Join-Path $env:USERPROFILE ".mineru-env")
    }

    if ($MinerUPath) {
        $resolvedMinerU = [IO.Path]::GetFullPath($MinerUPath)
        if (-not (Test-Path -LiteralPath $resolvedMinerU -PathType Leaf)) {
            throw "The selected mineru.exe does not exist: $resolvedMinerU"
        }
        $pythonBesideMinerU = Join-Path (Split-Path -Parent $resolvedMinerU) "python.exe"
        if (-not (Test-Path -LiteralPath $pythonBesideMinerU -PathType Leaf)) {
            throw "python.exe was not found beside the selected MinerU executable: $pythonBesideMinerU"
        }
        return (Assert-CompatibleMinerU $pythonBesideMinerU $resolvedMinerU)
    }

    foreach ($candidate in $environmentCandidates) {
        $candidatePython = Join-Path $candidate "Scripts\python.exe"
        $candidateMinerU = Join-Path $candidate "Scripts\mineru.exe"
        if (
            (Test-Path -LiteralPath $candidatePython -PathType Leaf) -and
            (Test-Path -LiteralPath $candidateMinerU -PathType Leaf)
        ) {
            return (Assert-CompatibleMinerU $candidatePython $candidateMinerU)
        }
    }

    if ($EnvironmentPath) {
        throw "The selected MinerU environment is incomplete: $EnvironmentPath. Run Install-MinerU.ps1 for that same path."
    }
    throw "MinerU is not installed in a supported environment. Run Install-MinerU.ps1 first."
}

try {
    if (-not (Test-Path -LiteralPath $pythonScript -PathType Leaf)) {
        throw "The bundled Python converter is missing: $pythonScript"
    }

    $environment = Resolve-MinerUEnvironment
    $arguments = @(
        $pythonScript,
        "--pdf", $PdfPath,
        "--output", $OutputDirectory,
        "--mineru", $environment.MinerU,
        "--heartbeat-seconds", $HeartbeatSeconds,
        "--timeout-minutes", $TimeoutMinutes,
        "--model-source", $ModelSource,
        "--method", $Method
    )
    if ($DiagnosticLogPath) {
        $arguments += "--diagnostic-log-path"
        $arguments += $DiagnosticLogPath
    }
    if ($Language) {
        $arguments += "--language"
        $arguments += $Language
    }
    if ($StartPage -ge 0) {
        $arguments += "--start-page"
        $arguments += $StartPage
    }
    if ($EndPage -ge 0) {
        $arguments += "--end-page"
        $arguments += $EndPage
    }
    if ($DisableFormula) {
        $arguments += "--disable-formula"
    }
    if ($DisableTable) {
        $arguments += "--disable-table"
    }

    & $environment.Python @arguments
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
catch {
    Write-Error $_.Exception.Message
    exit 1
}
