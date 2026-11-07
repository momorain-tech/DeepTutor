'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { ArrowLeft, ArrowRight, CheckCircle2, GraduationCap, Loader2, MessageSquareText, NotebookPen, PenLine } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { LearningEmptyState, LearningErrorState, LearningShell, LearningSkeleton } from '@/components/learning/LearningShell'
import { isApiError } from '@/lib/api'
import {
  courseLessonRoute,
  courseRoute,
  getEnrolledLesson,
  getEnrollment,
  saveReadingProgress,
} from '@/lib/courses-api'
import type { EnrolledLesson, Enrollment, LessonProgress } from '@/lib/courses-types'
import { masterySessionRoute } from '@/lib/mastery-mode'
import { createNote } from '@/lib/notes-api'
import { setPendingPrompt } from '@/lib/pending-prompt'
import { CoursePractice } from './CoursePractice'
import { LessonMarkdown, SourceList, useMyEnrollments } from './CourseParts'

const SAVE_DELAY_MS = 1500

/**
 * /learning/courses/<id>/lessons/<lesson>：加入课程后的课时学习页。
 *
 * - 阅读：读到哪里由服务端记录（刷新、换设备、退出重进都能回到原处）。
 *   **阅读本身不调用模型**，也不算掌握——掌握度只来自练习和辅导里的证据。
 * - 开始辅导：打开这条学习路径已有的辅导会话，并把开场白填进输入框，
 *   由学员自己按发送（不会替学员自动发起付费对话）。
 * - 练习：课程题库，客观题即时判分，开放题提交时才调模型。
 * - 记笔记：在笔记里建一页，标题带上课程和课时。
 */
