"""MSIX 用のロゴ画像を 1 枚のソース (assets/icons/splash.png) から生成する。

出力先: packaging/Assets/ （AppxManifest.xml が参照する各サイズ）。
実行: .venv/Scripts/python.exe packaging/generate_logos.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "assets" / "icons" / "splash.png"
OUT = Path(__file__).resolve().parent / "Assets"

# (ファイル名, 幅, 高さ) — 正方形は高さ省略時に幅と同じ
SQUARE = {
    "Square44x44Logo.png": 44,
    "Square71x71Logo.png": 71,
    "Square150x150Logo.png": 150,
    "Square310x310Logo.png": 310,
    "StoreLogo.png": 50,
}
# 横長 / スプラッシュは透過キャンバスにアイコンを中央配置
WIDE = {
    "Wide310x150Logo.png": (310, 150, 120),   # キャンバス幅, 高さ, アイコン辺
    "SplashScreen.png": (620, 300, 200),
}


def _icon(size: int) -> Image.Image:
    return Image.open(SRC).convert("RGBA").resize(
        (size, size), Image.Resampling.LANCZOS)


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"ソース画像がありません: {SRC}")
    OUT.mkdir(parents=True, exist_ok=True)

    for name, size in SQUARE.items():
        _icon(size).save(OUT / name)

    for name, (w, h, icon_side) in WIDE.items():
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ic = _icon(icon_side)
        canvas.alpha_composite(ic, ((w - icon_side) // 2, (h - icon_side) // 2))
        canvas.save(OUT / name)

    made = sorted(p.name for p in OUT.glob("*.png"))
    print(f"生成しました ({len(made)}枚) -> {OUT}")
    for m in made:
        print("  ", m)


if __name__ == "__main__":
    main()
