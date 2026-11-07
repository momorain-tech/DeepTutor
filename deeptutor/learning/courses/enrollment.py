"""加入课程与个人学习实例（P2）。

"加入"做的事：把官方课程的一个**固定版本**复制成当前用户、当前工作区里的一条
掌握路径（课时 → 模块，知识点 → 路径知识点），再记下"这条路径来自哪门课、
哪个版本、每个 ID 对应到哪里"。之后的辅导、练习、复习全部复用掌握路径现有的
引擎，课程这边不维护第二套分数。

几条硬规则，都在这里落实：

- **一次事务**：路径、知识点、前置关系、来源、实例记录、ID 映射写在同一个
  SQLite 事务里（和掌握路径同一个库），失败就全部回滚，不会留下"路径建了、
  实例没存"的半成品。
- **只有一个有效实例**：同一工作区同一门课只能有一个 active 实例，靠库里的
  唯一索引兜底。重复点击、并发请求、网络重试都返回已有实例。
- **不调用模型**：路径名直接用课程标题，绕开自动命名；不建向量。
- **版本固定**：实例记住加入时的版本；课程发新版不会自动升级。
- **大纲上锁**：官方课程的路径不能被普通编辑接口或 AI 改纲工具改写
  （见 :func:`assert_outline_editable`）。

隔离靠存储本身：LearningStore 的库文件按"用户 + 工作区"分开，别人的实例
根本不在你的库里，所以跨账号、跨工作区读取天然失败（返回"不存在"）。
"""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
import time
from typing import Any
import uuid

from deeptutor.learning.courses.contract import (
    CourseErrorCode,
    Enrollment,
    LessonProgressRecord,
)
from deeptutor.learning.courses.package import CourseVersionPackage
from deeptutor.learning.courses.repository import CourseRepository, get_course_repository
from deeptutor.learning.models import (
    KnowledgePoint,
    KnowledgeType,
    LearningModule,
    TopicMetadata,
    TopicSource,
    TopicSourceKind,
)
from deeptutor.learning.objective_relations import (
    ObjectiveRelationError,
    validate_objective_relations,
)
from deeptutor.learning.storage import LearningStore

_SCHEMA = """
CREATE TABLE IF NOT EXISTS course_enrollments (
    enrollment_id TEXT PRIMARY KEY,
    course_id TEXT NOT NULL,
    course_version TEXT NOT NULL,
    path_id TEXT NOT NULL UNIQUE REFERENCES mastery_paths(path_id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'active',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
-- 同一门课只能有一个 active 实例：并发加入时第二个事务会撞上这条索引。
CREATE UNIQUE INDEX IF NOT EXISTS idx_course_enrollments_one_active
    ON course_enrollments(course_id) WHERE status = 'active';

-- 课程 ID ↔ 个人路径 ID。kind 是 lesson（课时 ↔ 模块）或 knowledge_point。
CREATE TABLE IF NOT EXISTS course_enrollment_map (
    enrollment_id TEXT NOT NULL REFERENCES course_enrollments(enrollment_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    course_ref TEXT NOT NULL,
    path_ref TEXT NOT NULL,
    PRIMARY KEY (enrollment_id, kind, course_ref)
);

-- 阅读进度。只记"读到哪、读完没"，不是掌握度。
CREATE TABLE IF NOT EXISTS course_lesson_progress (
    enrollment_id TEXT NOT NULL REFERENCES course_enrollments(enrollment_id) ON DELETE CASCADE,
    lesson_id TEXT NOT NULL,
    state TEXT NOT NULL,
    anchor TEXT NOT NULL DEFAULT '',
    percentage REAL NOT NULL DEFAULT 0,
    started_at REAL,
    read_at REAL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (enrollment_id, lesson_id)
);

-- 题库提交（P4）：submission_id 由前端生成，重试时带同一个，用来去重。
CREATE TABLE IF NOT EXISTS course_submissions (
    enrollment_id TEXT NOT NULL REFERENCES course_enrollments(enrollment_id) ON DELETE CASCADE,
    submission_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    question_version INTEGER NOT NULL,
    answer_hash TEXT NOT NULL,
    answer_json TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (enrollment_id, submission_id)
);
CREATE INDEX IF NOT EXISTS idx_course_submissions_question
    ON course_submissions(enrollment_id, question_id, created_at);

-- 已经对这个实例执行过的撤题（P4），保证每条勘误只处理一次。
CREATE TABLE IF NOT EXISTS course_errata_applied (
    enrollment_id TEXT NOT NULL REFERENCES course_enrollments(enrollment_id) ON DELETE CASCADE,
    erratum_key TEXT NOT NULL,
    applied_at REAL NOT NULL,
    PRIMARY KEY (enrollment_id, erratum_key)
);
"""

