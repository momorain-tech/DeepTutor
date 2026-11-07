// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// 官方课程前端：访客只读得到公共课程接口；教材按 {#锚点} 切节；引用协议能还原成课时地址。

function mockBackend(authenticated: boolean) {
  const calls: { url: string; method: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      calls.push({ url, method: (init?.method ?? "GET").toUpperCase() });
      if (url.includes("/api/auth/status")) {
        return new Response(JSON.stringify({ enabled: true, authenticated }), { status: 200 });
      }
      return new Response(JSON.stringify({ courses: [] }), { status: 200 });
    }),
  );
  return calls;
}

beforeEach(() => {
  vi.resetModules();
  vi.stubEnv("NEXT_PUBLIC_BASE_PATH", "/deeptutor");
  vi.stubEnv("NEXT_PUBLIC_MOMORAIN_ENABLED", "true");
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { origin: "http://127.0.0.1", pathname: "/deeptutor/learning/courses", search: "", hash: "", href: "" },
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("访客与课程接口", () => {
  it("目录、详情、样课可读；选课、教材、提交不发请求", async () => {
    const calls = mockBackend(false);
    const events: unknown[] = [];
    window.addEventListener("momorain:login-required", (e) => events.push((e as CustomEvent).detail));
    const { apiFetch } = await import("@/shared/api/client");

    for (const path of ["/api/courses", "/api/courses/demo-signals", "/api/courses/demo-signals/preview"]) {
      expect((await apiFetch(path)).status, path).toBe(200);
    }
    for (const path of ["/api/course-enrollments", "/api/courses/Bad%20Id", "/api/courses/demo/lessons/x"]) {
      expect((await apiFetch(path)).status, path).toBe(401);
    }
    expect((await apiFetch("/api/course-enrollments", { method: "POST", body: "{}" })).status).toBe(401);
    expect((await apiFetch("/api/courses", { method: "POST", body: "{}" })).status).toBe(401);
    const sent = calls.map((c) => new URL(c.url, "http://x").pathname).sort();
    expect(sent).toEqual([
      "/deeptutor/api/auth/status",
      "/deeptutor/api/courses",
      "/deeptutor/api/courses/demo-signals",
      "/deeptutor/api/courses/demo-signals/preview",
    ]);
    expect(events).toEqual([{ kind: "action" }, { kind: "action" }]);
  });
});

describe("教材与路由工具", () => {
  it("按锚点切节，代码块里的标题不算", async () => {
    const { splitLessonSections } = await import("@/lib/courses-api");
    const sections = splitLessonSections(
      "# 标题\n\n导语\n\n## 第一节 {#one}\n正文\n\n```md\n## 不是标题 {#fake}\n```\n\n## 第二节 {#two}\n更多\n",
    );
    expect(sections.map((s) => s.anchor)).toEqual(["", "one", "two"]);
    expect(sections[1].markdown).toContain("## 第一节\n");
    expect(sections[1].markdown).toContain("{#fake}");
    expect(sections[1].markdown).not.toContain("{#one}");
  });

  it("引用协议还原成课时地址，非法引用返回 null", async () => {
    const { courseCitationRoute, courseLessonRoute } = await import("@/lib/courses-api");
    expect(courseCitationRoute("course:demo-signals@1.0.0/colors#three-colors")).toBe(
      courseLessonRoute("demo-signals", "colors", "three-colors"),
    );
    expect(courseLessonRoute("demo-signals", "colors", "", "")).toBe("/learning/courses/demo-signals/lessons/colors");
    expect(courseCitationRoute("course:../etc@1.0.0/x")).toBeNull();
    expect(courseCitationRoute("https://evil.example")).toBeNull();
  });

  it("每次作答的提交 ID 合法且互不相同", async () => {
    const { newSubmissionId } = await import("@/lib/courses-api");
    const ids = new Set(Array.from({ length: 50 }, () => newSubmissionId()));
    expect(ids.size).toBe(50);
    for (const id of ids) expect(id).toMatch(/^[A-Za-z0-9_-]{8,80}$/);
  });
});
