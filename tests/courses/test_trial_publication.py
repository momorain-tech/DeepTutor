"""公开试读必须诚实保留未审校状态，不能削弱正式课程的审核门槛。"""
from pathlib import Path

from deeptutor.learning.courses.package import check_lock, scan_catalog, write_lockfile
from deeptutor.learning.courses.repository import CourseRepository
from .conftest import COURSE, edit_json


def make_trial(catalog: Path, notice: str = "试读版，尚未经过专业医学审校"):
    edit_json(catalog / COURSE / "1.0.0" / "manifest.json",
              lambda d: d.update(release_stage="trial", trial_notice=notice, review=None))


def test_trial_can_be_published_without_fabricating_review(catalog: Path):
    make_trial(catalog)
    # 模拟首次发布的新内容，不覆盖任何已有发布哈希。
    (catalog / "courses.lock.json").unlink()
    courses, report = scan_catalog(catalog)
    expected = check_lock(catalog, courses, report)
    assert {i.code.value for i in report.errors} == {"COURSE_VERSION_UNLOCKED"}
    write_lockfile(catalog, expected)
    repo = CourseRepository(catalog)
    detail = repo.course_detail(COURSE)
    assert detail.release_stage == "trial" and detail.review is None
    assert "尚未" in detail.trial_notice
    assert all(c.release_stage == "trial" for c in repo.list_courses())
    preview = repo.preview(COURSE)
    assert all(c.release_stage == "trial" and c.trial_notice for c in preview.lessons)
    assert detail.model_dump(mode="json")["review"] is None


def test_trial_notice_is_required(catalog: Path):
    make_trial(catalog, "  ")
    _, report = scan_catalog(catalog)
    assert any(i.code.value == "COURSE_FIELD_INVALID" and "trial_notice" in i.message for i in report.errors)


def test_reviewed_publication_still_requires_real_review(catalog: Path):
    edit_json(catalog / COURSE / "1.0.0" / "manifest.json", lambda d: d.update(review=None))
    _, report = scan_catalog(catalog)
    assert any(i.code.value == "COURSE_REVIEW_MISSING" for i in report.errors)