export function CourseLessonPage({ courseId, lessonId }: { courseId: string; lessonId: string }) {
  const { t } = useTranslation()
  const router = useRouter()
  const { allowed, enrollments, loading: enrollmentsLoading, error: enrollmentsError, reload } = useMyEnrollments()
  const listed = enrollments.find(item => item.course_id === courseId)
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null)
  const [lesson, setLesson] = useState<EnrolledLesson | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [progress, setProgress] = useState<LessonProgress | null>(null)
  const [practiceOpen, setPracticeOpen] = useState(false)
  const [noting, setNoting] = useState(false)
  const restoredRef = useRef(false)

  const enrollmentId = listed?.enrollment_id
  const loadLesson = useCallback(() => {
    if (!enrollmentId) return
    setError(null)
    restoredRef.current = false
    Promise.all([getEnrolledLesson(enrollmentId, lessonId), getEnrollment(enrollmentId)])
      .then(([content, described]) => {
        setLesson(content)
        setProgress(content.progress)
        setEnrollment(described)
      })
      .catch(err => setError(isApiError(err) ? err.message : String(err)))
  }, [enrollmentId, lessonId])
  useEffect(loadLesson, [loadLesson])

  // 切换账号 / 退出：清掉上一个人的内容，不让它停留在屏幕上。
  useEffect(() => {
    if (!allowed) {
      setLesson(null)
      setEnrollment(null)
      setProgress(null)
    }
  }, [allowed])

  // ── 阅读位置：恢复 + 节流保存 ───────────────────────────────────────────────
  useEffect(() => {
    if (!lesson || restoredRef.current) return
    restoredRef.current = true
    const hash = typeof window !== 'undefined' ? decodeURIComponent(window.location.hash.slice(1)) : ''
    const target = hash || lesson.progress?.anchor
    if (target) {
      requestAnimationFrame(() => document.getElementById(target)?.scrollIntoView({ block: 'start' }))
    }
  }, [lesson])

  const pendingSave = useRef<{ anchor: string; percentage: number } | null>(null)
  const saveTimer = useRef<number | null>(null)
  const flush = useCallback(() => {
    if (!enrollmentId || !pendingSave.current) return
    const body = pendingSave.current
    pendingSave.current = null
    saveReadingProgress(enrollmentId, lessonId, body)
      .then(setProgress)
      .catch(() => {
        /* 阅读位置是尽力保存的：失败不打扰学员，下次滚动会再存 */
      })
  }, [enrollmentId, lessonId])

  const contentRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!lesson) return
    const onScroll = () => {
      const root = contentRef.current
      if (!root) return
      const rect = root.getBoundingClientRect()
      const viewport = window.innerHeight
      const percentage = Math.max(0, Math.min(1, (viewport - rect.top) / Math.max(rect.height, 1)))
      const sections = Array.from(root.querySelectorAll<HTMLElement>('[data-course-anchor]'))
      // 正在读的小节 = 标题已经越过屏幕上方 35% 的最后一节；
      // 读到底时（短课文标题可能永远到不了那条线）取屏幕内的最后一节。
      const atEnd = rect.bottom <= viewport + 4
      const line = atEnd ? viewport : viewport * 0.35
      const current = sections.filter(section => section.getBoundingClientRect().top < line).pop()
      pendingSave.current = { anchor: current?.dataset.courseAnchor ?? '', percentage }
      if (saveTimer.current) window.clearTimeout(saveTimer.current)
      saveTimer.current = window.setTimeout(flush, SAVE_DELAY_MS)
    }
    // 页面真正滚动的是布局里的某个内层容器（两种布局还不一样），scroll 事件不冒泡，
    // 所以在 document 上用捕获阶段统一接住，不管是谁在滚都能记录位置。
    document.addEventListener('scroll', onScroll, { passive: true, capture: true })
    const onHide = () => {
      if (document.visibilityState === 'hidden') flush()
    }
    document.addEventListener('visibilitychange', onHide)
    return () => {
      document.removeEventListener('scroll', onScroll, { capture: true })
      document.removeEventListener('visibilitychange', onHide)
      if (saveTimer.current) window.clearTimeout(saveTimer.current)
      flush()
    }
  }, [lesson, flush])

  const markRead = async () => {
    if (!enrollmentId) return
    try {
      const saved = await saveReadingProgress(enrollmentId, lessonId, { completed: true })
      setProgress(saved)
    } catch (err) {
      setError(isApiError(err) ? err.message : String(err))
    }
  }

  const startTutoring = () => {
    if (!lesson) return
    // 填进输入框而不是直接发送：发不发、什么时候发由学员决定。
    setPendingPrompt(
      t('Let\'s study the lesson "{{lesson}}". Please teach me from the course material.', {
        lesson: lesson.content.lesson.title,
      }),
      'mastery_path',
    )
    router.push(masterySessionRoute(lesson.path_id, 'study'))
  }

  const takeNote = async () => {
    if (!lesson || noting) return
    setNoting(true)
    try {
      const title = `${enrollment?.title ?? ''} · ${lesson.content.lesson.title}`
      const doc = await createNote(title, null, `> ${lesson.content.lesson.objectives.join('；')}\n\n`)
      router.push(`/co-writer/${encodeURIComponent(doc.id)}`)
    } catch (err) {
      if (!(isApiError(err) && err.status === 401)) setError(isApiError(err) ? err.message : String(err))
    } finally {
      setNoting(false)
    }
  }

  const masteryRows = useMemo(() => {
    if (!lesson || !enrollment) return []
    return lesson.content.lesson.knowledge_points.map(kp => ({
      kp,
      state: enrollment.mastery.find(item => item.knowledge_point_id === kp.id),
    }))
  }, [lesson, enrollment])

  // ── 各种"进不来"的情况 ─────────────────────────────────────────────────────
  if (!allowed) {
    return (
      <LearningShell title={t('Official courses')} subtitle="" icon={GraduationCap}>
        <LearningEmptyState
          title={t('Sign in to study this lesson')}
          description={t('Lessons are part of your own learning path. You can still read the free preview on the course page.')}
          action={
            <Link href={courseRoute(courseId)} className="text-sm text-primary hover:underline">
              {t('Open course page')}
            </Link>
          }
        />
      </LearningShell>
    )
  }
  if (enrollmentsError) {
    return (
      <LearningShell title={t('Official courses')} subtitle="" icon={GraduationCap}>
        <LearningErrorState message={enrollmentsError} onRetry={reload} />
      </LearningShell>
    )
  }
  if (!enrollmentsLoading && !listed) {
    return (
      <LearningShell title={t('Official courses')} subtitle="" icon={GraduationCap}>
        <LearningEmptyState
          title={t('You have not joined this course yet')}
          action={
            <Link href={courseRoute(courseId)} className="text-sm text-primary hover:underline">
              {t('Open course page')}
            </Link>
          }
        />
      </LearningShell>
    )
  }
  if (!lesson) {
    return (
      <LearningShell title={t('Official courses')} subtitle="" icon={GraduationCap}>
        {error ? <LearningErrorState message={error} onRetry={loadLesson} /> : <LearningSkeleton count={4} />}
      </LearningShell>
    )
  }

  const content = lesson.content
  const answered = enrollment?.mastery.some(item => item.lesson_id === content.lesson.id && item.assessed)
  return (
    <LearningShell title={content.lesson.title} subtitle={enrollment?.title ?? ''} icon={GraduationCap}>
      <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-[var(--muted-foreground)]">
        <Link href={courseRoute(courseId)} className="hover:text-[var(--foreground)]">
          ← {t('Course contents')}
        </Link>
        <span>v{content.version}</span>
      </div>
      {error && <LearningErrorState message={error} onRetry={loadLesson} />}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_17rem]">
        <article ref={contentRef} className="min-w-0 rounded-2xl border border-border bg-card p-5 sm:p-7">
          <div className="mb-5 rounded-xl bg-muted/50 px-4 py-3 text-[12.5px] leading-[1.7]">
            <div className="mb-1 font-medium">{t('Learning objectives')}</div>
            <ul className="list-disc pl-5">
              {content.lesson.objectives.map(item => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
          <LessonMarkdown markdown={content.markdown} />
          {content.sources.length > 0 && (
            <div className="mt-6 border-t border-border pt-4">
              <h2 className="mb-2 text-[12px] font-medium text-[var(--muted-foreground)]">{t('Sources')}</h2>
              <SourceList sources={content.sources} />
            </div>
          )}
          <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
            {progress?.state === 'read' ? (
              <span className="inline-flex items-center gap-1.5 text-[13px] text-emerald-700 dark:text-emerald-300">
                <CheckCircle2 size={16} aria-hidden="true" />
                {t('Lesson read')}
              </span>
            ) : (
              <button
                type="button"
                onClick={markRead}
                className="inline-flex items-center gap-1.5 rounded-xl border border-border px-3.5 py-2 text-[13px] hover:bg-muted"
              >
                <CheckCircle2 size={16} aria-hidden="true" />
                {t('I have finished reading')}
              </button>
            )}
            <div className="flex gap-2">
              {lesson.previous_lesson_id && (
                <Link
                  href={courseLessonRoute(courseId, lesson.previous_lesson_id)}
                  className="inline-flex items-center gap-1 rounded-xl px-3 py-2 text-[13px] hover:bg-muted"
                >
                  <ArrowLeft size={15} aria-hidden="true" />
                  {t('Previous lesson')}
                </Link>
              )}
              {lesson.next_lesson_id && (
                <Link
                  href={courseLessonRoute(courseId, lesson.next_lesson_id)}
                  className="inline-flex items-center gap-1 rounded-xl bg-primary px-3.5 py-2 text-[13px] font-medium text-primary-foreground hover:brightness-110"
                >
                  {t('Next lesson')}
                  <ArrowRight size={15} aria-hidden="true" />
                </Link>
              )}
            </div>
          </div>
        </article>

        <aside className="space-y-4 lg:sticky lg:top-4 lg:self-start">
          <div className="space-y-2 rounded-2xl border border-border bg-card p-4">
            <button
              type="button"
              onClick={startTutoring}
              disabled={!enrollment?.learning_open}
              className="flex w-full items-center gap-2 rounded-xl bg-primary px-3.5 py-2.5 text-[13px] font-medium text-primary-foreground hover:brightness-110 disabled:opacity-60"
            >
              <MessageSquareText size={16} aria-hidden="true" />
              {t('Start tutoring')}
            </button>
            <button
              type="button"
              onClick={() => setPracticeOpen(open => !open)}
              aria-expanded={practiceOpen}
              className="flex w-full items-center gap-2 rounded-xl border border-border px-3.5 py-2.5 text-[13px] hover:bg-muted"
            >
              <PenLine size={16} aria-hidden="true" />
              {t('Practice')}
            </button>
            <button
              type="button"
              onClick={takeNote}
              disabled={noting}
              className="flex w-full items-center gap-2 rounded-xl border border-border px-3.5 py-2.5 text-[13px] hover:bg-muted disabled:opacity-60"
            >
              {noting ? <Loader2 size={16} className="animate-spin" aria-hidden="true" /> : <NotebookPen size={16} aria-hidden="true" />}
              {t('Take notes')}
            </button>
            <p className="pt-1 text-[11px] leading-[1.6] text-[var(--muted-foreground)]">
              {t('Reading is free. Tutoring and grading of open questions use your model balance.')}
            </p>
          </div>

          {/* 三种状态分开展示：读过 ≠ 检查过理解 ≠ 掌握 */}
          <div className="rounded-2xl border border-border bg-card p-4 text-[12.5px]">
            <h2 className="mb-3 text-[12.5px] font-semibold">{t('Where you are')}</h2>
            <dl className="space-y-2">
              <div className="flex justify-between gap-2">
                <dt className="text-[var(--muted-foreground)]">{t('Reading')}</dt>
                <dd>
                  {progress?.state === 'read'
                    ? t('Read')
                    : progress?.state === 'reading'
                      ? t('{{percent}}% read', { percent: Math.round((progress.percentage ?? 0) * 100) })
                      : t('Not started')}
                </dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-[var(--muted-foreground)]">{t('Understanding check')}</dt>
                <dd>{answered ? t('Started') : t('Not yet')}</dd>
              </div>
            </dl>
            <h3 className="mb-2 mt-4 text-[12px] font-medium text-[var(--muted-foreground)]">{t('Mastery')}</h3>
            <ul className="space-y-2">
              {masteryRows.map(({ kp, state }) => (
                <li key={kp.id}>
                  <div className="flex justify-between gap-2">
                    <span className="min-w-0 truncate">{kp.name}</span>
                    <span className={state?.mastered ? 'text-emerald-700 dark:text-emerald-300' : 'text-[var(--muted-foreground)]'}>
                      {state?.mastered ? t('Mastered') : state?.assessed ? t('Learning') : t('Not started')}
                    </span>
                  </div>
                  <div className="mt-1 h-1 overflow-hidden rounded-full bg-muted">
                    <div className="h-full rounded-full bg-primary" style={{ width: `${Math.round((state?.mastery ?? 0) * 100)}%` }} />
                  </div>
                </li>
              ))}
            </ul>
            <p className="mt-3 text-[11px] leading-[1.6] text-[var(--muted-foreground)]">
              {t('Mastery comes from practice and tutoring, not from reading alone.')}
            </p>
          </div>
        </aside>
      </div>

      {practiceOpen && enrollmentId && (
        <CoursePractice
          enrollmentId={enrollmentId}
          lessonId={lessonId}
          onGraded={() => getEnrollment(enrollmentId).then(setEnrollment).catch(() => {})}
        />
      )}
    </LearningShell>
  )
}
