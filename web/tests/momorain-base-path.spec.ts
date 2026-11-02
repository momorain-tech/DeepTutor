import { describe, it, expect, beforeEach, vi } from "vitest";

beforeEach(() => {
  vi.resetModules();
  vi.stubEnv("NEXT_PUBLIC_BASE_PATH", "/deeptutor");
  vi.stubEnv("NEXT_PUBLIC_MOMORAIN_ENABLED", "true");
});

describe("momorain 子路径和全站接口", () => {
  it("只给本应用网络请求补一次前缀", async () => {
    const { withBasePath, withoutBasePath } = await import("@/lib/base-path");
    expect(withBasePath("/api/sessions?limit=20")).toBe("/deeptutor/api/sessions?limit=20");
    expect(withBasePath("/ws?dt_workspace=")).toBe("/deeptutor/ws?dt_workspace=");
    expect(withBasePath("/deeptutor/api/sessions")).toBe("/deeptutor/api/sessions");
    expect(withBasePath("/api/auth/session")).toBe("/api/auth/session");
    expect(withBasePath("/one-api/api/momorain/provision")).toBe("/one-api/api/momorain/provision");
    expect(withBasePath("https://example.com/video.mp4")).toBe("https://example.com/video.mp4");
    expect(withoutBasePath("/deeptutor/chat")).toBe("/chat");
    expect(withoutBasePath("/deeptutor-other/chat")).toBe("/deeptutor-other/chat");
  });

  it("导航的工作区地址保持应用内路径，网络地址才加前缀", async () => {
    const { scopedUrl } = await import("@/lib/workspace-scope");
    const { apiUrl } = await import("@/shared/api/client");
    expect(scopedUrl("/chat", "work-1")).toBe("/chat?dt_workspace=work-1");
    expect(apiUrl("/api/sessions")).toMatch(/^\/deeptutor\/api\/sessions\?/);
  });

  it("中央登录回跳保留应用前缀，拒绝外部和协议相对地址", async () => {
    const { loginHref } = await import("@/shared/auth/return-url");
    const href = new URL(loginHref("/deeptutor/chat/session-1"));
    expect(href.origin + href.pathname).toBe("https://momorain.com/login");
    expect(href.searchParams.get("next")).toBe("/deeptutor/chat/session-1");
    for (const bad of ["//evil.test", "https://evil.test", "/\\evil.test"]) {
      const safe = new URL(loginHref(bad)).searchParams.get("next");
      expect(safe).toMatch(/^\/deeptutor\//);
    }
  });
});
