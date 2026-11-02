"""单副本网页部署的数据卷锁，拒绝第二个内存协调器误恢复仍在执行的回合。"""
from contextlib import asynccontextmanager, contextmanager

from . import enabled


@contextmanager
def instance_lease(path):
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("此 data 卷已有 DeepTutor 网页实例运行；请使用单副本和 stop-first 更新") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def web_lifespan(original):
    @asynccontextmanager
    async def wrapped(app):
        if not enabled():
            async with original(app):
                yield
            return
        from deeptutor.runtime.home import get_runtime_data_root
        with instance_lease(get_runtime_data_root() / ".momorain-web.lock"):
            async with original(app):
                yield
    return wrapped
