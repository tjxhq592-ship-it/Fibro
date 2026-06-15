import pytest

from app.engine.rename_engine import (
    CaseMode, RenameEngine, RenameRule, Status, Target,
    is_valid_filename, split_name,
)


@pytest.fixture
def engine():
    return RenameEngine()


class TestSplitName:
    def test_normal(self):
        assert split_name("document_v1.pdf") == ("document_v1", ".pdf")

    def test_no_ext(self):
        assert split_name("README") == ("README", "")

    def test_dotfile(self):
        assert split_name(".gitignore") == (".gitignore", "")

    def test_multi_dot(self):
        assert split_name("archive.tar.gz") == ("archive.tar", ".gz")


class TestValidation:
    @pytest.mark.parametrize("name", ["a.txt", "日本語.pdf", "file (2).jpg"])
    def test_valid(self, name):
        assert is_valid_filename(name)

    @pytest.mark.parametrize("name", [
        "", ".", "..", "a<b.txt", "a:b.txt", "con.txt", "NUL",
        "trailing. ", "a/b.txt", "a\\b.txt",
    ])
    def test_invalid(self, name):
        assert not is_valid_filename(name)

    def test_bad_regex_reported(self, engine):
        rule = RenameRule(search="[", use_regex=True)
        assert engine.validate_rule(rule) is not None

    def test_good_regex_ok(self, engine):
        rule = RenameRule(search=r"_v\d+", use_regex=True)
        assert engine.validate_rule(rule) is None


class TestSimpleReplace:
    def test_replace_in_name(self, engine):
        rule = RenameRule(search="_v1", replace="_final")
        plan = engine.build_plan(["document_v1.pdf"], rule)
        assert plan.items[0].new_name == "document_final.pdf"
        assert plan.items[0].status is Status.OK

    def test_name_target_does_not_touch_ext(self, engine):
        rule = RenameRule(search="pdf", replace="txt", target=Target.NAME)
        plan = engine.build_plan(["pdf_notes.pdf"], rule)
        assert plan.items[0].new_name == "txt_notes.pdf"

    def test_ext_target(self, engine):
        rule = RenameRule(search="jpeg", replace="jpg", target=Target.EXT)
        plan = engine.build_plan(["photo.jpeg"], rule)
        assert plan.items[0].new_name == "photo.jpg"

    def test_both_target(self, engine):
        rule = RenameRule(search="a", replace="b", target=Target.BOTH)
        plan = engine.build_plan(["aaa.aaa"], rule)
        assert plan.items[0].new_name == "bbb.bbb"

    def test_unchanged(self, engine):
        rule = RenameRule(search="zzz", replace="yyy")
        plan = engine.build_plan(["document.pdf"], rule)
        assert plan.items[0].status is Status.UNCHANGED


class TestRegex:
    def test_regex_replace(self, engine):
        rule = RenameRule(search=r"_v\d+", replace="_final", use_regex=True)
        plan = engine.build_plan(["doc_v12.pdf"], rule)
        assert plan.items[0].new_name == "doc_final.pdf"

    def test_regex_groups(self, engine):
        rule = RenameRule(search=r"(\d+)_(\w+)", replace=r"\2_\1",
                          use_regex=True)
        plan = engine.build_plan(["001_photo.jpg"], rule)
        assert plan.items[0].new_name == "photo_001.jpg"


class TestCounter:
    def test_counter_basic(self, engine):
        rule = RenameRule(search=r".+", replace="photo_${n}", use_regex=True,
                          counter_start=1, counter_digits=3)
        plan = engine.build_plan(["a.jpg", "b.jpg", "c.jpg"], rule)
        assert [i.new_name for i in plan.items] == [
            "photo_001.jpg", "photo_002.jpg", "photo_003.jpg"]

    def test_counter_start_step(self, engine):
        rule = RenameRule(search=r".+", replace="f_${n}", use_regex=True,
                          counter_start=10, counter_step=5, counter_digits=2)
        plan = engine.build_plan(["a.txt", "b.txt"], rule)
        assert [i.new_name for i in plan.items] == ["f_10.txt", "f_15.txt"]


class TestCase:
    def test_upper(self, engine):
        rule = RenameRule(case_mode=CaseMode.UPPER)
        plan = engine.build_plan(["abc.txt"], rule)
        assert plan.items[0].new_name == "ABC.txt"

    def test_title_both(self, engine):
        rule = RenameRule(case_mode=CaseMode.TITLE, target=Target.NAME)
        plan = engine.build_plan(["my document.pdf"], rule)
        assert plan.items[0].new_name == "My Document.pdf"


class TestConflict:
    def test_conflict_auto_resolved(self, engine):
        rule = RenameRule(search=r"_v\d+", replace="_final", use_regex=True)
        plan = engine.build_plan(["doc_v1.pdf", "doc_v2.pdf"], rule)
        assert plan.items[0].new_name == "doc_final.pdf"
        assert plan.items[1].new_name == "doc_final (2).pdf"
        assert plan.items[1].status is Status.RESOLVED

    def test_conflict_with_existing_file(self, engine):
        rule = RenameRule(search="old", replace="new")
        plan = engine.build_plan(
            ["old.txt"], rule, existing_names={"old.txt", "new.txt"})
        assert plan.items[0].new_name == "new (2).txt"

    def test_conflict_not_resolved_flagged(self, engine):
        rule = RenameRule(search=r"_v\d+", replace="", use_regex=True)
        plan = engine.build_plan(["doc_v1.pdf", "doc_v2.pdf"], rule,
                                 auto_resolve=False)
        assert plan.items[1].status is Status.CONFLICT
        assert plan.has_errors

    def test_swap_within_selection_ok(self, engine):
        # a→b, b→a の入れ替えは選択内なので衝突にならない…が
        # 同時に b が生成されるためプラン上は順次解決される
        rule = RenameRule(search="a", replace="b")
        plan = engine.build_plan(["a.txt"], rule, existing_names={"a.txt"})
        assert plan.items[0].new_name == "b.txt"
        assert plan.items[0].status is Status.OK

    def test_invalid_new_name(self, engine):
        rule = RenameRule(search="doc", replace="d:c")
        plan = engine.build_plan(["doc.txt"], rule)
        assert plan.items[0].status is Status.INVALID
