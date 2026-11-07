"""P0/P7：课程包校验能认出每一类坏包，合法的模拟课程能通过。"""

from __future__ import annotations

from pathlib import Path

import pytest

from deeptutor.learning.courses.markdown import check_markdown
from deeptutor.learning.courses.package import check_lock, scan_catalog

from .conftest import COURSE, FIXTURE_CATALOG, edit_json


def codes(catalog: Path) -> set[str]:
    courses, report = scan_catalog(catalog)
    check_lock(catalog, courses, report)
    return {issue.code.value for issue in report.errors}


def test_fixture_catalog_is_valid_and_locked():
    courses, report = scan_catalog(FIXTURE_CATALOG)
    check_lock(FIXTURE_CATALOG, courses, report)
    assert report.ok, [i.model_dump() for i in report.errors]
    assert list(courses) == [COURSE]
    assert list(courses[COURSE].versions) == ["1.0.0"]


def test_builtin_catalog_is_valid():
    """线上目录（哪怕是空的）在 CI 里也必须能通过校验。"""
    from deeptutor.learning.courses.repository import _BUILTIN_CATALOG

    courses, report = scan_catalog(_BUILTIN_CATALOG)
    check_lock(_BUILTIN_CATALOG, courses, report)
    assert report.ok, [i.model_dump() for i in report.errors]


OUTLINE = f"{COURSE}/1.0.0/outline.json"


@pytest.mark.parametrize(
    ("relative", "mutate", "expected"),
    [
        (
            OUTLINE,
            lambda d: d["knowledge_points"].append(dict(d["knowledge_points"][0])),
            "COURSE_DUPLICATE_ID",
        ),
        (
            OUTLINE,
            lambda d: d["lessons"][0]["knowledge_points"].append("ghost"),
            "COURSE_DANGLING_REFERENCE",
        ),
        (
            OUTLINE,
            lambda d: d["knowledge_points"][0]["prerequisites"].append("combo-reading"),
            "COURSE_PREREQUISITE_CYCLE",
        ),
        (
            OUTLINE,
            lambda d: d["lessons"][0].update(material="../../../etc/passwd.md"),
            "COURSE_ILLEGAL_PATH",
        ),
        (
            OUTLINE,
            lambda d: d["lessons"][0].update(material="/abs/lessons/x.md"),
            "COURSE_ILLEGAL_PATH",
        ),
        (
            OUTLINE,
            lambda d: d["lessons"][0].update(material="lessons/colors.html"),
            "COURSE_ILLEGAL_PATH",
        ),
        (OUTLINE, lambda d: d["lessons"][0].pop("objectives"), "COURSE_FIELD_INVALID"),
        (OUTLINE, lambda d: d["knowledge_points"][0].update(id="Bad ID"), "COURSE_FIELD_INVALID"),
        (
            OUTLINE,
            lambda d: d["knowledge_points"][0]["anchors"].append("nowhere"),
            "COURSE_DANGLING_REFERENCE",
        ),
        (
            OUTLINE,
            lambda d: d["lessons"][1]["knowledge_points"].append("color-meaning"),
            "COURSE_ORPHAN_KNOWLEDGE_POINT",
        ),
        (OUTLINE, lambda d: d["lessons"][1].update(order=1), "COURSE_DUPLICATE_ID"),
        (
            f"{COURSE}/1.0.0/manifest.json",
            lambda d: d.update(course_id="other"),
            "COURSE_ID_MISMATCH",
        ),
        (
            f"{COURSE}/1.0.0/manifest.json",
            lambda d: d.update(schema_version=99),
            "COURSE_SCHEMA_VERSION_UNSUPPORTED",
        ),
        (f"{COURSE}/1.0.0/manifest.json", lambda d: d.pop("review"), "COURSE_REVIEW_MISSING"),
        (
            f"{COURSE}/1.0.0/sources.json",
            lambda d: d["sources"][1].update(url="", locator=""),
            "COURSE_FIELD_INVALID",
        ),
        (
            f"{COURSE}/1.0.0/questions.json",
            lambda d: d["questions"][0]["answer"].update(choices=["Z"]),
            "COURSE_QUESTION_INVALID",
        ),
        (
            f"{COURSE}/1.0.0/questions.json",
            lambda d: d["questions"][0]["answer"].update(choices=["A", "B"]),
            "COURSE_QUESTION_INVALID",
        ),
        (
            f"{COURSE}/1.0.0/questions.json",
            lambda d: d["questions"][4].pop("rubric"),
            "COURSE_QUESTION_INVALID",
        ),
        (
            f"{COURSE}/1.0.0/questions.json",
            lambda d: d["questions"][0].update(knowledge_point="combo-reading"),
            "COURSE_DANGLING_REFERENCE",
        ),
        (
            f"{COURSE}/course.json",
            lambda d: d["versions"].update({"2.0.0": "draft"}),
            "COURSE_FILE_MISSING",
        ),
    ],
)
def test_broken_packages_are_reported(catalog: Path, relative, mutate, expected):
    edit_json(catalog / relative, mutate)
    assert expected in codes(catalog)


