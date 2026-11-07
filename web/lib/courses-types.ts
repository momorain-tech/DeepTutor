/**
 * 官方课程的前端类型——和后端 deeptutor/learning/courses/contract.py 的
 * "对外接口的数据结构"一一对应。改字段要两边一起改（见 CONTRACT.md）。
 *
 * 注意分清两类数据：
 * - 公共课程数据（Course*、Lesson*）：所有人看到的都一样，访客也能读；
 * - 个人选课状态（Enrollment*、Submission*）：只属于当前用户、当前工作区。
 */

export interface CoursePerson {
  name: string
  role: string
  affiliation: string
}

export interface CourseReview {
  reviewers: CoursePerson[]
  reviewed_at: string
  note: string
}

export interface CourseSource {
  id: string
  title: string
  kind: string
  locator: string
  url: string
  edition: string
  publisher: string
  reviewed: boolean
}

export interface CourseKnowledgePoint {
  id: string
  name: string
  type: 'memory' | 'concept' | 'procedure' | 'design'
}

export interface CourseLesson {
  id: string
  title: string
  order: number
  objectives: string[]
  preview: boolean
  estimated_minutes: number | null
  knowledge_points: CourseKnowledgePoint[]
  suspended: boolean
  suspended_reason: string
}

export interface CourseUnit {
  id: string
  title: string
  lessons: string[]
}

export interface CourseSummary {
  course_id: string
  version: string
  title: string
  subtitle: string
  summary: string
  language: string
  tags: string[]
  editors: CoursePerson[]
  review: CourseReview | null
  updated_at: string
  lesson_count: number
  knowledge_point_count: number
  question_count: number
  preview_lessons: string[]
  enrollment_open: boolean
}

export interface CourseDetail extends CourseSummary {
  disclaimer: string
  changelog: string
  units: CourseUnit[]
  lessons: CourseLesson[]
  sources: CourseSource[]
  learning_open: boolean
}

export interface LessonContent {
  course_id: string
  version: string
  lesson: CourseLesson
  /** 审核过的受限 Markdown 原文。 */
  markdown: string
  anchors: string[]
  sources: CourseSource[]
}

export interface CoursePreview {
  course_id: string
  version: string
  lessons: LessonContent[]
}

// ── 个人选课状态 ──────────────────────────────────────────────────────────────

export type LessonReadState = 'not_started' | 'reading' | 'read'

export interface LessonProgress {
  lesson_id: string
  state: LessonReadState
  anchor: string
  percentage: number
  started_at: number | null
  read_at: number | null
  updated_at: number | null
}

/** 掌握状态由学习引擎计算，前端只展示、不推算。 */
export interface EnrollmentMastery {
  knowledge_point_id: string
  path_knowledge_point_id: string
  lesson_id: string
  mastery: number
  mastered: boolean
  /** 做过理解检查（答过题或讲解被评估过）。和"读过""掌握"是三件事。 */
  assessed: boolean
  review_due_at: number | null
}

export interface Enrollment {
  enrollment_id: string
  course_id: string
  course_version: string
  path_id: string
  status: 'active' | 'archived'
  created_at: number
  updated_at: number
  latest_version: string | null
  learning_open: boolean
  title: string
  lessons: LessonProgress[]
  mastery: EnrollmentMastery[]
  review_due_count: number
  current_lesson_id: string
}

export interface EnrolledLesson {
  content: LessonContent
  progress: LessonProgress | null
  previous_lesson_id: string | null
  next_lesson_id: string | null
  module_id: string
  path_id: string
}

export interface QuestionOption {
  label: string
  body: string
}

export type QuestionType = 'single_choice' | 'multiple_choice' | 'true_false' | 'fill_blank' | 'open'
export type GradeResult = 'correct' | 'partial' | 'incorrect'

/** 给学员看的题目：没有答案和解析。 */
export interface PublicQuestion {
  question_id: string
  question_version: number
  lesson_id: string
  knowledge_point_id: string
  type: QuestionType
  prompt: string
  options: QuestionOption[]
  difficulty: string
  last_result: GradeResult | 'pending' | null
}

export interface RubricCriterionResult {
  id: string
  description: string
  points: number
  met: boolean
  comment: string
}

export interface SubmissionResult {
  submission_id: string
  question_id: string
  question_version: number
  status: 'graded' | 'pending' | 'failed'
  result: GradeResult | null
  correct_answer: unknown
  explanation: string
  feedback: string
  criteria: RubricCriterionResult[]
  sources: CourseSource[]
  mastery: number | null
  replayed: boolean
}

/** 后端统一错误码（contract.py 的 CourseErrorCode）。 */
export type CourseErrorCode =
  | 'COURSE_NOT_FOUND'
  | 'COURSE_LESSON_NOT_FOUND'
  | 'COURSE_NOT_ENROLLABLE'
  | 'COURSE_LEARNING_CLOSED'
  | 'COURSE_LESSON_SUSPENDED'
  | 'COURSE_VERSION_UNAVAILABLE'
  | 'COURSE_ENROLLMENT_NOT_FOUND'
  | 'COURSE_QUESTION_NOT_FOUND'
  | 'COURSE_QUESTION_RETIRED'
  | 'COURSE_SUBMISSION_CONFLICT'
  | 'COURSE_FIELD_INVALID'
  | 'COURSE_PATH_LOCKED'
