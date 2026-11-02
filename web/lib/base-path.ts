/** 只有本应用的网络与静态资源补前缀；全站账号和网关始终在站点根。 */
export const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "";

export function withBasePath(value: string): string {
  if (!BASE_PATH || !value.startsWith("/") || value.startsWith("//")) return value;
  if (value === BASE_PATH || value.startsWith(BASE_PATH + "/") || value.startsWith(BASE_PATH + "?")) return value;
  if (/^\/(?:api\/auth\/(?:session|captcha|sms|logout|profile)|one-api)(?:\/|\?|$)/.test(value)) return value;
  return BASE_PATH + value;
}

export function appFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  if (typeof input === "string") return fetch(withBasePath(input), init);
  return fetch(input, init);
}

/** 浏览器 location 含前缀，Next router 则接收应用内路径。 */
export function withoutBasePath(value: string): string {
  if (BASE_PATH && (value === BASE_PATH || value.startsWith(BASE_PATH + "/"))) {
    return value.slice(BASE_PATH.length) || "/";
  }
  return value;
}
