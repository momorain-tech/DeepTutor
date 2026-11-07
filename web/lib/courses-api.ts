/**
 * 官方课程的接口客户端和页面路由。
 *
 * 全部走 requestJson → apiFetch：自动带 /deeptutor 前缀和当前工作区参数
 * （dt_workspace），访客的写操作由 apiFetch 统一弹登录框，这里不另做登录流程。
 */
import { requestJson } from '@/lib/api'
import { LEARNING_HUB } from '@/lib/learning-routes'
import { activeWorkspaceId, scopedUrl } from '@/lib/workspace-scope'
import type {
  CourseDetail,
  CoursePreview,
  CourseSummary,
  EnrolledLesson,
  Enrollment,
  LessonProgress,
  PublicQuestion,
  SubmissionResult,
} from './courses-types'

// ── 页面路由（浏览器地址，不是接口） ───────────────────────────────────────────

export const COURSES_HOME = `${LEARNING_HUB}/courses`

const seg = (value: string) => encodeURIComponent(value)
const inWorkspace = (path: string, workspaceId: string) => (workspaceId ? scopedUrl(path, workspaceId) : path)

export function courseRoute(courseId: string, workspaceId = activeWorkspaceId()): string {
  return inWorkspace(`${COURSES_HOME}/${seg(courseId)}`, workspaceId)
}

export function courseLessonRoute(
  courseId: string,
  lessonId: string,
  anchor = '',
  workspaceId = activeWorkspaceId(),
): string {
  const path = inWorkspace(`${COURSES_HOME}/${seg(courseId)}/lessons/${seg(lessonId)}`, workspaceId)
  return anchor ? `${path}#${seg(anchor)}` : path
}

/**
 * 辅导回答里的教材引用：`course:{课程}@{版本}/{课时}#{锚点}`（见 CONTRACT.md）。
 * 解析成课时页面地址；格式不对返回 null。版本由学员自己的选课实例决定，这里只用于核对。
 */
export function courseCitationRoute(ref: string): string | null {
  const match = /^course:([a-z0-9][a-z0-9_-]*)@(\d+\.\d+\.\d+)\/([a-z0-9][a-z0-9_-]*)(?:#([a-z0-9][a-z0-9_-]*))?$/.exec(
    ref.trim(),
  )
  if (!match) return null
  return courseLessonRoute(match[1], match[3], match[4] ?? '')
}

// ── 公共课程数据（访客也能读） ─────────────────────────────────────────────────

export const listCourses = () =>
  requestJson<{ courses: CourseSummary[] }>('/api/courses', { scope: 'network' }).then(r => r.courses)

export const getCourse = (courseId: string) => requestJson<CourseDetail>(`/api/courses/${seg(courseId)}`)

export const getCoursePreview = (courseId: string) =>
  requestJson<CoursePreview>(`/api/courses/${seg(courseId)}/preview`)

// ── 个人选课（需要登录） ───────────────────────────────────────────────────────

const ENROLLMENTS = '/api/course-enrollments'

export const listEnrollments = () =>
  requestJson<{ enrollments: Enrollment[] }>(ENROLLMENTS).then(r => r.enrollments)

/** 加入课程。后端幂等：重复调用返回已有实例，不会建第二份、不会收费。 */
export const enrollCourse = (courseId: string) =>
  requestJson<{ enrollment: Enrollment; created: boolean }>(ENROLLMENTS, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ course_id: courseId }),
  })

export const getEnrollment = (enrollmentId: string) =>
  requestJson<Enrollment>(`${ENROLLMENTS}/${seg(enrollmentId)}`)

/** 所学版本的目录（可能不是最新版本）。 */
export const getEnrollmentCourse = (enrollmentId: string) =>
  requestJson<CourseDetail>(`${ENROLLMENTS}/${seg(enrollmentId)}/course`)

export const getEnrolledLesson = (enrollmentId: string, lessonId: string) =>
  requestJson<EnrolledLesson>(`${ENROLLMENTS}/${seg(enrollmentId)}/lessons/${seg(lessonId)}`)

export const saveReadingProgress = (
  enrollmentId: string,
  lessonId: string,
  body: { anchor?: string; percentage?: number; completed?: boolean },
) =>
  requestJson<LessonProgress>(`${ENROLLMENTS}/${seg(enrollmentId)}/lessons/${seg(lessonId)}/progress`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

export const listLessonQuestions = (enrollmentId: string, lessonId: string) =>
  requestJson<{ questions: PublicQuestion[] }>(
    `${ENROLLMENTS}/${seg(enrollmentId)}/lessons/${seg(lessonId)}/questions`,
  ).then(r => r.questions)

/** 提交答案。submissionId 由调用方生成并在重试时复用——后端据此只计一次分。 */
export const submitAnswer = (enrollmentId: string, questionId: string, submissionId: string, answer: unknown) =>
  requestJson<SubmissionResult>(`${ENROLLMENTS}/${seg(enrollmentId)}/questions/${seg(questionId)}/submissions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ submission_id: submissionId, answer }),
  })

export function newSubmissionId(): string {
  const random =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`
  return `sub-${random}`.replace(/[^A-Za-z0-9_-]/g, '').slice(0, 80)
}

// ── 教材 Markdown：按 {#锚点} 切成小节 ─────────────────────────────────────────

export interface LessonSection {
  /** 小节标题上的锚点；文首没有锚点的部分为 ''。 */
  anchor: string
  markdown: string
}

/**
 * 课程教材的标题可以带 `{#anchor}`（受限 Markdown 唯一的扩展语法）。
 * 通用 Markdown 渲染器不认识它，所以这里先把正文切成"一个锚点一节"，
 * 每节外面包一个带 id 的 <section>，引用和阅读位置都能精确跳到这里。
 */
export function splitLessonSections(markdown: string): LessonSection[] {
  const sections: LessonSection[] = [{ anchor: '', markdown: '' }]
  let inFence = false
  for (const line of markdown.split('\n')) {
    if (/^\s*(```|~~~)/.test(line)) inFence = !inFence
    const heading = !inFence && /^(#{1,6})\s+(.*?)\s*\{#([a-z0-9][a-z0-9_-]{0,63})\}\s*$/.exec(line)
    if (heading) {
      sections.push({ anchor: heading[3], markdown: `${heading[1]} ${heading[2]}\n` })
    } else {
      sections[sections.length - 1].markdown += `${line}\n`
    }
  }
  return sections.filter(section => section.anchor || section.markdown.trim())
}
