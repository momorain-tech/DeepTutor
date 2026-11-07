// @vitest-environment jsdom
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { CoursePractice } from '@/components/learning/courses/CoursePractice'
import { submitAnswer } from '@/lib/courses-api'

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }))
vi.mock('@/lib/courses-api', () => ({
  newSubmissionId: vi.fn(() => 'submission-stays-the-same'),
  listLessonQuestions: vi.fn(async () => [{question_id:'q',question_version:1,type:'open',prompt:'解释概念',options:[],last_result:null}]),
  submitAnswer: vi.fn(),
}))

it('retains the submission ID while grading and only offers a fresh attempt once graded', async () => {
  const done=vi.fn()
  const base={submission_id:'submission-stays-the-same',question_id:'q',question_version:1,criteria:[],sources:[],correct_answer:null,feedback:'',explanation:'',result:null}
  vi.mocked(submitAnswer).mockResolvedValueOnce({...base,status:'pending',replayed:true} as never)
    .mockResolvedValueOnce({...base,status:'graded',result:'correct',replayed:true} as never)
  render(<CoursePractice enrollmentId="enr" lessonId="lesson" onGraded={done} />)
  fireEvent.change(await screen.findByRole('textbox'),{target:{value:'我的解释'}})
  fireEvent.click(screen.getByRole('button',{name:'Submit'}))
  const retry=await screen.findByRole('button',{name:'Retry submission'})
  expect(screen.queryByRole('button',{name:'Try again'})).not.toBeInTheDocument()
  expect(done).not.toHaveBeenCalled()
  fireEvent.click(retry)
  await screen.findByRole('button',{name:'Try again'})
  expect(vi.mocked(submitAnswer).mock.calls.map(call=>call[2])).toEqual(['submission-stays-the-same','submission-stays-the-same'])
  await waitFor(()=>expect(done).toHaveBeenCalledTimes(1))
})