LOCKED_OUTLINE_MESSAGE = (
    "这是官方课程的学习路径，大纲由课程统一维护，不能在这里修改。"
    "可以照常学习、练习和复习；想自己改编，请另建一个学习目标。"
)


class CourseError(Exception):
    """带错误码的课程错误，路由层把它翻成 HTTP 响应。"""

    def __init__(self, code: CourseErrorCode, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class CourseOutlineLockedError(ObjectiveRelationError):
    """继承 ObjectiveRelationError，是为了让现有的改纲入口（路由和 AI 工具）
    都已经会接住它：路由回 422、工具把这句话转告给模型，而不是 500。"""

    code = CourseErrorCode.PATH_LOCKED


class _AlreadyEnrolled(Exception):
    def __init__(self, enrollment_id: str) -> None:
        self.enrollment_id = enrollment_id


def ensure_schema(conn: sqlite3.Connection) -> None:
    """建表（幂等）。在 LearningStore 的库里执行，所以和掌握路径共享事务与备份。

    ⚠️ 只能在事务之外调用：sqlite3 的 executescript 会先 COMMIT 当前事务。
    """
    conn.executescript(_SCHEMA)


def _table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'course_enrollments'"
    ).fetchone()
    return row is not None


def course_enrollment_for_path(conn: sqlite3.Connection, path_id: str) -> sqlite3.Row | None:
    if not _table_exists(conn):
        return None
    return conn.execute("SELECT * FROM course_enrollments WHERE path_id = ?", (path_id,)).fetchone()


def assert_outline_editable(conn: sqlite3.Connection, path_id: str) -> None:
    """改纲前调用：官方课程的路径一律拒绝。由 LearningService 的三个改纲入口调用。"""
    if course_enrollment_for_path(conn, path_id) is not None:
        raise CourseOutlineLockedError(LOCKED_OUTLINE_MESSAGE)


# ── ID 映射 ──────────────────────────────────────────────────────────────────


def module_id_for(path_id: str, lesson_id: str) -> str:
    return f"{path_id}_l_{lesson_id}"


def kp_id_for(path_id: str, kp_id: str) -> str:
    return f"{path_id}_k_{kp_id}"


def source_id_for(path_id: str, lesson_id: str) -> str:
    return f"{path_id}_s_{lesson_id}"


def lesson_source_ref(course_id: str, version: str, lesson_id: str) -> str:
    """课时来源的稳定标识，也是引用定位协议的前半段（见 CONTRACT.md）。"""
    return f"course:{course_id}@{version}/{lesson_id}"


@dataclass
class _Plan:
    modules: list[LearningModule]
    sources: list[TopicSource]
    lesson_map: dict[str, str]
    kp_map: dict[str, str]


def _plan(package: CourseVersionPackage, path_id: str, enrollment_id: str) -> _Plan:
    lessons = package.ordered_lessons()
    kp_map = {kp.id: kp_id_for(path_id, kp.id) for kp in package.outline.knowledge_points}
    lesson_map = {lesson.id: module_id_for(path_id, lesson.id) for lesson in lessons}
    sources = [
        TopicSource(
            id=source_id_for(path_id, lesson.id),
            kind=TopicSourceKind.COURSE,
            source_id=lesson_source_ref(package.course_id, package.version, lesson.id),
            label=lesson.title,
            excerpt="；".join(lesson.objectives)[:500],
            position=index,
            available=True,
            # 读取教材时（P3）会用 enrollment_id 回查实例、核对课程和版本，
            # 不会信任这里的任何路径——这里根本也没有文件路径。
            metadata={
                "enrollment_id": enrollment_id,
                "course_id": package.course_id,
                "course_version": package.version,
                "lesson_id": lesson.id,
            },
        )
        for index, lesson in enumerate(lessons)
    ]
    modules: list[LearningModule] = []
    for order, lesson in enumerate(lessons):
        module_id = lesson_map[lesson.id]
        points = []
        for course_kp_id in lesson.knowledge_points:
            kp = package.knowledge_point(course_kp_id)
            if kp is None:  # 校验器保证不会发生
                continue
            points.append(
                KnowledgePoint(
                    id=kp_map[kp.id],
                    name=kp.name,
                    type=KnowledgeType(kp.type),
                    module_id=module_id,
                    prerequisite_ids=[kp_map[parent] for parent in kp.prerequisites],
                    topic_source_ids=[source_id_for(path_id, lesson.id)],
                )
            )
        modules.append(
            LearningModule(
                id=module_id,
                name=lesson.title,
                order=order,
                objective="；".join(lesson.objectives)[:300],
                knowledge_points=points,
            )
        )
    return _Plan(modules=modules, sources=sources, lesson_map=lesson_map, kp_map=kp_map)


