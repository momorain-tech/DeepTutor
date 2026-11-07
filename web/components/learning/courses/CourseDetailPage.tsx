'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { BookOpenCheck, GraduationCap, Loader2, Lock, Map as MapIcon, PlayCircle } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { LearningErrorState, LearningShell, LearningSkeleton } from '@/components/learning/LearningShell'
import { isApiError } from '@/lib/api'
import { COURSES_HOME, courseLessonRoute, enrollCourse, getCourse, getCoursePreview } from '@/lib/courses-api'
import type { CourseDetail, CoursePreview, Enrollment } from '@/lib/courses-types'
import { masteryTopicRoute } from '@/lib/learning-routes'
import { requestLogin } from '@/lib/momorain-guest'
import { useMomorainPrincipal } from '@/lib/momorain-guest-hooks'
import { CourseCredits, CourseTrialNotice, LessonMarkdown, SourceList, readProgressOf, useMyEnrollments } from './CourseParts'

/**
 * /learning/courses/<id>：课程介绍、目录、公开样课、加入/继续学习。
 *
 * 浏览这一页不调用模型、不建向量；"加入课程"只是把课程复制成个人学习路径，
 * 同样不收费。真正花钱的只有之后学员主动发起的辅导和开放题评分。
 */
export function CourseDetailPage({ courseId }: { courseId: string }) {
  const { t } = useTranslation()
  const router = useRouter()
  const principal = useMomorainPrincipal()
  const [course, setCourse] = useState<CourseDetail | null>(null)
  const [preview, setPreview] = useState<CoursePreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [joining, setJoining] = useState(false)
  const [joinError, setJoinError] = useState<string | null>(null)
  const joinInFlight = useRef(false)
  const { enrollments, loading: enrollmentsLoading } = useMyEnrollments()
  const enrollment: Enrollment | undefined = enrollments.find(item => item.course_id === courseId)

  const load = useCallback(() => {
    setError(null)
    Promise.all([getCourse(courseId), getCoursePreview(courseId)])
      .then(([detail, sample]) => {
        setCourse(detail)
        setPreview(sample)
      })
      .catch(err => {
        if (isApiError(err) && err.status === 404) setNotFound(true)
        else setError(isApiError(err) ? err.message : String(err))
      })
  }, [courseId])
  useEffect(load, [load])

  const join = async () => {
    // 重复点击保护：请求在途时再点无效（后端本身也幂等，这里只是不让用户白等两次）。
    if (joinInFlight.current) return
    if (principal === 'guest') {
      requestLogin({ kind: 'action', message: t('Sign in to join this course. Your progress is saved to your own account.') })
      return
    }
    joinInFlight.current = true
    setJoining(true)
    setJoinError(null)
    try {
      const { enrollment: joined } = await enrollCourse(courseId)
      // 加入后去第一节没读完的课——这只是打开教材，不会自动开始付费辅导。
      router.push(courseLessonRoute(courseId, joined.current_lesson_id || course?.lessons[0]?.id || ''))
    } catch (err) {
      if (isApiError(err) && err.status === 401) return // 访客：apiFetch 已经弹了登录框
      setJoinError(isApiError(err) ? err.message : String(err))
    } finally {
      joinInFlight.current = false
      setJoining(false)
    }
  }

  if (notFound) {
    return (
      <LearningShell title={t('Course not found')} subtitle={t('It may not be published yet, or has been withdrawn.')} icon={GraduationCap}>
        <Link href={COURSES_HOME} className="text-sm text-primary hover:underline">
          {t('Back to all courses')}
        </Link>
      </LearningShell>
    )
  }
  if (!course) {
    return (
      <LearningShell title={t('Official courses')} subtitle="" icon={GraduationCap}>
        {error ? <LearningErrorState message={error} onRetry={load} /> : <LearningSkeleton count={3} />}
      </LearningShell>
    )
  }

  const lessonsById = new Map(course.lessons.map(lesson => [lesson.id, lesson]))
  const grouped = course.units.length
    ? course.units.map(unit => ({ unit, lessons: unit.lessons.map(id => lessonsById.get(id)).filter(Boolean) }))
    : [{ unit: null, lessons: course.lessons }]
  const progressOf = (lessonId: string) => enrollment?.lessons.find(item => item.lesson_id === lessonId)?.state

  const action = enrollment ? (
    <div className="flex flex-wrap gap-2">
      <Link
        href={courseLessonRoute(courseId, enrollment.current_lesson_id)}
        className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-[13px] font-medium text-primary-foreground shadow-sm hover:brightness-110"
      >
        <PlayCircle size={16} aria-hidden="true" />
        {t('Continue learning')}
      </Link>
      <Link
        href={masteryTopicRoute(enrollment.path_id)}
        className="inline-flex items-center gap-2 rounded-xl border border-border px-4 py-2.5 text-[13px] font-medium hover:bg-muted"
      >
        <MapIcon size={16} aria-hidden="true" />
        {t('Knowledge map')}
      </Link>
    </div>
  ) : (
    <button
      type="button"
      onClick={join}
      disabled={joining || enrollmentsLoading || !course.enrollment_open}
      className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-[13px] font-medium text-primary-foreground shadow-sm hover:brightness-110 disabled:opacity-60"
    >
      {joining ? <Loader2 size={16} className="animate-spin" aria-hidden="true" /> : <BookOpenCheck size={16} aria-hidden="true" />}
      {!course.enrollment_open ? t('Not accepting new learners') : principal === 'guest' ? t('Sign in to join') : t('Join course')}
    </button>
  )

  return (
    <LearningShell title={course.title} subtitle={course.subtitle || course.summary} icon={GraduationCap} action={action}>
      <div className="mb-3 text-[12px]">
        <Link href={COURSES_HOME} className="text-[var(--muted-foreground)] hover:text-[var(--foreground)]">
          ← {t('All official courses')}
        </Link>
      </div>
      <CourseTrialNotice course={course} />
      {joinError && <LearningErrorState message={joinError} onRetry={join} />}
      {enrollment?.latest_version && (
        <p className="mb-4 rounded-xl bg-amber-500/10 px-4 py-3 text-[12.5px] text-amber-800 dark:text-amber-300">
          {t('You are studying version {{mine}}. Version {{latest}} is available; your current version stays unchanged.', {
            mine: enrollment.course_version,
            latest: enrollment.latest_version,
          })}
        </p>
      )}
      <section className="mb-8 rounded-2xl border border-border bg-card p-5">
        <p className="mb-4 text-[13.5px] leading-[1.8]">{course.summary}</p>
        <CourseCredits course={course} />
        {course.disclaimer && (
          <p className="mt-4 rounded-lg bg-muted/60 px-3 py-2 text-[12px] leading-[1.7] text-[var(--muted-foreground)]">
            {course.disclaimer}
          </p>
        )}
        {enrollment && (
          <p className="mt-4 text-[12.5px] text-[var(--muted-foreground)]">
            {t('Joined · {{percent}}% read', { percent: Math.round(readProgressOf(enrollment) * 100) })}
            {enrollment.review_due_count > 0 && ` · ${t('{{count}} reviews due', { count: enrollment.review_due_count })}`}
          </p>
        )}
      </section>

      <section aria-labelledby="course-outline" className="mb-8">
        <h2 id="course-outline" className="mb-3 text-[13.5px] font-semibold">
          {t('Contents')}
        </h2>
        <div className="space-y-5">
          {grouped.map(({ unit, lessons }) => (
            <div key={unit?.id ?? 'all'}>
              {unit && <h3 className="mb-2 text-[12.5px] font-medium text-[var(--muted-foreground)]">{unit.title}</h3>}
              <ol className="divide-y divide-border overflow-hidden rounded-xl border border-border">
                {lessons.map(lesson => {
                  if (!lesson) return null
                  const state = progressOf(lesson.id)
                  const openable = !!enrollment && !lesson.suspended
                  const body = (
                    <div className="flex items-start gap-3 px-4 py-3">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2 text-[13.5px] font-medium">
                          {lesson.title}
                          {lesson.preview && !enrollment && (
                            <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[11px] text-emerald-700 dark:text-emerald-300">
                              {t('Free preview')}
                            </span>
                          )}
                          {lesson.suspended && (
                            <span className="rounded-full bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-700 dark:text-amber-300">
                              {t('Paused')}
                            </span>
                          )}
                        </div>
                        <p className="mt-1 text-[12px] leading-[1.7] text-[var(--muted-foreground)]">
                          {lesson.objectives.join('；')}
                        </p>
                      </div>
                      <span className="shrink-0 text-[11.5px] text-[var(--muted-foreground)]">
                        {state === 'read'
                          ? t('Read')
                          : state === 'reading'
                            ? t('Reading')
                            : lesson.estimated_minutes
                              ? t('{{count}} min', { count: lesson.estimated_minutes })
                              : !enrollment && !lesson.preview
                                ? <Lock size={13} aria-label={t('Join to read')} />
                                : null}
                      </span>
                    </div>
                  )
                  return (
                    <li key={lesson.id}>
                      {openable ? (
                        <Link href={courseLessonRoute(courseId, lesson.id)} className="block hover:bg-muted/50">
                          {body}
                        </Link>
                      ) : (
                        body
                      )}
                    </li>
                  )
                })}
              </ol>
            </div>
          ))}
        </div>
      </section>

      {!enrollment && preview && preview.lessons.length > 0 && (
        <section aria-labelledby="course-preview" className="mb-8">
          <h2 id="course-preview" className="mb-3 text-[13.5px] font-semibold">
            {t('Free preview')}
          </h2>
          {preview.lessons.map(lesson => (
            <article key={lesson.lesson.id} className="mb-6 rounded-2xl border border-border bg-card p-5">
              <h3 className="mb-3 text-[15px] font-semibold">{lesson.lesson.title}</h3>
              <LessonMarkdown markdown={lesson.markdown} />
              {lesson.sources.length > 0 && (
                <div className="mt-5 border-t border-border pt-3">
                  <h4 className="mb-2 text-[12px] font-medium text-[var(--muted-foreground)]">{t('Sources')}</h4>
                  <SourceList sources={lesson.sources} />
                </div>
              )}
            </article>
          ))}
        </section>
      )}

      <section aria-labelledby="course-sources" className="mb-8">
        <h2 id="course-sources" className="mb-3 text-[13.5px] font-semibold">
          {t('Sources')}
        </h2>
        <SourceList sources={course.sources} />
      </section>
    </LearningShell>
  )
}
