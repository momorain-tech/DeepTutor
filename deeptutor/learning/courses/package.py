"""读取并校验课程包（P0 的校验规则 + P1 读取时用的同一套代码）。

目录结构：

    <catalog>/
      courses.lock.json            已发布版本的内容哈希（发布后不可改，CI 校验）
      <course_id>/
        course.json                各版本发布状态 + 运营开关（可改）
        <version>/                 发布后不可改
          manifest.json
          outline.json
          sources.json
          questions.json           只在服务端读取，任何公开响应都不含答案
          lessons/<lesson>.md      受限 Markdown

校验的原则是"一次说全"：不在第一个错误处停下，把能发现的问题全列出来，
写课程的人改一轮就能过，而不是改一个、跑一次、再冒出下一个。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from deeptutor.learning.courses.contract import (
    ID_PATTERN,
    OBJECTIVE_TYPES,
    SCHEMA_VERSION,
    VERSION_PATTERN,
    CourseErrorCode,
    CourseIssue,
    CourseManifest,
    CourseOutline,
    CourseQuestion,
    CourseQuestions,
    CourseRecord,
    CourseSource,
    CourseSources,
    OutlineKnowledgePoint,
    OutlineLesson,
    VersionStatus,
    version_key,
)
from deeptutor.learning.courses.markdown import check_markdown

LOCK_FILENAME = "courses.lock.json"
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_LESSON_BYTES = 256 * 1024
_M = TypeVar("_M", bound=BaseModel)


@dataclass
class CourseVersionPackage:
    """一个校验通过的课程版本，读进内存后只读使用。"""

    course_id: str
    version: str
    root: Path
    manifest: CourseManifest
    outline: CourseOutline
    sources: list[CourseSource]
    questions: list[CourseQuestion]
    lessons_markdown: dict[str, str]
    lesson_anchors: dict[str, list[str]]
    content_hash: str

    def lesson(self, lesson_id: str) -> OutlineLesson | None:
        return next((item for item in self.outline.lessons if item.id == lesson_id), None)

    def knowledge_point(self, kp_id: str) -> OutlineKnowledgePoint | None:
        return next((item for item in self.outline.knowledge_points if item.id == kp_id), None)

    def question(self, question_id: str) -> CourseQuestion | None:
        return next((item for item in self.questions if item.id == question_id), None)

    def source(self, source_id: str) -> CourseSource | None:
        return next((item for item in self.sources if item.id == source_id), None)

    def ordered_lessons(self) -> list[OutlineLesson]:
        return sorted(self.outline.lessons, key=lambda item: item.order)

    def lesson_of_knowledge_point(self, kp_id: str) -> OutlineLesson | None:
        return next((item for item in self.outline.lessons if kp_id in item.knowledge_points), None)


@dataclass
class CoursePackage:
    """一门课：课程级记录 + 所有能读出来的版本。"""

    course_id: str
    root: Path
    record: CourseRecord
    versions: dict[str, CourseVersionPackage] = field(default_factory=dict)

    def status(self, version: str) -> VersionStatus | None:
        return self.record.versions.get(version)

    def latest_published(self) -> CourseVersionPackage | None:
        published = [
            package
            for version, package in self.versions.items()
            if self.record.versions.get(version) == VersionStatus.PUBLISHED
        ]
        return max(published, key=lambda item: version_key(item.version), default=None)


@dataclass
class ValidationReport:
    issues: list[CourseIssue] = field(default_factory=list)

    def add(
        self,
        code: CourseErrorCode,
        message: str,
        where: str = "",
        *,
        severity: str = "error",
    ) -> None:
        self.issues.append(CourseIssue(code=code, message=message, where=where, severity=severity))

    @property
    def errors(self) -> list[CourseIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def extend(self, other: ValidationReport) -> None:
        self.issues.extend(other.issues)


# ── 小工具 ────────────────────────────────────────────────────────────────────


def _contains_symlink(path: Path, stop: Path) -> bool:
    """从 path 往上一直查到 stop，任何一级是软链接都算——软链接可以指到包外。"""
    current = path
    while True:
        if current.is_symlink():
            return True
        if current == stop or current.parent == current:
            return False
        current = current.parent


def _read_json(path: Path, report: ValidationReport, where: str) -> Any:
    if not path.is_file():
        report.add(CourseErrorCode.FILE_MISSING, "文件不存在", where)
        return None
    if path.is_symlink():
        report.add(CourseErrorCode.ILLEGAL_PATH, "课程文件不能是软链接", where)
        return None
    if path.stat().st_size > MAX_JSON_BYTES:
        report.add(CourseErrorCode.FIELD_INVALID, "文件超过 2MB", where)
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        report.add(CourseErrorCode.FILE_INVALID_JSON, f"不是合法的 UTF-8 JSON：{exc}", where)
        return None


def _parse(model: type[_M], raw: Any, report: ValidationReport, where: str) -> _M | None:
    if raw is None:
        return None
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"])
            report.add(
                CourseErrorCode.FIELD_INVALID,
                f"{location or '(根)'}：{error['msg']}",
                where,
            )
        return None


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    dupes: list[str] = []
    for value in values:
        if value in seen and value not in dupes:
            dupes.append(value)
        seen.add(value)
    return dupes


def content_hash(version_root: Path) -> str:
    """版本目录下所有文件（含相对路径）的 SHA256。改一个字节、增删一个文件都会变。"""
    digest = hashlib.sha256()
    for path in sorted(p for p in version_root.rglob("*") if p.is_file()):
        relative = path.relative_to(version_root).as_posix()
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


# ── 单个版本 ──────────────────────────────────────────────────────────────────


def _safe_material_path(version_root: Path, material: str) -> Path | None:
    pure = PurePosixPath(material)
    if (
        not material
        or "\\" in material
        or pure.is_absolute()
        or ".." in pure.parts
        or not pure.parts
        or pure.parts[0] != "lessons"
        or pure.suffix != ".md"
    ):
        return None
    candidate = version_root.joinpath(*pure.parts)
    try:
        candidate.resolve().relative_to(version_root.resolve())
    except ValueError:
        return None
    if _contains_symlink(candidate, version_root):
        return None
    return candidate


def _find_cycle(prerequisites: dict[str, list[str]]) -> list[str] | None:
    state: dict[str, int] = {}  # 1 = 正在访问，2 = 访问完
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for parent in prerequisites.get(node, []):
            if parent not in prerequisites:
                continue
            if state.get(parent) == 1:
                return stack[stack.index(parent) :] + [parent]
            if state.get(parent) is None:
                found = visit(parent)
                if found:
                    return found
        stack.pop()
        state[node] = 2
        return None

    for node in prerequisites:
        if state.get(node) is None:
            found = visit(node)
            if found:
                return found
    return None


def _check_question(
    question: CourseQuestion,
    *,
    lessons: dict[str, OutlineLesson],
    source_ids: set[str],
    report: ValidationReport,
    where: str,
) -> None:
    lesson = lessons.get(question.lesson)
    if lesson is None:
        report.add(CourseErrorCode.DANGLING_REFERENCE, f"引用了不存在的课时 {question.lesson!r}", where)
    elif question.knowledge_point not in lesson.knowledge_points:
        report.add(
            CourseErrorCode.DANGLING_REFERENCE,
            f"知识点 {question.knowledge_point!r} 不属于课时 {question.lesson!r}",
            where,
        )
    for source_id in question.sources:
        if source_id not in source_ids:
            report.add(CourseErrorCode.DANGLING_REFERENCE, f"引用了不存在的来源 {source_id!r}", where)

    def bad(message: str) -> None:
        report.add(CourseErrorCode.QUESTION_INVALID, message, where)

    labels = [option.label for option in question.options]
    answer = question.answer
    if question.type in {"single_choice", "multiple_choice"}:
        if len(question.options) < 2:
            bad("选择题至少要有两个选项")
        if _duplicates(labels):
            bad(f"选项标签重复：{_duplicates(labels)}")
        if answer is None or not answer.choices:
            bad("选择题要在 answer.choices 里给出正确选项的标签")
        else:
            unknown = [label for label in answer.choices if label not in labels]
            if unknown:
                bad(f"answer.choices 里的 {unknown} 不是任何选项的标签")
            if question.type == "single_choice" and len(answer.choices) != 1:
                bad("单选题只能有一个正确选项")
            if _duplicates(answer.choices):
                bad("answer.choices 有重复")
    elif question.type == "true_false":
        if question.options:
            bad("判断题不要填 options")
        if answer is None or answer.value is None:
            bad("判断题要在 answer.value 里填 true 或 false")
    elif question.type == "fill_blank":
        if answer is None or not [item for item in answer.accepted if item.strip()]:
            bad("填空题要在 answer.accepted 里列出可接受的答案")
    elif question.type == "open":
        if question.rubric is None:
            bad("开放题必须给出评分规则 rubric")
        elif question.rubric.partial_ratio >= question.rubric.pass_ratio:
            bad("rubric.partial_ratio 必须小于 pass_ratio")
        if question.rubric is not None and _duplicates([c.id for c in question.rubric.criteria]):
            bad("评分项 ID 重复")
    if question.type in OBJECTIVE_TYPES and question.rubric is not None:
        bad("客观题由代码判分，不需要 rubric")


def load_version(
    version_root: Path,
    *,
    course_id: str,
    version: str,
    status: VersionStatus,
) -> tuple[CourseVersionPackage | None, ValidationReport]:
    """读取并校验一个版本目录。有任何错误就不返回包（只返回报告）。"""
    report = ValidationReport()
    label = f"{course_id}/{version}"
    if _contains_symlink(version_root, version_root.parent.parent):
        report.add(CourseErrorCode.ILLEGAL_PATH, "版本目录不能是软链接", label)
        return None, report
    for path in version_root.rglob("*"):
        if path.is_symlink():
            report.add(
                CourseErrorCode.ILLEGAL_PATH,
                "课程包里不能有软链接",
                f"{label}/{path.relative_to(version_root).as_posix()}",
            )
    manifest = _parse(
        CourseManifest,
        _read_json(version_root / "manifest.json", report, f"{label}/manifest.json"),
        report,
        f"{label}/manifest.json",
    )
    outline = _parse(
        CourseOutline,
        _read_json(version_root / "outline.json", report, f"{label}/outline.json"),
        report,
        f"{label}/outline.json",
    )
    sources_file = _parse(
        CourseSources,
        _read_json(version_root / "sources.json", report, f"{label}/sources.json"),
        report,
        f"{label}/sources.json",
    )
    questions_file = _parse(
        CourseQuestions,
        _read_json(version_root / "questions.json", report, f"{label}/questions.json"),
        report,
        f"{label}/questions.json",
    )

    if manifest is not None:
        where = f"{label}/manifest.json"
        if manifest.schema_version != SCHEMA_VERSION:
            report.add(
                CourseErrorCode.SCHEMA_VERSION_UNSUPPORTED,
                f"不认识的格式版本 {manifest.schema_version}（当前只支持 {SCHEMA_VERSION}）",
                where,
            )
        if manifest.course_id != course_id:
            report.add(
                CourseErrorCode.ID_MISMATCH,
                f"course_id 写的是 {manifest.course_id!r}，但目录名是 {course_id!r}",
                where,
            )
        if manifest.version != version:
            report.add(
                CourseErrorCode.ID_MISMATCH,
                f"version 写的是 {manifest.version!r}，但目录名是 {version!r}",
                where,
            )
        if status != VersionStatus.DRAFT and manifest.review is None:
            report.add(CourseErrorCode.REVIEW_MISSING, "已发布或已撤下的版本必须有审核信息 review", where)

    sources = sources_file.sources if sources_file is not None else []
    source_ids = {item.id for item in sources}
    for dupe in _duplicates([item.id for item in sources]):
        report.add(CourseErrorCode.DUPLICATE_ID, f"来源 ID {dupe!r} 重复", f"{label}/sources.json")

    lessons_markdown: dict[str, str] = {}
    lesson_anchors: dict[str, list[str]] = {}
    lessons: dict[str, OutlineLesson] = {}
    if outline is not None:
        where = f"{label}/outline.json"
        for dupe in _duplicates([item.id for item in outline.units]):
            report.add(CourseErrorCode.DUPLICATE_ID, f"单元 ID {dupe!r} 重复", where)
        for dupe in _duplicates([item.id for item in outline.lessons]):
            report.add(CourseErrorCode.DUPLICATE_ID, f"课时 ID {dupe!r} 重复", where)
        for dupe in _duplicates([item.id for item in outline.knowledge_points]):
            report.add(CourseErrorCode.DUPLICATE_ID, f"知识点 ID {dupe!r} 重复", where)
        for dupe in _duplicates([str(item.order) for item in outline.lessons]):
            report.add(CourseErrorCode.DUPLICATE_ID, f"有多节课的 order 都是 {dupe}", where)

        lessons = {lesson.id: lesson for lesson in outline.lessons}
        kps = {kp.id: kp for kp in outline.knowledge_points}

        unit_membership: dict[str, str] = {}
        for unit in outline.units:
            for lesson_id in unit.lessons:
                if lesson_id not in lessons:
                    report.add(
                        CourseErrorCode.DANGLING_REFERENCE,
                        f"单元 {unit.id!r} 引用了不存在的课时 {lesson_id!r}",
                        where,
                    )
                elif lesson_id in unit_membership:
                    report.add(
                        CourseErrorCode.DUPLICATE_ID,
                        f"课时 {lesson_id!r} 同时出现在单元 {unit_membership[lesson_id]!r} 和 {unit.id!r}",
                        where,
                    )
                unit_membership.setdefault(lesson_id, unit.id)

        owner: dict[str, list[str]] = {}
        for lesson in outline.lessons:
            for dupe in _duplicates(lesson.knowledge_points):
                report.add(
                    CourseErrorCode.DUPLICATE_ID,
                    f"课时 {lesson.id!r} 里知识点 {dupe!r} 写了两次",
                    where,
                )
            for kp_id in lesson.knowledge_points:
                if kp_id not in kps:
                    report.add(
                        CourseErrorCode.DANGLING_REFERENCE,
                        f"课时 {lesson.id!r} 引用了不存在的知识点 {kp_id!r}",
                        where,
                    )
                owner.setdefault(kp_id, [])
                if lesson.id not in owner[kp_id]:
                    owner[kp_id].append(lesson.id)

            material_where = f"{label}/{lesson.material}"
            material_path = _safe_material_path(version_root, lesson.material)
            if material_path is None:
                report.add(
                    CourseErrorCode.ILLEGAL_PATH,
                    f"课时 {lesson.id!r} 的教材路径 {lesson.material!r} 不合法"
                    "（必须是 lessons/ 下的相对 .md 路径，不能含 ..、反斜杠或软链接）",
                    where,
                )
                continue
            if not material_path.is_file():
                report.add(CourseErrorCode.FILE_MISSING, "教材文件不存在", material_where)
                continue
            if material_path.stat().st_size > MAX_LESSON_BYTES:
                report.add(CourseErrorCode.FIELD_INVALID, "单节教材超过 256KB", material_where)
                continue
            try:
                text = material_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                report.add(CourseErrorCode.FIELD_INVALID, "教材不是 UTF-8 编码", material_where)
                continue
            checked = check_markdown(text)
            for problem in checked.problems:
                report.add(CourseErrorCode.UNSAFE_MARKDOWN, problem, material_where)
            lessons_markdown[lesson.id] = text
            lesson_anchors[lesson.id] = checked.anchors

        for kp in outline.knowledge_points:
            lessons_of_kp = owner.get(kp.id, [])
            if len(lessons_of_kp) != 1:
                report.add(
                    CourseErrorCode.ORPHAN_KNOWLEDGE_POINT,
                    f"知识点 {kp.id!r} 必须恰好属于一节课，现在属于 {lessons_of_kp or '没有课时'}",
                    where,
                )
            for parent in kp.prerequisites:
                if parent not in kps:
                    report.add(
                        CourseErrorCode.DANGLING_REFERENCE,
                        f"知识点 {kp.id!r} 的前置 {parent!r} 不存在",
                        where,
                    )
                elif parent == kp.id:
                    report.add(CourseErrorCode.PREREQUISITE_CYCLE, f"知识点 {kp.id!r} 把自己当前置", where)
                elif lessons_of_kp and owner.get(parent):
                    own = lessons[lessons_of_kp[0]].order if lessons_of_kp[0] in lessons else 0
                    other = lessons[owner[parent][0]].order if owner[parent][0] in lessons else 0
                    if other > own:
                        report.add(
                            CourseErrorCode.DANGLING_REFERENCE,
                            f"知识点 {kp.id!r} 的前置 {parent!r} 安排在更后面的课里",
                            where,
                            severity="warning",
                        )
            for source_id in kp.sources:
                if source_id not in source_ids:
                    report.add(
                        CourseErrorCode.DANGLING_REFERENCE,
                        f"知识点 {kp.id!r} 引用了不存在的来源 {source_id!r}",
                        where,
                    )
            if lessons_of_kp and lessons_of_kp[0] in lesson_anchors:
                for anchor in kp.anchors:
                    if anchor not in lesson_anchors[lessons_of_kp[0]]:
                        report.add(
                            CourseErrorCode.DANGLING_REFERENCE,
                            f"知识点 {kp.id!r} 的锚点 {anchor!r} 在课时 {lessons_of_kp[0]!r} 的教材里找不到",
                            where,
                        )
        cycle = _find_cycle(
            {kp.id: [p for p in kp.prerequisites if p != kp.id] for kp in outline.knowledge_points}
        )
        if cycle:
            report.add(
                CourseErrorCode.PREREQUISITE_CYCLE,
                "前置关系成环：" + " → ".join(cycle),
                where,
            )

    questions = questions_file.questions if questions_file is not None else []
    for dupe in _duplicates([item.id for item in questions]):
        report.add(CourseErrorCode.DUPLICATE_ID, f"题目 ID {dupe!r} 重复", f"{label}/questions.json")
    if outline is not None:
        for question in questions:
            _check_question(
                question,
                lessons=lessons,
                source_ids=source_ids,
                report=report,
                where=f"{label}/questions.json#{question.id}",
            )

    if not report.ok or manifest is None or outline is None:
        return None, report
    return (
        CourseVersionPackage(
            course_id=course_id,
            version=version,
            root=version_root,
            manifest=manifest,
            outline=outline,
            sources=sources,
            questions=questions,
            lessons_markdown=lessons_markdown,
            lesson_anchors=lesson_anchors,
            content_hash=content_hash(version_root),
        ),
        report,
    )


# ── 一门课 / 整个目录 ─────────────────────────────────────────────────────────


def load_course(course_root: Path) -> tuple[CoursePackage | None, ValidationReport]:
    """读一门课。坏掉的版本会被跳过并记进报告，不影响同一门课的其他版本。"""
    report = ValidationReport()
    course_id = course_root.name
    where = f"{course_id}/course.json"
    if not ID_PATTERN.match(course_id):
        report.add(CourseErrorCode.FIELD_INVALID, f"课程目录名 {course_id!r} 不是合法 ID", course_id)
        return None, report
    if course_root.is_symlink():
        report.add(CourseErrorCode.ILLEGAL_PATH, "课程目录不能是软链接", course_id)
        return None, report
    record = _parse(CourseRecord, _read_json(course_root / "course.json", report, where), report, where)
    if record is None:
        return None, report
    if record.schema_version != SCHEMA_VERSION:
        report.add(
            CourseErrorCode.SCHEMA_VERSION_UNSUPPORTED,
            f"不认识的格式版本 {record.schema_version}",
            where,
        )
        return None, report
    if record.course_id != course_id:
        report.add(
            CourseErrorCode.ID_MISMATCH,
            f"course_id 写的是 {record.course_id!r}，但目录名是 {course_id!r}",
            where,
        )
        return None, report

    package = CoursePackage(course_id=course_id, root=course_root, record=record)
    for child in sorted(course_root.iterdir()):
        if child.is_dir() and child.name not in record.versions:
            report.add(
                CourseErrorCode.FIELD_INVALID,
                f"版本目录 {child.name!r} 没有在 course.json 的 versions 里登记，已忽略",
                f"{course_id}/{child.name}",
                severity="warning",
            )
    for version, status in record.versions.items():
        version_root = course_root / version
        if not version_root.is_dir():
            report.add(
                CourseErrorCode.FILE_MISSING,
                f"course.json 登记了版本 {version}，但目录不存在",
                where,
            )
            continue
        loaded, version_report = load_version(
            version_root, course_id=course_id, version=version, status=status
        )
        report.extend(version_report)
        if loaded is not None:
            package.versions[version] = loaded

    known_lessons = {
        lesson.id for loaded in package.versions.values() for lesson in loaded.outline.lessons
    }
    for item in record.suspended_lessons:
        if package.versions and item.lesson_id not in known_lessons:
            report.add(
                CourseErrorCode.DANGLING_REFERENCE,
                f"suspended_lessons 引用了不存在的课时 {item.lesson_id!r}",
                where,
                severity="warning",
            )
        for version in item.versions:
            if version not in record.versions:
                report.add(
                    CourseErrorCode.DANGLING_REFERENCE,
                    f"suspended_lessons 引用了未登记的版本 {version!r}",
                    where,
                )
    known_questions = {
        question.id for loaded in package.versions.values() for question in loaded.questions
    }
    for erratum in record.errata:
        if package.versions and erratum.question_id not in known_questions:
            report.add(
                CourseErrorCode.DANGLING_REFERENCE,
                f"errata 引用了不存在的题目 {erratum.question_id!r}",
                where,
                severity="warning",
            )
        for version in erratum.course_versions:
            if version not in record.versions:
                report.add(
                    CourseErrorCode.DANGLING_REFERENCE,
                    f"errata 引用了未登记的版本 {version!r}",
                    where,
                )
    return package, report


def read_lockfile(catalog_root: Path) -> dict[str, str]:
    path = catalog_root / LOCK_FILENAME
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    versions = data.get("versions") if isinstance(data, dict) else None
    return dict(versions) if isinstance(versions, dict) else {}


def write_lockfile(catalog_root: Path, versions: dict[str, str]) -> None:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "note": "已发布课程版本的内容哈希。发布后版本目录不可修改；要改内容请发新版本。",
        "versions": dict(sorted(versions.items())),
    }
    (catalog_root / LOCK_FILENAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def check_lock(
    catalog_root: Path, courses: dict[str, CoursePackage], report: ValidationReport
) -> dict[str, str]:
    """已发布/已撤下的版本必须登记在锁文件里，且哈希一致。返回应有的锁内容。"""
    locked = read_lockfile(catalog_root)
    expected: dict[str, str] = dict(locked)
    for course in courses.values():
        for version, status in course.record.versions.items():
            if status == VersionStatus.DRAFT:
                continue
            loaded = course.versions.get(version)
            if loaded is None:
                continue
            key = f"{course.course_id}@{version}"
            if key not in locked:
                report.add(
                    CourseErrorCode.VERSION_UNLOCKED,
                    "已发布版本还没写进锁文件（运行 python -m deeptutor.learning.courses lock）",
                    key,
                )
                expected[key] = loaded.content_hash
            elif locked[key] != loaded.content_hash:
                report.add(
                    CourseErrorCode.VERSION_MODIFIED,
                    "已发布版本的文件被改动过。发布后不能改内容，请复制成新版本号再改",
                    key,
                )
    return expected


def scan_catalog(catalog_root: Path) -> tuple[dict[str, CoursePackage], ValidationReport]:
    """扫描整个课程目录。一门课坏了只影响它自己，其他课照常可用。"""
    report = ValidationReport()
    courses: dict[str, CoursePackage] = {}
    if not catalog_root.is_dir():
        return courses, report
    for child in sorted(catalog_root.iterdir()):
        if not child.is_dir() or child.name.startswith((".", "_")):
            continue
        course, course_report = load_course(child)
        report.extend(course_report)
        if course is not None:
            courses[course.course_id] = course
    return courses, report


def is_valid_version(value: str) -> bool:
    return bool(VERSION_PATTERN.match(value or ""))


__all__ = [
    "LOCK_FILENAME",
    "CoursePackage",
    "CourseVersionPackage",
    "ValidationReport",
    "check_lock",
    "content_hash",
    "is_valid_version",
    "load_course",
    "load_version",
    "read_lockfile",
    "scan_catalog",
    "write_lockfile",
]
