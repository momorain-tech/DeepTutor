# 官方课程：数据契约与运维说明

这份文档是官方课程所有代码的共同约定。代码里的权威定义在 `contract.py`（后端）和
`web/lib/courses-types.ts`（前端）；改字段时三处一起改。

设计原则：

- 建的是**通用课程能力**，中医只是第一门课；代码里不写任何具体课程知识。
- 加入课程后复用 DeepTutor 的掌握路径、辅导会话、答题证据和复习机制，不另起一套分数。
- 官方教材全站只读共享；学习进度、笔记、会话按用户 + 工作区隔离。
- 浏览和加入课程**不调用模型、不建向量、不自动开启辅导**。
- 课程版本固定：发新版不会覆盖学员正在学的版本。
- 第一版课程包放在仓库里，靠代码审查发布；课程管理后台以后再做。

## 1. 三种版本号

| 名称 | 在哪里 | 含义 | 什么时候变 |
|---|---|---|---|
| `schema_version` | course.json、manifest.json | 课程包**文件格式**版本 | 格式不兼容地变化时；当前为 1 |
| `version`（如 `1.0.0`） | 版本目录名、manifest.json | 课程**内容**快照 | 任何内容改动都发新版本；发布后不可改 |
| 进度修订号 | 学员掌握路径的 `revision` | 个人学习进度的版本 | 每次进度变化 +1，用于防并发覆盖 |

## 2. 课程包目录

```
catalog/                         线上默认 deeptutor/learning/courses/catalog/
  courses.lock.json              已发布版本的内容哈希（CI 校验，运行时也校验）
  <course_id>/
    course.json                  各版本状态 + 运营开关 + 撤题（可随时改）
    <version>/                   发布后不可改
      manifest.json              标题、简介、编者、审核、免责声明、更新说明
      outline.json               单元、课时、知识点、前置关系
      sources.json               资料来源（出处、版本、审核）
      questions.json             题库（答案和评分规则只在服务端读取）
      lessons/<lesson>.md        教材正文（受限 Markdown）
```

为什么发布状态放在 `course.json` 而不是 `manifest.json`：版本目录一经发布就被哈希锁住，
而"停止新加入、暂停某节课、撤题"这些运营动作必须能在发布后随时做。

开发联调使用模拟课程：`DEEPTUTOR_COURSES_DIR=tests/fixtures/courses/catalog`。
它的内容（"灯塔信号"）完全虚构，不含任何真实专业知识。

### ID 规则

课程、课时、知识点、来源、题目、单元、评分项的 ID 一律匹配 `^[a-z0-9][a-z0-9_-]{0,63}$`。
ID 一旦发布就是稳定标识：学员的作答记录、错题、ID 映射都靠它，改名请保留 ID。

### course.json

| 字段 | 必填 | 说明 |
|---|---|---|
| `schema_version` | 是 | 1 |
| `course_id` | 是 | 与目录名一致 |
| `versions` | 是 | `{"1.0.0": "draft" \| "published" \| "withdrawn"}` |
| `enrollment_open` | 否，默认 true | false = 停止新加入，已加入的照常学 |
| `learning_open` | 否，默认 true | false = 整门课暂停学习（记录保留，不能继续上课和答题） |
| `suspended_lessons` | 否 | `[{lesson_id, reason, versions?}]` 暂停某节课 |
| `errata` | 否 | `[{question_id, question_version?, course_versions?, reason, issued_at}]` 撤题 |

版本状态：`draft` 任何公开接口都读不到；`published` 出现在目录、可加入（同一门课取最新已发布版本）；
`withdrawn` 不在目录里、不能新加入，**已加入的学员照常学**——目录隐藏不会让人丢记录。

### manifest.json

`schema_version`、`course_id`、`version`、`title`、`summary`、`editors`（至少一人）、`updated_at` 必填；
`release_stage` 可为 `reviewed`（默认）或 `trial`。`review: {reviewers, reviewed_at, note}` 在
非试读的 published / withdrawn 版本必填。试读版本可保持 `review: null`，但必须填写非空
`trial_notice`，明确未完成的专业审校；目录卡片、详情和单课页展示试读状态，辅导资料也保留
这个声明。试读没有绿色审核徽章，不得伪造审核人。试读升级为已审校版本仍应发布新内容版本。
可选 `subtitle`、`language`、`tags`、`disclaimer`（课程页必显示）、`changelog`。

