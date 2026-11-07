"""P4：题库出题、判分、去重、开放题、撤题纠错。"""

from __future__ import annotations

import asyncio
import json

import pytest

from deeptutor.learning.courses.enrollment import CourseError
import deeptutor.learning.courses.practice as practice_module
from deeptutor.learning.courses.practice import PracticeService, attempt_question_id

from .conftest import COURSE, edit_json


@pytest.fixture
def setup(enrollments):
    enrollment, _ = enrollments.enroll(COURSE)
    return PracticeService(enrollments), enrollment


def run(coro):
    return asyncio.run(coro)


def kp_mastery(service: PracticeService, enrollment, course_kp: str) -> float:
    described = service.enrollments.describe(enrollment.enrollment_id)
    return next(item.mastery for item in described.mastery if item.knowledge_point_id == course_kp)


def attempts(service: PracticeService, enrollment, *, include_voided: bool = False):
    progress = service.store.load(enrollment.path_id)
    return [a for a in progress.quiz_attempts if include_voided or not a.voided]


def test_questions_have_no_answers(setup):
    service, enrollment = setup
    questions = service.list_questions(enrollment.enrollment_id, "colors")
    assert [q.question_id for q in questions] == ["q-green", "q-blink", "q-red-word"]
    blob = json.dumps([q.model_dump() for q in questions], ensure_ascii=False)
    for secret in ["answer", "explanation", "rubric", "教材第 1 课", "accepted", "choices"]:
        assert secret not in blob


def test_objective_grading_updates_mastery_once(setup):
    service, enrollment = setup
    first = run(service.submit(enrollment.enrollment_id, "q-green", "sub-00000001", "A"))
    assert first.status == "graded" and first.result == "correct" and not first.replayed
    assert first.correct_answer == ["A"] and first.explanation
    assert kp_mastery(service, enrollment, "color-meaning") > 0
    again = run(service.submit(enrollment.enrollment_id, "q-green", "sub-00000001", "A"))
    assert again.replayed and again.result == "correct"
    assert len(attempts(service, enrollment)) == 1
    assert attempts(service, enrollment)[0].question_id == attempt_question_id("q-green", 1)


def test_same_submission_with_different_answer_conflicts(setup):
    service, enrollment = setup
    run(service.submit(enrollment.enrollment_id, "q-green", "sub-00000002", "A"))
    with pytest.raises(CourseError) as exc:
        run(service.submit(enrollment.enrollment_id, "q-green", "sub-00000002", "B"))
    assert exc.value.code.value == "COURSE_SUBMISSION_CONFLICT"


def test_answer_formats(setup):
    service, enrollment = setup
    eid = enrollment.enrollment_id
    assert run(service.submit(eid, "q-blink", "sub-tf-00001", False)).result == "correct"
    assert run(service.submit(eid, "q-red-word", "sub-fb-00001", " 停船！")).result == "correct"
    assert run(service.submit(eid, "q-combo-multi", "sub-mc-00001", ["C", "A"])).result == "correct"
    assert run(service.submit(eid, "q-combo-multi", "sub-mc-00002", ["A"])).result == "incorrect"
    with pytest.raises(CourseError):
        run(service.submit(eid, "q-green", "sub-bad-0001", "Z"))
    with pytest.raises(CourseError):
        run(service.submit(eid, "q-blink", "sub-bad-0002", "false"))


def test_wrong_answer_creates_error_record_and_review(setup):
    service, enrollment = setup
    result = run(service.submit(enrollment.enrollment_id, "q-green", "sub-00000003", "B"))
    assert result.result == "incorrect"
    progress = service.store.load(enrollment.path_id)
    assert progress.error_records and progress.error_records[0].question_id == "course:q-green@v1"


def test_open_question_calls_model_once_across_retries(setup, monkeypatch):
    service, enrollment = setup
    calls = []

    async def fake_judge(question, answer, lesson_text):
        calls.append(answer)
        await asyncio.sleep(0.05)
        return {
            "result": "partial",
            "feedback": "读法对了，含义不全",
            "criteria": [{"id": "order", "met": True}, {"id": "meaning", "met": False}],
        }

    monkeypatch.setattr(practice_module, "judge_open_answer", fake_judge)

    async def concurrent():
        return await asyncio.gather(
            *[
                service.submit(
                    enrollment.enrollment_id, "q-combo-open", "sub-open-0001", "先看闪烁的"
                )
                for _ in range(4)
            ]
        )

    results = run(concurrent())
    assert len(calls) == 1
    assert sorted(r.status for r in results).count("graded") >= 1
    final = run(
        service.submit(enrollment.enrollment_id, "q-combo-open", "sub-open-0001", "先看闪烁的")
    )
    assert final.status == "graded" and final.result == "partial" and final.replayed
    assert len(attempts(service, enrollment)) == 1


def test_open_question_model_failure_does_not_score(setup, monkeypatch):
    service, enrollment = setup

    async def failing(*_a):
        raise CourseError(
            practice_module.CourseErrorCode.SUBMISSION_CONFLICT, "评分暂时不可用", 503
        )

    monkeypatch.setattr(practice_module, "judge_open_answer", failing)
    with pytest.raises(CourseError):
        run(service.submit(enrollment.enrollment_id, "q-combo-open", "sub-open-0002", "答案"))
    assert attempts(service, enrollment) == []

    async def works(*_a):
        return {"result": "correct", "feedback": "", "criteria": []}

    monkeypatch.setattr(practice_module, "judge_open_answer", works)
    retried = run(service.submit(enrollment.enrollment_id, "q-combo-open", "sub-open-0002", "答案"))
    assert retried.result == "correct" and len(attempts(service, enrollment)) == 1


def test_errata_voids_attempts_and_recomputes(catalog, setup):
    service, enrollment = setup
    eid = enrollment.enrollment_id
    run(service.submit(eid, "q-green", "sub-00000010", "A"))
    run(service.submit(eid, "q-red-word", "sub-00000011", "停船"))
    before = kp_mastery(service, enrollment, "color-meaning")
    assert before > 0
    edit_json(
        catalog / COURSE / "course.json",
        lambda d: d.update(
            errata=[{"question_id": "q-green", "reason": "答案依据有误", "issued_at": "2026-11-08"}]
        ),
    )
    voided = service.apply_errata(eid)
    assert voided == 1
    assert service.apply_errata(eid) == 0  # 每条勘误只执行一次
    remaining = attempts(service, enrollment)
    assert [a.question_id for a in remaining] == ["course:q-red-word@v1"]
    assert all(
        a.void_reason.startswith("课程撤题")
        for a in attempts(service, enrollment, include_voided=True)
        if a.voided
    )
    progress = service.store.load(enrollment.path_id)
    assert not any(e.question_id == "course:q-green@v1" for e in progress.learning_evidence)
    # 撤下的题不再出现、不能再提交
    assert "q-green" not in [q.question_id for q in service.list_questions(eid, "colors")]
    with pytest.raises(CourseError) as exc:
        run(service.submit(eid, "q-green", "sub-00000012", "A"))
    assert exc.value.code.value == "COURSE_QUESTION_RETIRED"


def test_paused_course_rejects_submissions(catalog, setup):
    service, enrollment = setup
    edit_json(catalog / COURSE / "course.json", lambda d: d.update(learning_open=False))
    with pytest.raises(CourseError) as exc:
        run(service.submit(enrollment.enrollment_id, "q-green", "sub-00000020", "A"))
    assert exc.value.code.value == "COURSE_LEARNING_CLOSED"
