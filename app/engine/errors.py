"""エンジン共通の例外定義。"""
from __future__ import annotations


class ContentReadError(Exception):
    """ファイル内容を読めなかった（破損・暗号化・オンライン専用等）。

    silent skip せず呼び出し側に通知し、skipped_read_error 統計に計上させる
    ための例外。Excel / Word / PowerPoint / PDF の各リーダーで共用する。
    ジェネレータから送出されるため、呼び出し側はイテレーション開始時
    （for 文）にも捕捉できるよう try で囲むこと。
    """
