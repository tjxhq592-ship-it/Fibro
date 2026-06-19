# Fibro MSIX パッケージ作成（Microsoft Store 提出用）
#
# 前提:
#   - 先に PyInstaller でビルド済み（dist\Fibro\Fibro.exe が存在）。
#     未ビルドなら自動で .\build.ps1 を呼びます。
#   - Windows SDK（makeappx.exe）が入っていること。
#
# 使い方（Partner Center の値を渡す）:
#   .\build_msix.ps1 `
#       -IdentityName "12345Hiros.Fibro" `
#       -IdentityPublisher "CN=ABCD1234-5678-90AB-CDEF-1234567890AB" `
#       -PublisherDisplayName "hiros"
#
# ローカル動作確認用に自己署名して署名済み .msix も作る場合は -SelfSignTest を付与。
# （その場合 IdentityPublisher が自己署名証明書の Subject に一致している必要あり）

param(
    [string]$IdentityName = "{{IDENTITY_NAME}}",
    [string]$IdentityPublisher = "{{IDENTITY_PUBLISHER}}",
    [string]$PublisherDisplayName = "{{PUBLISHER_DISPLAY_NAME}}",
    [switch]$SelfSignTest
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$distApp = Join-Path $PSScriptRoot "dist\Fibro"
$pkgDir  = Join-Path $PSScriptRoot "packaging"
$staging = Join-Path $PSScriptRoot "build\msix_staging"
$outDir  = Join-Path $PSScriptRoot "dist"

# --- バージョン取得（app/__init__.py の __version__ -> 4 桁 x.y.z.0） ---
$verLine = Select-String -Path (Join-Path $PSScriptRoot "app\__init__.py") `
    -Pattern '__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $verLine) { Write-Error "app\__init__.py に __version__ が見つかりません"; exit 1 }
$ver = $verLine.Matches[0].Groups[1].Value
$parts = $ver.Split('.')
while ($parts.Count -lt 3) { $parts += '0' }
$version4 = "{0}.{1}.{2}.0" -f $parts[0], $parts[1], $parts[2]
Write-Host "バージョン: $version4" -ForegroundColor Cyan

# --- PyInstaller 成果物が無ければビルド ---
if (-not (Test-Path (Join-Path $distApp "Fibro.exe"))) {
    Write-Host "dist\Fibro が無いのでビルドします..." -ForegroundColor Yellow
    & (Join-Path $PSScriptRoot "build.ps1")
    if ($LASTEXITCODE -ne 0) { Write-Error "PyInstaller ビルド失敗"; exit 1 }
}

# --- ロゴ生成 ---
Write-Host "ロゴ生成..." -ForegroundColor Cyan
& $python (Join-Path $pkgDir "generate_logos.py")
if ($LASTEXITCODE -ne 0) { Write-Error "ロゴ生成失敗"; exit 1 }

# --- ステージングを組み立て ---
if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
New-Item -ItemType Directory -Path $staging -Force | Out-Null
Copy-Item $distApp (Join-Path $staging "Fibro") -Recurse
# 実行時に生成された config/（個人設定）は同梱しない。MSIX では %LOCALAPPDATA% 側へ。
$stagedConfig = Join-Path $staging "Fibro\config"
if (Test-Path $stagedConfig) { Remove-Item $stagedConfig -Recurse -Force }
Copy-Item (Join-Path $pkgDir "Assets") (Join-Path $staging "Assets") -Recurse

# --- マニフェストを置換して配置 ---
$manifest = Get-Content (Join-Path $pkgDir "AppxManifest.template.xml") -Raw -Encoding utf8
$manifest = $manifest.Replace("{{IDENTITY_NAME}}", $IdentityName)
$manifest = $manifest.Replace("{{IDENTITY_PUBLISHER}}", $IdentityPublisher)
$manifest = $manifest.Replace("{{PUBLISHER_DISPLAY_NAME}}", $PublisherDisplayName)
$manifest = $manifest.Replace("{{VERSION}}", $version4)
# テンプレートの説明コメント（<!-- ... -->）は成果物に含めない
$manifest = [regex]::Replace($manifest, '(?s)<!--.*?-->\s*', '')
if ($manifest -match "\{\{") {
    Write-Warning "未置換のプレースホルダが残っています。Partner Center の値を引数で渡してください。"
}
Set-Content -Path (Join-Path $staging "AppxManifest.xml") -Value $manifest -Encoding utf8

# --- makeappx.exe を探す（SDK の最新 x64） ---
$kits = "C:\Program Files (x86)\Windows Kits\10\bin"
$makeappx = Get-ChildItem -Path $kits -Recurse -Filter "makeappx.exe" -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -like "*\x64\*" } |
    Sort-Object FullName -Descending | Select-Object -First 1
if (-not $makeappx) { Write-Error "makeappx.exe が見つかりません（Windows SDK を入れてください）"; exit 1 }

# --- パッケージ作成 ---
$outMsix = Join-Path $outDir ("Fibro-{0}.msix" -f $version4)
Write-Host "パッケージ作成: $outMsix" -ForegroundColor Cyan
& $makeappx.FullName pack /d $staging /p $outMsix /o
if ($LASTEXITCODE -ne 0) { Write-Error "makeappx pack 失敗"; exit 1 }
Write-Host "完了（未署名・Store 提出用）: $outMsix" -ForegroundColor Green
Write-Host "  -> Partner Center にこの .msix をアップロードしてください（Store が署名します）。"

# --- 任意: ローカル確認用に自己署名 ---
if ($SelfSignTest) {
    Write-Host "ローカル確認用に自己署名します..." -ForegroundColor Cyan
    $signtool = Get-ChildItem -Path $kits -Recurse -Filter "signtool.exe" -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -like "*\x64\*" } |
        Sort-Object FullName -Descending | Select-Object -First 1
    if (-not $signtool) { Write-Error "signtool.exe が見つかりません"; exit 1 }

    $cert = New-SelfSignedCertificate -Type Custom -Subject $IdentityPublisher `
        -KeyUsage DigitalSignature -FriendlyName "Fibro Test" `
        -CertStoreLocation "Cert:\CurrentUser\My" `
        -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3", "2.5.29.19={text}")
    $pfxPwd = ConvertTo-SecureString -String "fibrotest" -Force -AsPlainText
    $pfx = Join-Path $outDir "Fibro_test.pfx"
    Export-PfxCertificate -Cert $cert -FilePath $pfx -Password $pfxPwd | Out-Null
    & $signtool.FullName sign /fd SHA256 /a /f $pfx /p "fibrotest" $outMsix
    if ($LASTEXITCODE -ne 0) { Write-Error "署名失敗（IdentityPublisher が証明書 Subject と一致しているか確認）"; exit 1 }
    Write-Host "自己署名完了: $outMsix" -ForegroundColor Green
    Write-Host "  インストール前に証明書を信頼してください（管理者 PowerShell）:" -ForegroundColor Yellow
    Write-Host "    Import-PfxCertificate -FilePath `"$pfx`" -CertStoreLocation Cert:\LocalMachine\TrustedPeople -Password (ConvertTo-SecureString 'fibrotest' -AsPlainText -Force)"
    Write-Host "  その後: Add-AppxPackage `"$outMsix`""
}
