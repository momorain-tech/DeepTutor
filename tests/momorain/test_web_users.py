"""验证真实存储和 ASGI 边界：多用户、并发多会话、重启、事件回放及托管扣费身份。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException
import httpx
import pytest

from deeptutor.momorain import access
from deeptutor.momorain.middleware import MomorainMiddleware

A = "u_" + "a" * 32
B = "u_" + "b" * 32


@pytest.fixture(autouse=True)
def sandbox(monkeypatch, tmp_path):
    monkeypatch.setenv("MOMORAIN_ENABLED", "true")
    from deeptutor.multi_user import identity, paths

    monkeypatch.setattr(identity, "USERS_FILE", tmp_path / "system/auth/users.json")
    monkeypatch.setattr(identity, "LEGACY_USERS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(paths, "USERS_ROOT", tmp_path / "users")
    monkeypatch.setattr(paths, "SYSTEM_ROOT", tmp_path / "system")
    monkeypatch.setattr(paths, "_legacy_migration_done", True)
    monkeypatch.setattr(paths, "_path_services", {})
    # provision 有进程内缓存，每个测试从空缓存开始，免得上一个测试的假 key 串进来。
    access.clear_cache()
    yield
    access.clear_cache()


def fake_json(url, cookie):
    if cookie == "down":
        raise HTTPException(503, "登录服务暂时不可用")
    if cookie not in {"alice", "bob"}:
        raise HTTPException(401, "请先登录")
    uid = A if cookie == "alice" else B
    if "session" in url:
        return {"status": "ok", "data": {"userId": uid, "nickname": cookie}}
    return {
        "success": True,
        "data": {
            "ready": True,
            "key": "sk-" + cookie,
            "baseUrl": "https://momorain.com/one-api",
            "quota": {"display": "¥1.00"},
            "modelInfos": [
                {"id": "chat", "capabilities": ["text"]},
                {"id": "embed", "capabilities": ["embedding"]},
                {"id": "unknown", "capabilities": []},
            ],
            "topupUrl": "https://momorain.com/account",
        },
    }


def app_with_boundary():
    app = FastAPI()
    app.add_middleware(MomorainMiddleware)

    @app.get("/api/probe")
    async def probe():
        from deeptutor.multi_user.context import get_current_user
        from deeptutor.services.session import get_session_store

        await asyncio.sleep(0)
        return {"uid": get_current_user().id, "path": str(get_session_store().db_path)}

    return app


@pytest.mark.asyncio
async def test_http_isolation_and_auth_service_failure(monkeypatch):
    monkeypatch.setattr(access, "_json", fake_json)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with_boundary()), base_url="http://testserver"
    ) as client:
        assert (await client.get("/api/probe")).status_code == 401
        assert (
            await client.get("/api/probe", headers={"Cookie": "session=down"})
        ).status_code == 503
        responses = await asyncio.gather(
            *[
                client.get("/api/probe", headers={"Cookie": f"session={who}"})
                for who in ["alice", "bob"] * 4
            ]
        )
        for i, response in enumerate(responses):
            uid = A if i % 2 == 0 else B
            assert response.status_code == 200
            assert response.json()["uid"] == uid
            assert uid in response.json()["path"]
        assert access.identity.get() is None
        assert (
            await client.post(
                "/api/probe", headers={"Cookie": "session=alice", "Origin": "https://evil.test"}
            )
        ).status_code == 403
        for path in ["/api/subagents", "/api/auth/register", "/api/auth/login", "/api/space/mcp"]:
            assert (await client.get(path)).status_code == 404
        result = await client.get("/api/momorain/access", headers={"Cookie": "session=alice"})
        assert result.status_code == 200
        assert "sk-alice" not in result.text
        assert "key" not in result.json()


@pytest.mark.asyncio
async def test_multiple_sessions_survive_new_store_and_cannot_cross_owner(tmp_path):
    from deeptutor.multi_user.context import (
        reset_current_user,
        set_current_user,
        user_from_token_payload,
    )
    from deeptutor.services.auth import TokenPayload
    from deeptutor.services.session import get_session_store
    from deeptutor.services.session.sqlite_store import SQLiteSessionStore

    async def write(uid, title):
        token = set_current_user(
            user_from_token_payload(TokenPayload(username=uid, user_id=uid, role="user"))
        )
        try:
            store = get_session_store()
            session = await store.create_session(title=title)
            await store.add_message(session["id"], "user", f"{uid}:{title}")
            await store.add_message(session["id"], "assistant", f"回答:{title}")
            turn = await store.create_turn(session["id"], "chat")
            events = await store.append_events(
                turn["id"], [{"type": "token", "content": "一"}, {"type": "token", "content": "二"}]
            )
            return store.db_path, session["id"], turn["id"], events
        finally:
            reset_current_user(token)

    saved = await asyncio.gather(write(A, "数学"), write(A, "英语"), write(B, "物理"))
    assert saved[0][0] == saved[1][0] != saved[2][0]
    for path, sid, tid, events in saved:
        restarted = SQLiteSessionStore(db_path=path)
        history = await restarted.get_session_with_messages(sid)
        assert len(history["messages"]) == 2
        replay = await restarted.get_turn_events(tid, after_seq=events[0]["seq"])
        assert len(replay) == 1
        assert replay[0]["content"] == "二"
    alice = SQLiteSessionStore(db_path=saved[0][0])
    bob = SQLiteSessionStore(db_path=saved[2][0])
    assert await alice.get_session(saved[2][1]) is None
    assert await bob.get_session(saved[0][1]) is None
    assert len(await alice.list_sessions()) == 2
    assert len(await bob.list_sessions()) == 1


def test_keys_rotate_and_both_model_roles_use_current_owner(monkeypatch):
    calls = []

    def rotating(url, cookie):
        calls.append(cookie)
        body = fake_json(url, cookie)
        body["data"]["key"] += f"-{len(calls)}"
        return body

    monkeypatch.setattr(access, "_json", rotating)
    from deeptutor.services.config.model_catalog import ModelCatalogService
    from deeptutor.services.config.provider_runtime import (
        resolve_embedding_runtime_config,
        resolve_llm_runtime_config,
    )

    for who, uid in [("alice", A), ("bob", B)]:
        token = access.identity.set(access.Identity(uid, who, who))
        try:
            chat = resolve_llm_runtime_config({})
            embed = resolve_embedding_runtime_config({})
            again = resolve_llm_runtime_config({})
            assert who in chat.api_key and who in embed.api_key
            # 缓存期内同一用户复用同一把 key，不再每次都去打 one-api。
            assert chat.api_key == again.api_key
            access.clear_cache()
            rotated = resolve_llm_runtime_config({})
            assert who in rotated.api_key and rotated.api_key != chat.api_key
            assert embed.model == "embed"
            assert "sk-" not in str(ModelCatalogService().load())
            with pytest.raises(PermissionError):
                ModelCatalogService().save({})
        finally:
            access.identity.reset(token)


def test_unready_never_calls_model_and_selection_is_enforced(monkeypatch):
    monkeypatch.setattr(access, "_json", fake_json)
    token = access.identity.set(access.Identity(A, "alice", "alice"))
    try:
        with pytest.raises(HTTPException) as exc:
            access.resolve_model({"profile_id": "momorain", "model_id": "embed"})
        assert exc.value.status_code == 400
        assert [
            m["id"] for m in access.models(fake_json("provision", "alice")["data"], "text")
        ] == ["chat", "unknown"]

        def empty(url, cookie):
            body = fake_json(url, cookie)
            body["data"].update(ready=False, reason="no_quota")
            return body

        monkeypatch.setattr(access, "_json", empty)
        access.clear_cache()  # 模拟缓存过期后上游变成额度不足
        with pytest.raises(HTTPException) as exc:
            access.resolve_model()
        assert exc.value.status_code == 402
    finally:
        access.identity.reset(token)


def test_second_instance_cannot_recover_live_users(tmp_path):
    from deeptutor.momorain.lease import instance_lease

    path = tmp_path / "data/.momorain-web.lock"
    with instance_lease(path):
        with pytest.raises(RuntimeError, match="已有"):
            with instance_lease(path):
                pass
    with instance_lease(path):
        pass  # 首个实例退出后，下一次重启能正常取得锁。


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_provision_cache_is_per_user_and_expires(monkeypatch):
    calls = []

    def counting(url, cookie):
        calls.append(cookie)
        return fake_json(url, cookie)

    clock = Clock()
    monkeypatch.setattr(access, "_json", counting)
    monkeypatch.setattr(access, "_now", clock)
    for who, uid in [("alice", A), ("bob", B), ("alice", A), ("bob", B)]:
        token = access.identity.set(access.Identity(uid, who, who))
        try:
            assert access.provision()["key"] == "sk-" + who
        finally:
            access.identity.reset(token)
    # 两个用户各取一次；第二轮命中各自的缓存，没有串号。
    assert calls == ["alice", "bob"]

    clock.t += access.CACHE_TTL + 1
    token = access.identity.set(access.Identity(A, "alice", "alice"))
    try:
        access.provision()
    finally:
        access.identity.reset(token)
    assert calls == ["alice", "bob", "alice"]


def test_provision_rate_limited_falls_back_to_recent_cache(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(access, "_now", clock)
    monkeypatch.setattr(access, "_json", fake_json)
    token = access.identity.set(access.Identity(A, "alice", "alice"))
    try:
        assert access.provision(require_ready=True)["key"] == "sk-alice"

        def limited(url, cookie):
            raise HTTPException(503, "模型服务繁忙，请稍等片刻再试")

        monkeypatch.setattr(access, "_json", limited)
        clock.t += access.CACHE_TTL + 1  # 缓存过期，但还在宽限期内
        assert access.provision(require_ready=True)["key"] == "sk-alice"

        clock.t += access.STALE_GRACE  # 超出宽限期：如实报错
        with pytest.raises(HTTPException) as exc:
            access.provision(require_ready=True)
        assert exc.value.status_code == 503
    finally:
        access.identity.reset(token)


def test_provision_logout_drops_cache(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(access, "_now", clock)
    monkeypatch.setattr(access, "_json", fake_json)
    token = access.identity.set(access.Identity(A, "alice", "alice"))
    try:
        access.provision()
    finally:
        access.identity.reset(token)
    clock.t += access.CACHE_TTL + 1
    # 同一用户、Cookie 已失效：401 不能被旧缓存兜住。
    token = access.identity.set(access.Identity(A, "alice", "expired"))
    try:
        with pytest.raises(HTTPException) as exc:
            access.provision()
        assert exc.value.status_code == 401
    finally:
        access.identity.reset(token)
    assert A not in access._cache
