"""Feather Icons (https://feathericons.com, MIT License) の埋め込み。

SVG を文字列で同梱し、テーマに応じた stroke 色で 24x24 にレンダリングする。
外部アセットファイル不要で PyInstaller 同梱も自動。
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray
from PySide6.QtGui import QIcon, QPixmap

_SVG_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
    'viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
)

# Feather Icons v4.29 のパスデータ
_PATHS = {
    "search": '<circle cx="11" cy="11" r="8"/>'
              '<line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    "edit-3": '<path d="M12 20h9"/>'
              '<path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 '
              '3.5z"/>',
    "rotate-ccw": '<polyline points="1 4 1 10 7 10"/>'
                  '<path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>',
    "refresh-cw": '<polyline points="23 4 23 10 17 10"/>'
                  '<polyline points="1 20 1 14 7 14"/>'
                  '<path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 '
                  '4.36A9 9 0 0 0 20.49 15"/>',
    "trash-2": '<polyline points="3 6 5 6 21 6"/>'
               '<path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 '
               '0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>'
               '<line x1="10" y1="11" x2="10" y2="17"/>'
               '<line x1="14" y1="11" x2="14" y2="17"/>',
    "moon": '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
    "sun": '<circle cx="12" cy="12" r="5"/>'
           '<line x1="12" y1="1" x2="12" y2="3"/>'
           '<line x1="12" y1="21" x2="12" y2="23"/>'
           '<line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/>'
           '<line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>'
           '<line x1="1" y1="12" x2="3" y2="12"/>'
           '<line x1="21" y1="12" x2="23" y2="12"/>'
           '<line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/>'
           '<line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>',
    "star": '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 '
            '12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/>',
    "arrow-left": '<line x1="19" y1="12" x2="5" y2="12"/>'
                  '<polyline points="12 19 5 12 12 5"/>',
    "arrow-right": '<line x1="5" y1="12" x2="19" y2="12"/>'
                   '<polyline points="12 5 19 12 12 19"/>',
    "arrow-up": '<line x1="12" y1="19" x2="12" y2="5"/>'
                '<polyline points="5 12 12 5 19 12"/>',
    "edit": '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 '
            '2-2v-7"/>'
            '<path d="M18.2 2.3a2.4 2.4 0 0 1 3.4 3.4L11 16.3 7 17l.7-4L18.2 '
            '2.3z"/>',
}

LIGHT_COLOR = "#404040"
DARK_COLOR = "#d8d8d8"


def feather_icon(name: str, dark: bool = False) -> QIcon:
    """名前と現在テーマからアイコンを生成。"""
    color = DARK_COLOR if dark else LIGHT_COLOR
    svg = _SVG_TEMPLATE.format(color=color, body=_PATHS[name])
    pixmap = QPixmap()
    pixmap.loadFromData(QByteArray(svg.encode()), "SVG")
    return QIcon(pixmap)
