"""システムクリップボード経由のファイルコピー/切り取り（エクスプローラー相互運用）。

Windows のファイル コピー/貼り付けは、クリップボードに CF_HDROP（ファイル一覧）と
"Preferred DropEffect"（コピー=1 / 移動=2）を載せる方式。Qt の QMimeData は URL を
CF_HDROP に対応づけ、`application/x-qt-windows-mime;value="..."` で任意の Windows
クリップボード形式を扱える。これによりエクスプローラーと相互にコピー/貼り付けできる。
"""
from __future__ import annotations

import struct

from PySide6.QtCore import QByteArray, QMimeData, QUrl
from PySide6.QtWidgets import QApplication

# Qt が Windows クリップボード形式 "Preferred DropEffect" を表す MIME 名
_DROPEFFECT_MIME = 'application/x-qt-windows-mime;value="Preferred DropEffect"'
_DROPEFFECT_COPY = 1
_DROPEFFECT_MOVE = 2


def set_files(paths: list[str], move: bool) -> bool:
    """paths をシステムクリップボードへ（move=True で切り取り）。成功で True。"""
    clip = QApplication.clipboard()
    if clip is None or not paths:
        return False
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
    effect = _DROPEFFECT_MOVE if move else _DROPEFFECT_COPY
    mime.setData(_DROPEFFECT_MIME,
                 QByteArray(struct.pack("<I", effect)))
    clip.setMimeData(mime)
    return True


def get_files() -> tuple[list[str], bool] | None:
    """クリップボードのファイル一覧と移動フラグを返す。無ければ None。

    戻り値: (paths, move)。move は切り取り（移動）なら True。
    """
    clip = QApplication.clipboard()
    if clip is None:
        return None
    mime = clip.mimeData()
    if mime is None or not mime.hasUrls():
        return None
    paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
    if not paths:
        return None
    move = False
    data = mime.data(_DROPEFFECT_MIME)
    if data is not None and data.size() >= 4:
        effect = struct.unpack("<I", bytes(data)[:4])[0]
        move = bool(effect & _DROPEFFECT_MOVE)
    return paths, move


def has_files() -> bool:
    """クリップボードにファイルがあるか（貼り付け可否の判定用）。"""
    clip = QApplication.clipboard()
    if clip is None:
        return False
    mime = clip.mimeData()
    return mime is not None and mime.hasUrls() and any(
        u.isLocalFile() for u in mime.urls())


def clear() -> None:
    """クリップボードをクリア（移動の貼り付け後に呼ぶ）。"""
    clip = QApplication.clipboard()
    if clip is not None:
        clip.clear()
