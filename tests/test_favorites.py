from app.models.favorite import FavoriteStore


class TestPersistence:
    def test_add_and_reload(self, tmp_path):
        config = tmp_path / "config" / "favorites.json"
        store = FavoriteStore(config)
        store.add("プロジェクト", r"C:\Users\hiros\projects",
                  tags=["work"], note="2026年度")
        # 再読み込み（再起動相当）
        store2 = FavoriteStore(config)
        assert len(store2.favorites) == 1
        fav = store2.favorites[0]
        assert fav.label == "プロジェクト"
        assert fav.tags == ["work"]
        assert fav.note == "2026年度"

    def test_remove(self, tmp_path):
        config = tmp_path / "favorites.json"
        store = FavoriteStore(config)
        fav = store.add("a", "C:\\")
        assert store.remove(fav.id)
        assert FavoriteStore(config).favorites == []

    def test_remove_missing_returns_false(self, tmp_path):
        store = FavoriteStore(tmp_path / "favorites.json")
        assert not store.remove("nonexistent")


class TestCorruption:
    def test_corrupt_json_falls_back(self, tmp_path):
        config = tmp_path / "favorites.json"
        config.write_text("{ broken json !!!", encoding="utf-8")
        store = FavoriteStore(config)
        assert store.favorites == []
        # 保存し直せば復旧する
        store.add("x", "C:\\")
        assert len(FavoriteStore(config).favorites) == 1

    def test_wrong_schema_falls_back(self, tmp_path):
        config = tmp_path / "favorites.json"
        config.write_text('{"favorites": [{"nope": 1}]}', encoding="utf-8")
        assert FavoriteStore(config).favorites == []


class TestReachability:
    def test_existing_dir(self, tmp_path):
        store = FavoriteStore(tmp_path / "f.json")
        fav = store.add("here", str(tmp_path))
        assert fav.is_reachable()

    def test_missing_dir(self, tmp_path):
        store = FavoriteStore(tmp_path / "f.json")
        fav = store.add("gone", str(tmp_path / "no_such_dir"))
        assert not fav.is_reachable()

    def test_find_by_path(self, tmp_path):
        store = FavoriteStore(tmp_path / "f.json")
        store.add("here", str(tmp_path))
        assert store.find_by_path(str(tmp_path)) is not None
        assert store.find_by_path("C:\\nope") is None
