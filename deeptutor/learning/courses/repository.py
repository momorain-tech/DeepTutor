"""课程目录（P1）：扫描课程包、缓存、把内部结构筛成公开字段。

**这里的每个函数都只读部署级的课程文件**，不读任何用户目录、不调用模型、
不建向量——所以访客白名单（momorain/guest.py）可以直接调用它们作答，
不必进入会回退到本地管理员身份的上游路由。

公开字段是"白名单式"投影：响应结构在 contract.py 里逐字段定义，
题目答案、评分规则、解析、草稿版本根本没有对应字段，想漏也漏不出去。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import threading

from deeptutor.learning.courses.contract import (
    CourseDetail,
    CoursePreview,
    CourseSource,
    CourseSummary,
    LessonContent,
    OutlineLesson,
    PublicKnowledgePoint,
    PublicLesson,
    PublicPerson,
    PublicReview,
    PublicSource,
    PublicUnit,
    QuestionErratum,
    VersionStatus,
)
from deeptutor.learning.courses.package import (
    CoursePackage,
    CourseVersionPackage,
    ValidationReport,
    read_lockfile,
    scan_catalog,
)

logger = logging.getLogger(__name__)

_BUILTIN_CATALOG = Path(__file__).with_name("catalog")


def default_catalog_root() -> Path:
    """线上课程目录。开发联调可以用环境变量指到模拟课程：

    DEEPTUTOR_COURSES_DIR=tests/fixtures/courses/catalog
    """
    configured = os.environ.get("DEEPTUTOR_COURSES_DIR", "").strip()
    return Path(configured).expanduser() if configured else _BUILTIN_CATALOG


class CourseRepository:
    """进程内共享的只读课程目录。文件有变化（mtime/大小）时自动重新扫描。"""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root
        self._lock = threading.Lock()
        self._fingerprint: tuple | None = None
        self._courses: dict[str, CoursePackage] = {}
        self._report = ValidationReport()

    @property
    def root(self) -> Path:
        return self._root or default_catalog_root()

    def _current_fingerprint(self, root: Path) -> tuple:
        if not root.is_dir():
            return (str(root),)
        rows = []
        for path in root.rglob("*"):
            try:
                stat = path.stat()
            except OSError:
                continue
            rows.append((path.as_posix(), stat.st_mtime_ns, stat.st_size))
        return (str(root), tuple(sorted(rows)))

    def _refresh(self) -> None:
        root = self.root
        fingerprint = self._current_fingerprint(root)
        if fingerprint == self._fingerprint:
            return
        with self._lock:
            if fingerprint == self._fingerprint:
                return
            courses, report = scan_catalog(root)
            # 运行时也守住"已发布版本不可改"：锁文件里登记过、但哈希对不上的版本
            # 直接不提供——宁可这一版暂时打不开，也不能让学员读到没审核过的改动。
            locked = read_lockfile(root)
            for course in courses.values():
                for version in list(course.versions):
                    key = f"{course.course_id}@{version}"
                    expected = locked.get(key)
                    if expected and expected != course.versions[version].content_hash:
                        logger.error("课程版本 %s 与锁文件哈希不符，已停止提供", key)
                        del course.versions[version]
            for issue in report.errors:
                logger.warning("课程包问题 %s: %s (%s)", issue.where, issue.message, issue.code.value)
            self._courses = courses
            self._report = report
            self._fingerprint = fingerprint

    # ── 内部读取（给 P2/P3/P4 用，不直接返回给前端） ──────────────────────────

    def course(self, course_id: str) -> CoursePackage | None:
        self._refresh()
        return self._courses.get(course_id)

    def published(self, course_id: str) -> CourseVersionPackage | None:
        """目录里展示、新加入时使用的版本：最新的已发布版本。"""
        course = self.course(course_id)
        return course.latest_published() if course else None

    def learnable_version(self, course_id: str, version: str) -> CourseVersionPackage | None:
        """已加入的学员读取自己所学的版本：已发布和已撤下的都能读，草稿永远读不到。"""
        course = self.course(course_id)
        if course is None:
            return None
        status = course.status(version)
        if status not in {VersionStatus.PUBLISHED, VersionStatus.WITHDRAWN}:
            return None
        return course.versions.get(version)

    def lesson_suspension(self, course_id: str, version: str, lesson_id: str) -> str | None:
        course = self.course(course_id)
        if course is None:
            return None
        for item in course.record.suspended_lessons:
            if item.lesson_id == lesson_id and (not item.versions or version in item.versions):
                return item.reason
        return None

    def errata(self, course_id: str, version: str) -> list[QuestionErratum]:
        course = self.course(course_id)
        if course is None:
            return []
        return [
            item
            for item in course.record.errata
            if not item.course_versions or version in item.course_versions
        ]

    def retired(
        self, course_id: str, version: str, question_id: str, question_version: int
    ) -> QuestionErratum | None:
        for item in self.errata(course_id, version):
            if item.question_id == question_id and item.question_version in {
                None,
                question_version,
            }:
                return item
        return None

    def report(self) -> ValidationReport:
        self._refresh()
        return self._report

    # ── 公开投影 ──────────────────────────────────────────────────────────────

    def list_courses(self) -> list[CourseSummary]:
        self._refresh()
        summaries = []
        for course in self._courses.values():
            package = course.latest_published()
            if package is not None:
                summaries.append(self._summary(course, package))
        return sorted(summaries, key=lambda item: item.title)

    def course_detail(self, course_id: str) -> CourseDetail | None:
        course = self.course(course_id)
        package = course.latest_published() if course else None
        if course is None or package is None:
            return None
        return self.version_detail(course, package)

    def version_detail(self, course: CoursePackage, package: CourseVersionPackage) -> CourseDetail:
        summary = self._summary(course, package)
        return CourseDetail(
            **summary.model_dump(),
            disclaimer=package.manifest.disclaimer,
            changelog=package.manifest.changelog,
            units=[
                PublicUnit(id=unit.id, title=unit.title, lessons=list(unit.lessons))
                for unit in package.outline.units
            ],
            lessons=[self.public_lesson(package, lesson) for lesson in package.ordered_lessons()],
            sources=[public_source(source) for source in package.sources],
            learning_open=course.record.learning_open,
        )

    def preview(self, course_id: str) -> CoursePreview | None:
        """公开样课：只有 outline 里标了 preview 的课时，访客不登录也能读全文。"""
        package = self.published(course_id)
        if package is None:
            return None
        lessons = [
            self.lesson_content(package, lesson)
            for lesson in package.ordered_lessons()
            if lesson.preview
            and self.lesson_suspension(course_id, package.version, lesson.id) is None
        ]
        return CoursePreview(course_id=course_id, version=package.version, lessons=lessons)

    def public_lesson(self, package: CourseVersionPackage, lesson: OutlineLesson) -> PublicLesson:
        reason = self.lesson_suspension(package.course_id, package.version, lesson.id)
        return PublicLesson(
            id=lesson.id,
            title=lesson.title,
            order=lesson.order,
            objectives=list(lesson.objectives),
            preview=lesson.preview,
            estimated_minutes=lesson.estimated_minutes,
            knowledge_points=[
                PublicKnowledgePoint(id=kp.id, name=kp.name, type=kp.type)
                for kp_id in lesson.knowledge_points
                if (kp := package.knowledge_point(kp_id)) is not None
            ],
            suspended=reason is not None,
            suspended_reason=reason or "",
        )

    def lesson_content(self, package: CourseVersionPackage, lesson: OutlineLesson) -> LessonContent:
        source_ids: list[str] = []
        for kp_id in lesson.knowledge_points:
            kp = package.knowledge_point(kp_id)
            for source_id in kp.sources if kp else []:
                if source_id not in source_ids:
                    source_ids.append(source_id)
        return LessonContent(
            course_id=package.course_id,
            version=package.version,
            lesson=self.public_lesson(package, lesson),
            markdown=package.lessons_markdown.get(lesson.id, ""),
            anchors=list(package.lesson_anchors.get(lesson.id, [])),
            sources=[
                public_source(source)
                for source_id in source_ids
                if (source := package.source(source_id)) is not None
            ],
        )

    def _summary(self, course: CoursePackage, package: CourseVersionPackage) -> CourseSummary:
        manifest = package.manifest
        return CourseSummary(
            course_id=course.course_id,
            version=package.version,
            title=manifest.title,
            subtitle=manifest.subtitle,
            summary=manifest.summary,
            language=manifest.language,
            tags=list(manifest.tags),
            editors=[PublicPerson(**person.model_dump()) for person in manifest.editors],
            review=PublicReview(
                reviewers=[PublicPerson(**person.model_dump()) for person in manifest.review.reviewers],
                reviewed_at=manifest.review.reviewed_at.isoformat(),
                note=manifest.review.note,
            )
            if manifest.review
            else None,
            updated_at=manifest.updated_at.isoformat(),
            lesson_count=len(package.outline.lessons),
            knowledge_point_count=len(package.outline.knowledge_points),
            question_count=len(package.questions),
            preview_lessons=[lesson.id for lesson in package.ordered_lessons() if lesson.preview],
            enrollment_open=course.record.enrollment_open and course.record.learning_open,
        )


def public_source(source: CourseSource) -> PublicSource:
    return PublicSource(
        id=source.id,
        title=source.title,
        kind=source.kind,
        locator=source.locator,
        url=source.url,
        edition=source.edition,
        publisher=source.publisher,
        reviewed=source.review is not None,
    )


# ── 公开读取的响应体（路由和访客白名单共用同一份，保证访客看到的和登录用户一样） ──


def public_courses() -> dict:
    return {"courses": [item.model_dump(mode="json") for item in get_course_repository().list_courses()]}


def public_course_detail(course_id: str) -> dict | None:
    detail = get_course_repository().course_detail(course_id)
    return detail.model_dump(mode="json") if detail else None


def public_course_preview(course_id: str) -> dict | None:
    preview = get_course_repository().preview(course_id)
    return preview.model_dump(mode="json") if preview else None


_repository: CourseRepository | None = None
_repository_lock = threading.Lock()


def get_course_repository() -> CourseRepository:
    global _repository
    with _repository_lock:
        if _repository is None:
            _repository = CourseRepository()
        return _repository


def reset_course_repository(root: Path | None = None) -> CourseRepository:
    """测试用：换一个目录重新建仓库。"""
    global _repository
    with _repository_lock:
        _repository = CourseRepository(root)
        return _repository


__all__ = [
    "CourseRepository",
    "default_catalog_root",
    "get_course_repository",
    "public_course_detail",
    "public_course_preview",
    "public_courses",
    "public_source",
    "reset_course_repository",
]
