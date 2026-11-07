"""官方课程的数据契约（P0）：课程包文件格式 + 对外接口的数据结构 + 统一错误码。

这里是所有课程相关代码的"共同语言"。课程包读取（P1）、加入课程（P2）、
辅导取材（P3）、题库（P4）、前端（P5/P6，对应 web/lib/courses-types.ts）
都只认这里定义的结构；要加字段，先改这里和 CONTRACT.md，再改各自的代码。

三种"版本"千万别混：

- ``schema_version``：课程包**文件格式**的版本（这个文件定义的结构）。
  格式不兼容地变了才加一，读取方见到不认识的格式版本直接拒绝。
- ``version``（课程内容版本，形如 1.0.0）：一份**发布出去就不可再改**的教材快照。
  勘误、加课都发新版本；已加入的学员继续学自己加入时的那个版本。
- 学习进度修订号：用户个人掌握路径的 ``LearningProgress.version``（数据库里叫
  revision），每次进度变化加一，用来防并发覆盖。它属于学员，和课程无关。

为什么发布状态不放进 ``<version>/manifest.json``：版本目录发布后被哈希锁死
（见 lockfile），而"停止新加入""暂停某课时""撤题"这些运营动作必须能在发布后
随时调整。所以它们放在课程级的 ``course.json`` 里，这个文件可以改，版本目录不行。
"""

from __future__ import annotations

from datetime import date
from enum import Enum
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: 当前读得懂的课程包格式版本。
SCHEMA_VERSION = 1

#: 课程、课时、知识点、来源、题目的 ID：小写字母数字开头，只含小写字母、数字、- 和 _。
#: 这么严是因为它们会拼进路径、URL 和数据库键，宁可在写课程时报错，也别到线上才出事。
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
#: 课程内容版本只接受三段数字（1.0.0），方便排序，也避免 latest 这种会变的名字。
VERSION_PATTERN = re.compile(r"^(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})\.(0|[1-9]\d{0,3})$")


def version_key(version: str) -> tuple[int, int, int]:
    """把 "1.10.0" 变成 (1, 10, 0)，这样比较大小才对（按字符串比 1.10 会排在 1.9 前面）。"""
    major, minor, patch = (int(part) for part in version.split("."))
    return major, minor, patch


def _check_id(value: str, what: str) -> str:
    if not isinstance(value, str) or not ID_PATTERN.match(value):
        raise ValueError(
            f"{what} {value!r} 不合法：只能用小写字母、数字、- 和 _，且以字母或数字开头"
        )
    return value


# ── 统一错误码 ────────────────────────────────────────────────────────────────


class CourseErrorCode(str, Enum):
    """校验和接口共用的错误码。前端按 code 判断，不要去解析 message 文案。"""

    # 课程包校验（写课程时、CI 和服务启动扫描时出现）
    SCHEMA_VERSION_UNSUPPORTED = "COURSE_SCHEMA_VERSION_UNSUPPORTED"
    FILE_MISSING = "COURSE_FILE_MISSING"
    FILE_INVALID_JSON = "COURSE_FILE_INVALID_JSON"
    FIELD_INVALID = "COURSE_FIELD_INVALID"  # 缺必填字段、类型不对、取值不合法
    ID_MISMATCH = "COURSE_ID_MISMATCH"  # 目录名和文件里写的 ID / 版本不一致
    DUPLICATE_ID = "COURSE_DUPLICATE_ID"
    DANGLING_REFERENCE = "COURSE_DANGLING_REFERENCE"  # 引用了不存在的课时/知识点/来源
    PREREQUISITE_CYCLE = "COURSE_PREREQUISITE_CYCLE"
    ILLEGAL_PATH = "COURSE_ILLEGAL_PATH"  # 教材路径越界、绝对路径、软链接、非 .md
    UNSAFE_MARKDOWN = "COURSE_UNSAFE_MARKDOWN"  # 教材里出现 HTML / 脚本 / MDX
    ORPHAN_KNOWLEDGE_POINT = "COURSE_ORPHAN_KNOWLEDGE_POINT"  # 知识点不属于任何课时或属于多个
    QUESTION_INVALID = "COURSE_QUESTION_INVALID"  # 题型和答案/评分规则对不上
    REVIEW_MISSING = "COURSE_REVIEW_MISSING"  # 已发布版本没有审核信息
    VERSION_MODIFIED = "COURSE_VERSION_MODIFIED"  # 已发布版本的文件被改了（锁文件哈希不符）
    VERSION_UNLOCKED = "COURSE_VERSION_UNLOCKED"  # 已发布版本还没写进锁文件

    # 接口（运行时）
    COURSE_NOT_FOUND = "COURSE_NOT_FOUND"
    LESSON_NOT_FOUND = "COURSE_LESSON_NOT_FOUND"
    NOT_ENROLLABLE = "COURSE_NOT_ENROLLABLE"  # 课程停止新加入
    LEARNING_CLOSED = "COURSE_LEARNING_CLOSED"  # 整门课暂停学习
    LESSON_SUSPENDED = "COURSE_LESSON_SUSPENDED"
    VERSION_UNAVAILABLE = "COURSE_VERSION_UNAVAILABLE"  # 学员所学的版本文件已不在服务器上
    ENROLLMENT_NOT_FOUND = "COURSE_ENROLLMENT_NOT_FOUND"
    QUESTION_NOT_FOUND = "COURSE_QUESTION_NOT_FOUND"
    QUESTION_RETIRED = "COURSE_QUESTION_RETIRED"
    SUBMISSION_CONFLICT = "COURSE_SUBMISSION_CONFLICT"  # 同一个提交 ID 带了不同的答案
    PATH_LOCKED = "COURSE_PATH_LOCKED"  # 想用普通编辑接口改官方课程的大纲


