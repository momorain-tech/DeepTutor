"use client"

import { useParams } from "next/navigation"
import { CourseLessonPage } from "@/components/learning/courses/CourseLessonPage"

export default function CourseLessonRoute() {
  const params = useParams<{ courseId: string; lessonId: string }>()
  const courseId = decodeURIComponent(params?.courseId ?? "")
  const lessonId = decodeURIComponent(params?.lessonId ?? "")
  return <CourseLessonPage key={`${courseId}/${lessonId}`} courseId={courseId} lessonId={lessonId} />
}
