param([string]$Output = "docs\CHECKSUMS.sha256")

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$target = Join-Path $repo $Output
$roots = @("src", "assets", "templates", "third_party")
$lines = foreach ($relativeRoot in $roots) {
    Get-ChildItem -LiteralPath (Join-Path $repo $relativeRoot) -File -Recurse |
        Where-Object { $_.FullName -notmatch '[\\/](bin|obj|__pycache__)[\\/]' } |
        Sort-Object FullName | ForEach-Object {
        $relative = $_.FullName.Substring($repo.Length + 1).Replace('\', '/')
        $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
        "$hash  $relative"
    }
}
Set-Content -LiteralPath $target -Value $lines -Encoding ascii
Write-Host "Wrote $($lines.Count) checksums to $target"
