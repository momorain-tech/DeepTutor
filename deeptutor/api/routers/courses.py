"""官方课程接口。

两个路由：

- ``public_router``（/api/courses）：课程目录、详情、公开样课。只读部署级课程文件，
  不碰用户数据、不调用模型。访客请求不会走到这里——momorain 中间件在
  guest.py 里用同一批函数（:func:`public_courses` 等）单独作答。
- ``router``（/api/course-enrollments）：加入课程、读所学版本的教材、阅读进度、
  练习与提交。全部需要登录，数据存在当前用户、当前工作区的学习库里。

错误统一是 ``{"detail": {"code": "COURSE_...", "message": "..."}}``，前端按 code 分支。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from deeptutor.learning.courses.contract import CourseErrorCode, error_detail
from deeptutor.learning.courses.enrollment import CourseError, EnrollmentService
from deeptutor.learning.courses.repository import (
    public_course_detail,
    public_course_preview,
    public_courses,
)

public_router = APIRouter()
router = APIRouter()


def _raise(exc: CourseError) -> None:
    raise HTTPException(exc.status_code, error_detail(exc.code, exc.message)) from exc


def _not_found(message: str = "课程不存在或尚未发布") -> HTTPException:
    return HTTPException(404, error_detail(CourseErrorCode.COURSE_NOT_FOUND, message))


# ── 公开读取（访客白名单 momorain/guest.py 调用的是同一批函数） ─────────────────


@public_router.get("")
async def list_courses():
    return await asyncio.to_thread(public_courses)


@public_router.get("/{course_id}")
async def get_course(course_id: str):
    payload = await asyncio.to_thread(public_course_detail, course_id)
    if payload is None:
        raise _not_found()
    return payload


@public_router.get("/{course_id}/preview")
async def get_course_preview(course_id: str):
    payload = await asyncio.to_thread(public_course_preview, course_id)
    if payload is None:
        raise _not_found()
    return payload


# ── 个人选课 ──────────────────────────────────────────────────────────────────


class EnrollRequest(BaseModel):
    course_id: str = Field(min_length=1, max_length=64)


class ReadingProgressRequest(BaseModel):
    anchor: str = Field(default="", max_length=64)
    percentage: float = Field(default=0.0, ge=0.0, le=1.0)
    completed: bool = False


class SubmissionRequest(BaseModel):
    #: 前端为每次作答生成（如 crypto.randomUUID()），重试时带同一个。
    submission_id: str = Field(min_length=8, max_length=80)
    answer: Any = None


def _service() -> EnrollmentService:
    # 每个请求新建：LearningStore 绑定当前请求的用户和工作区。
    return EnrollmentService()


async def _call(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except CourseError as exc:
        _raise(exc)


@router.get("")
async def list_enrollments():
    def run():
        service = _service()
        return {
            "enrollments": [
                service.describe(row["enrollment_id"]).model_dump(mode="json")
                for row in service.list_active()
            ]
        }

    return await _call(run)


@router.post("")
async def enroll(body: EnrollRequest, response: Response):
    """加入课程。重复调用返回已有实例（200），新建返回 201。不调用模型、不收费。"""

    def run():
        enrollment, created = _service().enroll(body.course_id.strip())
        return enrollment, created

    enrollment, created = await _call(run)
    response.status_code = 201 if created else 200
    return {"enrollment": enrollment.model_dump(mode="json"), "created": created}


@router.get("/{enrollment_id}")
async def get_enrollment(enrollment_id: str):
    def run():
        from deeptutor.learning.courses.practice import PracticeService

        service = _service()
        PracticeService(service).apply_errata(enrollment_id)
        return service.describe(enrollment_id).model_dump(mode="json")

    return await _call(run)


@router.get("/{enrollment_id}/course")
async def get_enrollment_course(enrollment_id: str):
    """所学版本的课程目录（可能不是最新版）。"""

    def run():
        service = _service()
        row = service._get_row(enrollment_id)
        package = service.package_for(row)
        course = service.repository.course(row["course_id"])
        return service.repository.version_detail(course, package).model_dump(mode="json")

    return await _call(run)


@router.get("/{enrollment_id}/lessons/{lesson_id}")
async def get_lesson(enrollment_id: str, lesson_id: str):
    """读教材正文。只是阅读：不启动辅导、不调用模型、不写掌握证据。"""

    def run():
        service = _service()
        row = service._get_row(enrollment_id)
        package = service.assert_learnable(row, lesson_id)
        lesson = package.lesson(lesson_id)
        content = service.repository.lesson_content(package, lesson)
        progress = service.lesson_progress(enrollment_id).get(lesson_id)
        ordered = [item.id for item in package.ordered_lessons()]
        index = ordered.index(lesson_id)
        return {
            "content": content.model_dump(mode="json"),
            "progress": progress.model_dump(mode="json") if progress else None,
            "previous_lesson_id": ordered[index - 1] if index > 0 else None,
            "next_lesson_id": ordered[index + 1] if index + 1 < len(ordered) else None,
            "module_id": service.mapping(enrollment_id, "lesson").get(lesson_id, ""),
            "path_id": row["path_id"],
        }

    return await _call(run)


@router.put("/{enrollment_id}/lessons/{lesson_id}/progress")
async def put_reading_progress(enrollment_id: str, lesson_id: str, body: ReadingProgressRequest):
    def run():
        return (
            _service()
            .record_reading(
                enrollment_id,
                lesson_id,
                anchor=body.anchor,
                percentage=body.percentage,
                completed=body.completed,
            )
            .model_dump(mode="json")
        )

    return await _call(run)


@router.get("/{enrollment_id}/lessons/{lesson_id}/questions")
async def list_lesson_questions(enrollment_id: str, lesson_id: str):
    def run():
        from deeptutor.learning.courses.practice import PracticeService

        questions = PracticeService(_service()).list_questions(enrollment_id, lesson_id)
        return {"questions": [item.model_dump(mode="json") for item in questions]}

    return await _call(run)


@router.post("/{enrollment_id}/questions/{question_id}/submissions")
async def submit_answer(enrollment_id: str, question_id: str, body: SubmissionRequest):
    """提交答案。客观题立即判分；开放题这一步才调用模型（按用户计费）。
    同一个 submission_id 重试只计一次分、只调一次模型。"""
    from deeptutor.learning.courses.practice import PracticeService

    try:
        service = await asyncio.to_thread(_service)
        result = await PracticeService(service).submit(
            enrollment_id, question_id, body.submission_id, body.answer
        )
    except CourseError as exc:
        _raise(exc)
    return result.model_dump(mode="json")
