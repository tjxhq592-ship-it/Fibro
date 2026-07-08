"""プロジェクト（お気に入り・タブ・テーマ等の保存スコープ）の管理。"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.atomicio import atomic_write_text


@dataclass
class ProjectMeta:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = ""
    order: int = 0


class ProjectManager:
    """projects.json（プロジェクト一覧・並び順・アクティブID）の永続化。

    デフォルトプロジェクトはここには登録しない（active_project_id が None/空の
    ときがデフォルトプロジェクト状態）。メニューには self.projects のみが表示される。
    """

    def __init__(self, config_dir: Path) -> None:
        self._config_dir = config_dir
        self._path = config_dir / "projects.json"
        self.projects: list[ProjectMeta] = []
        self.active_project_id: str | None = None
        self.load()

    def load(self) -> None:
        if not self._path.exists():
            self.projects, self.active_project_id = [], None
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self.projects = [
                ProjectMeta(id=p["id"], name=p["name"], order=int(p.get("order", 0)))
                for p in data.get("projects", [])
            ]
            self.projects.sort(key=lambda p: p.order)
            self.active_project_id = data.get("active_project_id") or None
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError):
            self.projects, self.active_project_id = [], None

    def save(self) -> None:
        data = {
            "projects": [asdict(p) for p in self.projects],
            "active_project_id": self.active_project_id,
        }
        try:
            atomic_write_text(
                self._path, json.dumps(data, ensure_ascii=False, indent=2))
        except OSError:
            pass

    def add(self, name: str) -> ProjectMeta:
        order = len(self.projects)
        proj = ProjectMeta(name=name, order=order)
        self.projects.append(proj)
        self.project_dir(proj.id).mkdir(parents=True, exist_ok=True)
        self.save()
        return proj

    def rename(self, project_id: str, new_name: str) -> None:
        for p in self.projects:
            if p.id == project_id:
                p.name = new_name
                self.save()
                return

    def remove(self, project_id: str) -> None:
        """プロジェクトのメタデータとデータディレクトリを削除する。

        削除したプロジェクトがアクティブだった場合は active_project_id を
        None（デフォルトプロジェクト）へフォールバックする。
        """
        self.projects = [p for p in self.projects if p.id != project_id]
        for i, p in enumerate(self.projects):
            p.order = i
        if self.active_project_id == project_id:
            self.active_project_id = None
        self.save()
        import shutil
        d = self.project_dir(project_id)
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)

    def reorder(self, ordered_ids: list[str]) -> None:
        by_id = {p.id: p for p in self.projects}
        self.projects = []
        for i, pid in enumerate(ordered_ids):
            if pid in by_id:
                by_id[pid].order = i
                self.projects.append(by_id[pid])
        self.save()

    def set_active(self, project_id: str | None) -> None:
        self.active_project_id = project_id
        self.save()

    @property
    def active(self) -> ProjectMeta | None:
        return next((p for p in self.projects
                     if p.id == self.active_project_id), None)

    def project_dir(self, project_id: str) -> Path:
        return self._config_dir / "projects" / project_id

    def store_paths(self, project_id: str | None) -> dict[str, Path]:
        """アクティブなプロジェクト（None ならデフォルト）に対応する
        favorites/rename_presets/project_settings のファイルパスを返す。"""
        if project_id is None:
            return {
                "favorites": self._config_dir / "favorites.json",
                "rename_presets": self._config_dir / "rename_presets.json",
                "project_settings": self._config_dir / "default_project_settings.json",
            }
        d = self.project_dir(project_id)
        d.mkdir(parents=True, exist_ok=True)
        return {
            "favorites": d / "favorites.json",
            "rename_presets": d / "rename_presets.json",
            "project_settings": d / "settings.json",
        }