class CourseIssue(BaseModel):
    """校验发现的一个问题。``where`` 指到具体文件和字段，方便写课程的人直接定位。"""

    model_config = ConfigDict(extra="forbid")

    code: CourseErrorCode
    message: str
    where: str = ""
    severity: Literal["error", "warning"] = "error"


def error_detail(code: CourseErrorCode, message: str, **extra: Any) -> dict[str, Any]:
    """接口错误统一长这样：``{"code": ..., "message": ...}``，放在 HTTPException.detail 里。

    同时给一份 ``error_code``：前端公共请求层（web/shared/api/client.ts）认的是这个字段名。
    """
    return {"code": code.value, "error_code": code.value, "message": message, **extra}


# ── 课程包文件：course.json（课程级、可改） ─────────────────────────────────────


class VersionStatus(str, Enum):
    DRAFT = "draft"  # 写作中：不出现在目录里，任何公开接口都读不到
    PUBLISHED = "published"  # 已发布：出现在目录里，可加入
    WITHDRAWN = "withdrawn"  # 已撤下：目录里不再显示、不可新加入，已加入的学员照常学


class SuspendedLesson(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lesson_id: str
    reason: str = Field(min_length=1, max_length=300)
    #: 只暂停这些版本；留空 = 所有版本。
    versions: list[str] = Field(default_factory=list)

    _id = field_validator("lesson_id")(lambda v: _check_id(v, "课时 ID"))


class QuestionErratum(BaseModel):
    """撤下一道题。已作答的记录保留做审计，但不再计入掌握度（见 P4 的纠错机制）。"""

    model_config = ConfigDict(extra="forbid")

    question_id: str
    #: 只撤这一版题目；留空 = 这道题的所有版本。
    question_version: int | None = Field(default=None, ge=1)
    #: 只在这些课程版本里撤；留空 = 所有课程版本。
    course_versions: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1, max_length=500)
    issued_at: date

    _id = field_validator("question_id")(lambda v: _check_id(v, "题目 ID"))


