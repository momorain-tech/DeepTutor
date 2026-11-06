"use client";

import Tooltip from "@/shared/ui/Tooltip";
import { LogOut } from "lucide-react";
import { useRouter } from "next/navigation";
import { useTranslation } from "react-i18next";
import { logout } from "@/lib/auth";
import { useAuthStatus } from "@/hooks/useAuthStatus";
import { MOMORAIN_ENABLED } from "@/lib/momorain-guest";

interface LogoutButtonProps {
  collapsed?: boolean;
}

export function LogoutButton({ collapsed = false }: LogoutButtonProps) {
  const router = useRouter();
  const { t } = useTranslation();
  const { enabled, authenticated } = useAuthStatus();

  // 统一账号的退出操作在顶部昵称链接的账号中心完成；上游按钮调用的是已关闭的本地退出接口。
  if (MOMORAIN_ENABLED || !enabled || !authenticated) return null;

  async function handleLogout() {
    await logout();
    router.replace("/login");
  }

  if (collapsed) {
    return (
      <Tooltip label={t("Sign out")} side="top">
        <button
          onClick={handleLogout}
          className="rounded-lg p-2 text-[var(--muted-foreground)] transition-colors hover:bg-[var(--background)]/50 hover:text-red-500"
          aria-label={t("Sign out")}
        >
          <LogOut size={16} strokeWidth={1.5} />
        </button>
      </Tooltip>
    );
  }

  return (
    <button
      onClick={handleLogout}
      className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-[13.5px] text-[var(--muted-foreground)] transition-colors hover:bg-[var(--background)]/50 hover:text-red-500"
    >
      <LogOut size={16} strokeWidth={1.5} />
      <span>{t("Sign out")}</span>
    </button>
  );
}
