"""「先浏览、保存或调用模型时再登录」的服务端边界。

访客只能读白名单里的公共只读接口；这些接口不进原路由、不建用户目录、不取 provision。
其余一切（私人数据、写入、WebSocket、回合入口）没有身份就拒绝——不依赖前端是否拦住。
"""

from __future__ import annotations

from fastapi import FastAPI, WebSocket
import httpx
import pytest

from deeptutor.momorain import access
from deeptutor.momorain.guest import GUEST_ROUTES
from deeptutor.momorain.middleware import MomorainMiddleware

from .test_web_users import fake_json, sandbox  # noqa: F401  （sandbox 是 autouse fixture）


def boundary_app(reached: list[str]) -> FastAPI:
    """每条路由都记一笔「被执行了」——访客请求一条都不该走到这里。"""
    app = FastAPI()
    app.add_middleware(MomorainMiddleware)

    for path in [*GUEST_ROUTES, "/api/sessions", "/api/task-board", "/api/settings"]:

        def make(p):
            async def handler():
                reached.append(p)
                return {"from": "original-route"}

            return handler

        app.get(path)(make(path))
        app.post(path)(make(path))
        app.put(path)(make(path))

    @app.websocket("/api/v1/ws")
    async def ws(socket: WebSocket):
        reached.append("ws")
        await socket.accept()
        await socket.close()

    return app


@pytest.fixture
def no_upstream(monkeypatch):
    """记录对账号服务 / one-api 的每次调用：访客流程里 provision 一次都不能出现。"""
    calls: list[str] = []

    def recording(url, cookie):
        calls.append(url)
        return fake_json(url, cookie)

    monkeypatch.setattr(access, "_json", recording)
    return calls


async def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


@pytest.mark.asyncio
@pytest.mark.parametrize("cookie", [None, "expired"])
async def test_guest_reads_only_the_whitelist_without_touching_user_dirs(no_upstream, cookie):
    from deeptutor.multi_user import paths

    reached: list[str] = []
    headers = {"Cookie": f"session={cookie}"} if cookie else {}
    async with await client_for(boundary_app(reached)) as client:
        for path in GUEST_ROUTES:
            response = await client.get(path, headers=headers)
            assert response.status_code == 200, path
            assert response.json() != {"from": "original-route"}, path
            assert response.headers["cache-control"] == "no-store"
        status = (await client.get("/api/auth/status", headers=headers)).json()
        assert status == {"enabled": True, "authenticated": False}
        # 私人数据读取和所有写入：没有身份就 401。
        for path in ["/api/sessions", "/api/task-board", "/api/settings"]:
            assert (await client.get(path, headers=headers)).status_code == 401, path
        for path in [*GUEST_ROUTES, "/api/sessions", "/api/settings"]:
            # 白名单只放 GET：同一路径的写入照样要登录（/api/modules 的写入本来就整体关闭，是 404）。
            for method in (client.post, client.put):
                assert (await method(path, headers=headers)).status_code in {401, 404}, path
    assert reached == []
    # 没建用户目录，也没去 one-api 取 provision。
    assert not paths.USERS_ROOT.exists() or not any(paths.USERS_ROOT.iterdir())
    assert not any("provision" in url for url in no_upstream)


@pytest.mark.asyncio
async def test_guest_websocket_is_closed_before_reaching_the_route(no_upstream):
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    reached: list[str] = []
    with TestClient(boundary_app(reached)) as client:
        with pytest.raises(WebSocketDisconnect) as closed:
            with client.websocket_connect("/api/v1/ws"):
                pass
    assert closed.value.code == 4401
    assert reached == []


@pytest.mark.asyncio
async def test_logged_in_users_still_get_their_own_routes(no_upstream):
    reached: list[str] = []
    async with await client_for(boundary_app(reached)) as client:
        # 已登录时白名单路径也走原路由（读的是自己的设置），不会被换成访客默认值。
        response = await client.get("/api/settings/ui", headers={"Cookie": "session=alice"})
        assert response.json() == {"from": "original-route"}
        assert (
            await client.get("/api/sessions", headers={"Cookie": "session=alice"})
        ).status_code == 200
    assert reached == ["/api/settings/ui", "/api/sessions"]


@pytest.mark.asyncio
async def test_auth_outage_is_not_mistaken_for_a_guest(no_upstream):
    """登录服务挂了不能当成「未登录」给默认值：已登录用户会看到别人的界面配置后误操作。"""
    reached: list[str] = []
    async with await client_for(boundary_app(reached)) as client:
        response = await client.get("/api/settings/ui", headers={"Cookie": "session=down"})
    assert response.status_code == 503
    assert reached == []


def test_guest_defaults_do_not_read_saved_preferences(monkeypatch):
    """访客默认值只来自出厂配置，即使有人改过自己的偏好也读不到。"""
    from deeptutor.api.routers import settings as settings_router
    from deeptutor.momorain import guest

    def leak():
        raise AssertionError("访客响应不能读用户保存的设置")

    monkeypatch.setattr(settings_router, "load_ui_settings", leak)
    assert set(guest._ui_settings()) == {"theme", "language", "response_language"}
    assert guest._page_customization()["chat"]["starters"] == {}


@pytest.mark.asyncio
async def test_turn_entry_refuses_without_identity(no_upstream):
    """最后一道门：就算哪个路由漏挂了鉴权，开回合（花钱的地方）没有身份也取不到 key。"""
    from fastapi import HTTPException

    from deeptutor.app.service import TurnApplicationService

    assert access.identity.get() is None
    with pytest.raises(HTTPException) as refused:
        await TurnApplicationService.start_turn(object.__new__(TurnApplicationService), {})
    assert refused.value.status_code == 401
    assert no_upstream == []


def test_new_users_default_to_chinese(monkeypatch):
    """momorain 部署面向中文用户：没保存过语言的新用户（含访客）界面和回复都默认中文。"""
    import importlib

    from deeptutor.services.settings import interface_settings

    try:
        monkeypatch.setenv("MOMORAIN_ENABLED", "true")
        zh = importlib.reload(interface_settings)
        assert zh.DEFAULT_UI_SETTINGS["language"] == "zh"
        assert zh.DEFAULT_UI_SETTINGS["response_language"] == "zh"
        # 已保存过语言的用户以自己的选择为准。
        assert zh.resolve_languages({"language": "en"})["language"] == "en"
        monkeypatch.setenv("MOMORAIN_ENABLED", "false")
        assert importlib.reload(interface_settings).DEFAULT_UI_SETTINGS["language"] == "en"
    finally:
        monkeypatch.undo()
        importlib.reload(interface_settings)
