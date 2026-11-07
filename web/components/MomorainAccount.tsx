"use client";

/**
 * momorain 统一账号在 DeepTutor 里的两块：
 *
 * 1. <MomorainSession />：挂在根 layout，**自己不画任何东西**（只在需要时弹登录框）。
 *    负责拉登录状态和余额、Cookie 续期、换号检测、统一弹「需要登录」框。
 * 2. <MomorainAccountItem />：显示昵称/余额/登录入口，放进侧栏的 footerSlot。
 *
 * 为什么拆开：以前这里是一条贴在页面最顶上的 44px 账号栏。它不是 DeepTutor 的样式，
 * 还要把整个外壳高度减掉 44px 去补（不补的话经典布局的「设置」会被挤出屏幕）。
 * 上游本来就给账号留了位置——侧栏 footerSlot：经典布局显示在侧栏底部，
 * 现代布局自动收进左上角的工作区菜单。放进去就能直接用上游的样式和收起/抽屉逻辑。
 *
 * 两块之间用一个模块级的小 store 共享状态：侧栏可能同时挂好几份条目
 * （收起的图标栏、展开的侧栏、手机抽屉），但请求只该发一次。
 */

import { appFetch } from "@/lib/base-path";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { LogIn, Wallet } from "lucide-react";
import { apiFetch, apiUrl } from "@/lib/api";
import { loginHref } from "@/shared/auth/return-url";
import { UserAvatar } from "@/components/UserAvatar";
import Tooltip from "@/shared/ui/Tooltip";
import {
  LOGIN_REQUIRED_EVENT,
  MOMORAIN_ENABLED,
  goToLogin,
  noteSessionExpired,
  type LoginReason,
} from "@/lib/momorain-guest";

const ACCOUNT_URL = "https://momorain.com/account";

type Account = { nickname: string; userId: string };
type Access = { ready: boolean; reason?: string; quota?: { display?: string }; topupUrl?: string; consoleUrl?: string };
type AccountState = {
  /** loading：还没问过；guest：没登录；user：已登录 */
  status: "loading" | "guest" | "user";
  user: Account | null;
  access: Access | null;
  error: string;
};

// ---- 共享状态（极简版的外部 store，配合 useSyncExternalStore 用）-------------
let state: AccountState = { status: "loading", user: null, access: null, error: "" };
const listeners = new Set<() => void>();
function setState(patch: Partial<AccountState>) {
  state = { ...state, ...patch };
  listeners.forEach((listener) => listener());
}
function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
// 服务端渲染时没有登录信息，固定返回 loading，保证和客户端首帧一致、不报水合错误。
const SERVER_STATE: AccountState = { status: "loading", user: null, access: null, error: "" };
function useAccountState() {
  return useSyncExternalStore(subscribe, () => state, () => SERVER_STATE);
}

function currentLoginHref(): string {
  return loginHref(typeof window === "undefined" ? "/deeptutor/chat" : window.location.pathname + window.location.search);
}

/** 点「登录」：先让输入框把草稿存好（见 lib/momorain-guest.ts 的 goToLogin），再跳统一登录页。 */
function startLogin() {
  void goToLogin(currentLoginHref());
}

