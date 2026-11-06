import { activeWorkspaceId } from './workspace-scope'
import { fetchAuthStatus } from './auth'

export type WorkspaceDraft = {
  text: string
  attachments: { filename: string; base64?: string; mimeType?: string }[]
}
async function key(workspaceId = activeWorkspaceId(), pathname = window.location.pathname): Promise<string> {
  const location = `${workspaceId}:${pathname}`
  const status = await fetchAuthStatus()
  if (!status || (status.enabled && (!status.authenticated || !status.user_id)))
    throw new Error('Account identity is unavailable for saving drafts.')
  return `${status.user_id || 'local-admin'}:${location}`
}

async function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('deeptutor-workspace-drafts', 1)
    request.onupgradeneeded = () => request.result.createObjectStore('drafts')
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

export async function saveWorkspaceDraft(draft: WorkspaceDraft, workspaceId?: string, pathname?: string): Promise<void> {
  const draftKey = await key(workspaceId, pathname)
  const db = await database()
  try {
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction('drafts', 'readwrite')
      const store = tx.objectStore('drafts')
      if (draft.text || draft.attachments.length) store.put(draft, draftKey)
      else store.delete(draftKey)
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
      tx.onabort = () => reject(tx.error)
    })
  } finally {
    db.close()
  }
}

export async function readWorkspaceDraft(workspaceId?: string, pathname?: string): Promise<WorkspaceDraft | undefined> {
  const draftKey = await key(workspaceId, pathname)
  const db = await database()
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction('drafts', 'readonly')
      const store = tx.objectStore('drafts')
      const request = store.get(draftKey)
      tx.oncomplete = () => resolve(request.result)
      tx.onerror = () => reject(tx.error)
      tx.onabort = () => reject(tx.error)
    })
  } finally {
    db.close()
  }
}

/** Move the current unsent message with an explicit composer assignment. */
export async function transferWorkspaceDraft(destination: string): Promise<void> {
  const source = activeWorkspaceId()
  if (source === destination) return
  const draft = await readWorkspaceDraft()
  if (!draft || (!draft.text && !draft.attachments.length)) return
  const prior = await readWorkspaceDraft(destination, '/chat')
  await saveWorkspaceDraft({
    text: [prior?.text, draft.text].filter(Boolean).join('\n\n'),
    attachments: [...(prior?.attachments || []), ...draft.attachments],
  }, destination, '/chat')
  await saveWorkspaceDraft({ text: '', attachments: [] })
}

// ---- momorain：去登录前暂存的草稿 ----
//
// 访客在输入框里打的字，点「发送」时才发现要登录。登录页在 momorain.com/login，
// 一去一回整页刷新，页面内存里的东西全没了；而上面那套草稿按用户 id 存，访客
// 没有 id、存不进去。所以单独留一格：跳登录前存进来，登录回来**取一次就删**。
//
// - 只放在本机浏览器（IndexedDB），不上传服务器；
// - 30 分钟后作废，免得共用电脑时下一个人登录看到上一个访客的草稿；
// - 只恢复到同一个页面路径，并且只填回输入框，不自动发送——
//   发送会花钱，必须等用户看过、自己再点一次。
const LOGIN_HANDOFF_KEY = 'momorain:login-handoff'
const LOGIN_HANDOFF_TTL_MS = 30 * 60 * 1000

export type LoginHandoffDraft = WorkspaceDraft & {
  /** 对话模式（如 deep_research），登录回来恢复选择 */
  capability?: string | null
  pathname: string
  savedAt: number
}

async function handoffStore<T>(mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest<T> | void): Promise<T | undefined> {
  const db = await database()
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction('drafts', mode)
      const request = run(tx.objectStore('drafts'))
      tx.oncomplete = () => resolve(request ? request.result : undefined)
      tx.onerror = () => reject(tx.error)
      tx.onabort = () => reject(tx.error)
    })
  } finally {
    db.close()
  }
}

export async function saveLoginHandoffDraft(draft: WorkspaceDraft & { capability?: string | null }): Promise<void> {
  if (!draft.text.trim() && !draft.attachments.length) return
  const record: LoginHandoffDraft = { ...draft, pathname: window.location.pathname, savedAt: Date.now() }
  await handoffStore('readwrite', store => { store.put(record, LOGIN_HANDOFF_KEY) })
}

/**
 * 当前页面的草稿：取出并删除。过期的顺手删掉；
 * 没过期但属于别的页面的留着，等用户回到那个页面再取。
 */
export async function takeLoginHandoffDraft(): Promise<LoginHandoffDraft | undefined> {
  const record = await handoffStore<LoginHandoffDraft | undefined>('readonly', store => store.get(LOGIN_HANDOFF_KEY))
  if (!record) return undefined
  const fresh = Date.now() - record.savedAt < LOGIN_HANDOFF_TTL_MS
  const here = record.pathname === window.location.pathname
  if (!fresh || here) await handoffStore('readwrite', store => { store.delete(LOGIN_HANDOFF_KEY) })
  return fresh && here ? record : undefined
}
