"""未登录访客能读到的东西：一张明确的白名单，每一项都由这里单独作答。

为什么不直接「放行所有 GET」：
- 有些 GET 读的是个人数据（会话列表、知识库、任务板）；
- 有些 GET 会花钱或建目录（模型列表要取 provision，工作区列表会顺手建用户目录）；
- 更要紧的是，上游的 get_current_user() 在缺身份时会回退到本地管理员，
  匿名请求一旦进了原路由，读写的就是管理员目录。

所以访客请求**从不进入原有路由**：命中白名单就由这里返回一份与用户无关的
默认响应；不在白名单里的一律 401。这里的每个函数都只能读部署级的全局信息
（模块清单、能力清单、出厂默认值），不能读任何用户目录或调用模型。

前端 web/lib/momorain-guest.ts 里有同一张表（GUEST_PATHS / GUEST_PATTERNS），两边要一起改。

官方课程的目录、详情、公开样课也对访客开放（GUEST_PATTERNS）：它们只读部署级的
课程包文件，答案和草稿在投影层就没有字段，访客读到的和登录用户完全一样。
"""

from __future__ import annotations

import asyncio
import inspect
import re
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


def _courses() -> dict[str, Any]:
    from deeptutor.learning.courses.repository import public_courses

    return public_courses()


def _course_detail(course_id: str) -> dict[str, Any] | None:
    from deeptutor.learning.courses.repository import public_course_detail

    return public_course_detail(course_id)


def _course_preview(course_id: str) -> dict[str, Any] | None:
    from deeptutor.learning.courses.repository import public_course_preview

    return public_course_preview(course_id)


GUEST_ROUTES["/api/courses"] = _courses

# 带参数的公共路径。参数只允许课程 ID 的字符集，免得拼出奇怪的东西。
_COURSE_ID = r"([a-z0-9][a-z0-9_-]{0,63})"
GUEST_PATTERNS: list[tuple[re.Pattern[str], Callable[[str], Any]]] = [
    (re.compile(rf"^/api/courses/{_COURSE_ID}$"), _course_detail),
    (re.compile(rf"^/api/courses/{_COURSE_ID}/preview$"), _course_preview),
]

#: 匹配上了公共路径、但资源不存在（课程不存在或没发布）。中间件据此回 404 而不是 401。
NOT_FOUND: dict[str, Any] = {"detail": {"code": "COURSE_NOT_FOUND", "message": "课程不存在或尚未发布"}}


async def guest_response(path: str, method: str) -> dict[str, Any] | None:
    """命中白名单就返回访客响应，否则 None（调用方应回 401）。

    带参数的公共路径找不到资源时返回 :data:`NOT_FOUND`（同一个对象，调用方用 ``is`` 判断）。
    """
    if method not in {"GET", "HEAD"}:
        return None
    handler = GUEST_ROUTES.get(path)
    if handler is None:
        for pattern, param_handler in GUEST_PATTERNS:
            match = pattern.match(path)
            if match:
                body = await asyncio.to_thread(param_handler, match.group(1))
                return NOT_FOUND if body is None else body
        return None
    if inspect.iscoroutinefunction(handler):
        return await handler()
    # 同步函数（如模块清单要查已装包的版本）放到线程里，别卡住事件循环。
    return await asyncio.to_thread(handler)
