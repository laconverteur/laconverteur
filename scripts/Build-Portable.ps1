param(
    [Parameter(Mandatory = $true)]
    [string]$RuntimeSource,
    [string]$Configuration = "Release"
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runtime = (Resolve-Path -LiteralPath $RuntimeSource).Path
$python = Join-Path $runtime "python\python.exe"
$draw = Join-Path $runtime "occt\win64\vc14\bin\DRAWEXE.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Python runtime not found: $python" }
if (-not (Test-Path -LiteralPath $draw)) { throw "OCCT DRAWEXE not found: $draw" }

$work = Join-Path $repo ".build-current"
$publish = Join-Path $work "publish"
$stage = Join-Path $work "package"
$support = Join-Path $stage "converteur"
$dist = Join-Path $repo "dist"
$zip = Join-Path $dist "La-Converteur-portable.zip"

foreach ($target in @($work, $zip)) {
    $full = [IO.Path]::GetFullPath($target)
    if (-not $full.StartsWith($repo, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe build target: $full"
    }
}
if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force }
New-Item -ItemType Directory -Path $publish, $stage, $support, $dist -Force | Out-Null

dotnet publish (Join-Path $repo "src\gui\SoundvisionToRDShape.csproj") `
    -c $Configuration -r win-x64 --self-contained true -o $publish
if ($LASTEXITCODE -ne 0) { throw "dotnet publish failed with exit code $LASTEXITCODE" }

Copy-Item -Path (Join-Path $publish "*") -Destination $stage -Recurse -Force
Copy-Item -LiteralPath $runtime -Destination (Join-Path $support "runtime") -Recurse
$converterSource = Join-Path $repo "src\converter"
Copy-Item -LiteralPath (Join-Path $converterSource "converteur.py") -Destination $support
Copy-Item -LiteralPath (Join-Path $converterSource "e5_eprj_prototype.py") -Destination $support
Copy-Item -LiteralPath (Join-Path $converterSource "occt_colors.html") -Destination $support
Copy-Item -LiteralPath (Join-Path $converterSource "e5-prototype") -Destination (Join-Path $support "e5-prototype") -Recurse
$geoSupport = Join-Path $support "geo"
New-Item -ItemType Directory -Path $geoSupport -Force | Out-Null
foreach ($geoFile in @("__init__.py", "geo_phase0.py", "geo_to_s3d.py", "elevation.py", "stitch_tiles.ps1")) {
    Copy-Item -LiteralPath (Join-Path $repo "src\geo\$geoFile") -Destination $geoSupport
}
Copy-Item -LiteralPath (Join-Path $repo "templates\rdshape") -Destination (Join-Path $support "rdshape") -Recurse
Copy-Item -LiteralPath (Join-Path $repo "assets") -Destination (Join-Path $support "assets") -Recurse
Copy-Item -LiteralPath (Join-Path $repo "src\gui\strings.en.json") -Destination (Join-Path $support "strings.en.json")

$unexpected = @(Get-ChildItem -LiteralPath $stage -Force | Where-Object { $_.Name -notin @("La Converteur.exe", "converteur") })
if ($unexpected.Count -gt 0) {
    throw "Unexpected files beside the executable: $($unexpected.Name -join ', ')"
}

if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::CreateFromDirectory(
    $stage,
    $zip,
    [IO.Compression.CompressionLevel]::Optimal,
    $false
)

$hash = Get-FileHash -LiteralPath $zip -Algorithm SHA256
$hashFile = "$zip.sha256"
Set-Content -LiteralPath $hashFile -Value "$($hash.Hash)  $([IO.Path]::GetFileName($zip))" -Encoding ascii
Get-Item -LiteralPath $zip | Select-Object FullName, Length, LastWriteTime
$hash
