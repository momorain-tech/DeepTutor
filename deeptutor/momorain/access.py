"""只把登录凭证放进操作上下文；每次解析模型时重新取托管 key，绝不落盘。"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
import os
import re
from typing import Any

from fastapi import HTTPException
import httpx

REFERER = "https://momorain.com/deeptutor/"
PROFILE = "momorain"


@dataclass(frozen=True)
class Identity:
    user_id: str
    nickname: str
    cookie: str = field(repr=False)


identity: ContextVar[Identity | None] = ContextVar("momorain_identity", default=None)
# 仅存于一次回合的执行上下文，避免同步解析器反复阻塞事件循环；不跨回合复用。
operation_access: ContextVar[dict | None] = ContextVar("momorain_operation_access", default=None)


def _json(url: str, cookie: str) -> dict[str, Any]:
    # 每次新建客户端，不在并发用户之间共用有 CookieJar 的 Session。
    try:
        with httpx.Client(timeout=8, follow_redirects=False, trust_env=False) as client:
            response = client.get(url, cookies={"session": cookie}, headers={"Referer": REFERER})
        if response.status_code == 401:
            raise HTTPException(401, "请先登录 momorain")
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise ValueError("invalid response")
        return body
    except (httpx.HTTPError, ValueError) as exc:
        # 不把带 Cookie / key 的请求或上游响应串到日志、错误文本里。
        raise HTTPException(503, "账号或模型服务暂时不可用，请稍后重试") from exc


def authenticate(cookie: str) -> Identity:
    if not cookie:
        raise HTTPException(401, "请先登录 momorain")
    body = _json(
        os.environ.get("MOMORAIN_SESSION_URL", "https://momorain.com/api/auth/session"), cookie
    )
    data = body.get("data") or {}
    if not isinstance(data, dict) or body.get("status") != "ok":
        raise HTTPException(503, "登录服务返回异常")
    uid = str(data.get("userId") or "")
    # uid 参与磁盘路径，必须在可信登录服务返回后仍检查格式。
    if not re.fullmatch(r"u_[0-9a-f]{32}", uid):
        raise HTTPException(503, "登录服务返回异常用户编号")
    return Identity(uid, str(data.get("nickname") or "momorain 用户"), cookie)


def provision(*, require_ready: bool = False) -> dict[str, Any]:
    current = identity.get()
    if current is None:
        raise HTTPException(401, "请先登录 momorain")
    cached = operation_access.get()
    body = (
        {"success": True, "data": cached}
        if cached is not None
        else _json(
            os.environ.get(
                "MOMORAIN_PROVISION_URL", "https://momorain.com/one-api/api/momorain/provision"
            ),
            current.cookie,
        )
    )
    if not body.get("success"):
        raise HTTPException(
            401 if body.get("need_login") else 503,
            "请先登录 momorain" if body.get("need_login") else "模型服务暂时不可用",
        )
    data = body.get("data")
    if not isinstance(data, dict):
        raise HTTPException(503, "模型服务返回异常")
    if require_ready and data.get("ready") is not True:
        raise HTTPException(
            402 if data.get("reason") == "no_quota" else 503,
            {
                "message": "模型额度不足，请充值"
                if data.get("reason") == "no_quota"
                else "模型服务尚未就绪",
                **{k: data.get(k) for k in ("reason", "topupUrl", "consoleUrl")},
            },
        )
    return data


def models(access: dict, capability: str) -> list[dict]:
    infos = access.get("modelInfos")
    if not isinstance(infos, list):
        infos = [{"id": value, "capabilities": []} for value in access.get("models", [])]
    # 已标记的向量模型不能混入对话列表；未知能力仍保留。
    return [
        m
        for m in infos
        if isinstance(m, dict)
        and m.get("id")
        and (not m.get("capabilities") or capability in m["capabilities"])
    ]


def catalog() -> dict:
    """只含模型引用，不含凭证的动态目录，可供界面和选择校验共同使用。"""
    access = provision()
    services = {}
    for service, capability in (("llm", "text"), ("embedding", "embedding")):
        rows = models(access, capability)
        profile = {
            "id": PROFILE,
            "name": "momorain 托管",
            "binding": "custom",
            "api_key": "",
            "models": [
                {"id": m["id"], "model": m["id"], "name": m.get("name") or m["id"]} for m in rows
            ],
        }
        services[service] = {
            "profiles": [profile],
            "active_profile_id": PROFILE,
            "active_model_id": rows[0]["id"] if rows else "",
        }
    services["task"] = {"mode": "inherit", "profiles": []}
    return {"version": 1, "connections": [], "services": services}


def resolve_model(selection: Any = None, *, capability: str = "text") -> tuple[dict, dict]:
    access = provision(require_ready=True)
    rows = models(access, capability)
    selected_id = (
        selection.get("model_id")
        if isinstance(selection, dict)
        else getattr(selection, "model_id", None)
    )
    profile_id = (
        selection.get("profile_id")
        if isinstance(selection, dict)
        else getattr(selection, "profile_id", None)
    )
    if profile_id and profile_id != PROFILE:
        raise HTTPException(400, "请选择 momorain 托管模型")
    model = (
        next((m for m in rows if m["id"] == selected_id), None)
        if selected_id
        else next(iter(rows), None)
    )
    if model is None:
        raise HTTPException(
            400,
            "所选模型不可用，请重新选择"
            if selected_id
            else "暂无可用的向量模型"
            if capability == "embedding"
            else "暂无可用的对话模型",
        )
    if not access.get("key") or not access.get("baseUrl"):
        raise HTTPException(503, "模型服务返回不完整的凭证")
    return access, model