# ── 读取 ─────────────────────────────────────────────────────────────────────


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


class EnrollmentService:
    def __init__(
        self,
        store: LearningStore | None = None,
        repository: CourseRepository | None = None,
    ) -> None:
        self.store = store or LearningStore()
        self.repository = repository or get_course_repository()
        with self.store._connect() as conn:
            ensure_schema(conn)

    # 基础查询 -----------------------------------------------------------------

    def _get_row(self, enrollment_id: str) -> sqlite3.Row:
        with self.store._connect() as conn:
            row = conn.execute(
                "SELECT * FROM course_enrollments WHERE enrollment_id = ?", (enrollment_id,)
            ).fetchone()
        if row is None:
            raise CourseError(CourseErrorCode.ENROLLMENT_NOT_FOUND, "没有找到这个选课记录", 404)
        return row

    def find_active(self, course_id: str) -> sqlite3.Row | None:
        with self.store._connect() as conn:
            return conn.execute(
                "SELECT * FROM course_enrollments WHERE course_id = ? AND status = 'active'",
                (course_id,),
            ).fetchone()

    def list_active(self) -> list[sqlite3.Row]:
        with self.store._connect() as conn:
            return conn.execute(
                "SELECT * FROM course_enrollments WHERE status = 'active' ORDER BY updated_at DESC"
            ).fetchall()

    def mapping(self, enrollment_id: str, kind: str) -> dict[str, str]:
        with self.store._connect() as conn:
            rows = conn.execute(
                "SELECT course_ref, path_ref FROM course_enrollment_map "
                "WHERE enrollment_id = ? AND kind = ?",
                (enrollment_id, kind),
            ).fetchall()
        return {row["course_ref"]: row["path_ref"] for row in rows}

    def package_for(self, row: sqlite3.Row) -> CourseVersionPackage:
        """学员所学的那个版本。版本文件不在了要明确报错，不能悄悄换成别的版本。"""
        package = self.repository.learnable_version(row["course_id"], row["course_version"])
        if package is None:
            raise CourseError(
                CourseErrorCode.VERSION_UNAVAILABLE,
                f"你所学的课程版本 {row['course_version']} 暂时无法读取，学习记录都还在，请稍后再试",
                503,
            )
        return package

    # 加入 ---------------------------------------------------------------------

    def enroll(self, course_id: str) -> tuple[Enrollment, bool]:
        """加入课程。返回 (实例, 是否新建)。重复调用返回已有实例。"""
        existing = self.find_active(course_id)
        if existing is not None:
            return self.describe(existing["enrollment_id"]), False

        course = self.repository.course(course_id)
        package = self.repository.published(course_id)
        if course is None or package is None:
            raise CourseError(CourseErrorCode.COURSE_NOT_FOUND, "课程不存在或尚未发布", 404)
        if not course.record.enrollment_open or not course.record.learning_open:
            raise CourseError(CourseErrorCode.NOT_ENROLLABLE, "这门课暂时停止加入", 409)

        enrollment_id = f"enr_{uuid.uuid4().hex}"
        path_id = f"course_{uuid.uuid4().hex}"
        plan = _plan(package, path_id, enrollment_id)
        validate_objective_relations(plan.modules, plan.sources)
        manifest = package.manifest
        metadata = TopicMetadata(
            path_id=path_id,
            goal=manifest.summary[:2000],
            description=manifest.subtitle[:500],
            emoji="📘",
            map_seed=LearningStore._default_map_seed(path_id),
        )

        def create(tx) -> None:
            conn = tx._conn
            # 注意：这里不能调 ensure_schema——executescript 会先隐式 COMMIT，
            # 把 BEGIN IMMEDIATE 提前结束，并发保护和回滚就都失效了（测试踩过）。
            # 建表已经在 __init__ 里做过。
            # BEGIN IMMEDIATE 已经把并发写入排成队，这里再查一次就不会两个都建。
            again = conn.execute(
                "SELECT enrollment_id FROM course_enrollments "
                "WHERE course_id = ? AND status = 'active'",
                (course_id,),
            ).fetchone()
            if again is not None:
                raise _AlreadyEnrolled(again["enrollment_id"])
            from deeptutor.learning.service import LearningService

            tx.progress.name = manifest.title[:120]
            tx.put_topic(metadata, plan.sources)
            LearningService(self.store).replace_modules(tx.progress, plan.modules)
            tx.progress.current_module_id = plan.modules[0].id if plan.modules else ""
            tx.progress.current_kp_index = 0
            now = time.time()
            conn.execute(
                "INSERT INTO course_enrollments (enrollment_id, course_id, course_version, "
                "path_id, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, ?)",
                (enrollment_id, course_id, package.version, path_id, now, now),
            )
            conn.executemany(
                "INSERT INTO course_enrollment_map (enrollment_id, kind, course_ref, path_ref) "
                "VALUES (?, ?, ?, ?)",
                [(enrollment_id, "lesson", k, v) for k, v in plan.lesson_map.items()]
                + [(enrollment_id, "knowledge_point", k, v) for k, v in plan.kp_map.items()],
            )
            tx.touch()
            tx.emit(
                "topic.created",
                {
                    "module_count": len(plan.modules),
                    "knowledge_point_count": len(plan.kp_map),
                    "source_count": len(plan.sources),
                    "course_id": course_id,
                    "course_version": package.version,
                    "enrollment_id": enrollment_id,
                },
            )

        try:
            self.store.mutate(path_id, create, create=True)
        except _AlreadyEnrolled as exc:
            return self.describe(exc.enrollment_id), False
        except sqlite3.IntegrityError:
            # 唯一索引兜底（理论上 BEGIN IMMEDIATE 已经挡住了）：别人先建成了，返回它。
            winner = self.find_active(course_id)
            if winner is None:
                raise
            return self.describe(winner["enrollment_id"]), False
        return self.describe(enrollment_id), True

    # 描述（给前端的实例视图） -------------------------------------------------

    def describe(self, enrollment_id: str) -> Enrollment:
        row = self._get_row(enrollment_id)
        course = self.repository.course(row["course_id"])
        latest = self.repository.published(row["course_id"])
        package = self.repository.learnable_version(row["course_id"], row["course_version"])
        progress_rows = self.lesson_progress(enrollment_id)
        lessons: list[LessonProgressRecord] = []
        if package is not None:
            for lesson in package.ordered_lessons():
                lessons.append(
                    progress_rows.get(lesson.id) or LessonProgressRecord(lesson_id=lesson.id)
                )
        mastery, due_count = self._mastery(row)
        current = next((item.lesson_id for item in lessons if item.state != "read"), "")
        if not current and lessons:
            current = lessons[-1].lesson_id
        return Enrollment(
            enrollment_id=row["enrollment_id"],
            course_id=row["course_id"],
            course_version=row["course_version"],
            path_id=row["path_id"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            latest_version=latest.version
            if latest is not None and latest.version != row["course_version"]
            else None,
            learning_open=bool(course.record.learning_open) if course else False,
            title=package.manifest.title if package else row["course_id"],
            lessons=lessons,
            mastery=mastery,
            review_due_count=due_count,
            current_lesson_id=current,
        )

    def _mastery(self, row: sqlite3.Row):
        """掌握状态从学习引擎读，前端不推算。"""
        from deeptutor.learning import policy as learning_policy
        from deeptutor.learning.courses.contract import EnrollmentMastery

        progress = self.store.load(row["path_id"])
        if progress is None:
            return [], 0
        kp_map = self.mapping(row["enrollment_id"], "knowledge_point")
        package = self.repository.learnable_version(row["course_id"], row["course_version"])
        due_tasks = learning_policy.due_reviews(progress)
        scheduled = {task.knowledge_point_id: task.due_at for task in progress.review_queue}
        items = []
        for course_kp, path_kp in kp_map.items():
            kp, _module_id, _module_name = learning_policy.find_knowledge_point(progress, path_kp)
            if kp is None:
                continue
            items.append(
                EnrollmentMastery(
                    knowledge_point_id=course_kp,
                    path_knowledge_point_id=path_kp,
                    lesson_id=(
                        lesson.id
                        if package and (lesson := package.lesson_of_knowledge_point(course_kp))
                        else ""
                    ),
                    mastery=learning_policy.display_mastery(progress, kp),
                    mastered=learning_policy.is_mastered(progress, kp),
                    assessed=any(
                        a.knowledge_point_id == path_kp and not a.voided
                        for a in progress.quiz_attempts
                    )
                    or path_kp in progress.qualitative_mastery,
                    review_due_at=scheduled.get(path_kp),
                )
            )
        return items, len(due_tasks)

    # 阅读进度 -----------------------------------------------------------------

    def lesson_progress(self, enrollment_id: str) -> dict[str, LessonProgressRecord]:
        with self.store._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM course_lesson_progress WHERE enrollment_id = ?", (enrollment_id,)
            ).fetchall()
        return {
            row["lesson_id"]: LessonProgressRecord(
                lesson_id=row["lesson_id"],
                state=row["state"],
                anchor=row["anchor"],
                percentage=row["percentage"],
                started_at=row["started_at"],
                read_at=row["read_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        }

    def assert_learnable(
        self, row: sqlite3.Row, lesson_id: str | None = None
    ) -> CourseVersionPackage:
        """继续上课/答题前的检查：整门课暂停、课时暂停、课时不存在都明确报错。"""
        course = self.repository.course(row["course_id"])
        if course is not None and not course.record.learning_open:
            raise CourseError(
                CourseErrorCode.LEARNING_CLOSED, "这门课暂停学习，你的记录都会保留", 409
            )
        package = self.package_for(row)
        if lesson_id is not None:
            if package.lesson(lesson_id) is None:
                raise CourseError(CourseErrorCode.LESSON_NOT_FOUND, "课时不存在", 404)
            reason = self.repository.lesson_suspension(
                row["course_id"], row["course_version"], lesson_id
            )
            if reason is not None:
                raise CourseError(
                    CourseErrorCode.LESSON_SUSPENDED, f"这节课暂停学习：{reason}", 409
                )
        return package

    def record_reading(
        self,
        enrollment_id: str,
        lesson_id: str,
        *,
        anchor: str = "",
        percentage: float = 0.0,
        completed: bool = False,
    ) -> LessonProgressRecord:
        """记阅读位置。读完只是"读过"——不会写任何掌握证据。"""
        row = self._get_row(enrollment_id)
        package = self.assert_learnable(row, lesson_id)
        if anchor and anchor not in package.lesson_anchors.get(lesson_id, []):
            anchor = ""
        percentage = max(0.0, min(1.0, float(percentage)))
        now = time.time()
        with self.store._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                current = conn.execute(
                    "SELECT * FROM course_lesson_progress WHERE enrollment_id = ? AND lesson_id = ?",
                    (enrollment_id, lesson_id),
                ).fetchone()
                was_read = current is not None and current["state"] == "read"
                state = "read" if completed or was_read else "reading"
                # 进度只往前走：重新打开一节读完的课回到开头，不会把"已读完"抹掉。
                best = max(percentage, float(current["percentage"]) if current else 0.0)
                conn.execute(
                    """
                    INSERT INTO course_lesson_progress (
                        enrollment_id, lesson_id, state, anchor, percentage,
                        started_at, read_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(enrollment_id, lesson_id) DO UPDATE SET
                        state = excluded.state,
                        anchor = excluded.anchor,
                        percentage = excluded.percentage,
                        read_at = COALESCE(course_lesson_progress.read_at, excluded.read_at),
                        updated_at = excluded.updated_at
                    """,
                    (
                        enrollment_id,
                        lesson_id,
                        state,
                        anchor,
                        1.0 if state == "read" else best,
                        now,
                        now if state == "read" else None,
                        now,
                    ),
                )
                conn.execute(
                    "UPDATE course_enrollments SET updated_at = ? WHERE enrollment_id = ?",
                    (now, enrollment_id),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return self.lesson_progress(enrollment_id)[lesson_id]


__all__ = [
    "CourseError",
    "CourseOutlineLockedError",
    "EnrollmentService",
    "LOCKED_OUTLINE_MESSAGE",
    "assert_outline_editable",
    "course_enrollment_for_path",
    "ensure_schema",
    "kp_id_for",
    "lesson_source_ref",
    "module_id_for",
]
