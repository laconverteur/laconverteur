param(
    [Parameter(Mandatory = $true)][string]$Manifest,
    [Parameter(Mandatory = $true)][string]$Output
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing
$spec = Get-Content -LiteralPath $Manifest -Raw | ConvertFrom-Json
$width = [Math]::Max(1, [int][Math]::Ceiling([double]$spec.pixelWidth))
$height = [Math]::Max(1, [int][Math]::Ceiling([double]$spec.pixelHeight))
$minGlobalX = [double](($spec.tiles | Measure-Object -Property globalX -Minimum).Minimum)
$minGlobalY = [double](($spec.tiles | Measure-Object -Property globalY -Minimum).Minimum)
$maxGlobalX = [double](($spec.tiles | Measure-Object -Property globalX -Maximum).Maximum)
$maxGlobalY = [double](($spec.tiles | Measure-Object -Property globalY -Maximum).Maximum)
$mosaicWidth = [int]($maxGlobalX - $minGlobalX + 256)
$mosaicHeight = [int]($maxGlobalY - $minGlobalY + 256)
$mosaic = [System.Drawing.Bitmap]::new($mosaicWidth, $mosaicHeight, [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
$mosaicGraphics = [System.Drawing.Graphics]::FromImage($mosaic)
$mosaicGraphics.Clear([System.Drawing.Color]::White)
$bitmap = [System.Drawing.Bitmap]::new($width, $height, [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.Clear([System.Drawing.Color]::White)
$graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
try {
    foreach ($tile in $spec.tiles) {
        $image = [System.Drawing.Image]::FromFile([string]$tile.path)
        try {
            $x = [int](([double]$tile.globalX) - $minGlobalX)
            $y = [int](([double]$tile.globalY) - $minGlobalY)
            $mosaicGraphics.DrawImageUnscaled($image, $x, $y)
        }
        finally { $image.Dispose() }
    }
    $source = [System.Drawing.RectangleF]::new(
        [single](([double]$spec.left) - $minGlobalX),
        [single](([double]$spec.top) - $minGlobalY),
        [single]([double]$spec.pixelWidth),
        [single]([double]$spec.pixelHeight)
    )
    $destination = [System.Drawing.RectangleF]::new(0, 0, [single]$width, [single]$height)
    $graphics.DrawImage($mosaic, $destination, $source, [System.Drawing.GraphicsUnit]::Pixel)
    $directory = Split-Path -Parent $Output
    if ($directory) { New-Item -ItemType Directory -Path $directory -Force | Out-Null }
    $bitmap.Save($Output, [System.Drawing.Imaging.ImageFormat]::Png)
}
finally {
    $graphics.Dispose()
    $bitmap.Dispose()
    $mosaicGraphics.Dispose()
    $mosaic.Dispose()
}