/** 根 layout 里挂一份。不显示账号信息，只负责拉状态和弹登录框。 */
export function MomorainSession() {
  const [prompt, setPrompt] = useState<LoginReason | null>(null);
  const principal = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    if (!MOMORAIN_ENABLED) return;
    let alive = true;
    const refresh = async () => {
      try {
        // 必须访问站点根，让 user-login 给 Cookie 滑动续期。
        const response = await appFetch("/api/auth/session", { cache: "no-store" });
        if (response.status === 401) {
          // 原来登录着、现在 401：登录过期了。不直接刷新整页（没保存的输入会丢），
          // 而是弹框提示，让用户自己点「重新登录」（点的时候会先暂存草稿）。
          if (principal.current) { noteSessionExpired(); if (alive) setState({ status: "guest", user: null }); return; }
          if (alive) { principal.current = null; setState({ status: "guest", user: null, access: null, error: "" }); }
          return;
        }
        if (!response.ok) throw new Error("登录服务暂时不可用");
        const body = await response.json();
        if (!alive) return;
        // 在别的标签页登录了、或换了账号：整页刷新，免得旧账号的数据和连接留在页面上串号。
        if (principal.current !== undefined && principal.current !== body.data.userId) {
          window.location.reload();
          return;
        }
        principal.current = body.data.userId;
        setState({ status: "user", user: body.data });
        const result = await apiFetch(apiUrl("/api/momorain/access"), { cache: "no-store", skipAuthRedirect: true });
        if (!result.ok) throw new Error("模型服务暂时不可用");
        const data = await result.json();
        if (alive) setState({ access: data, error: "" });
      } catch (cause) { if (alive) setState({ error: cause instanceof Error ? cause.message : "服务暂时不可用" }); }
    };
    void refresh();
    window.addEventListener("focus", refresh);
    // 任何地方需要登录（访客点发送/保存、或登录过期）都发这个事件，由这里统一弹框。
    const onLoginRequired = (event: Event) => setPrompt((event as CustomEvent<LoginReason>).detail);
    window.addEventListener(LOGIN_REQUIRED_EVENT, onLoginRequired);
    return () => {
      alive = false;
      window.removeEventListener("focus", refresh);
      window.removeEventListener(LOGIN_REQUIRED_EVENT, onLoginRequired);
    };
  }, []);
  useEffect(() => {
    if (!prompt) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setPrompt(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [prompt]);
  if (!MOMORAIN_ENABLED || !prompt) return null;
  return <LoginPrompt reason={prompt} onClose={() => setPrompt(null)} />;
}

// 和上游 ProfileLink 同一套样式，放在侧栏里看起来是「原生」的一行。
// 现代布局的工作区菜单会用 .dt-menu-slot 的规则把它改成菜单项样式（modern.css）。
const ROW =
  "flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-[13.5px] text-[var(--muted-foreground)] transition-colors hover:bg-[var(--background)]/50 hover:text-[var(--foreground)]";
const ICON =
  "rounded-lg p-2 text-[var(--muted-foreground)] transition-colors hover:bg-[var(--background)]/50 hover:text-[var(--foreground)]";

/**
 * 侧栏里的账号条目。collapsed=true 是侧栏收起后的图标栏，只放一个图标、悬停看详情。
 * 已登录：昵称（去账号中心）+ 余额（去充值）两行；访客：一行「登录」。
 */
export function MomorainAccountItem({ collapsed = false }: { collapsed?: boolean }) {
  const { status, user, access, error } = useAccountState();
  if (!MOMORAIN_ENABLED) return null;
  if (status === "loading") {
    // 还没问到登录状态时先不画，避免「登录」闪一下又变成昵称。
    if (!error) return null;
    // 但登录服务出错（503、断网）时要说出来，不能装成访客，也不能什么都不显示。
    if (collapsed)
      return (
        <Tooltip label={error} side="right">
          <span className={ICON} role="status" aria-label={error}><LogIn size={16} strokeWidth={1.6} className="opacity-50" /></span>
        </Tooltip>
      );
    return <span role="status" className={`${ROW} cursor-default`}><LogIn size={16} strokeWidth={1.6} className="shrink-0 opacity-50" /><span className="truncate">{error}</span></span>;
  }

  if (status === "guest" || !user) {
    const label = "登录 · 免配置用模型";
    const onClick = (event: React.MouseEvent) => { event.preventDefault(); startLogin(); };
    if (collapsed)
      return (
        <Tooltip label={label} side="right">
          <a href={currentLoginHref()} onClick={onClick} className={ICON} aria-label={label}>
            <LogIn size={16} strokeWidth={1.6} />
          </a>
        </Tooltip>
      );
    return (
      <a href={currentLoginHref()} onClick={onClick} className={ROW}>
        <LogIn size={16} strokeWidth={1.6} className="shrink-0" />
        <span className="truncate">{label}</span>
      </a>
    );
  }

  // 余额那行：出错时显示错误（比如模型服务挂了），正常时显示额度；点进去是充值页。
  const balance = error || access?.quota?.display || "余额";
  const topupUrl = access?.topupUrl || ACCOUNT_URL;
  const avatar = <UserAvatar username={user.nickname} userId={user.userId} size={collapsed ? 18 : 16} suppressTitle />;

  if (collapsed)
    return (
      <Tooltip label={`${user.nickname} · ${balance}`} side="right">
        <a href={ACCOUNT_URL} className={ICON} aria-label={`momorain 账号：${user.nickname}`}>
          {avatar}
        </a>
      </Tooltip>
    );
  return (
    <>
      <a href={ACCOUNT_URL} className={ROW} title="momorain 账号中心（改昵称、退出登录）">
        {avatar}
        <span className="truncate">{user.nickname}</span>
      </a>
      <a href={topupUrl} className={ROW} title={error ? error : "查看余额 / 充值"}>
        <Wallet size={16} strokeWidth={1.6} className="shrink-0" />
        <span className="truncate">{balance}</span>
      </a>
    </>
  );
}

/**
 * 现代布局专用：访客在侧栏「新对话」上方看到的一行登录入口。
 * 现代布局把账号收进了工作区菜单，访客不点开菜单就看不到「登录」，所以这里单独露一行；
 * 只给访客显示，登录后消失（那时昵称/余额在菜单里）。经典布局底部本来就有，不用这个。
 */
export function MomorainGuestLogin() {
  const { status } = useAccountState();
  if (!MOMORAIN_ENABLED || status !== "guest") return null;
  return (
    <a
      href={currentLoginHref()}
      onClick={(event) => { event.preventDefault(); startLogin(); }}
      className="mb-2 flex w-full items-center justify-center gap-1.5 rounded-md py-1.5 text-[12.5px] text-[var(--muted-foreground)] transition-colors hover:bg-[var(--accent)] hover:text-[var(--foreground)]"
    >
      <LogIn size={14} strokeWidth={1.7} className="shrink-0" />
      <span>登录 · 免配置用模型</span>
    </a>
  );
}

/** 「需要登录」说明框：讲清楚为什么要登录，由用户决定去不去，不自动跳走。 */
function LoginPrompt({ reason, onClose }: { reason: LoginReason; onClose: () => void }) {
  const expired = reason.kind === "expired";
  const title = expired ? "登录已过期" : "登录后才能继续";
  const message = expired
    ? "重新登录后就能接着用。输入框里还没发出去的内容会先帮你暂存，登录回来自动填回。"
    : (reason.message || "这一步会保存到你的账号或调用大模型，需要先登录 momorain 账号。");
  return <div className="fixed inset-0 z-[1000] flex items-center justify-center bg-black/40 px-4" onClick={onClose}>
    <div role="dialog" aria-modal="true" aria-labelledby="momorain-login-title"
      className="w-full max-w-sm rounded-xl border bg-[var(--background)] p-5 text-[var(--foreground)] shadow-xl"
      onClick={(event) => event.stopPropagation()}>
      <h2 id="momorain-login-title" className="mb-2 text-base font-semibold">{title}</h2>
      <p className="mb-2 text-sm leading-relaxed">{message}</p>
      {!expired && <p className="mb-4 text-xs leading-relaxed opacity-70">
        登录用手机号验证码，全站通用；新账号自带试用额度，不用自己配置模型 key。
        已输入的内容会先暂存，登录回来自动填回输入框，确认后再点发送。
      </p>}
      <div className="flex justify-end gap-2">
        <button type="button" className="rounded-md border px-3 py-1.5 text-sm" onClick={onClose}>
          {expired ? "稍后" : "先随便看看"}
        </button>
        <button type="button" autoFocus className="rounded-md bg-[var(--primary)] px-3 py-1.5 text-sm text-[var(--primary-foreground)]" onClick={startLogin}>
          {expired ? "重新登录" : "去登录"}
        </button>
      </div>
    </div>
  </div>;
}
