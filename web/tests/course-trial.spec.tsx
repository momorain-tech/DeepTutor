// @vitest-environment jsdom
import { render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { CourseCredits, CourseTrialNotice } from '@/components/learning/courses/CourseParts'
import { CourseCard } from '@/components/learning/courses/CourseCatalog'
import type { CourseSummary } from '@/lib/courses-types'

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }))

const course: CourseSummary = {
  course_id:'trial',version:'1.0.0',title:'试读教材',subtitle:'',summary:'',language:'zh-CN',tags:[],
  editors:[{name:'AI 草稿',role:'编写',affiliation:''}],updated_at:'2026-11-07',
  lesson_count:8,knowledge_point_count:24,question_count:48,preview_lessons:[],enrollment_open:true,
  release_stage:'trial',trial_notice:'尚未经过专业医学审校',
  review:{reviewers:[{name:'不可冒充专家审核',role:'',affiliation:''}],reviewed_at:'2026-11-07',note:''},
}

it('marks trial cards and never presents their incidental review data as reviewed', () => {
  render(<CourseCard course={course} />)
  expect(screen.getByText('Trial edition')).toBeInTheDocument()
  expect(screen.queryByText('Reviewed')).not.toBeInTheDocument()
})

it('shows the notice and pending review status on the content pages', () => {
  render(<><CourseTrialNotice course={course} /><CourseCredits course={course} /></>)
  expect(screen.getByRole('note')).toHaveTextContent(course.trial_notice)
  expect(screen.getByText('Not reviewed')).toBeInTheDocument()
  expect(screen.queryByText(/不可冒充专家审核/)).not.toBeInTheDocument()
})