def test_missing_file_and_invalid_json(catalog: Path):
    (catalog / COURSE / "1.0.0" / "sources.json").unlink()
    assert "COURSE_FILE_MISSING" in codes(catalog)
    (catalog / COURSE / "1.0.0" / "sources.json").write_text("{not json", encoding="utf-8")
    assert "COURSE_FILE_INVALID_JSON" in codes(catalog)


def test_symlinked_material_is_rejected(catalog: Path, tmp_path: Path):
    outside = tmp_path / "secret.md"
    outside.write_text("# secret", encoding="utf-8")
    lesson = catalog / COURSE / "1.0.0" / "lessons" / "colors.md"
    lesson.unlink()
    lesson.symlink_to(outside)
    assert "COURSE_ILLEGAL_PATH" in codes(catalog)


def test_published_version_cannot_be_edited(catalog: Path):
    lesson = catalog / COURSE / "1.0.0" / "lessons" / "colors.md"
    lesson.write_text(lesson.read_text(encoding="utf-8") + "\n改了一个字。\n", encoding="utf-8")
    assert "COURSE_VERSION_MODIFIED" in codes(catalog)


def test_unlocked_published_version(catalog: Path):
    (catalog / "courses.lock.json").unlink()
    assert "COURSE_VERSION_UNLOCKED" in codes(catalog)


@pytest.mark.parametrize(
    "line",
    [
        "<script>alert(1)</script>",
        '<img src=x onerror="alert(1)">',
        "<!-- comment -->",
        "import Foo from './foo'",
        "export const x = 1",
        "正文 {danger()} 表达式",
        "[点我](javascript:alert(1))",
        "![图](lessons/a.png)",
        "## 标题 {#Bad Anchor}",
    ],
)
def test_unsafe_markdown(line: str):
    assert not check_markdown(f"# 标题\n\n{line}\n").ok


def test_safe_markdown_and_anchors():
    text = (
        "# 标题 {#top}\n\n段落 **强调** [链接](https://example.com) [本页](#top)\n\n"
        "```html\n<div>代码块里可以写</div>\n```\n\n行内 `<b>` 也可以，`{1,2}` 也行\n\n## 二级 {#second}\n"
    )
    result = check_markdown(text)
    assert result.ok, result.problems
    assert result.anchors == ["top", "second"]


def test_one_broken_course_does_not_hide_others(catalog: Path):
    import shutil

    shutil.copytree(catalog / COURSE, catalog / "broken-course")
    edit_json(
        catalog / "broken-course" / "course.json", lambda d: d.update(course_id="broken-course")
    )
    edit_json(catalog / "broken-course" / "1.0.0" / "outline.json", lambda d: d["lessons"].clear())
    courses, report = scan_catalog(catalog)
    assert not report.ok
    assert COURSE in courses and courses[COURSE].versions
    assert not courses["broken-course"].versions
