<#
.SYNOPSIS
  Builds Lumen Reader and packages it as MSIX (one per architecture) + .msixbundle + .msixupload.

.DESCRIPTION
  - .msixupload  -> upload this to Microsoft Partner Center (the Store signs it for you).
  - .msixbundle  -> for local testing / sideloading; sign it with -CertificatePath.

  Needs: .NET 10 SDK and the Windows 10/11 SDK (makeappx.exe, makepri.exe, signtool.exe).
  Both are preinstalled on GitHub's windows-latest runners.

.EXAMPLE
  ./build/pack-msix.ps1 -Version 1.0.0.0
  ./build/pack-msix.ps1 -Version 1.2.0.0 -IdentityName "12345Name.LumenReader" -Publisher "CN=ABCD-1234-..." -PublisherDisplayName "Your Name"
#>
param(
    [string]   $Version = "1.0.0.0",
    [string[]] $Architectures = @("x64", "arm64"),
    [string]   $IdentityName = "LumenReader",
    [string]   $Publisher = "CN=LumenReaderDev",
    [string]   $PublisherDisplayName = "Lumen Reader",
    [string]   $OutputDir = "artifacts",
    [string]   $CertificatePath = "",
    [string]   $CertificatePassword = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$project = Join-Path $root "src/LumenReader/LumenReader.csproj"

if ($Version -notmatch '^\d+\.\d+\.\d+\.0$') {
    throw "Version must look like 1.2.3.0 (the Store requires the last number to be 0)."
}
$appVersion = $Version.Substring(0, $Version.LastIndexOf('.'))

function Find-SdkTool([string] $name) {
    $kits = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
    $tool = Get-ChildItem -Path $kits -Recurse -Filter $name -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '\\bin\\\d+\.\d+\.\d+\.\d+\\x64\\' } |
        Sort-Object { [version]($_.FullName -replace '.*\\bin\\([\d\.]+)\\.*', '$1') } -Descending |
        Select-Object -First 1
    if (-not $tool) { throw "$name not found. Install the Windows 10/11 SDK." }
    return $tool.FullName
}

$makeappx = Find-SdkTool "makeappx.exe"
$makepri = Find-SdkTool "makepri.exe"

$out = Join-Path $root $OutputDir
New-Item -ItemType Directory -Force -Path $out | Out-Null
$packages = @()

foreach ($arch in $Architectures) {
    Write-Host "=== $arch ===" -ForegroundColor Cyan
    $publishDir = Join-Path $out "publish-$arch"
    $layout = Join-Path $out "layout-$arch"
    $priRoot = Join-Path $out "pri-$arch"
    foreach ($dir in @($publishDir, $layout, $priRoot)) {
        if (Test-Path $dir) { Remove-Item $dir -Recurse -Force }
    }

    dotnet publish $project -c Release -r "win-$arch" --self-contained true `
        -p:Version=$appVersion -p:DebugType=none -o $publishDir
    if ($LASTEXITCODE -ne 0) { throw "dotnet publish failed ($arch)" }

    # Package layout = published app + logos + manifest
    Copy-Item $publishDir $layout -Recurse
    Copy-Item (Join-Path $root "packaging/Assets") (Join-Path $layout "Assets") -Recurse -Force

    $manifest = Get-Content (Join-Path $root "packaging/Package.appxmanifest") -Raw
    $manifest = $manifest.Replace('$IDENTITY$', $IdentityName).Replace('$PUBLISHER$', $Publisher).
        Replace('$PUBLISHERDISPLAYNAME$', $PublisherDisplayName).Replace('$VERSION$', $Version).Replace('$ARCH$', $arch)
    $manifestPath = Join-Path $layout "AppxManifest.xml"
    [IO.File]::WriteAllText($manifestPath, $manifest, (New-Object System.Text.UTF8Encoding $false))

    # resources.pri (lets Windows pick scale/targetsize logo variants). Indexed from a small folder
    # containing only the assets, not the whole .NET runtime.
    New-Item -ItemType Directory -Force -Path $priRoot | Out-Null
    Copy-Item (Join-Path $root "packaging/Assets") (Join-Path $priRoot "Assets") -Recurse
    $priConfig = Join-Path $out "priconfig.xml"
    & $makepri createconfig /cf $priConfig /dq en-US /pv 10.0.0 /o | Out-Host
    & $makepri new /pr $priRoot /cf $priConfig /mn $manifestPath /of (Join-Path $layout "resources.pri") /o | Out-Host
    if ($LASTEXITCODE -ne 0) {
        # Not fatal: the unqualified logo files are enough for the package to work.
        Write-Warning "makepri failed ($arch) - continuing without resources.pri"
        Remove-Item (Join-Path $layout "resources.pri") -ErrorAction SilentlyContinue
    }

    $msix = Join-Path $out "LumenReader_${Version}_$arch.msix"
    & $makeappx pack /d $layout /p $msix /o | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "makeappx pack failed ($arch)" }
    $packages += $msix
}

# Bundle all architectures
$bundleDir = Join-Path $out "bundle-input"
if (Test-Path $bundleDir) { Remove-Item $bundleDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $bundleDir | Out-Null
$packages | ForEach-Object { Copy-Item $_ $bundleDir }

$bundle = Join-Path $out "LumenReader_$Version.msixbundle"
& $makeappx bundle /d $bundleDir /p $bundle /bv $Version /o | Out-Host
if ($LASTEXITCODE -ne 0) { throw "makeappx bundle failed" }

# Store upload file (.msixupload is a zip that contains the bundle)
$upload = Join-Path $out "LumenReader_$Version.msixupload"
$zip = "$upload.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path $bundle -DestinationPath $zip
Move-Item $zip $upload -Force

# Optional signing for sideloading (the Store does not need this)
if ($CertificatePath) {
    $signtool = Find-SdkTool "signtool.exe"
    & $signtool sign /fd SHA256 /f $CertificatePath /p $CertificatePassword $bundle | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "signtool failed" }
}

Write-Host ""
Write-Host "Store upload : $upload" -ForegroundColor Green
Write-Host "Bundle       : $bundle" -ForegroundColor Green
