"""官方课程接口在真实 momorain 门禁后面的行为：访客能浏览目录、样课；
其他一切要登录；登录后按用户隔离；重复加入、重复提交都只算一次。"""

from __future__ import annotations

from pathlib import Path
import shutil

from fastapi import FastAPI
import httpx
import pytest

from deeptutor.api.routers.courses import public_router, router
from deeptutor.learning.courses.repository import reset_course_repository
from deeptutor.momorain import access
from deeptutor.momorain.middleware import MomorainMiddleware

from .test_web_users import fake_json, sandbox  # noqa: F401  （sandbox 是 autouse fixture）

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "courses" / "catalog"


@pytest.fixture
def calls(monkeypatch, tmp_path):
    shutil.copytree(FIXTURE, tmp_path / "catalog")
    reset_course_repository(tmp_path / "catalog")
    seen: list[str] = []

    def recording(url, cookie):
        seen.append(url)
        return fake_json(url, cookie)

    monkeypatch.setattr(access, "_json", recording)
    yield seen
    reset_course_repository(None)


def make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(MomorainMiddleware)
    app.include_router(public_router, prefix="/api/courses")
    app.include_router(router, prefix="/api/course-enrollments")
    return app


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app()), base_url="http://t")


ALICE = {"Cookie": "session=alice"}
BOB = {"Cookie": "session=bob"}


@pytest.mark.asyncio
async def test_guest_browses_catalog_and_preview_without_side_effects(calls):
    from deeptutor.multi_user import paths

    async with client() as c:
        listing = await c.get("/api/courses")
        assert listing.status_code == 200
        assert [x["course_id"] for x in listing.json()["courses"]] == ["demo-signals"]
        assert (await c.get("/api/courses/demo-signals")).status_code == 200
        preview = (await c.get("/api/courses/demo-signals/preview")).json()
        assert [x["lesson"]["id"] for x in preview["lessons"]] == ["colors"]
        missing = await c.get("/api/courses/no-such-course")
        assert missing.status_code == 404
        assert missing.json()["detail"]["code"] == "COURSE_NOT_FOUND"
        # 路径参数不合法的不在白名单里 → 401，不会被当成课程 ID 去读文件
        assert (await c.get("/api/courses/..%2F..%2Fetc")).status_code in {401, 404}
        assert (await c.get("/api/course-enrollments")).status_code == 401
        assert (await c.post("/api/course-enrollments", json={"course_id": "demo-signals"})).status_code == 401
    assert not paths.USERS_ROOT.exists() or not any(paths.USERS_ROOT.iterdir())
    assert not any("provision" in url for url in calls)


@pytest.mark.asyncio
async def test_enroll_study_and_practice_over_http(calls):
    async with client() as c:
        first = await c.post("/api/course-enrollments", json={"course_id": "demo-signals"}, headers=ALICE)
        assert first.status_code == 201
        enrollment = first.json()["enrollment"]
        eid = enrollment["enrollment_id"]
        again = await c.post("/api/course-enrollments", json={"course_id": "demo-signals"}, headers=ALICE)
        assert again.status_code == 200 and again.json()["enrollment"]["enrollment_id"] == eid

        lesson = (await c.get(f"/api/course-enrollments/{eid}/lessons/colors", headers=ALICE)).json()
        assert lesson["content"]["lesson"]["id"] == "colors" and lesson["next_lesson_id"] == "combos"
        progress = await c.put(
            f"/api/course-enrollments/{eid}/lessons/colors/progress",
            json={"anchor": "three-colors", "percentage": 0.4},
            headers=ALICE,
        )
        assert progress.json()["state"] == "reading"

        questions = (await c.get(f"/api/course-enrollments/{eid}/lessons/colors/questions", headers=ALICE)).json()
        assert "answer" not in questions["questions"][0]
        body = {"submission_id": "sub-http-0001", "answer": "A"}
        graded = (await c.post(f"/api/course-enrollments/{eid}/questions/q-green/submissions", json=body, headers=ALICE)).json()
        replay = (await c.post(f"/api/course-enrollments/{eid}/questions/q-green/submissions", json=body, headers=ALICE)).json()
        assert graded["result"] == "correct" and not graded["replayed"] and replay["replayed"]

        # bob 看不到 alice 的实例
        for path in [f"/api/course-enrollments/{eid}", f"/api/course-enrollments/{eid}/lessons/colors"]:
            denied = await c.get(path, headers=BOB)
            assert denied.status_code == 404
            assert denied.json()["detail"]["code"] == "COURSE_ENROLLMENT_NOT_FOUND"
        assert (await c.get("/api/course-enrollments", headers=BOB)).json() == {"enrollments": []}

        mine = (await c.get("/api/course-enrollments", headers=ALICE)).json()["enrollments"]
        assert [x["enrollment_id"] for x in mine] == [eid]
        assert mine[0]["lessons"][0]["state"] == "reading"
    # 整个流程只有客观题，不该取 provision（不调模型、不扣费）。
    assert not any("provision" in url for url in calls)
