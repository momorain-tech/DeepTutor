/**
 * momorain 访客模式：「先浏览，保存或调用模型时再登录」。
 *
 * 为什么集中放在这里：页面上有几十个组件一挂载就各自读接口（会话历史、任务板、
 * 知识库、设置……）。以前它们收到 401 就由 apiFetch 把整页跳去登录，于是访客
 * 根本看不到页面。逐个组件去改既容易漏，又会和上游代码冲突，所以在 apiFetch
 * 这一个出口统一判断：
 *
 * - 访客读公共接口（GUEST_PATHS）照常请求；
 * - 访客读其他接口：**根本不发请求**，本地直接回一个 401，组件当成「没数据」处理；
 * - 访客做写操作：同样不发请求，并弹出「需要登录」的说明框；
 * - 已登录用户中途收到真实的 401（登录过期）：弹框提示重新登录，不再把整页带走。
 *
 * 真正的门禁仍在后端（deeptutor/momorain/middleware.py）；这里只是让页面不去
 * 碰注定被拒的接口，并把登录提示放到用户真正动手的那一刻。
 */
import { BASE_PATH } from "./base-path";

export const MOMORAIN_ENABLED = process.env.NEXT_PUBLIC_MOMORAIN_ENABLED === "true";

/**
 * 访客也能读的公共只读接口。必须和后端 deeptutor/momorain/guest.py 的
 * GUEST_ROUTES 保持一致：前端多放一个只会白白收到 401，少放一个则页面缺数据。
 */
export const GUEST_PATHS: ReadonlySet<string> = new Set([
  "/api/auth/status",
  "/api/settings/ui",
  "/api/settings/page-customization",
  "/api/modules",
  "/api/capabilities/registered",
]);

/** unknown = 还没问过后端；guest = 未登录；user = 已登录。 */
export type Principal = "unknown" | "guest" | "user";

let principal: Principal = "unknown";
let probe: Promise<Principal> | null = null;
let expiredNotified = false;
// 登录过期后仍记住原账号，仅用于防止其草稿被恢复给另一个账号。
let lastUserId: string | null = null;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((listener) => listener());
}

export function currentPrincipal(): Principal {
  return principal;
}

export function lastAuthenticatedUserId(): string | null {
  return lastUserId;
}

export function subscribePrincipal(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** lib/auth.ts 每拿到一次登录状态就同步过来，保证两边对「是谁」的判断一致。 */
export function notePrincipalFromStatus(
  status: { enabled?: boolean; authenticated?: boolean; user_id?: string } | null,
): void {
  if (!MOMORAIN_ENABLED || !status) return;
  const next: Principal = status.authenticated ? "user" : "guest";
  if (next === "guest" && principal === "user") {
    noteSessionExpired();
    return;
  }
  if (next === "user") {
    lastUserId = typeof status.user_id === "string" ? status.user_id : null;
    expiredNotified = false;
  }
  if (next === principal) return;
  principal = next;
  emit();
}

/**
 * 问一次后端「我是谁」，同一页面里所有请求共用这一次结果。
 * 这里直接用 fetch（不走 apiFetch），否则 apiFetch 判断身份时又要先判断身份，会绕圈。
 * 问不到（网络故障）就返回 unknown：照常发请求，让后端去拦。
 */
export function resolvePrincipal(force = false): Promise<Principal> {
  if (!force && principal !== "unknown") return Promise.resolve(principal);
  if (!probe) {
    probe = fetch(`${BASE_PATH}/api/auth/status`, {
      credentials: "include",
      cache: "no-store",
    })
      .then(async (response) => {
        if (!response.ok) return "unknown" as Principal;
        notePrincipalFromStatus(await response.json());
        return principal;
      })
      .catch(() => "unknown" as Principal)
      .finally(() => {
        probe = null;
      });
  }
  return probe;
}

/** 把任意请求地址还原成应用内的接口路径（去掉站点前缀和查询串）。 */
export function apiPathOf(input: string): string {
  let pathname = input;
  try {
    pathname = new URL(input, "http://local.invalid").pathname;
  } catch {
    /* 解析不了就按原样比较 */
  }
  if (BASE_PATH && pathname.startsWith(BASE_PATH + "/")) pathname = pathname.slice(BASE_PATH.length);
  return pathname;
}

export function isGuestReadable(path: string, method: string): boolean {
  return (method === "GET" || method === "HEAD") && GUEST_PATHS.has(path);
}

// ---- 「需要登录」的提示 ----

export type LoginReason =
  /** 访客点了要登录才能做的事（发送、保存、上传……） */
  | { kind: "action"; message?: string }
  /** 已登录用户的登录过期了 */
  | { kind: "expired" };

export const LOGIN_REQUIRED_EVENT = "momorain:login-required";
/**
 * 跳去登录页之前广播这个事件，让输入框等组件把还没发出去的内容先存起来。
 * 监听者往 event.detail 里 push 一个 Promise，跳转会等它们都完成。
 * （做法和上游的 deeptutor:before-workspace-switch 一样。）
 */
export const BEFORE_LOGIN_EVENT = "momorain:before-login";

export function requestLogin(reason: LoginReason): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent<LoginReason>(LOGIN_REQUIRED_EVENT, { detail: reason }));
}

/** 已登录用户的请求被后端拒了：只提示一次，后续请求按访客处理，不再连环 401。 */
export function noteSessionExpired(): void {
  if (principal !== "user") return;
  principal = "guest";
  emit();
  if (!expiredNotified) {
    expiredNotified = true;
    requestLogin({ kind: "expired" });
  }
}

/** 本地伪造的 401：访客的请求根本没发出去。body 带 guest 标记，方便调试时分辨。 */
export function guestRefusal(): Response {
  return new Response(JSON.stringify({ detail: "请先登录 momorain", guest: true }), {
    status: 401,
    headers: { "Content-Type": "application/json" },
  });
}

/** 先让各组件存好草稿，再跳统一登录页；登录后回到当前页面。 */
export async function goToLogin(loginUrl: string): Promise<void> {
  const pending: Promise<unknown>[] = [];
  window.dispatchEvent(new CustomEvent<Promise<unknown>[]>(BEFORE_LOGIN_EVENT, { detail: pending }));
  // 存不下草稿也照样去登录：草稿丢了能重打，卡在这里用户就走不了了。
  await Promise.allSettled(pending);
  window.location.href = loginUrl;
}

/** 接口因为没登录被拒。组件可以据此显示「登录后查看」，而不是笼统的「加载失败」。 */
export class LoginRequiredError extends Error {
  readonly loginRequired = true;
  constructor(message = "登录后查看") {
    super(message);
    this.name = "LoginRequiredError";
  }
}

export function isLoginRequired(error: unknown): boolean {
  if (error instanceof LoginRequiredError) return true;
  // shared/api/client.ts 抛的 ApiError 把 HTTP 状态码放在 appError.status 里。
  const appError = (error as { appError?: { status?: unknown } } | null)?.appError;
  return appError?.status === 401;
}
