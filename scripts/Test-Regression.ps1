param(
    [switch]$IncludeLarge,
    [string]$PackageRoot = ""
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($PackageRoot)) {
    $PackageRoot = Join-Path $repo ".build-current\package"
}
$package = (Resolve-Path -LiteralPath $PackageRoot).Path
$support = Join-Path $package "converteur"
$python = Join-Path $support "runtime\python\python.exe"
$converter = Join-Path $support "converteur.py"
$occt = Join-Path $support "runtime\occt"
foreach ($required in @($python, $converter, $occt)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Required package component not found: $required" }
}

$manifest = Get-Content -LiteralPath (Join-Path $repo "tests\expected\regression-cases.json") -Raw | ConvertFrom-Json
$outputRoot = Join-Path $repo "tests\output"
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
$failures = @()

Push-Location $support
try {
    foreach ($case in $manifest.cases) {
        if ($case.large -and -not $IncludeLarge) {
            Write-Host "SKIP $($case.id) (large)"
            continue
        }
        $input = Join-Path $repo $case.input
        $output = Join-Path $outputRoot "$($case.id).s3d"
        Write-Host "RUN  $($case.id)"
        & $python $converter $input $output --occt $occt
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $output) -or (Get-Item $output).Length -eq 0) {
            $failures += $case.id
        }
    }
}
finally {
    Pop-Location
}

if ($failures.Count -gt 0) {
    throw "Regression conversion failures: $($failures -join ', ')"
}
Write-Host "All selected regression conversions completed successfully."
