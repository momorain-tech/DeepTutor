'use client'

import Link from 'next/link'
import { ArrowRight, GraduationCap, RotateCcw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { pageGridClass } from '@/components/layout/FeaturePage'
import { COURSES_HOME, courseLessonRoute, listCourses } from '@/lib/courses-api'
import type { CourseSummary } from '@/lib/courses-types'
import { masterySessionRoute } from '@/lib/mastery-mode'
import { CourseCard } from './CourseCatalog'
import { readProgressOf, useMyEnrollments } from './CourseParts'

/**
 * 学习首页上的"官方课程"区块（经典、现代两种布局共用：由共享的 LearningWays 渲染）。
 *
 * - 没有任何已发布课程时整个区块不出现，不给首页添空架子；
 * - 已加入的课程显示"继续学习"和到期复习，复习跳回该知识点所在的课时；
 * - 只读：不调用模型。
 */
export function OfficialCoursesHome() {
  const { t } = useTranslation()
  const [courses, setCourses] = useState<CourseSummary[]>([])
  const { enrollments } = useMyEnrollments()

  useEffect(() => {
    let alive = true
    listCourses()
      .then(items => alive && setCourses(items))
      .catch(() => {
        /* 首页上的附加区块：读不到就不显示，不打扰主流程 */
      })
    return () => {
      alive = false
    }
  }, [])

  if (!courses.length && !enrollments.length) return null
  const joined = new Map(enrollments.map(item => [item.course_id, item]))
  return (
    <section aria-labelledby="official-courses" className="mb-10">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 id="official-courses" className="flex items-center gap-2 text-[13.5px] font-semibold">
          <GraduationCap size={16} aria-hidden="true" />
          {t('Official courses')}
        </h2>
        <Link href={COURSES_HOME} className="inline-flex items-center gap-1 text-[12.5px] text-primary hover:underline">
          {t('All courses')}
          <ArrowRight size={13} aria-hidden="true" />
        </Link>
      </div>
      {enrollments.length > 0 && (
        <div className={`${pageGridClass(2)} mb-4`}>
          {enrollments.map(enrollment => {
            // 到期数量由后端算好（review_due_count）；这里只挑最早到期的那个知识点所在的课时。
            const reviewLesson = enrollment.mastery
              .filter(item => item.mastered && item.review_due_at !== null)
              .sort((a, b) => (a.review_due_at ?? 0) - (b.review_due_at ?? 0))[0]?.lesson_id
            return (
              <div key={enrollment.enrollment_id} className="flex flex-col gap-2 rounded-xl bg-muted/45 p-4">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-medium">{enrollment.title}</span>
                  <span className="shrink-0 text-[11.5px] text-[var(--muted-foreground)]">
                    {t('{{percent}}% read', { percent: Math.round(readProgressOf(enrollment) * 100) })}
                  </span>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Link
                    href={courseLessonRoute(enrollment.course_id, enrollment.current_lesson_id)}
                    className="inline-flex items-center gap-1 rounded-lg bg-primary px-3 py-1.5 text-[12.5px] font-medium text-primary-foreground hover:brightness-110"
                  >
                    {t('Continue learning')}
                  </Link>
                  {enrollment.review_due_count > 0 && (
                    <Link
                      href={reviewLesson ? courseLessonRoute(enrollment.course_id, reviewLesson) : masterySessionRoute(enrollment.path_id, 'review')}
                      className="inline-flex items-center gap-1 rounded-lg border border-border px-3 py-1.5 text-[12.5px] hover:bg-muted"
                    >
                      <RotateCcw size={13} aria-hidden="true" />
                      {t('{{count}} reviews due', { count: enrollment.review_due_count })}
                    </Link>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
      {courses.some(course => !joined.has(course.course_id)) && (
        <div className={pageGridClass(2)}>
          {courses
            .filter(course => !joined.has(course.course_id))
            .slice(0, 2)
            .map(course => (
              <CourseCard key={course.course_id} course={course} />
            ))}
        </div>
      )}
    </section>
  )
}
