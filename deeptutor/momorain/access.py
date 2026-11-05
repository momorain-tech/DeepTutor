"""只把登录凭证放进操作上下文；托管 key 只在进程内存里按用户短暂缓存，绝不落盘。"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
import os
import re
import threading
import time
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
# 仅存于一次回合的执行上下文，避免同步解析器反复阻塞事件循环。
operation_access: ContextVar[dict | None] = ContextVar("momorain_operation_access", default=None)

# ---- provision 的按用户缓存 ----
# 为什么要缓存：one-api 的 provision 接口按「来源 IP」限流（60 次 / 20 分钟）。
# 别的站是浏览器直接调，一人一个 IP；DeepTutor 是服务端代调，所有用户的请求在
# one-api 看来都来自服务器这一个 IP，共用同一个桶。而页面上读模型列表、工具状态、
# 保存设置都会取一次 provision，打开几个页面就能把桶打空，然后连发消息都 429。
# 2026-11-04 线上就是这么挂的。
#
# 缓存规则：
# - 只按 userId 分格子，绝不跨用户复用（userId 每次请求都由登录服务重新核验）。
# - 只放进程内存，不写磁盘、不进日志；进程重启即清空。
# - ready 的结果缓存 CACHE_TTL 秒；未就绪（如额度不足）只缓存 UNREADY_TTL 秒，
#   这样用户充值后很快能用上。
# - 上游 429 / 5xx 时，退回使用不太旧（STALE_GRACE 秒内）的缓存，而不是直接报错。
# - 上游说未登录（401）时立刻删掉该用户的缓存。
CACHE_TTL = float(os.environ.get("MOMORAIN_PROVISION_CACHE_SECONDS", "60"))
UNREADY_TTL = 10.0
STALE_GRACE = 300.0
_MAX_ENTRIES = 2000

_now = time.monotonic  # 测试里可替换
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_cache_guard = threading.Lock()
# 同一用户的并发请求（页面一加载就是十来个接口）只让一个去真正请求上游，其余等它的结果。
_user_locks: dict[str, threading.Lock] = {}


def clear_cache() -> None:
    with _cache_guard:
        _cache.clear()
        _user_locks.clear()


def _user_lock(user_id: str) -> threading.Lock:
    with _cache_guard:
        lock = _user_locks.get(user_id)
        if lock is None:
            lock = _user_locks[user_id] = threading.Lock()
        return lock


def _cache_get(user_id: str) -> dict[str, Any] | None:
    with _cache_guard:
        entry = _cache.get(user_id)
    if entry is None:
        return None
    stored_at, data = entry
    ttl = CACHE_TTL if data.get("ready") is True else UNREADY_TTL
    return data if _now() - stored_at <= ttl else None


def _cache_put(user_id: str, data: dict[str, Any]) -> None:
    with _cache_guard:
        if len(_cache) >= _MAX_ENTRIES:
            # 简单兜底：清掉已过宽限期的条目，避免长期运行后无限增长。
            cutoff = _now() - STALE_GRACE
            for uid in [u for u, (t, _) in _cache.items() if t < cutoff]:
                _cache.pop(uid, None)
        _cache[user_id] = (_now(), data)


def _cache_drop(user_id: str) -> None:
    with _cache_guard:
        _cache.pop(user_id, None)


def _json(url: str, cookie: str) -> dict[str, Any]:
    # 每次新建客户端，不在并发用户之间共用有 CookieJar 的 Session。
    try:
        with httpx.Client(timeout=8, follow_redirects=False, trust_env=False) as client:
            response = client.get(url, cookies={"session": cookie}, headers={"Referer": REFERER})
        if response.status_code == 401:
            raise HTTPException(401, "请先登录 momorain")
        if response.status_code == 429:
            # 单独给一句话：这是「太频繁」，等一会儿就好，不是账号坏了。
            raise HTTPException(503, "模型服务繁忙，请稍等片刻再试")
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


def _fetch_provision(current: Identity) -> dict[str, Any]:
    """向 one-api 取一次；成功则写入缓存，失败时在宽限期内退回旧缓存。"""
    try:
        body = _json(
            os.environ.get(
                "MOMORAIN_PROVISION_URL", "https://momorain.com/one-api/api/momorain/provision"
            ),
            current.cookie,
        )
    except HTTPException as exc:
        if exc.status_code == 401:
            _cache_drop(current.user_id)
            raise
        # 上游暂时不可用（429 / 5xx / 超时）：宽限期内的旧结果拿来临时顶一下，
        # 不重新写回缓存、不延长它的寿命；只认 ready 的旧结果，未就绪的照常报错。
        with _cache_guard:
            entry = _cache.get(current.user_id)
        if entry is not None and entry[1].get("ready") is True and _now() - entry[0] <= STALE_GRACE:
            return entry[1]
        raise
    if not body.get("success"):
        if body.get("need_login"):
            _cache_drop(current.user_id)
            raise HTTPException(401, "请先登录 momorain")
        raise HTTPException(503, "模型服务暂时不可用")
    data = body.get("data")
    if not isinstance(data, dict):
        raise HTTPException(503, "模型服务返回异常")
    _cache_put(current.user_id, data)
    return data


def provision(*, require_ready: bool = False) -> dict[str, Any]:
    current = identity.get()
    if current is None:
        raise HTTPException(401, "请先登录 momorain")
    data = operation_access.get()
    if data is None:
        data = _cache_get(current.user_id)
    if data is None:
        with _user_lock(current.user_id):
            # 拿到锁后再看一眼：可能排在前面的请求已经替我们取回来了。
            data = _cache_get(current.user_id)
            if data is None:
                data = _fetch_provision(current)
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
