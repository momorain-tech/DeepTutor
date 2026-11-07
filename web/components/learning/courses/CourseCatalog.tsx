'use client'

import Link from 'next/link'
import { GraduationCap, ShieldCheck } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { pageGridClass } from '@/components/layout/FeaturePage'
import {
  LearningEmptyState,
  LearningErrorState,
  LearningShell,
  LearningSkeleton,
} from '@/components/learning/LearningShell'
import { isApiError } from '@/lib/api'
import { courseRoute, listCourses } from '@/lib/courses-api'
import type { CourseSummary, Enrollment } from '@/lib/courses-types'
import { readProgressOf, useMyEnrollments } from './CourseParts'

/** /learning/courses：官方课程目录。访客可以完整浏览；只读，不调用模型。 */
export function CourseCatalog() {
  const { t } = useTranslation()
  const [courses, setCourses] = useState<CourseSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { enrollments } = useMyEnrollments()

  const [attempt, setAttempt] = useState(0)
  const load = useCallback(() => {
    setError(null)
    setAttempt(value => value + 1)
  }, [])
  useEffect(() => {
    let alive = true
    listCourses()
      .then(items => alive && setCourses(items))
      .catch(err => alive && setError(isApiError(err) ? err.message : String(err)))
    return () => {
      alive = false
    }
  }, [attempt])

  const enrolledBy = new Map(enrollments.map(item => [item.course_id, item]))
  return (
    <LearningShell
      title={t('Official courses')}
      subtitle={t('Follow a structured course at your own pace. Trial editions are clearly marked.')}
      icon={GraduationCap}
    >
      {error && <LearningErrorState message={error} onRetry={load} />}
      {!courses && !error && <LearningSkeleton count={3} />}
      {courses && !courses.length && (
        <LearningEmptyState
          title={t('No courses yet')}
          description={t('Official courses will appear here once they are published.')}
        />
      )}
      {courses && courses.length > 0 && (
        <div className={pageGridClass(2)}>
          {courses.map(course => (
            <CourseCard key={course.course_id} course={course} enrollment={enrolledBy.get(course.course_id)} />
          ))}
        </div>
      )}
    </LearningShell>
  )
}

export function CourseCard({ course, enrollment }: { course: CourseSummary; enrollment?: Enrollment }) {
  const { t } = useTranslation()
  return (
    <Link
      href={courseRoute(course.course_id)}
      className="group flex min-w-0 flex-col gap-3 rounded-2xl border border-border bg-card p-5 shadow-sm transition hover:border-primary/30 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
    >
      <div className="flex items-start gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-indigo-500/10 text-indigo-600 dark:text-indigo-400">
          <GraduationCap size={19} aria-hidden="true" />
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="text-[15px] font-semibold leading-snug">{course.title}</h3>
          {course.subtitle && (
            <p className="mt-0.5 text-[12px] text-[var(--muted-foreground)]">{course.subtitle}</p>
          )}
        </div>
      </div>
      <p className="line-clamp-3 text-[13px] leading-[1.7] text-[var(--muted-foreground)]">{course.summary}</p>
      <div className="mt-auto flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-[var(--muted-foreground)]">
        <span>{t('{{count}} lessons', { count: course.lesson_count })}</span>
        {course.release_stage === 'trial' && (
          <span className="font-medium text-amber-700 dark:text-amber-300">{t('Trial edition')}</span>
        )}
        {course.review && course.release_stage !== 'trial' && (
          <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
            <ShieldCheck size={12} aria-hidden="true" />
            {t('Reviewed')}
          </span>
        )}
        <span>v{course.version}</span>
        {enrollment ? (
          <span className="ml-auto rounded-full bg-primary/10 px-2 py-0.5 font-medium text-primary">
            {t('Joined · {{percent}}% read', { percent: Math.round(readProgressOf(enrollment) * 100) })}
          </span>
        ) : course.preview_lessons.length > 0 ? (
          <span className="ml-auto">{t('Free preview')}</span>
        ) : null}
      </div>
    </Link>
  )
}
