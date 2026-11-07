"""从一个已验收的 DeepTutor 镜像里「抽出」运行时基础镜像（只含依赖、不含应用代码）。

在生产服务器上执行（不联网、不编译，只做文件复制）：

    ssh root@<服务器> python3 - <已验收镜像> < deploy/momorain/runtime-base.py

做了什么：
  1. 在旧镜像上删掉所有应用代码（/app/web、/app/deeptutor ……）；
  2. `FROM scratch` + `COPY --from=旧镜像 / /`，把文件系统「压平」成一层——
     旧镜像里一层层叠起来、又被删掉的历史代码就不会再被带进来；
  3. 照抄旧镜像的 ENV / WORKDIR / VOLUME / EXPOSE / HEALTHCHECK / CMD；
  4. 打标签 momorain-deeptutor-runtime:lock-<依赖锁文件 sha256 前 12 位>，
     并跑一遍冒烟检查（关键 Python 包能 import、node / nginx 在）。

什么时候需要重新生成：requirements/momorain-linux.lock、Node 主版本、apt 包或启动命令变了。
那时不能再用这个脚本「抽」，要先用完整 Dockerfile.momorain 在别处构建并验收一个新镜像，再从它抽。
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = "momorain-deeptutor-runtime"
# 应用代码：基础镜像里不该有，统一由 Dockerfile.app 每次放进来。
APP_PATHS = [
    "/app/web", "/app/deeptutor", "/app/deeptutor_cli", "/app/packages", "/app/scripts",
    "/app/pyproject.toml", "/app/build_metadata.py", "/app/build_support.py", "/app/backup.py",
    "/tmp/deeptutor-frontend-built.json",
]
LOCK = "/app/requirements/momorain-linux.lock"


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def seconds(ns: int) -> str:
    return f"{int(ns // 1_000_000_000)}s"


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("用法: python3 - <已验收镜像> < runtime-base.py")
    source = sys.argv[1]

    # 和部署一样的内存门槛：可用内存太少就不动，免得挤到线上其他站点。
    available_kb = int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines()
                            if line.startswith("MemAvailable:")))
    if available_kb < 2_200_000:
        sys.exit(f"可用内存只有 {available_kb // 1024} MB，低于 2.2GB 门槛，先不做")

    config = json.loads(run("docker", "image", "inspect", source, "--format", "{{json .Config}}"))
    lock_sha = run("docker", "run", "--rm", "--network=none", "--entrypoint", "sha256sum", source, LOCK).split()[0]
    tag = f"{REPO}:lock-{lock_sha[:12]}"

    lines = [
        f"FROM {source} AS src",
        "RUN rm -rf " + " ".join(APP_PATHS),
        "FROM scratch",
        "COPY --from=src / /",
    ]
    for env in config.get("Env") or []:
        key, _, value = env.partition("=")
        lines.append(f"ENV {key}={json.dumps(value)}")
    lines.append(f"WORKDIR {config.get('WorkingDir') or '/app'}")
    for volume in config.get("Volumes") or {}:
        lines.append(f"VOLUME {json.dumps([volume])}")
    for port in config.get("ExposedPorts") or {}:
        lines.append(f"EXPOSE {port}")
    health = config.get("Healthcheck")
    if health and health.get("Test"):
        test = health["Test"]
        options = []
        for key, flag in (("Interval", "interval"), ("Timeout", "timeout"), ("StartPeriod", "start-period")):
            if health.get(key):
                options.append(f"--{flag}={seconds(health[key])}")
        if health.get("Retries"):
            options.append(f"--retries={health['Retries']}")
        command = f"CMD {test[1]}" if test[0] == "CMD-SHELL" else f"CMD {json.dumps(test[1:])}"
        lines.append(f"HEALTHCHECK {' '.join(options)} {command}")
    if config.get("Entrypoint"):
        lines.append(f"ENTRYPOINT {json.dumps(config['Entrypoint'])}")
    if config.get("Cmd"):
        lines.append(f"CMD {json.dumps(config['Cmd'])}")
    lines.append(f'LABEL org.momorain.runtime.lock-sha256="{lock_sha}" org.momorain.runtime.source="{source}"')

    with tempfile.TemporaryDirectory(prefix="deeptutor-runtime-") as context:
        os.chmod(context, 0o700)
        (Path(context) / "Dockerfile").write_text("\n".join(lines) + "\n")
        print("\n".join(lines), flush=True)
        subprocess.run(["docker", "build", "--network=none", "-t", tag, context], check=True)

    # 冒烟检查：依赖都在、应用代码确实不在。
    smoke = ("import fastapi, uvicorn, llama_index.core, faiss, fitz, openai; "
             "import os; assert not os.path.exists('/app/deeptutor'), 'app code leaked into runtime'; print('python ok')")
    subprocess.run(["docker", "run", "--rm", "--network=none", "--entrypoint", "python", tag, "-c", smoke], check=True)
    subprocess.run(["docker", "run", "--rm", "--network=none", "--entrypoint", "sh", tag, "-c",
                    "node --version && nginx -v && supervisord --version"], check=True)
    size = run("docker", "image", "inspect", tag, "--format", "{{.Size}}")
    print(f"RUNTIME_READY {tag} {int(size) // 1048576}MB", flush=True)


if __name__ == "__main__":
    main()