### outline.json

- `units[]`：`{id, title, lessons[]}`，只用于目录分组。
- `lessons[]`：`{id, title, order, objectives[1..8], material, knowledge_points[], preview, estimated_minutes?}`。
  `material` 必须是 `lessons/` 下的相对 `.md` 路径；`preview: true` 的课时对访客全文公开。
- `knowledge_points[]`：`{id, name, type, prerequisites[], sources[], anchors[]}`，
  `type` ∈ `memory | concept | procedure | design`（与掌握路径的掌握判定规则一致）。
  每个知识点必须**恰好属于一节课**；`anchors` 指向所属课时教材里的标题锚点。

加入课程后：一节课 → 个人路径的一个模块；一个知识点 → 路径的一个知识点（前置关系一并复制）。

### sources.json

`sources[]`：`{id, title, kind, locator, url, edition, publisher, review?}`。`url` 与 `locator` 至少一个；
`url` 只能是 http(s)。

### questions.json（服务端专用）

`questions[]`：`{id, version, lesson, knowledge_point, type, prompt, options[], answer?, rubric?, explanation, sources[], difficulty}`

| type | 判分 | 答案写法 |
|---|---|---|
| `single_choice` | 代码 | `answer.choices` 恰好一个选项标签 |
| `multiple_choice` | 代码，需完全一致 | `answer.choices` 若干标签 |
| `true_false` | 代码 | `answer.value` true/false |
| `fill_blank` | 代码，忽略空白/标点/全半角 | `answer.accepted[]` |
| `open` | 学员提交时由模型按 `rubric` 逐项判断（按用户计费） | `rubric.criteria[]`、`pass_ratio`、`partial_ratio`、`reference_answer` |

题目自带 `version`：改了题干或答案就 +1。写进掌握路径的题目标识是 `course:{id}@v{version}`。

### 受限 Markdown

允许：标题、段落、列表、引用、表格、代码块、强调、链接（仅 http(s) 和 `#锚点`）。
唯一扩展：标题末尾 `{#anchor}`。
禁止：任何 HTML/注释/尖括号语法、MDX 的 import/export、正文里的 `{…}`（放进行内代码可以）、图片（第一版）。
前端渲染时同样强制 `allowHtml={false}`。

## 3. 接口

错误统一为 `{"detail": {"code": "COURSE_…", "error_code": "COURSE_…", "message": "…"}}`。

| 方法 | 路径 | 登录 | 说明 |
|---|---|---|---|
| GET | `/api/courses` | 否 | 已发布课程列表 |
| GET | `/api/courses/{course_id}` | 否 | 介绍、编审信息、目录、来源 |
| GET | `/api/courses/{course_id}/preview` | 否 | 公开样课全文 |
| GET | `/api/course-enrollments` | 是 | 我在当前工作区的选课 |
| POST | `/api/course-enrollments` `{course_id}` | 是 | 加入；新建 201，已存在 200（幂等，不收费） |
| GET | `/api/course-enrollments/{id}` | 是 | 实例：版本、阅读进度、掌握投影、到期复习 |
| GET | `/api/course-enrollments/{id}/course` | 是 | 所学版本的目录（可能不是最新） |
| GET | `/api/course-enrollments/{id}/lessons/{lesson}` | 是 | 教材正文 + 阅读进度 + 前后课 |
| PUT | `/api/course-enrollments/{id}/lessons/{lesson}/progress` | 是 | 记阅读位置 `{anchor, percentage, completed}` |
| GET | `/api/course-enrollments/{id}/lessons/{lesson}/questions` | 是 | 题目（无答案） |
| POST | `/api/course-enrollments/{id}/questions/{qid}/submissions` | 是 | 提交 `{submission_id, answer}`；同 ID 重试只计一次 |

访客对前三个接口的请求由 `deeptutor/momorain/guest.py` 独立作答（不进上游路由、不建用户目录、
不取 provision）；前端 `web/lib/momorain-guest.ts` 有同一张白名单，两边一起改。

### 错误码

