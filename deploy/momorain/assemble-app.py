"""在生产服务器上把一次发布组装成镜像：只校验 + 复制文件，不联网、不编译、不动线上服务。

    ssh root@<服务器> python3 - <完整提交SHA> <发布目录> < deploy/momorain/assemble-app.py

<发布目录> 里需要事先放好三样东西（本机上传，或在服务器上直接下载）：
  frontend-linux-x64.tar.gz          CI 编译好的前端（GitHub release 资产）
  frontend-linux-x64.tar.gz.sha256   它的校验值
  source.tar.gz                      同一个提交的后端源码，本机执行：
      git archive --format=tar.gz -o source.tar.gz <SHA> \
        deeptutor deeptutor_cli packages scripts pyproject.toml build_metadata.py \
        build_support.py requirements/momorain-linux.lock deploy/momorain

产出：momorain-deeptutor-8zsa9l:app-<SHA前12位>。之后由人确认再执行
  docker service update --detach=true --no-resolve-image --image <标签> momorain-deeptutor-8zsa9l

发布目录本身就是这个版本的「回滚包」（约 40MB）：基础镜像在的话，任何旧版本都能几秒重建，
不再需要每次 docker save 整个 700MB 镜像。
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

SERVICE_REPO = "momorain-deeptutor-8zsa9l"
RUNTIME_REPO = "momorain-deeptutor-runtime"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit("用法: python3 - <完整提交SHA> <发布目录> < assemble-app.py")
    sha, release = sys.argv[1], Path(sys.argv[2])
    if len(sha) != 40:
        sys.exit("请给完整的 40 位提交 SHA，避免并发提交混进来")
    os.umask(0o077)

    # 1. 前端产物：校验值 + 元数据都要对上这次的提交和子路径。
    archive = release / "frontend-linux-x64.tar.gz"
    expected = (release / "frontend-linux-x64.tar.gz.sha256").read_text().split()[0]
    if sha256_file(archive) != expected:
        sys.exit("前端产物 SHA256 不一致")
    context = release / "context"
    if context.exists():
        shutil.rmtree(context)
    (context / "web").mkdir(parents=True)
    with tarfile.open(archive) as tar:
        tar.extractall(context / "web", filter="data")
    meta = json.loads((context / "web" / "frontend-build.json").read_text())
    want = {"commit": sha, "basePath": "/deeptutor", "platform": "linux", "arch": "x64"}
    if meta != want:
        sys.exit(f"前端元数据不对: {meta}，应为 {want}")

    # 2. 后端源码。
    with tarfile.open(release / "source.tar.gz") as tar:
        tar.extractall(context, filter="data")

    # 3. 依赖没变才能复用基础镜像：源码里锁文件的哈希就是基础镜像的标签。
    lock_sha = sha256_file(context / "requirements" / "momorain-linux.lock")
    runtime = f"{RUNTIME_REPO}:lock-{lock_sha[:12]}"
    if subprocess.run(["docker", "image", "inspect", runtime], capture_output=True).returncode != 0:
        sys.exit(f"没有基础镜像 {runtime}：依赖变了（或还没生成过）。"
                 "需要先构建并验收完整镜像，再用 runtime-base.py 抽出新的基础镜像。")

    # 4. 只构建应用层。
    tag = f"{SERVICE_REPO}:app-{sha[:12]}"
    subprocess.run([
        "docker", "build", "--network=none",
        "-f", str(context / "deploy" / "momorain" / "Dockerfile.app"),
        "--build-arg", f"RUNTIME={runtime}", "--build-arg", f"REVISION={sha}",
        "-t", tag, str(context),
    ], check=True)
    shutil.rmtree(context)  # 解压出来的东西不留，发布目录只保留三个原始文件当回滚包

    # 5. 冒烟检查：代码和前端都在、提交对得上。
    check = ("import json; m=json.load(open('/app/web/frontend-build.json')); "
             f"assert m['commit']=={sha!r}, m; "
             "import os; assert os.path.isfile('/app/deeptutor/api/main.py'); print('app ok')")
    subprocess.run(["docker", "run", "--rm", "--network=none", "--entrypoint", "python", tag, "-c", check], check=True)
    size = int(subprocess.check_output(["docker", "image", "inspect", tag, "--format", "{{.Size}}"], text=True))
    print(f"APP_READY {tag} runtime={runtime} total={size // 1048576}MB", flush=True)


if __name__ == "__main__":
    main()
