"""官方课程测试的公共夹具：每个测试拿到一份模拟课程目录的副本，可以随便改。"""

from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from deeptutor.learning.courses.enrollment import EnrollmentService
from deeptutor.learning.courses.package import check_lock, scan_catalog, write_lockfile
from deeptutor.learning.courses.repository import CourseRepository, reset_course_repository
from deeptutor.learning.storage import LearningStore

FIXTURE_CATALOG = Path(__file__).resolve().parents[1] / "fixtures" / "courses" / "catalog"
COURSE = "demo-signals"


@pytest.fixture
def catalog(tmp_path: Path) -> Path:
    root = tmp_path / "catalog"
    shutil.copytree(FIXTURE_CATALOG, root)
    return root


@pytest.fixture
def repo(catalog: Path) -> CourseRepository:
    # 全局仓库也指过去：materials.py / 路由用的是全局那个。
    return reset_course_repository(catalog)


def make_store(tmp_path: Path, name: str) -> LearningStore:
    return LearningStore(root=tmp_path / "users" / name / "learning")


@pytest.fixture
def store(tmp_path: Path) -> LearningStore:
    return make_store(tmp_path, "alice")


@pytest.fixture
def enrollments(store: LearningStore, repo: CourseRepository) -> EnrollmentService:
    return EnrollmentService(store, repo)


def edit_json(path: Path, mutate) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def publish_new_version(catalog: Path, version: str = "1.1.0", *, title: str = "新版标题") -> None:
    """复制 1.0.0 成新版本、改个标题、发布并写锁——模拟课程更新。"""
    course_root = catalog / COURSE
    shutil.copytree(course_root / "1.0.0", course_root / version)
    edit_json(course_root / version / "manifest.json", lambda d: d.update(version=version, title=title))
    edit_json(course_root / "course.json", lambda d: d["versions"].update({version: "published"}))
    courses, report = scan_catalog(catalog)
    write_lockfile(catalog, check_lock(catalog, courses, report))
