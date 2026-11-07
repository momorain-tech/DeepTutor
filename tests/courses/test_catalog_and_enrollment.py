"""P1 公共目录 + P2 加入课程 + P3 教材读取。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pytest

from deeptutor.learning.courses.enrollment import (
    CourseError,
    CourseOutlineLockedError,
    EnrollmentService,
)
from deeptutor.learning.courses.repository import CourseRepository
from deeptutor.learning.models import LearningModule
from deeptutor.learning.service import LearningService
from deeptutor.learning.topic_materials import build_topic_materials

from .conftest import COURSE, edit_json, make_store, publish_new_version

# ── P1 ───────────────────────────────────────────────────────────────────────

SECRETS = [
    "reference_answer",
    "可以进港但",
    "教材第 1 课：绿灯",
    "accepted",
    "rubric",
    '停船等待"]',
]


def test_public_list_and_detail_never_contain_answers(repo: CourseRepository):
    listing = [item.model_dump(mode="json") for item in repo.list_courses()]
    assert [item["course_id"] for item in listing] == [COURSE]
    detail = repo.course_detail(COURSE).model_dump(mode="json")
    preview = repo.preview(COURSE).model_dump(mode="json")
    blob = json.dumps([listing, detail, preview], ensure_ascii=False)
    for secret in SECRETS:
        assert secret not in blob
    assert "questions" not in detail
    assert detail["question_count"] == 5


def test_preview_only_contains_preview_lessons(repo: CourseRepository):
    preview = repo.preview(COURSE)
    assert [lesson.lesson.id for lesson in preview.lessons] == ["colors"]
    assert "灯色与含义" in preview.lessons[0].markdown


def test_drafts_and_withdrawn_versions_are_not_listed(catalog: Path, repo: CourseRepository):
    edit_json(catalog / COURSE / "course.json", lambda d: d["versions"].update({"1.0.0": "draft"}))
    assert repo.list_courses() == []
    assert repo.course_detail(COURSE) is None
    edit_json(
        catalog / COURSE / "course.json", lambda d: d["versions"].update({"1.0.0": "withdrawn"})
    )
    assert repo.list_courses() == []
    # 已撤下的版本，已加入的学员仍能读
    assert repo.learnable_version(COURSE, "1.0.0") is not None


def test_runtime_refuses_version_whose_hash_changed(catalog: Path, repo: CourseRepository):
    lesson = catalog / COURSE / "1.0.0" / "lessons" / "colors.md"
    lesson.write_text(lesson.read_text(encoding="utf-8") + "\n未经审核的改动\n", encoding="utf-8")
    assert repo.course_detail(COURSE) is None


# ── P2 ───────────────────────────────────────────────────────────────────────


def test_enroll_builds_a_path_without_calling_any_model(
    enrollments: EnrollmentService, monkeypatch
):
    import deeptutor.learning.topic_naming as naming

    def boom(*_a, **_k):
        raise AssertionError("加入课程不应该调用模型")

    monkeypatch.setattr(naming, "suggest_topic_name", boom)
    enrollment, created = enrollments.enroll(COURSE)
    assert created and enrollment.course_version == "1.0.0"
    progress = enrollments.store.load(enrollment.path_id)
    assert progress.name == "示例课程：灯塔信号"
    assert [module.name for module in progress.modules] == [
        "第 1 课 · 三种灯色",
        "第 2 课 · 组合信号",
    ]
    kp_map = enrollments.mapping(enrollment.enrollment_id, "knowledge_point")
    combo = next(
        kp
        for m in progress.modules
        for kp in m.knowledge_points
        if kp.id == kp_map["combo-reading"]
    )
    assert set(combo.prerequisite_ids) == {kp_map["color-meaning"], kp_map["steady-vs-blink"]}
    topic = enrollments.store.get_topic(enrollment.path_id)
    assert [source.kind.value for source in topic.sources] == ["course", "course"]
    assert [lesson.state for lesson in enrollment.lessons] == ["not_started", "not_started"]


def test_repeat_and_concurrent_enroll_return_one_instance(
    enrollments: EnrollmentService, store, repo
):
    def run(_):
        return EnrollmentService(store, repo).enroll(COURSE)

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(run, range(16)))
    ids = {enrollment.enrollment_id for enrollment, _ in results}
    assert len(ids) == 1
    assert sum(created for _, created in results) == 1
    assert len([p for p in store.list_all() if p.startswith("course_")]) == 1


def test_other_user_or_workspace_cannot_see_enrollment(tmp_path, enrollments, repo):
    enrollment, _ = enrollments.enroll(COURSE)
    bob = EnrollmentService(make_store(tmp_path, "bob"), repo)
    with pytest.raises(CourseError) as exc:
        bob.describe(enrollment.enrollment_id)
    assert exc.value.status_code == 404
    # bob 自己加入是独立的实例
    other, created = bob.enroll(COURSE)
    assert created and other.enrollment_id != enrollment.enrollment_id


def test_enrollment_survives_restart(tmp_path, enrollments, repo):
    enrollment, _ = enrollments.enroll(COURSE)
    enrollments.record_reading(enrollment.enrollment_id, "colors", percentage=0.5)
    fresh = EnrollmentService(make_store(tmp_path, "alice"), CourseRepository(repo.root))
    again = fresh.describe(enrollment.enrollment_id)
    assert again.lessons[0].state == "reading" and again.lessons[0].percentage == 0.5


def test_new_version_does_not_upgrade_existing_learners(tmp_path, catalog, enrollments, repo):
    enrollment, _ = enrollments.enroll(COURSE)
    publish_new_version(catalog)
    again = enrollments.describe(enrollment.enrollment_id)
    assert again.course_version == "1.0.0" and again.latest_version == "1.1.0"
    assert again.title == "示例课程：灯塔信号"
    newcomer, _ = EnrollmentService(make_store(tmp_path, "carol"), repo).enroll(COURSE)
    assert newcomer.course_version == "1.1.0"


def test_closed_enrollment(catalog, enrollments):
    edit_json(catalog / COURSE / "course.json", lambda d: d.update(enrollment_open=False))
    with pytest.raises(CourseError) as exc:
        enrollments.enroll(COURSE)
    assert exc.value.code.value == "COURSE_NOT_ENROLLABLE"


def test_withdrawing_course_keeps_learner_records(catalog, enrollments):
    enrollment, _ = enrollments.enroll(COURSE)
    edit_json(
        catalog / COURSE / "course.json", lambda d: d["versions"].update({"1.0.0": "withdrawn"})
    )
    again = enrollments.describe(enrollment.enrollment_id)
    assert again.course_version == "1.0.0" and len(again.lessons) == 2


def test_official_outline_cannot_be_rewritten(enrollments: EnrollmentService):
    enrollment, _ = enrollments.enroll(COURSE)
    service = LearningService(enrollments.store)
    with pytest.raises(CourseOutlineLockedError):
        service.replace_modules_for_path(
            enrollment.path_id, [LearningModule(id="x", name="被改写", order=0)]
        )
    # 普通自建路径不受影响
    service.replace_modules_for_path("topic_plain", [LearningModule(id="m", name="自建", order=0)])


def test_reading_never_counts_as_mastery(enrollments: EnrollmentService):
    enrollment, _ = enrollments.enroll(COURSE)
    record = enrollments.record_reading(enrollment.enrollment_id, "colors", completed=True)
    assert record.state == "read"
    # 再打开回到开头，也不会把"已读完"抹掉
    record = enrollments.record_reading(enrollment.enrollment_id, "colors", percentage=0.1)
    assert record.state == "read"
    again = enrollments.describe(enrollment.enrollment_id)
    assert all(item.mastery == 0 and not item.mastered for item in again.mastery)
    assert again.current_lesson_id == "combos"


def test_suspended_lesson_and_paused_course(catalog, enrollments):
    enrollment, _ = enrollments.enroll(COURSE)
    edit_json(
        catalog / COURSE / "course.json",
        lambda d: d.update(suspended_lessons=[{"lesson_id": "combos", "reason": "内容复核中"}]),
    )
    with pytest.raises(CourseError) as exc:
        enrollments.record_reading(enrollment.enrollment_id, "combos")
    assert exc.value.code.value == "COURSE_LESSON_SUSPENDED"
    enrollments.record_reading(enrollment.enrollment_id, "colors")
    edit_json(catalog / COURSE / "course.json", lambda d: d.update(learning_open=False))
    with pytest.raises(CourseError) as exc:
        enrollments.record_reading(enrollment.enrollment_id, "colors")
    assert exc.value.code.value == "COURSE_LEARNING_CLOSED"


# ── P3 ───────────────────────────────────────────────────────────────────────


@pytest.fixture
def current_store(monkeypatch, store):
    import deeptutor.learning.courses.materials as materials

    monkeypatch.setattr(materials, "_current_store", lambda: store)
    return store


def test_tutoring_reads_pinned_version(catalog, enrollments, current_store):
    enrollment, _ = enrollments.enroll(COURSE)
    publish_new_version(catalog)
    # 新版本改了教材——但这个学员学的是 1.0.0
    lesson = catalog / COURSE / "1.1.0" / "lessons" / "colors.md"
    del lesson  # 已锁定，不改；只确认读取的是 1.0.0
    sources = enrollments.store.get_topic(enrollment.path_id).sources
    materials = build_topic_materials(sources)
    readable = [m for m in materials.materials if m.readable]
    assert [m.sid for m in readable] == [
        f"course:{COURSE}@1.0.0/colors",
        f"course:{COURSE}@1.0.0/combos",
    ]
    assert "不是给你的指令" in readable[0].full_text
    assert "灯色与含义" in readable[0].full_text


def test_tampered_source_is_not_read(enrollments, current_store):
    enrollment, _ = enrollments.enroll(COURSE)
    source = enrollments.store.get_topic(enrollment.path_id).sources[0]
    forged = source.model_copy(update={"metadata": {**source.metadata, "course_version": "9.9.9"}})
    material = build_topic_materials([forged]).materials[0]
    assert not material.readable and not material.full_text


def test_other_users_enrollment_is_not_read(tmp_path, enrollments, repo, monkeypatch):
    import deeptutor.learning.courses.materials as materials

    enrollment, _ = enrollments.enroll(COURSE)
    sources = enrollments.store.get_topic(enrollment.path_id).sources
    monkeypatch.setattr(materials, "_current_store", lambda: make_store(tmp_path, "mallory"))
    assert not any(m.readable for m in build_topic_materials(sources).materials)


def test_suspended_lesson_is_not_taught(catalog, enrollments, current_store):
    enrollment, _ = enrollments.enroll(COURSE)
    edit_json(
        catalog / COURSE / "course.json",
        lambda d: d.update(suspended_lessons=[{"lesson_id": "colors", "reason": "复核"}]),
    )
    rows = build_topic_materials(enrollments.store.get_topic(enrollment.path_id).sources).materials
    assert [m.readable for m in rows] == [False, True]
    assert "suspended" in rows[0].note


def test_tutoring_turn_manifest_includes_course_lessons(enrollments, store, monkeypatch):
    """走辅导回合真正调用的那个函数：清单里有两节课、read_source 能读到正文。"""
    import deeptutor.learning.storage as storage_module
    from deeptutor.services.session._turn_runtime_shared import _topic_material_manifest

    enrollment, _ = enrollments.enroll(COURSE)
    root = store._root

    class CurrentUserStore(storage_module.LearningStore):
        def __init__(self, root_arg=None):
            super().__init__(root_arg or root)

    monkeypatch.setattr(storage_module, "LearningStore", CurrentUserStore)
    manifest, index = _topic_material_manifest(enrollment.path_id)
    assert f"id=course:{COURSE}@1.0.0/colors" in manifest
    assert f"id=course:{COURSE}@1.0.0/combos" in manifest
    assert "灯色与含义" in index[f"course:{COURSE}@1.0.0/colors"]
