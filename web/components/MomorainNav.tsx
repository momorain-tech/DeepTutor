"use client";


import { appFetch } from "@/lib/base-path";
import { useEffect, useState, useRef } from "react";
import { apiFetch, apiUrl } from "@/lib/api";
import { loginHref } from "@/shared/auth/return-url";
import {
  LOGIN_REQUIRED_EVENT,
  goToLogin,
  noteSessionExpired,
  type LoginReason,
} from "@/lib/momorain-guest";

type Account = { nickname: string; userId: string };
type Access = { ready: boolean; reason?: string; quota?: { display?: string }; topupUrl?: string; consoleUrl?: string };

function currentLoginHref(): string {
  return loginHref(typeof window === "undefined" ? "/deeptutor/chat" : window.location.pathname + window.location.search);
}

/** 点「去登录」：先让输入框把草稿存好（见 lib/momorain-guest.ts 的 goToLogin），再跳统一登录页。 */
function startLogin() {
  void goToLogin(currentLoginHref());
}

export default function MomorainNav() {
  const [user, setUser] = useState<Account | null>(null);
  const [access, setAccess] = useState<Access | null>(null);
  const [error, setError] = useState("");
  const [prompt, setPrompt] = useState<LoginReason | null>(null);
  const principal = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    if (process.env.NEXT_PUBLIC_MOMORAIN_ENABLED !== "true") return;
    let alive = true;
    const refresh = async () => {
      try {
        // 必须访问站点根，让 user-login 给 Cookie 滑动续期。
        const response = await appFetch("/api/auth/session", { cache: "no-store" });
        if (response.status === 401) {
          // 原来登录着、现在 401：登录过期了。以前这里直接刷新整页，没保存的输入会丢；
          // 现在改成弹框提示，让用户自己点「重新登录」（点的时候会先暂存草稿）。
          if (principal.current) { noteSessionExpired(); if (alive) setUser(null); return; }
          if (alive) { principal.current = null; setUser(null); setAccess(null); }
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
        setUser(body.data);
        const result = await apiFetch(apiUrl("/api/momorain/access"), { cache: "no-store", skipAuthRedirect: true });
        if (!result.ok) throw new Error("模型服务暂时不可用");
        const data = await result.json();
        if (alive) { setAccess(data); setError(""); }
      } catch (cause) { if (alive) setError(cause instanceof Error ? cause.message : "服务暂时不可用"); }
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
  if (process.env.NEXT_PUBLIC_MOMORAIN_ENABLED !== "true") return null;
  return <>
    <header className="flex h-[var(--momorain-nav-h)] items-center justify-between gap-2 border-b px-3 text-sm" aria-label="momorain 账号">
      <span className="shrink-0">momorain · DeepTutor</span>
      <div className="flex min-w-0 items-center justify-end gap-2">
        {error && <span role="status" className="truncate" title={error}>{error}</span>}
        {user ? <>
          <a className="min-w-[2em] max-w-[7em] truncate" href="https://momorain.com/account" title={user.nickname}>{user.nickname}</a>
          <a className="shrink-0" href={access?.topupUrl || "https://momorain.com/account"}>{access?.quota?.display || "余额"}</a>
        </> : <a className="shrink-0" href={currentLoginHref()} onClick={(event) => { event.preventDefault(); startLogin(); }}>登录免配置用模型</a>}
      </div>
    </header>
    {prompt && <LoginPrompt reason={prompt} onClose={() => setPrompt(null)} />}
  </>;
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
