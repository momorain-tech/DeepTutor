"use client";


import { appFetch } from "@/lib/base-path";
import { useEffect, useState, useRef } from "react";
import { apiFetch, apiUrl } from "@/lib/api";
import { loginHref } from "@/shared/auth/return-url";

type Account = { nickname: string; userId: string };
type Access = { ready: boolean; reason?: string; quota?: { display?: string }; topupUrl?: string; consoleUrl?: string };

export default function MomorainNav() {
  const [user, setUser] = useState<Account | null>(null);
  const [access, setAccess] = useState<Access | null>(null);
  const [error, setError] = useState("");
  const principal = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    if (process.env.NEXT_PUBLIC_MOMORAIN_ENABLED !== "true") return;
    let alive = true;
    const refresh = async () => {
      try {
        // 必须访问站点根，让 user-login 给 Cookie 滑动续期。
        const response = await appFetch("/api/auth/session", { cache: "no-store" });
        if (response.status === 401) { if (principal.current) { window.location.reload(); return; } if (alive) { principal.current = null; setUser(null); setAccess(null); } return; }
        if (!response.ok) throw new Error("登录服务暂时不可用");
        const body = await response.json();
        if (!alive) return;
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
    return () => { alive = false; window.removeEventListener("focus", refresh); };
  }, []);
  if (process.env.NEXT_PUBLIC_MOMORAIN_ENABLED !== "true") return null;
  return <header className="flex min-h-11 items-center justify-between gap-2 border-b px-3 text-sm" aria-label="momorain 账号">
    <span className="shrink-0">momorain · DeepTutor</span>
    <div className="flex min-w-0 items-center justify-end gap-2">
      {error && <span role="status" className="truncate" title={error}>{error}</span>}
      {user ? <>
        <a className="min-w-[2em] max-w-[7em] truncate" href="https://momorain.com/account" title={user.nickname}>{user.nickname}</a>
        <a className="shrink-0" href={access?.topupUrl || "https://momorain.com/account"}>{access?.quota?.display || "余额"}</a>
      </> : <a className="shrink-0" href={loginHref(typeof window === "undefined" ? "/deeptutor/chat" : window.location.pathname + window.location.search)}>登录免配置用模型</a>}
    </div>
  </header>;
}
