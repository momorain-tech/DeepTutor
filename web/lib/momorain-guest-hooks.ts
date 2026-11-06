"use client";

// React hook 单独放一个 "use client" 文件：lib/momorain-guest.ts 也会被服务端代码引用，
// 那里不能出现 useEffect 之类的客户端 API。
import { useEffect, useSyncExternalStore } from "react";

import {
  MOMORAIN_ENABLED,
  currentPrincipal,
  resolvePrincipal,
  subscribePrincipal,
  type Principal,
} from "./momorain-guest";

/**
 * 组件里读「是不是访客」。首次调用会顺带去问后端；问到之前是 unknown，
 * 需要按身份启动的后台同步（任务板、日历）应等到 "user" 再启动。
 */
export function useMomorainPrincipal(): Principal {
  const value = useSyncExternalStore(subscribePrincipal, currentPrincipal, () => "unknown" as Principal);
  useEffect(() => {
    if (MOMORAIN_ENABLED) void resolvePrincipal();
  }, []);
  return MOMORAIN_ENABLED ? value : "user";
}

/** 非 momorain 模式恒为 true；momorain 模式只有确认已登录才为 true。 */
export function usePersonalDataAllowed(): boolean {
  return useMomorainPrincipal() === "user";
}
