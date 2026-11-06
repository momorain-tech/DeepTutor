import { withBasePath } from "@/lib/base-path";
export interface SocketMessageEvent {
  data: unknown;
  /** close 事件才有：WebSocket 关闭码。momorain 后端用 4401 表示未登录/登录过期。 */
  code?: number;
}

export interface TurnSocket {
  readonly readyState: number;
  send(data: string): void;
  close(code?: number, reason?: string): void;
  addEventListener(
    type: "open" | "message" | "close" | "error",
    listener: (event: SocketMessageEvent) => void,
  ): void;
}

export type TurnSocketFactory = (url: string) => TurnSocket;

export const SOCKET_CONNECTING = 0;
export const SOCKET_OPEN = 1;

export function browserSocketFactory(url: string): TurnSocket {
  return new WebSocket(withBasePath(url)) as unknown as TurnSocket;
}