class CourseRecord(BaseModel):
    """``courses/<course_id>/course.json``：一门课所有版本的发布状态和运营开关。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: int
    course_id: str
    versions: dict[str, VersionStatus]
    #: 关掉 = 停止新加入（已加入的学员不受影响）。
    enrollment_open: bool = True
    #: 关掉 = 整门课暂停学习（学员仍能看到自己的记录，但不能继续上课和答题）。
    learning_open: bool = True
    suspended_lessons: list[SuspendedLesson] = Field(default_factory=list)
    errata: list[QuestionErratum] = Field(default_factory=list)

    _id = field_validator("course_id")(lambda v: _check_id(v, "课程 ID"))

    @field_validator("versions")
    @classmethod
    def _versions(cls, value: dict[str, VersionStatus]) -> dict[str, VersionStatus]:
        if not value:
            raise ValueError("至少要登记一个版本")
        for version in value:
            if not VERSION_PATTERN.match(version):
                raise ValueError(f"版本号 {version!r} 不合法，应形如 1.0.0")
        return value


# ── 课程包文件：<version>/manifest.json（版本级、发布后不可改） ──────────────────


class CoursePerson(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    role: str = Field(default="", max_length=80)
    affiliation: str = Field(default="", max_length=120)


class CourseReview(BaseModel):
    """这一版内容由谁、在哪天审核过。正式审校版必须有，试读版可保持空。"""

    model_config = ConfigDict(extra="forbid")

    reviewers: list[CoursePerson] = Field(min_length=1)
    reviewed_at: date
    note: str = Field(default="", max_length=500)


class CourseManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int
    course_id: str
    version: str
    title: str = Field(min_length=1, max_length=80)
    subtitle: str = Field(default="", max_length=120)
    summary: str = Field(min_length=1, max_length=2000)
    language: str = Field(default="zh-CN", max_length=16)
    tags: list[str] = Field(default_factory=list, max_length=12)
    editors: list[CoursePerson] = Field(min_length=1)
    # 试读可公开，但必须带明确提示；不得为满足发布校验伪造审核人。
    release_stage: Literal["reviewed", "trial"] = "reviewed"
    trial_notice: str = Field(default="", max_length=1000)
    review: CourseReview | None = None
    updated_at: date
    #: 课程页上必须展示的提示（比如"本课程内容不能替代专业意见"），由课程作者自己写。
    disclaimer: str = Field(default="", max_length=1000)
    #: 本版相对上一版改了什么，给已加入旧版本的学员看。
    changelog: str = Field(default="", max_length=2000)

    _id = field_validator("course_id")(lambda v: _check_id(v, "课程 ID"))

    @field_validator("version")
    @classmethod
    def _version(cls, value: str) -> str:
        if not VERSION_PATTERN.match(value):
            raise ValueError(f"版本号 {value!r} 不合法，应形如 1.0.0")
        return value


# ── 课程包文件：<version>/outline.json ─────────────────────────────────────────

#: 与掌握路径的 KnowledgeType 一一对应——课程知识点加入后直接变成路径知识点，
#: 掌握判定（记忆/程序型看答题，概念/设计型看讲解）沿用 DeepTutor 现有规则。
KnowledgeKind = Literal["memory", "concept", "procedure", "design"]


class OutlineUnit(BaseModel):
    """目录里的"章"：只用于展示分组，不参与学习逻辑。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str = Field(min_length=1, max_length=80)
    lessons: list[str] = Field(min_length=1)

    _id = field_validator("id")(lambda v: _check_id(v, "单元 ID"))


