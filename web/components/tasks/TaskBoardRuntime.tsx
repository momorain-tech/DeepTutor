'use client'

import { useEffect, type ReactNode } from 'react'
import { startTaskBoardSync } from '@/lib/task-board-store'
import { usePersonalDataAllowed } from '@/lib/momorain-guest-hooks'

export default function TaskBoardRuntime({ children }: { children: ReactNode }) {
  // momorain：任务板是个人数据，还会开一条事件流、失败后每 3 秒轮询。
  // 访客不启动它；登录状态确认是本人后才开始同步。（非 momorain 模式恒为 true。）
  const allowed = usePersonalDataAllowed()
  useEffect(() => (allowed ? startTaskBoardSync() : undefined), [allowed])
  return children
}
