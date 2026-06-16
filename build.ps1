# Fibro 再ビルドスクリプト（手動実行用）
#
# 使い方:
#   1. Fibro を閉じる（起動中だと exe がロックされ失敗します）
#   2. PowerShell でプロジェクト直下にて  .\build.ps1  を実行
#
# オプション:
#   .\build.ps1 -Run      ビルド後に dist\Fibro\Fibro.exe を起動
#   .\build.ps1 -NoClean  --clean を付けず差分ビルド（高速・まれに古い成果物が残る）

param(
    [switch]$Run,
    [switch]$NoClean
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error ".venv が見つかりません: $python"
    exit 1
}

# 起動中の Fibro があると exe を上書きできないので先に止める
$proc = Get-Process -Name "Fibro" -ErrorAction SilentlyContinue
if ($proc) {
    Write-Host "起動中の Fibro を終了します..." -ForegroundColor Yellow
    $proc | Stop-Process -Force
    Start-Sleep -Milliseconds 500
}

$pyArgs = @("-m", "PyInstaller", "Fibro.spec", "--noconfirm")
if (-not $NoClean) { $pyArgs += "--clean" }

Write-Host "ビルド中..." -ForegroundColor Cyan
& $python @pyArgs
if ($LASTEXITCODE -ne 0) {
    Write-Error "ビルドに失敗しました (exit $LASTEXITCODE)"
    exit $LASTEXITCODE
}

$exe = Join-Path $PSScriptRoot "dist\Fibro\Fibro.exe"
Write-Host "完了: $exe" -ForegroundColor Green

if ($Run) {
    Write-Host "起動します..." -ForegroundColor Cyan
    & $exe
}
