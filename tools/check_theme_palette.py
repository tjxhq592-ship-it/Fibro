"""テーマパレットの機械検証（配色の良し悪しを目視レビューに委ねない）。

「見分けがつくか」「文字が読めるか」を数値で判定する。外部ライブラリは
使わず math のみ（CI・ビルド環境に依存を増やさないため）。

判定の軸:
  - 見分け: 背景色の CIEDE2000（ΔE00）距離。テーマは並べて比較するのでは
    なく「切り替えて使い、記憶と照合して識別する」ものなので、ΔE 4 程度
    では足りない。10.0 を下限とする。
  - 可読性: WCAG 2.x のコントラスト比。ただし bg だけで測らない。実際に
    文字が乗るのは行ストライプやタブ・選択の `elevated` であり、ここが
    ほぼ常に最も厳しい面になる。bg / surface / elevated の 3 面すべてに
    対して基準を課す。

単体実行:  python tools/check_theme_palette.py
テスト経由: tests/test_theme_palette.py
"""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

# 単体実行（python tools/check_theme_palette.py）でもリポジトリルートを
# import できるようにする。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.gui.theme import THEME_META, THEME_ORDER, TOKENS  # noqa: E402

# --- 判定基準 ---------------------------------------------------------------

#: 文字が乗りうる面。B〜G はこの 3 面すべてに対して判定する。
SURFACE_KEYS = ("bg", "surface", "elevated")

#: 背景色の相互距離（ΔE00）の下限。
MIN_BG_DISTANCE = 10.0

#: 同一テーマ内の段差（bg→surface→elevated）の許容範囲。
#: 下限を割ると層が潰れて平坦に見え、上限を超えると縞が目立つ。
MIN_STEP, MAX_STEP = 2.0, 8.0

#: 背景距離の判定から除外するペアとその理由。
#: dark と high_contrast はどちらも無彩色の暗色で、これ以上引き離すと
#: 「純黒のハイコントラスト」という要件と衝突する。この 2 つは背景では
#: なく純白の本文・黄色のアクセント・太い境界線で識別させる設計。
BG_DISTANCE_EXCEPTIONS: dict[frozenset[str], str] = {
    frozenset({"dark", "high_contrast"}):
        "どちらも無彩色の暗色。high_contrast は純黒が要件のため引き離せない。"
        "識別は純白の本文・黄アクセント・太い境界線が担う。",
}

#: (トークンキー, 最小コントラスト比) — SURFACE_KEYS の 3 面すべてに課す。
CONTRAST_RULES: tuple[tuple[str, float], ...] = (
    ("text", 7.0),
    ("text_sub", 4.5),
    ("text_hint", 3.0),
    ("accent", 4.5),
    ("icon", 4.5),
    ("status_ok", 4.5),
    ("status_unchanged", 4.5),
    ("status_warn", 4.5),
    ("status_error", 4.5),
)

#: sel_bg の上には text がそのまま乗る（QSS の selection-color: text）。
MIN_SEL_BG_CONTRAST = 4.5

#: accent 上の文字色。
MIN_ON_ACCENT_CONTRAST = 4.5

HEX_RE = re.compile(r"^#[0-9a-f]{6}$")


# --- 色空間の変換 -----------------------------------------------------------