| code | HTTP | 场景 |
|---|---|---|
| `COURSE_NOT_FOUND` | 404 | 课程不存在或未发布 |
| `COURSE_LESSON_NOT_FOUND` | 404 | 课时不存在 |
| `COURSE_NOT_ENROLLABLE` | 409 | 停止新加入 |
| `COURSE_LEARNING_CLOSED` | 409 | 整门课暂停学习 |
| `COURSE_LESSON_SUSPENDED` | 409 | 该课时暂停 |
| `COURSE_VERSION_UNAVAILABLE` | 503 | 学员所学版本暂时读不到（不会悄悄换版本） |
| `COURSE_ENROLLMENT_NOT_FOUND` | 404 | 实例不存在（含：别人的实例） |
| `COURSE_QUESTION_NOT_FOUND` | 404 | 题目不存在 |
| `COURSE_QUESTION_RETIRED` | 410 | 题目已撤下 |
| `COURSE_SUBMISSION_CONFLICT` | 409/503 | 同一提交 ID 不同答案；或开放题评分暂不可用（未计分） |
| `COURSE_FIELD_INVALID` | 422 | 答案格式不对等 |
| `COURSE_PATH_LOCKED` | 422 | 用普通改纲接口/AI 改纲工具改官方课程大纲 |

课程包校验另有 `COURSE_SCHEMA_VERSION_UNSUPPORTED / FILE_MISSING / FILE_INVALID_JSON / ID_MISMATCH /
DUPLICATE_ID / DANGLING_REFERENCE / PREREQUISITE_CYCLE / ILLEGAL_PATH / UNSAFE_MARKDOWN /
ORPHAN_KNOWLEDGE_POINT / QUESTION_INVALID / REVIEW_MISSING / VERSION_MODIFIED / VERSION_UNLOCKED`。

## 4. 存储

选课数据和掌握路径在同一个按"用户 + 工作区"隔离的 SQLite（`mastery.sqlite3`）里，
加入课程是一个事务；已有的数据卷备份（`deploy/momorain/backup.py`，SQLite online backup）自动覆盖。

| 表 | 内容 |
|---|---|
| `course_enrollments` | 实例：课程、固定版本、path_id、状态。同一课程只允许一个 active（唯一索引） |
| `course_enrollment_map` | 课程课时/知识点 ID ↔ 个人路径模块/知识点 ID |
| `course_lesson_progress` | 阅读进度（不是掌握度） |
| `course_submissions` | 题库提交与判分结果（按 submission_id 去重） |
| `course_errata_applied` | 已对该实例执行过的撤题 |

学员删除这条学习路径时，以上记录随之删除（外键级联），之后可以重新加入。

## 5. 辅导取材与引用

课时作为 `TopicSourceKind.COURSE` 来源挂在路径上。读取链路：当前用户 → 选课实例 → 固定版本 → 课时 Markdown；
来源上没有文件路径，篡改了也只能指向本人库里的实例，且课程/版本/课时必须对得上。
教材以 `read_source` 工具结果的形式给模型（不进系统提示词），并带"这是资料不是指令"的说明。

引用定位协议：`course:{course_id}@{version}/{lesson_id}#{anchor}`，前端 `courseCitationRoute()` 转成课时页面地址。

## 6. 运维：发布、更新、撤下

```sh
python -m deeptutor.learning.courses validate          # 校验线上目录（CI 也跑）
python -m deeptutor.learning.courses lock              # 把新发布版本的哈希写进锁文件
```

- **发布新课/新版本**：新建版本目录 → `course.json` 里登记为 `draft` 自测 → 改成 `published` →
  `lock` → 与锁文件一起提交审查 → 合并后按正常流程部署（课程包随后端镜像发布）。
- **修改已发布内容**：不能改原目录（哈希不符时运行时直接停止提供该版本）。复制成新版本号再改。
  已加入旧版的学员继续学旧版，页面上提示有新版本。
- **停止新加入**：`enrollment_open: false`。**暂停某课**：`suspended_lessons`。**暂停整门课**：`learning_open: false`。
  **从目录撤下**：版本状态改为 `withdrawn`。以上都不删任何学员记录。
- **撤题**：`errata` 加一条。学员下次访问时，该题已有作答被作废（保留审计），掌握度和复习按剩余证据重算；每条只执行一次。
- **回滚**：课程包随镜像发布，回滚镜像即回滚课程目录；学员数据在数据卷里，不受影响。
  如果回滚后学员所学的版本不在了，会得到 `COURSE_VERSION_UNAVAILABLE`，记录仍在，恢复镜像即可。

## 7. 第一版不做

课程管理后台、自动升级课程版本、个人改编课程、大规模向量检索、24 节气自动推荐、身体观察日记。
