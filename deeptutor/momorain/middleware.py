"""ASGI 门禁同时覆盖 HTTP、上传和 WebSocket，身份在执行任务前安装并最终清理。"""
from __future__ import annotations

import asyncio
import os
from http.cookies import SimpleCookie
from urllib.parse import urlsplit

from fastapi import HTTPException
from starlette.responses import JSONResponse

from .access import authenticate, identity

# 服务端也关门；只藏按钮不能禁用本机代码执行或 OAuth 登录。
DISABLED = (
    "/api/partners", "/api/partner-groups", "/api/subagents", "/api/mcp", "/api/space/cli",
    "/api/space/mcp", "/api/plugins", "/api/system/update",
    "/api/auth/device", "/api/auth/session-handoff", "/api/auth/openai-codex",
    "/api/settings/providers", "/api/space/cli-apps", "/api/auth/github-copilot", "/api/auth/codebuddy", "/api/auth/users", "/api/multi-user/users",
    "/api/auth/register", "/api/auth/login", "/api/auth/profile", "/api/auth/logout",
)


def blocked(path: str, method: str = "GET") -> bool:
    if path.startswith("/api/modules") and method != "GET":
        return True
    return any(path == prefix or path.startswith(prefix + "/") for prefix in DISABLED)


class MomorainMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path in {"/health", "/health/live", "/health/ready", "/"}:
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        cookie = SimpleCookie()
        try:
            cookie.load(headers.get(b"cookie", b"").decode("latin-1"))
            token = cookie["session"].value if "session" in cookie else ""
        except Exception:
            token = ""
        context_token = None
        user_token = None
        try:
            if blocked(path, scope.get("method", "GET")):
                raise HTTPException(404, "Not Found")
            # 同域 Cookie 鉴权的写操作和 WS 需要检查 Origin，避免跨站代发。
            origin = headers.get(b"origin", b"").decode("latin-1")
            if origin and (scope["type"] == "websocket" or scope.get("method") not in {"GET", "HEAD", "OPTIONS"}):
                if origin.rstrip("/") != os.environ.get("MOMORAIN_PUBLIC_ORIGIN", "https://momorain.com").rstrip("/"):
                    raise HTTPException(403, "不允许跨站请求")
            try:
                user = await asyncio.to_thread(authenticate, token)
            except HTTPException as exc:
                if path == "/api/auth/status" and exc.status_code == 401 and scope["type"] == "http":
                    return await JSONResponse({"enabled": True, "authenticated": False}, headers={"Cache-Control": "no-store"})(scope, receive, send)
                raise
            from deeptutor.multi_user.identity import ensure_external_user
            await asyncio.to_thread(ensure_external_user, user.user_id)
            context_token = identity.set(user)
            # 所有用户（包括站点运营者）都有独立目录；不自动提升首个登录者。
            from deeptutor.services.auth import TokenPayload
            payload = TokenPayload(username=user.user_id, user_id=user.user_id, role="user")
            scope.setdefault("state", {})["momorain_payload"] = payload
            from deeptutor.multi_user.context import set_current_user, user_from_token_payload
            user_token = set_current_user(user_from_token_payload(payload))
        except HTTPException as exc:
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 4401 if exc.status_code == 401 else 1013 if exc.status_code == 503 else 4403})
            return await JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers={"Cache-Control": "no-store"})(scope, receive, send)
        try:
            if path == "/api/auth/status":
                return await JSONResponse({"enabled": True, "authenticated": True, "user_id": user.user_id,
                    "username": user.nickname, "role": "user", "preset": "standard"}, headers={"Cache-Control": "no-store"})(scope, receive, send)
            if path == "/api/momorain/access":
                from .access import provision
                access = await asyncio.to_thread(provision)
                # 公开给页面的响应只有显示信息，key 永不离开后端。
                return await JSONResponse({k: access.get(k) for k in ("ready", "reason", "modelInfos", "quota", "consoleUrl", "topupUrl")}, headers={"Cache-Control": "no-store"})(scope, receive, send)
            await self.app(scope, receive, send)
        except HTTPException as exc:
            # 中间件内部的 provision 异常也需要成为 HTTP 响应，而不是变成 500。
            if scope["type"] == "http":
                await JSONResponse({"detail": exc.detail}, status_code=exc.status_code)(scope, receive, send)
            else:
                await send({"type": "websocket.close", "code": 1013})
        finally:
            if user_token is not None:
                from deeptutor.multi_user.context import reset_current_user
                reset_current_user(user_token)
            if context_token is not None:
                identity.reset(context_token)
