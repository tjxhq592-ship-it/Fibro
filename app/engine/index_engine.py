"""SQLite FTS5 によるファイル名検索インデックス。

巨大ディレクトリの再検索を高速化する。trigram トークナイザで部分一致
（日本語含む）に対応。3文字未満のキーワードは LIKE にフォールバック。
インデックスはルートパス単位で持ち、再構築は丸ごと入れ替え（シンプル優先）。
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS roots (
    root TEXT PRIMARY KEY,
    indexed_at REAL NOT NULL,
    file_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    root TEXT NOT NULL,
    path TEXT NOT NULL,
    name TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_root ON files(root);
CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
    name, content='files', content_rowid='id', tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
    INSERT INTO files_fts(rowid, name) VALUES (new.id, new.name);
END;
CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
    INSERT INTO files_fts(files_fts, rowid, name)
    VALUES ('delete', old.id, old.name);
END;
"""


def _fts_quote(keyword: str) -> str:
    """FTS5 MATCH 用にダブルクォートでフレーズ化（演算子を無効化）。"""
    return '"' + keyword.replace('"', '""') + '"'


class SearchIndex:
    """ファイル名インデックス。スレッドごとに接続を分ける。"""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path)
            self._local.conn = conn
        return conn

    # ---- 構築 ----
    def build(self, root: str | Path,
              cancel: threading.Event | None = None) -> int:
        """root 以下を走査してインデックスを構築（既存分は置き換え）。

        戻り値は登録ファイル数。キャンセル時は変更を捨てて -1。
        """
        root = str(Path(root))
        cancel = cancel or threading.Event()
        conn = self._conn()
        rows: list[tuple[str, str, str]] = []
        stack = [root]
        while stack:
            if cancel.is_set():
                return -1
            current = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        if cancel.is_set():
                            return -1
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(entry.path)
                            else:
                                rows.append((root, entry.path, entry.name))
                        except OSError:
                            continue
            except OSError:
                continue
        with conn:
            conn.execute("DELETE FROM files WHERE root = ?", (root,))
            conn.executemany(
                "INSERT INTO files(root, path, name) VALUES (?, ?, ?)", rows)
            conn.execute(
                "INSERT OR REPLACE INTO roots(root, indexed_at, file_count) "
                "VALUES (?, ?, ?)", (root, time.time(), len(rows)))
        return len(rows)

    # ---- 照会 ----
    def indexed_at(self, root: str | Path) -> float | None:
        """root のインデックス構築時刻。未構築なら None。"""
        cur = self._conn().execute(
            "SELECT indexed_at FROM roots WHERE root = ?",
            (str(Path(root)),))
        row = cur.fetchone()
        return row[0] if row else None

    def query(self, root: str | Path, keyword: str,
              limit: int = 5000) -> list[str]:
        """ファイル名部分一致でパスのリストを返す（大小区別なし）。"""
        root = str(Path(root))
        conn = self._conn()
        if len(keyword) >= 3:
            # trigram FTS（大小無視・部分一致）
            cur = conn.execute(
                "SELECT f.path FROM files_fts "
                "JOIN files f ON f.id = files_fts.rowid "
                "WHERE files_fts MATCH ? AND f.root = ? LIMIT ?",
                (_fts_quote(keyword), root, limit))
        else:
            # 短いキーワードは LIKE フォールバック
            escaped = (keyword.replace("\\", "\\\\")
                       .replace("%", r"\%").replace("_", r"\_"))
            cur = conn.execute(
                "SELECT path FROM files "
                r"WHERE root = ? AND name LIKE ? ESCAPE '\' LIMIT ?",
                (root, f"%{escaped}%", limit))
        return [r[0] for r in cur.fetchall()]

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
