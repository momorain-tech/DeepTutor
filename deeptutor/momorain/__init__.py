"""momorain 网页多用户部署接入；不开启时不改变上游行为。"""

import os


def enabled() -> bool:
    return os.environ.get("MOMORAIN_ENABLED", "").lower() in {"1", "true", "yes"}