class OutlineLesson(BaseModel):
    """一节课。加入课程后，一节课对应个人掌握路径里的一个"模块"。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str = Field(min_length=1, max_length=80)
    order: int = Field(ge=1)
    objectives: list[str] = Field(min_length=1, max_length=8)
    #: 教材文件，相对版本目录，必须在 lessons/ 下、以 .md 结尾。
    material: str
    knowledge_points: list[str] = Field(min_length=1)
    #: 是否作为公开样课（访客不登录也能读全文）。
    preview: bool = False
    estimated_minutes: int | None = Field(default=None, ge=1, le=600)

    _id = field_validator("id")(lambda v: _check_id(v, "课时 ID"))


class OutlineKnowledgePoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str = Field(min_length=1, max_length=120)
    type: KnowledgeKind
    prerequisites: list[str] = Field(default_factory=list)
    #: 支撑这个知识点的资料来源（sources.json 里的 ID）。
    sources: list[str] = Field(default_factory=list)
    #: 教材里讲这个知识点的位置：所属课时 Markdown 里的标题锚点（``## 标题 {#anchor}``）。
    anchors: list[str] = Field(default_factory=list)

    _id = field_validator("id")(lambda v: _check_id(v, "知识点 ID"))


class CourseOutline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    units: list[OutlineUnit] = Field(default_factory=list)
    lessons: list[OutlineLesson] = Field(min_length=1)
    knowledge_points: list[OutlineKnowledgePoint] = Field(min_length=1)


# ── 课程包文件：<version>/sources.json ─────────────────────────────────────────


class SourceReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewed_by: str = Field(min_length=1, max_length=80)
    reviewed_at: date
    note: str = Field(default="", max_length=300)


class CourseSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str = Field(min_length=1, max_length=200)
    kind: Literal["book", "article", "guideline", "standard", "web", "other"] = "other"
    #: 书的章节页码、标准的条款号等——"去哪里核对"。url 和 locator 至少给一个。
    locator: str = Field(default="", max_length=300)
    url: str = Field(default="", max_length=500)
    edition: str = Field(default="", max_length=80)
    publisher: str = Field(default="", max_length=120)
    review: SourceReview | None = None

    _id = field_validator("id")(lambda v: _check_id(v, "来源 ID"))

    @model_validator(mode="after")
    def _where(self) -> CourseSource:
        if not self.locator.strip() and not self.url.strip():
            raise ValueError("url 和 locator 至少要给一个，否则读者没法核对出处")
        if self.url and not self.url.startswith(("https://", "http://")):
            raise ValueError("url 只接受 http(s) 链接")
        return self


class CourseSources(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: list[CourseSource] = Field(default_factory=list)


# ── 课程包文件：<version>/questions.json（只在服务端读取） ──────────────────────

QuestionType = Literal["single_choice", "multiple_choice", "true_false", "fill_blank", "open"]
#: 能由代码直接判对错的题型；open 要按评分规则交给模型评。
OBJECTIVE_TYPES: frozenset[str] = frozenset(
    {"single_choice", "multiple_choice", "true_false", "fill_blank"}
)


class QuestionOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=4)
    body: str = Field(min_length=1, max_length=500)


class QuestionAnswer(BaseModel):
    """标准答案。按题型只填其中一种：选择题填 choices，判断题填 value，填空题填 accepted。"""

    model_config = ConfigDict(extra="forbid")

    choices: list[str] = Field(default_factory=list)
    value: bool | None = None
    accepted: list[str] = Field(default_factory=list)


class RubricCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    description: str = Field(min_length=1, max_length=500)
    points: int = Field(ge=1, le=100)

    _id = field_validator("id")(lambda v: _check_id(v, "评分项 ID"))


class QuestionRubric(BaseModel):
    """开放题的评分规则：模型逐项判断是否达到，得分比例 ≥ pass_ratio 记为答对。"""

    model_config = ConfigDict(extra="forbid")

    criteria: list[RubricCriterion] = Field(min_length=1, max_length=10)
    #: 部分达到时记"部分正确"的下限；低于它记答错。
    partial_ratio: float = Field(default=0.4, ge=0, le=1)
    pass_ratio: float = Field(default=0.7, gt=0, le=1)
    reference_answer: str = Field(default="", max_length=2000)


class CourseQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    #: 题目自己的版本：改了题干或答案就加一。错题、作答记录都按 (id, version) 记，
    #: 撤题时可以只撤某一版。
    version: int = Field(ge=1)
    lesson: str
    knowledge_point: str
    type: QuestionType
    prompt: str = Field(min_length=1, max_length=2000)
    options: list[QuestionOption] = Field(default_factory=list)
    answer: QuestionAnswer | None = None
    rubric: QuestionRubric | None = None
    explanation: str = Field(default="", max_length=2000)
    sources: list[str] = Field(default_factory=list)
    difficulty: Literal["easy", "medium", "hard"] = "medium"

    _id = field_validator("id")(lambda v: _check_id(v, "题目 ID"))


class CourseQuestions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questions: list[CourseQuestion] = Field(default_factory=list)


# ── 对外接口的数据结构（P1/P2/P4/P6 的响应，前端类型见 web/lib/courses-types.ts） ──


class PublicPerson(BaseModel):
    name: str
    role: str = ""
    affiliation: str = ""


class PublicReview(BaseModel):
    reviewers: list[PublicPerson]
    reviewed_at: str
    note: str = ""


class PublicSource(BaseModel):
    id: str
    title: str
    kind: str
    locator: str = ""
    url: str = ""
    edition: str = ""
    publisher: str = ""
    reviewed: bool = False


class PublicKnowledgePoint(BaseModel):
    id: str
    name: str
    type: str


class PublicLesson(BaseModel):
    id: str
    title: str
    order: int
    objectives: list[str]
    preview: bool
    estimated_minutes: int | None = None
    knowledge_points: list[PublicKnowledgePoint]
    suspended: bool = False
    suspended_reason: str = ""


class PublicUnit(BaseModel):
    id: str
    title: str
    lessons: list[str]


class CourseSummary(BaseModel):
    """课程列表里的一张卡片。"""

    course_id: str
    version: str
    title: str
    subtitle: str = ""
    summary: str
    language: str
    tags: list[str]
    editors: list[PublicPerson]
    review: PublicReview | None
    release_stage: Literal["reviewed", "trial"] = "reviewed"
    trial_notice: str = ""
    updated_at: str
    lesson_count: int
    knowledge_point_count: int
    question_count: int
    preview_lessons: list[str]
    enrollment_open: bool


class CourseDetail(CourseSummary):
    disclaimer: str = ""
    changelog: str = ""
    units: list[PublicUnit]
    lessons: list[PublicLesson]
    sources: list[PublicSource]
    learning_open: bool = True


class LessonContent(BaseModel):
    """一节课的教材正文。试读状态随原文传递，前端按受限 Markdown 渲染。"""

    course_id: str
    version: str
    release_stage: Literal["reviewed", "trial"] = "reviewed"
    trial_notice: str = ""
    lesson: PublicLesson
    markdown: str
    anchors: list[str]
    sources: list[PublicSource]


class CoursePreview(BaseModel):
    course_id: str
    version: str
    lessons: list[LessonContent]


class LessonProgressRecord(BaseModel):
    """阅读进度：读到哪、是否读完。它只说明"读过"，**不代表掌握**——
    掌握度只来自答题和讲解证据，由学习引擎计算。"""

    lesson_id: str
    state: Literal["not_started", "reading", "read"] = "not_started"
    anchor: str = ""
    percentage: float = 0.0
    started_at: float | None = None
    read_at: float | None = None
    updated_at: float | None = None


class EnrollmentMastery(BaseModel):
    """从学习引擎投影出来的掌握状态（只读，前端不要自己推算）。"""

    knowledge_point_id: str  # 课程里的知识点 ID
    path_knowledge_point_id: str  # 个人路径里的知识点 ID
    lesson_id: str  # 所属课时：复习任务据此跳回原课时
    mastery: float
    mastered: bool
    #: 做过理解检查：答过题（未作废）或在辅导里被评估过讲解。和"读过"、"掌握"都不同。
    assessed: bool = False
    review_due_at: float | None = None


class Enrollment(BaseModel):
    enrollment_id: str
    course_id: str
    course_version: str
    path_id: str
    status: Literal["active", "archived"]
    created_at: float
    updated_at: float
    #: 课程有更新的版本时给出（不会自动升级，只提示）。
    latest_version: str | None = None
    learning_open: bool = True
    title: str = ""
    lessons: list[LessonProgressRecord] = Field(default_factory=list)
    mastery: list[EnrollmentMastery] = Field(default_factory=list)
    review_due_count: int = 0
    current_lesson_id: str = ""


class PublicQuestion(BaseModel):
    """给学员看的题目：没有答案、没有评分规则、没有解析。"""

    question_id: str
    question_version: int
    lesson_id: str
    knowledge_point_id: str
    type: str
    prompt: str
    options: list[QuestionOption] = Field(default_factory=list)
    difficulty: str = "medium"
    #: 学员上一次在这道题上的结果（没做过为 None）。
    last_result: Literal["correct", "partial", "incorrect", "pending"] | None = None


class SubmissionResult(BaseModel):
    submission_id: str
    question_id: str
    question_version: int
    status: Literal["graded", "pending", "failed"]
    result: Literal["correct", "partial", "incorrect"] | None = None
    #: 判分后才给：标准答案（选择题是选项标签）、解析、开放题的逐项评价。
    correct_answer: Any = None
    explanation: str = ""
    feedback: str = ""
    criteria: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[PublicSource] = Field(default_factory=list)
    mastery: float | None = None
    #: 本次是不是重放了之前的结果（重复提交时为 True，不会再计一次分）。
    replayed: bool = False


__all__ = [name for name in dir() if not name.startswith("_")]
