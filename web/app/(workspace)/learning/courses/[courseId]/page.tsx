"use client"

import { useParams } from "next/navigation"
import { CourseDetailPage } from "@/components/learning/courses/CourseDetailPage"

export default function CoursePage() {
  const params = useParams<{ courseId: string }>()
  const courseId = decodeURIComponent(params?.courseId ?? "")
  // key：换课程时整页重建，不把上一门课的状态带过来。
  return <CourseDetailPage key={courseId} courseId={courseId} />
}
