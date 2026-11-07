"""课程包命令行：写课程、发布前、CI 里都用它。

    python -m deeptutor.learning.courses validate [目录]   校验所有课程 + 锁文件
    python -m deeptutor.learning.courses lock [目录]       把已发布版本的哈希写进锁文件

目录默认是线上用的课程目录（DEEPTUTOR_COURSES_DIR 或仓库内置目录）。
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from deeptutor.learning.courses.package import check_lock, scan_catalog, write_lockfile
from deeptutor.learning.courses.repository import default_catalog_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m deeptutor.learning.courses")
    parser.add_argument("command", choices=["validate", "lock"])
    parser.add_argument("catalog", nargs="?", type=Path, default=None)
    args = parser.parse_args(argv)
    root: Path = args.catalog or default_catalog_root()

    courses, report = scan_catalog(root)
    expected_lock = check_lock(root, courses, report)
    if args.command == "lock":
        # 锁文件只"追加新发布的版本"；已登记但哈希不符的照旧报错，不会被悄悄改写。
        modified = [i for i in report.errors if i.code.value == "COURSE_VERSION_MODIFIED"]
        others = [i for i in report.errors if i.code.value not in {
            "COURSE_VERSION_MODIFIED", "COURSE_VERSION_UNLOCKED"}]
        for issue in modified + others:
            print(f"[error] {issue.where}: {issue.message} ({issue.code.value})")
        if modified or others:
            return 1
        write_lockfile(root, expected_lock)
        print(f"已写入 {root / 'courses.lock.json'}（{len(expected_lock)} 个版本）")
        return 0

    for issue in report.issues:
        print(f"[{issue.severity}] {issue.where}: {issue.message} ({issue.code.value})")
    total = sum(len(course.versions) for course in courses.values())
    print(f"{root}: {len(courses)} 门课、{total} 个可用版本，{len(report.errors)} 个错误")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
