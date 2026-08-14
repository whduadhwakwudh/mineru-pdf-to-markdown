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

    [string]$DiagnosticLogPath
)

$ErrorActionPreference = "Stop"
$utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8WithoutBom
$OutputEncoding = $utf8WithoutBom
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonScript = Join-Path $scriptDirectory "convert_pdf.py"

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
        return @{
            Python = $pythonBesideMinerU
            MinerU = $resolvedMinerU
        }
    }

    foreach ($candidate in $environmentCandidates) {
        $candidatePython = Join-Path $candidate "Scripts\python.exe"
        $candidateMinerU = Join-Path $candidate "Scripts\mineru.exe"
        if (
            (Test-Path -LiteralPath $candidatePython -PathType Leaf) -and
            (Test-Path -LiteralPath $candidateMinerU -PathType Leaf)
        ) {
            return @{
                Python = $candidatePython
                MinerU = $candidateMinerU
            }
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
        "--model-source", $ModelSource
    )
    if ($DiagnosticLogPath) {
        $arguments += "--diagnostic-log-path"
        $arguments += $DiagnosticLogPath
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