def hex_to_rgb(value: str) -> tuple[float, float, float]:
    """`#rrggbb` を 0..1 の sRGB タプルへ。"""
    v = value.strip().lstrip("#")
    return tuple(int(v[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _linearize(c: float) -> float:
    """sRGB のガンマ展開（区分関数。近似の 2.2 乗では誤差が出るので省略しない）。"""
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(value: str) -> float:
    """WCAG 2.x の相対輝度。"""
    r, g, b = (_linearize(c) for c in hex_to_rgb(value))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 2.x のコントラスト比（1.0〜21.0）。"""
    l1, l2 = relative_luminance(fg), relative_luminance(bg)
    if l1 < l2:
        l1, l2 = l2, l1
    return (l1 + 0.05) / (l2 + 0.05)


# D65 / 2°観測者の白色点。
_WHITE_D65 = (0.95047, 1.00000, 1.08883)


def srgb_to_lab(value: str) -> tuple[float, float, float]:
    """`#rrggbb` → CIE L*a*b*（D65 / 2°）。"""
    r, g, b = (_linearize(c) for c in hex_to_rgb(value))
    x = 0.4124564 * r + 0.3575761 * g + 0.1804375 * b
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = 0.0193339 * r + 0.1191920 * g + 0.9503041 * b

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116

    fx, fy, fz = (f(c / w) for c, w in zip((x, y, z), _WHITE_D65))
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def ciede2000(lab1: tuple[float, float, float],
              lab2: tuple[float, float, float]) -> float:
    """CIEDE2000 色差（ΔE00）。kL = kC = kH = 1。"""
    l1, a1, b1 = lab1
    l2, a2, b2 = lab2

    c1 = math.hypot(a1, b1)
    c2 = math.hypot(a2, b2)
    c_bar = (c1 + c2) / 2
    g = 0.5 * (1 - math.sqrt(c_bar ** 7 / (c_bar ** 7 + 25.0 ** 7))) \
        if c_bar > 0 else 0.0

    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360 if (b1 or a1p) else 0.0
    h2p = math.degrees(math.atan2(b2, a2p)) % 360 if (b2 or a2p) else 0.0

    dlp = l2 - l1
    dcp = c2p - c1p
    if c1p * c2p == 0:
        dhp = 0.0
    else:
        dhp = h2p - h1p
        if dhp > 180:
            dhp -= 360
        elif dhp < -180:
            dhp += 360
    dhp_big = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dhp / 2))

    lbp = (l1 + l2) / 2
    cbp = (c1p + c2p) / 2
    if c1p * c2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        hbp = (h1p + h2p + 360) / 2
    else:
        hbp = (h1p + h2p - 360) / 2

    t = (1
         - 0.17 * math.cos(math.radians(hbp - 30))
         + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6))
         - 0.20 * math.cos(math.radians(4 * hbp - 63)))
    dtheta = 30 * math.exp(-(((hbp - 275) / 25) ** 2))
    rc = 2 * math.sqrt(cbp ** 7 / (cbp ** 7 + 25.0 ** 7)) if cbp > 0 else 0.0
    sl = 1 + (0.015 * (lbp - 50) ** 2) / math.sqrt(20 + (lbp - 50) ** 2)
    sc = 1 + 0.045 * cbp
    sh = 1 + 0.015 * cbp * t
    rt = -math.sin(math.radians(2 * dtheta)) * rc

    term_l = dlp / sl
    term_c = dcp / sc
    term_h = dhp_big / sh
    return math.sqrt(term_l ** 2 + term_c ** 2 + term_h ** 2
                     + rt * term_c * term_h)


def delta_e(hex1: str, hex2: str) -> float:
    """`#rrggbb` 同士の ΔE00。"""
    return ciede2000(srgb_to_lab(hex1), srgb_to_lab(hex2))


def verify_ciede2000_implementation() -> None:
    """Sharma らの検証データで CIEDE2000 実装を自己検証する。

    この式は括弧の付け方・角度の折り返し・Rt の符号など踏み外しどころが
    多い。実装ミスに気づかないまま「基準を満たした」と報告するのが最悪の
    失敗なので、判定の前に必ず通す。
    """
    got = ciede2000((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485))
    if abs(got - 2.0425) > 1e-4:
        raise AssertionError(
            f"CIEDE2000 実装が検証データと不一致: expected 2.0425, got {got:.4f}")
    # 補助ケース（同じく Sharma の表より）。
    for lab1, lab2, expected in (
        ((50.0, 3.1571, -77.2803), (50.0, 0.0, -82.7485), 2.8615),
        ((50.0, 2.4900, -0.0010), (50.0, -2.4900, 0.0009), 7.1792),
        ((50.0, -1.3802, -84.2814), (50.0, 0.0, -82.7485), 1.0000),
        ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
    ):
        got = ciede2000(lab1, lab2)
        if abs(got - expected) > 1e-4:
            raise AssertionError(
                f"CIEDE2000 実装が検証データと不一致: {lab1} vs {lab2} → "
                f"expected {expected}, got {got:.4f}")


# --- 判定 -------------------------------------------------------------------

def _is_hex(value: object) -> bool:
    return isinstance(value, str) and bool(HEX_RE.match(value))


def _min_contrast(color: str, tokens: dict) -> tuple[float, str]:
    """3 面に対するコントラスト比の最小値と、その面のキーを返す。"""
    pairs = [(contrast_ratio(color, tokens[k]), k) for k in SURFACE_KEYS]
    return min(pairs)


def check_palette(tokens: dict[str, dict] | None = None,
                  order: list[str] | None = None,
                  meta: dict[str, dict] | None = None) -> list[str]:
    """全判定を実行し、失敗メッセージの一覧を返す（空なら PASS）。

    どのテーマの・どのトークンが・どの面に対して・いくつだったかを必ず
    出力する。合否だけ返すと調整が手探りになる。
    """
    verify_ciede2000_implementation()
    tokens = TOKENS if tokens is None else tokens
    order = THEME_ORDER if order is None else order
    meta = THEME_META if meta is None else meta
    fails: list[str] = []

    # K: 全値が #rrggbb 形式（半透明値は面に対して測定できないので許さない）
    for name in order:
        for key, value in sorted(tokens[name].items()):
            if not _is_hex(value):
                fails.append(f"[K] {name}.{key} = {value!r} が #rrggbb 形式でない")

    # J: 全テーマのキー構成が一致
    key_sets = {name: frozenset(tokens[name]) for name in order}
    base = key_sets[order[0]]
    for name, keys in key_sets.items():
        if keys != base:
            missing = sorted(base - keys)
            extra = sorted(keys - base)
            fails.append(
                f"[J] {name} のキー構成が {order[0]} と不一致 "
                f"(不足={missing} 余分={extra})")
        # キーが欠けた状態で色を測ると KeyError で全体が止まるため、
        # 以降の測定は共通キーを持つテーマだけを対象にする。
    if any(k != base for k in key_sets.values()):
        return fails

    # A: 背景色の相互距離
    for i, a in enumerate(order):
        for b in order[i + 1:]:
            if frozenset({a, b}) in BG_DISTANCE_EXCEPTIONS:
                continue  # A': 明示的な例外（理由は BG_DISTANCE_EXCEPTIONS）
            if not (_is_hex(tokens[a]["bg"]) and _is_hex(tokens[b]["bg"])):
                continue  # K で報告済み
            d = delta_e(tokens[a]["bg"], tokens[b]["bg"])
            if d < MIN_BG_DISTANCE:
                fails.append(
                    f"[A] bg が近すぎる: {a} ({tokens[a]['bg']}) / "
                    f"{b} ({tokens[b]['bg']}) ΔE00={d:.1f} < {MIN_BG_DISTANCE}")

    for name in order:
        t = tokens[name]
        surfaces_ok = all(_is_hex(t[k]) for k in SURFACE_KEYS)

        # B〜G: 文字色・アクセント・アイコン・ステータスを 3 面すべてに対して
        for key, minimum in CONTRAST_RULES:
            if not (surfaces_ok and _is_hex(t[key])):
                continue  # K で報告済み
            ratio, worst = _min_contrast(t[key], t)
            if ratio < minimum:
                fails.append(
                    f"[{_rule_id(key)}] {name}.{key} ({t[key]}) 対 "
                    f"{worst} ({t[worst]}) のコントラスト比 {ratio:.2f} "
                    f"< {minimum}")

        # H: on_accent 対 accent
        if _is_hex(t["on_accent"]) and _is_hex(t["accent"]):
            ratio = contrast_ratio(t["on_accent"], t["accent"])
            if ratio < MIN_ON_ACCENT_CONTRAST:
                fails.append(
                    f"[H] {name}.on_accent ({t['on_accent']}) 対 accent "
                    f"({t['accent']}) のコントラスト比 {ratio:.2f} "
                    f"< {MIN_ON_ACCENT_CONTRAST}")

        # H: sel_bg の上にも text がそのまま乗る
        if _is_hex(t["text"]) and _is_hex(t["sel_bg"]):
            ratio = contrast_ratio(t["text"], t["sel_bg"])
            if ratio < MIN_SEL_BG_CONTRAST:
                fails.append(
                    f"[H] {name}.text ({t['text']}) 対 sel_bg ({t['sel_bg']}) の"
                    f"コントラスト比 {ratio:.2f} < {MIN_SEL_BG_CONTRAST}")

        # I: 面の段差
        if surfaces_ok:
            for lo, hi in (("bg", "surface"), ("surface", "elevated")):
                d = delta_e(t[lo], t[hi])
                if not (MIN_STEP <= d <= MAX_STEP):
                    fails.append(
                        f"[I] {name}.{lo}→{hi} の段差 ΔE00={d:.1f} が "
                        f"{MIN_STEP}〜{MAX_STEP} の外")

    # メタ情報の整合（順序・表示名・明暗の欠落を検証と同じ場所で拾う）
    if set(order) != set(tokens) or set(order) != set(meta):
        fails.append(
            f"[J] THEME_ORDER / TOKENS / THEME_META のキー集合が不一致: "
            f"order={sorted(order)} tokens={sorted(tokens)} "
            f"meta={sorted(meta)}")

    return fails


def _rule_id(token_key: str) -> str:
    return {
        "text": "B", "text_sub": "C", "text_hint": "D",
        "accent": "E", "icon": "F",
    }.get(token_key, "G")


# --- 測定値のレポート -------------------------------------------------------

def report() -> str:
    """判定に使った実測値を表形式で返す（指示書の報告フォーマット用）。"""
    lines = ["## コントラスト比（bg / surface / elevated の最小値）",
             "| テーマ | text | text_sub | text_hint | accent | icon |",
             "|---|---|---|---|---|---|"]
    for name in THEME_ORDER:
        t = TOKENS[name]
        cells = [f"{_min_contrast(t[k], t)[0]:.2f}"
                 for k in ("text", "text_sub", "text_hint", "accent", "icon")]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")

    lines += ["", "## 背景色の相互距離（ΔE00）",
              "| | " + " | ".join(THEME_ORDER) + " |",
              "|---" * (len(THEME_ORDER) + 1) + "|"]
    for a in THEME_ORDER:
        cells = []
        for b in THEME_ORDER:
            cells.append("-" if a == b
                         else f"{delta_e(TOKENS[a]['bg'], TOKENS[b]['bg']):.1f}")
        lines.append(f"| {a} | " + " | ".join(cells) + " |")

    lines += ["", "## 面の段差（ΔE00）",
              "| テーマ | bg→surface | surface→elevated |", "|---|---|---|"]
    for name in THEME_ORDER:
        t = TOKENS[name]
        lines.append(
            f"| {name} | {delta_e(t['bg'], t['surface']):.1f} | "
            f"{delta_e(t['surface'], t['elevated']):.1f} |")
    return "\n".join(lines)


def main() -> int:
    fails = check_palette()
    if fails:
        print(f"FAIL: {len(fails)} 件")
        for f in fails:
            print("  " + f)
        return 1
    print(f"PASS: {len(THEME_ORDER)} テーマすべてが基準を満たしています")
    print()
    print(report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
