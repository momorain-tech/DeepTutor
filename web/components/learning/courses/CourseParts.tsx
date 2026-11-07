'use client'

/**
 * 课程页面共用的小部件：教材正文、编审信息、来源列表、"我是否已加入"的读取。
 *
 * 教材渲染用的是全站通用的 MarkdownRenderer，并且**强制 allowHtml={false}**：
 * 后端校验器已经拒绝了含 HTML 的教材，这里再关一道，双保险。
 */
import Markdown from '@/components/common/MarkdownRenderer'
import { ExternalLink, ShieldCheck } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { isApiError } from '@/lib/api'
import { listEnrollments, splitLessonSections } from '@/lib/courses-api'
import type { CourseSource, CourseSummary, Enrollment } from '@/lib/courses-types'
import { usePersonalDataAllowed } from '@/lib/momorain-guest-hooks'

export function LessonMarkdown({ markdown }: { markdown: string }) {
  return (
    <div className="space-y-2">
      {splitLessonSections(markdown).map((section, index) => (
        <section
          key={section.anchor || `intro-${index}`}
          id={section.anchor || undefined}
          data-course-anchor={section.anchor || undefined}
          className="scroll-mt-24"
        >
          <Markdown content={section.markdown} variant="prose" allowHtml={false} enableImages={false} />
        </section>
      ))}
    </div>
  )
}

/** 编选、审核、版本、更新时间——让学员知道这份教材是谁负责的。 */
export function CourseCredits({ course }: { course: CourseSummary }) {
  const { t } = useTranslation()
  const names = (people: { name: string; role: string }[]) =>
    people.map(person => (person.role ? `${person.name}（${person.role}）` : person.name)).join('、')
  return (
    <dl className="grid gap-x-6 gap-y-2 text-[12.5px] sm:grid-cols-2">
      <div className="flex gap-2">
        <dt className="shrink-0 text-[var(--muted-foreground)]">{t('Edited by')}</dt>
        <dd className="min-w-0">{names(course.editors)}</dd>
      </div>
      <div className="flex gap-2">
        <dt className="shrink-0 text-[var(--muted-foreground)]">{t('Reviewed by')}</dt>
        <dd className="min-w-0">
          {course.review ? (
            <span className="inline-flex items-center gap-1">
              <ShieldCheck size={13} className="shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
              {names(course.review.reviewers)} · {course.review.reviewed_at}
            </span>
          ) : (
            t('Not reviewed')
          )}
        </dd>
      </div>
      <div className="flex gap-2">
        <dt className="shrink-0 text-[var(--muted-foreground)]">{t('Version')}</dt>
        <dd>{course.version}</dd>
      </div>
      <div className="flex gap-2">
        <dt className="shrink-0 text-[var(--muted-foreground)]">{t('Updated')}</dt>
        <dd>{course.updated_at}</dd>
      </div>
    </dl>
  )
}

export function SourceList({ sources }: { sources: CourseSource[] }) {
  const { t } = useTranslation()
  if (!sources.length) return null
  return (
    <ul className="space-y-1.5 text-[12.5px]">
      {sources.map(source => (
        <li key={source.id} className="flex flex-wrap items-baseline gap-x-2">
          <span className="font-medium">{source.title}</span>
          {source.edition && <span className="text-[var(--muted-foreground)]">{source.edition}</span>}
          {source.locator && <span className="text-[var(--muted-foreground)]">{source.locator}</span>}
          {source.url && (
            <a
              href={source.url}
              target="_blank"
              rel="noreferrer noopener"
              className="inline-flex items-center gap-0.5 text-primary hover:underline"
            >
              {t('Link')}
              <ExternalLink size={11} aria-hidden="true" />
            </a>
          )}
          {source.reviewed && (
            <span className="text-[11px] text-emerald-600 dark:text-emerald-400">{t('Source reviewed')}</span>
          )}
        </li>
      ))}
    </ul>
  )
}

/**
 * 当前用户在当前工作区的选课记录。访客恒为空、且不发请求（个人数据）。
 * `principal` 一变（登录/退出/换号）就重新读，旧账号的记录不会残留。
 */
export function useMyEnrollments() {
  const allowed = usePersonalDataAllowed()
  const [tick, setTick] = useState(0)
  // 每次结果都记着"是哪一轮请求的"，loading 由此推出来，不需要在 effect 里同步改状态。
  const [state, setState] = useState<{ round: number; enrollments: Enrollment[]; error: string | null }>({
    round: -1,
    enrollments: [],
    error: null,
  })
  const reload = useCallback(() => setTick(value => value + 1), [])

  useEffect(() => {
    if (!allowed) return
    let alive = true
    listEnrollments()
      .then(items => alive && setState({ round: tick, enrollments: items, error: null }))
      .catch(err => alive && setState({ round: tick, enrollments: [], error: isApiError(err) ? err.message : String(err) }))
    return () => {
      alive = false
    }
  }, [allowed, tick])

  // 访客或换号后：直接当作没有记录，不显示上一个人的数据。
  if (!allowed) return { allowed, enrollments: [] as Enrollment[], loading: false, error: null, reload }
  return { allowed, enrollments: state.enrollments, loading: state.round !== tick, error: state.error, reload }
}

export function readProgressOf(enrollment: Enrollment): number {
  if (!enrollment.lessons.length) return 0
  return enrollment.lessons.filter(lesson => lesson.state === 'read').length / enrollment.lessons.length
}
