'use client'

import { CheckCircle2, CircleDashed, Loader2, RotateCcw, XCircle } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { LearningErrorState, LearningSkeleton } from '@/components/learning/LearningShell'
import { isApiError } from '@/lib/api'
import { listLessonQuestions, newSubmissionId, submitAnswer } from '@/lib/courses-api'
import type { PublicQuestion, SubmissionResult } from '@/lib/courses-types'
import { SourceList } from './CourseParts'

/**
 * 课时练习：题目来自课程题库（服务端不给答案），提交后才看到判分和解析。
 *
 * 去重的关键在 submissionId：每道题"这一次作答"生成一个 ID，网络失败重试时
 * 带同一个 ID，后端就只计一次分、开放题也只调一次模型；点"再做一次"才换新 ID。
 */
export function CoursePractice({
  enrollmentId,
  lessonId,
  onGraded,
}: {
  enrollmentId: string
  lessonId: string
  onGraded?: () => void
}) {
  const { t } = useTranslation()
  const [questions, setQuestions] = useState<PublicQuestion[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [attempt, setAttempt] = useState(0)
  const load = useCallback(() => {
    setError(null)
    setAttempt(value => value + 1)
  }, [])
  useEffect(() => {
    let alive = true
    listLessonQuestions(enrollmentId, lessonId)
      .then(items => alive && setQuestions(items))
      .catch(err => alive && setError(isApiError(err) ? err.message : String(err)))
    return () => {
      alive = false
    }
  }, [enrollmentId, lessonId, attempt])

  return (
    <section aria-labelledby="course-practice" className="mt-8">
      <h2 id="course-practice" className="mb-3 text-[13.5px] font-semibold">
        {t('Practice')}
      </h2>
      {error && <LearningErrorState message={error} onRetry={load} />}
      {!questions && !error && <LearningSkeleton count={2} />}
      {questions && !questions.length && (
        <p className="text-[13px] text-[var(--muted-foreground)]">{t('This lesson has no practice questions yet.')}</p>
      )}
      <div className="space-y-4">
        {questions?.map((question, index) => (
          <QuestionCard
            key={`${question.question_id}@${question.question_version}`}
            index={index + 1}
            question={question}
            enrollmentId={enrollmentId}
            onGraded={onGraded}
          />
        ))}
      </div>
    </section>
  )
}

function QuestionCard({
  index,
  question,
  enrollmentId,
  onGraded,
}: {
  index: number
  question: PublicQuestion
  enrollmentId: string
  onGraded?: () => void
}) {
  const { t } = useTranslation()
  const [choices, setChoices] = useState<string[]>([])
  const [truth, setTruth] = useState<boolean | null>(null)
  const [text, setText] = useState('')
  const [submissionId, setSubmissionId] = useState(newSubmissionId)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<SubmissionResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const locked = busy || !!result

  const answer = (): unknown => {
    switch (question.type) {
      case 'single_choice':
        return choices[0] ?? ''
      case 'multiple_choice':
        return choices
      case 'true_false':
        return truth
      default:
        return text.trim()
    }
  }
  const ready =
    question.type === 'single_choice' || question.type === 'multiple_choice'
      ? choices.length > 0
      : question.type === 'true_false'
        ? truth !== null
        : text.trim().length > 0

  const submit = async () => {
    if (!ready || busy) return
    setBusy(true)
    setError(null)
    try {
      const graded = await submitAnswer(enrollmentId, question.question_id, submissionId, answer())
      setResult(graded)
      if (graded.status === 'graded') onGraded?.()
    } catch (err) {
      if (isApiError(err) && err.status === 401) return
      // 保留同一个 submissionId：重试不会重复计分。
      setError(isApiError(err) ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const again = () => {
    setResult(null)
    setError(null)
    setChoices([])
    setTruth(null)
    setText('')
    setSubmissionId(newSubmissionId())
  }

  const toggle = (label: string) => {
    if (locked) return
    setChoices(current =>
      question.type === 'single_choice'
        ? [label]
        : current.includes(label)
          ? current.filter(item => item !== label)
          : [...current, label],
    )
  }

  const correctLabels = Array.isArray(result?.correct_answer) ? (result?.correct_answer as string[]) : []
  return (
    <div className="rounded-2xl border border-border bg-card p-5">
      <div className="mb-3 flex items-start gap-2">
        <span className="shrink-0 text-[12px] font-medium text-[var(--muted-foreground)]">{index}.</span>
        <p className="text-[13.5px] leading-[1.75]">{question.prompt}</p>
      </div>

      {(question.type === 'single_choice' || question.type === 'multiple_choice') && (
        <div role={question.type === 'single_choice' ? 'radiogroup' : 'group'} className="space-y-2">
          {question.type === 'multiple_choice' && (
            <p className="text-[11.5px] text-[var(--muted-foreground)]">{t('Select all that apply')}</p>
          )}
          {question.options.map(option => {
            const picked = choices.includes(option.label)
            const isKey = result && correctLabels.includes(option.label)
            return (
              <button
                key={option.label}
                type="button"
                role={question.type === 'single_choice' ? 'radio' : 'checkbox'}
                aria-checked={picked}
                disabled={locked}
                onClick={() => toggle(option.label)}
                className={`flex w-full items-start gap-2 rounded-xl border px-3.5 py-2.5 text-left text-[13px] transition ${
                  isKey
                    ? 'border-emerald-500/60 bg-emerald-500/5'
                    : picked
                      ? 'border-primary bg-primary/5'
                      : 'border-border hover:bg-muted/60'
                }`}
              >
                <span className="font-medium">{option.label}.</span>
                <span>{option.body}</span>
              </button>
            )
          })}
        </div>
      )}

      {question.type === 'true_false' && (
        <div role="radiogroup" className="flex gap-2">
          {[
            [true, t('True')],
            [false, t('False')],
          ].map(([value, label]) => (
            <button
              key={String(value)}
              type="button"
              role="radio"
              aria-checked={truth === value}
              disabled={locked}
              onClick={() => setTruth(value as boolean)}
              className={`rounded-xl border px-4 py-2 text-[13px] ${truth === value ? 'border-primary bg-primary/5' : 'border-border hover:bg-muted/60'}`}
            >
              {label as string}
            </button>
          ))}
        </div>
      )}

      {question.type === 'fill_blank' && (
        <input
          value={text}
          onChange={event => setText(event.target.value)}
          disabled={locked}
          maxLength={200}
          aria-label={t('Your answer')}
          className="w-full rounded-xl border border-border bg-transparent px-3.5 py-2 text-[13px] outline-none focus:border-primary"
        />
      )}

      {question.type === 'open' && (
        <>
          <textarea
            value={text}
            onChange={event => setText(event.target.value)}
            disabled={locked}
            maxLength={4000}
            rows={4}
            aria-label={t('Your answer')}
            className="w-full rounded-xl border border-border bg-transparent px-3.5 py-2 text-[13px] leading-[1.7] outline-none focus:border-primary"
          />
          <p className="mt-1 text-[11px] text-[var(--muted-foreground)]">
            {t('Open answers are graded by the model against the course rubric when you submit, using your balance.')}
          </p>
        </>
      )}

      {error && (
        <p role="alert" className="mt-3 text-[12.5px] text-[var(--destructive)]">
          {error}
        </p>
      )}

      {!result && (
        <div className="mt-4 flex items-center gap-3">
          <button
            type="button"
            onClick={submit}
            disabled={!ready || busy}
            className="inline-flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-[13px] font-medium text-primary-foreground hover:brightness-110 disabled:opacity-50"
          >
            {busy && <Loader2 size={15} className="animate-spin" aria-hidden="true" />}
            {error ? t('Retry submission') : t('Submit')}
          </button>
          {question.last_result && (
            <span className="text-[11.5px] text-[var(--muted-foreground)]">
              {t('Last time: {{result}}', { result: resultLabel(question.last_result, t) })}
            </span>
          )}
        </div>
      )}

      {result && (
        <div className="mt-4 rounded-xl bg-muted/50 p-4 text-[12.5px] leading-[1.7]">
          <div className="mb-2 flex items-center gap-2 text-[13px] font-medium">
            {result.status !== 'graded' ? (
              <CircleDashed size={16} aria-hidden="true" />
            ) : result.result === 'correct' ? (
              <CheckCircle2 size={16} className="text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
            ) : (
              <XCircle size={16} className={result.result === 'partial' ? 'text-amber-600' : 'text-[var(--destructive)]'} aria-hidden="true" />
            )}
            {result.status === 'graded' ? resultLabel(result.result, t) : t('Grading…')}
          </div>
          {question.type === 'true_false' && typeof result.correct_answer === 'boolean' && (
            <p>{t('Answer: {{value}}', { value: result.correct_answer ? t('True') : t('False') })}</p>
          )}
          {question.type === 'fill_blank' && correctLabels.length > 0 && (
            <p>{t('Answer: {{value}}', { value: correctLabels.join('、') })}</p>
          )}
          {question.type === 'open' && typeof result.correct_answer === 'string' && result.correct_answer && (
            <p>
              <span className="font-medium">{t('Reference answer')}：</span>
              {result.correct_answer}
            </p>
          )}
          {result.criteria.length > 0 && (
            <ul className="my-2 space-y-1">
              {result.criteria.map(item => (
                <li key={item.id} className="flex gap-2">
                  {item.met ? (
                    <CheckCircle2 size={14} className="mt-1 shrink-0 text-emerald-600" aria-hidden="true" />
                  ) : (
                    <XCircle size={14} className="mt-1 shrink-0 text-[var(--muted-foreground)]" aria-hidden="true" />
                  )}
                  <span>
                    {item.description}
                    {item.comment && <span className="text-[var(--muted-foreground)]"> — {item.comment}</span>}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {result.feedback && <p className="mt-1">{result.feedback}</p>}
          {result.explanation && (
            <p className="mt-1">
              <span className="font-medium">{t('Explanation')}：</span>
              {result.explanation}
            </p>
          )}
          {result.sources.length > 0 && (
            <div className="mt-2">
              <SourceList sources={result.sources} />
            </div>
          )}
          <button
            type="button"
            // 评分中重试沿用 submissionId；只有真正评完才能新开一次作答。
            onClick={result.status === 'graded' ? again : submit}
            disabled={busy}
            className="mt-3 inline-flex items-center gap-1.5 text-[12.5px] text-primary hover:underline disabled:opacity-50"
          >
            <RotateCcw size={13} aria-hidden="true" />
            {result.status === 'graded' ? t('Try again') : t('Retry submission')}
          </button>
        </div>
      )}
    </div>
  )
}

function resultLabel(result: string | null, t: (key: string) => string): string {
  if (result === 'correct') return t('Correct')
  if (result === 'partial') return t('Partly correct')
  if (result === 'pending') return t('Grading…')
  return t('Incorrect')
}
