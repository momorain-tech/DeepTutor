"""未登录访客能读到的东西：一张明确的白名单，每一项都由这里单独作答。

为什么不直接「放行所有 GET」：
- 有些 GET 读的是个人数据（会话列表、知识库、任务板）；
- 有些 GET 会花钱或建目录（模型列表要取 provision，工作区列表会顺手建用户目录）；
- 更要紧的是，上游的 get_current_user() 在缺身份时会回退到本地管理员，
  匿名请求一旦进了原路由，读写的就是管理员目录。

所以访客请求**从不进入原有路由**：命中白名单就由这里返回一份与用户无关的
默认响应；不在白名单里的一律 401。这里的每个函数都只能读部署级的全局信息
（模块清单、能力清单、出厂默认值），不能读任何用户目录或调用模型。

前端 web/lib/momorain-guest.ts 里有同一张表（GUEST_PATHS），两边要一起改。
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable


def _auth_status() -> dict[str, Any]:
    return {"enabled": True, "authenticated": False}


def _ui_settings() -> dict[str, Any]:
    # 只给出厂默认值，不读任何人保存的偏好（load_ui_settings 会落到当前用户目录）。
    from deeptutor.services.settings.interface_settings import DEFAULT_UI_SETTINGS

    return {k: DEFAULT_UI_SETTINGS.get(k) for k in ("theme", "language", "response_language")}


def _page_customization() -> dict[str, Any]:
    # 页面定制的出厂默认值；用户自己写的开场白是私人数据，不能出现在这里。
    from deeptutor.api.routers.settings import PageCustomization

    return PageCustomization().model_dump()


def _modules() -> dict[str, Any]:
    # 部署装了哪些模块：全站一份，与用户无关。前端靠它决定显示哪些功能入口。
    # 直接复用原路由的函数体——它只读部署级模块清单（已核对），不碰用户上下文。
    from deeptutor.api.routers.modules import list_modules

    return list_modules()


async def _capabilities() -> dict[str, Any]:
    # 能力（对话模式）的说明和配置结构：同样是部署级的，不含用户数据。
    from deeptutor.api.routers.capabilities import list_registered_capabilities

    return await list_registered_capabilities()


# 路径 → 访客响应。只接受 GET / HEAD。
GUEST_ROUTES: dict[str, Callable[[], Any]] = {
    "/api/auth/status": _auth_status,
    "/api/settings/ui": _ui_settings,
    "/api/settings/page-customization": _page_customization,
    "/api/modules": _modules,
    "/api/capabilities/registered": _capabilities,
}


async def guest_response(path: str, method: str) -> dict[str, Any] | None:
    """命中白名单就返回访客响应，否则 None（调用方应回 401）。"""
    if method not in {"GET", "HEAD"}:
        return None
    handler = GUEST_ROUTES.get(path)
    if handler is None:
        return None
    if inspect.iscoroutinefunction(handler):
        return await handler()
    # 同步函数（如模块清单要查已装包的版本）放到线程里，别卡住事件循环。
    return await asyncio.to_thread(handler)
