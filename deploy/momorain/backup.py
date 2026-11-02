"""运行中安全备份：SQLite 使用 online backup，不能只拷 db 而漏掉 WAL。"""
from pathlib import Path
import argparse
import shutil
import sqlite3


def backup(data: Path, destination: Path) -> None:
    data, destination = data.resolve(), destination.resolve()
    if destination == data or data in destination.parents:
        raise ValueError("备份目标必须在 data 目录之外")
    destination.mkdir(parents=True, exist_ok=False)
    for source in data.rglob("*"):
        if not source.is_file() or source.name.endswith(("-wal", "-shm")):
            continue
        target = destination / source.relative_to(data)
        target.parent.mkdir(parents=True, exist_ok=True)
        # 有些数据库文件没有 .sqlite 后缀，读文件头判断，避免漏掉它们。
        with source.open("rb") as stream:
            sqlite = stream.read(16) == b"SQLite format 3\0"
        if sqlite:
            with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src, sqlite3.connect(target) as dst:
                src.backup(dst)
        else:
            shutil.copy2(source, target)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    parser.add_argument("--data", type=Path, default=Path("/app/data"))
    args = parser.parse_args()
    backup(args.data, args.destination)
