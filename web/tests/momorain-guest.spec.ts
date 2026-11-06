// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// momorain 访客模式：先浏览，保存或调用模型时再登录。规则见 lib/momorain-guest.ts。

type Call = { url: string; method: string };

function mockBackend(authenticated: boolean, status = 200) {
  const calls: Call[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    calls.push({ url, method: (init?.method ?? "GET").toUpperCase() });
    if (url.includes("/api/auth/status")) {
      return new Response(JSON.stringify({ enabled: true, authenticated }), { status: 200 });
    }
    return new Response("{}", { status });
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

function loginEvents() {
  const seen: unknown[] = [];
  window.addEventListener("momorain:login-required", (event) => seen.push((event as CustomEvent).detail));
  return seen;
}

let navigatedTo: string | null;

beforeEach(() => {
  vi.resetModules();
  vi.stubEnv("NEXT_PUBLIC_BASE_PATH", "/deeptutor");
  vi.stubEnv("NEXT_PUBLIC_MOMORAIN_ENABLED", "true");
  navigatedTo = null;
  // apiFetch 以前遇到 401 会改 location.href 整页跳走；这里记录下来，断言它不再发生。
  Object.defineProperty(window, "location", {
    configurable: true,
    value: {
      origin: "http://127.0.0.1",
      pathname: "/deeptutor/chat",
      search: "",
      hash: "",
      get href() { return navigatedTo ?? "http://127.0.0.1/deeptutor/chat"; },
      set href(value: string) { navigatedTo = value; },
    },
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("访客", () => {
  it("公共接口照常请求，个人数据根本不发请求，也不整页跳登录", async () => {
    const calls = mockBackend(false);
    const events = loginEvents();
    const { apiFetch } = await import("@/shared/api/client");

    expect((await apiFetch("/api/modules")).status).toBe(200);
    for (const path of ["/api/sessions?limit=20", "/api/task-board", "/api/settings", "/api/momorain/access"]) {
      const response = await apiFetch(path);
      expect(response.status).toBe(401);
      expect(await response.json()).toMatchObject({ guest: true });
    }
    const sent = calls.map((c) => new URL(c.url, "http://x").pathname);
    // 只有「问一次我是谁」和公共接口真正发了出去（公共接口不用等身份，先后不定）。
    expect(sent.sort()).toEqual(["/deeptutor/api/auth/status", "/deeptutor/api/modules"]);
    expect(events).toEqual([]); // 后台读取不打扰用户
    expect(navigatedTo).toBeNull();
  });

  it("写操作和上传不发出去，弹「需要登录」说明", async () => {
    const calls = mockBackend(false);
    const events = loginEvents();
    const { apiFetch } = await import("@/shared/api/client");

    const form = new FormData();
    form.append("file", new Blob(["secret"]), "notes.pdf");
    expect((await apiFetch("/api/knowledge-bases/demo/upload", { method: "POST", body: form })).status).toBe(401);
    expect((await apiFetch("/api/settings/ui", { method: "PUT", body: "{}" })).status).toBe(401);
    // 公共接口的同一路径，写入也一样要登录
    expect(calls.filter((c) => c.method !== "GET")).toEqual([]);
    expect(events).toEqual([{ kind: "action" }, { kind: "action" }]);
    expect(navigatedTo).toBeNull();
  });
});

describe("已登录", () => {
  it("请求照常发出；登录过期只提示一次，之后按访客处理、不再连环请求", async () => {
    const calls = mockBackend(true, 401);
    const events = loginEvents();
    const { apiFetch } = await import("@/shared/api/client");

    expect((await apiFetch("/api/sessions")).status).toBe(401); // 真实发出，服务器说过期了
    await apiFetch("/api/task-board");
    await apiFetch("/api/settings/ui", { method: "PUT", body: "{}" });
    const sent = calls.map((c) => new URL(c.url, "http://x").pathname);
    expect(sent).toEqual(["/deeptutor/api/auth/status", "/deeptutor/api/sessions"]);
    expect(events[0]).toEqual({ kind: "expired" });
    expect(events.filter((e) => (e as { kind: string }).kind === "expired")).toHaveLength(1);
    expect(navigatedTo).toBeNull();
  });
});

describe("去登录", () => {
  it("先等各组件把草稿存好，再跳统一登录页", async () => {
    mockBackend(false);
    const { goToLogin, BEFORE_LOGIN_EVENT } = await import("@/lib/momorain-guest");
    const order: string[] = [];
    window.addEventListener(BEFORE_LOGIN_EVENT, (event) => {
      (event as CustomEvent<Promise<unknown>[]>).detail.push(
        new Promise((resolve) => setTimeout(() => { order.push("draft saved"); resolve(null); }, 10)),
      );
    });
    await goToLogin("https://momorain.com/login?next=%2Fdeeptutor%2Fchat");
    order.push("navigate");
    expect(order).toEqual(["draft saved", "navigate"]);
    expect(navigatedTo).toBe("https://momorain.com/login?next=%2Fdeeptutor%2Fchat");
  });
});

describe("握手失败的登录复查", () => {
  it.each([false, true])("1006：网络故障=%s", async (networkFailure) => {
    if (networkFailure) vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    else mockBackend(false);
    const events = loginEvents();
    const guest = await import("@/lib/momorain-guest");
    guest.notePrincipalFromStatus({ authenticated: true, user_id: "account-A" });
    const { TurnRuntimeClient } = await import("@/features/chat/transport/TurnRuntimeClient");
    const handlers = new Map<string, (e: unknown) => void>();
    const socket = { readyState: 0, send: vi.fn(), close: vi.fn(), addEventListener: (name: string, fn: (e: unknown) => void) => handlers.set(name, fn) };
    const factory = vi.fn(() => socket);
    const timer = vi.fn();
    const client = new TurnRuntimeClient({socketFactory: factory as never, onEvent: () => {}, scheduler: {setTimeout: timer, clearTimeout: () => {}}});
    client.setResumeCursor("active-turn", 0);
    client.connect();
    handlers.get("close")?.({code: 1006});
    if (networkFailure) {
      await vi.waitFor(() => expect(timer).toHaveBeenCalledTimes(1));
      expect(events).toEqual([]);
      expect(guest.currentPrincipal()).toBe("user");
    } else {
      await vi.waitFor(() => expect(client.state).toBe("stopped"));
      expect(timer).not.toHaveBeenCalled();
      expect(events).toEqual([{kind: "expired"}]);
      client.connect();
      expect(factory).toHaveBeenCalledTimes(1);
      expect(guest.lastAuthenticatedUserId()).toBe("account-A");
    }
    client.stop();
  });
});
