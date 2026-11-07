# 每次发布只构建这一个 Dockerfile：只放应用代码，几秒完成，可以 --network=none。
#
# 为什么拆成两层：
#   DeepTutor 的镜像约 1.6GB 是依赖（pip 1.2GB、apt、node），几个月都不变；
#   每次发布变的只有 web/（约 120MB）和 deeptutor/（约 17MB）。
#   以前每次部署都 `FROM 上一版镜像` 再盖一层新代码——旧代码那层删不掉，只是被遮住，
#   镜像越叠越大（叠到第 4 次已经 3GB）。现在固定 FROM 同一个运行时基础镜像，
#   应用层每次都是全新替换，镜像大小恒定，回滚只是换个标签。
#
# 运行时基础镜像由 runtime-base.py 生成，标签带着依赖锁文件的哈希
# （momorain-deeptutor-runtime:lock-<12位>），依赖一变哈希就对不上，assemble-app.py 会拒绝构建。
#
# 构建上下文 = 固定提交的 git archive + CI 编译好的 web/，由 assemble-app.py 准备。
ARG RUNTIME
FROM ${RUNTIME}
ARG REVISION
LABEL org.opencontainers.image.revision=${REVISION}
COPY web/ /app/web/
COPY deeptutor/ /app/deeptutor/
COPY deeptutor_cli/ /app/deeptutor_cli/
COPY packages/ /app/packages/
COPY scripts/ /app/scripts/
COPY pyproject.toml build_metadata.py build_support.py /app/
COPY deploy/momorain/backup.py /app/backup.py
COPY deploy/momorain/nginx.conf /etc/nginx/nginx.conf
COPY deploy/momorain/supervisord.conf /etc/supervisor/conf.d/deeptutor.conf
