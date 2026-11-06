# momorain 网页多用户部署

读者：维护本 fork、Dokploy 部署或用户会话的开发者。基线是上游 v1.7.0 后的 `1ea7df155`；`upstream` 保留 HKUDS/DeepTutor，`origin` 指向 momorain-tech/DeepTutor。

## 接入与费用

- `MOMORAIN_ENABLED=true` 开启网页模式。所有 API、文件下载和 WebSocket 在 ASGI 边界检查 momorain `session` Cookie；通过 `/api/auth/session` 查身份，不持有 JWT 签名密钥。
- **先浏览，保存或调用模型时再登录**（2026-11-06 起）。未登录访客只能读 `deeptutor/momorain/guest.py` 里 `GUEST_ROUTES` 白名单的公共只读接口（登录状态、模块/能力说明、界面与页面定制的出厂默认值），这些由 guest.py 单独作答，**不进原路由**——上游 `get_current_user()` 缺身份会回退到本地管理员。其余一律 401：私人数据读取、所有写入和上传、WebSocket、模型调用。访客不建用户目录、不取 provision。登录服务故障（503）不当作访客处理。不能简单放行所有 GET：有的 GET 读私人数据，有的会取 provision。
- 前端 `web/lib/momorain-guest.ts` 有同一张白名单（`GUEST_PATHS`），两边一起改。`apiFetch` 在 momorain 模式下：访客对非白名单接口**不发请求**、本地回 401（读操作静默，写操作弹「需要登录」说明框）；已登录用户收到真实 401 视为登录过期，只弹一次「重新登录」框，不再整页跳转。访客不启动任务板同步、不建聊天 WebSocket；点发送时拦下，内容留在输入框。
- 去登录前，输入框把草稿（文字、附件、对话模式）暂存到本机 IndexedDB，登录回到同一页面后填回输入框、**不自动发送**（避免登录回来自动扣费或重复提交），30 分钟过期。服务端仍独立把关：路由层 `require_auth`、回合入口 `start_turn` 的 provision、WebSocket 鉴权都各自检查身份。
- 浏览器访问站点根 `/api/auth/session` 续期；登录跳到 `https://momorain.com/login?next=...`，昵称链到全站 `/account`。
- 后端转发当前用户 Cookie 获取 one-api provision。模型选择只存 `{profile_id: "momorain", model_id: ...}`；模型 key 不写入数据库、目录、会话、环境变量或前端。
- 每个新回合在线程取一次 provision，后台回合只继承自己的操作上下文。provision 结果按 userId 在**进程内存**缓存（ready 60 秒、未就绪 10 秒，`MOMORAIN_PROVISION_CACHE_SECONDS` 可调），绝不跨用户复用、不落盘；上游 401 立即删缓存；上游 429 后该用户进入冷却（30 秒起、连续 429 翻倍、封顶 10 分钟，尊重 Retry-After），冷却期内不请求上游，5xx/超时冷却 5 秒；期间 30 分钟内的 ready 旧结果可临时顶用。one-api 侧已改为按用户限流（见 PLATFORM.md），两边需一起上线。原因：one-api 的 provision 按来源 IP 限流（60 次/20 分钟），DeepTutor 服务端代调时所有用户共用服务器 IP 这一个桶，不缓存会被页面上的模型列表、工具状态等读取打满（2026-11-04 事故）。模型列表等读取只返回去除 key 的元数据。
- 开回合被拒（取 key 失败、额度不足等）时，WebSocket 回 `start_turn_rejected` 并保持连接；前端收到后丢弃这条待发消息、不在重连后重发，重连退避在连接稳定 10 秒后才清零。
- 对话与后台任务模型走用户 key 的 `/v1/chat/completions`；知识库向量走同一用户 key 的 `/v1/embeddings`。按 `ready` 阻止未就绪的付费动作，额度不足返回 402，账号服务不可用返回 503。
- one-api 必须先配置已开通、有输入定价并标记 `embedding` 的向量模型。通义等使用 OpenAI 兼容渠道，避免原生适配丢掉 dimensions。没有模型时不会借用运营者的 key。

## 多会话保存设计

复用上游统一 SQLite 存储与 durable turn 引擎，避免在前端另外维护一份对话真相：

- 每位用户独占 `data/users/<momorain-userId>/`；会话数据库在其中的 `user/chat_history.db`。网页切换工作区时仍在该用户之内；本地身份记录只保存稳定 uid，不复制昵称、不分配密码、不提升首位用户。
- 每个会话拥有独立 id、标题、消息分支、模型引用和偏好。新建、重命名、搜索、删除/恢复均使用上游持久化接口。
- 每回合拥有 turn id、单调递增事件序号；刷新或掉线后按最后序号重放。提交带 client_submission_id，重试能识别同一条消息，避免重复保存或重复发起。
- 用户关闭网页后，已受理任务仍可结束并保存。进程重启后保留消息与事件；正在生成但未结束的回合标记可重试的中断，**不会自动重放付费调用**。
- SQLite 启用 WAL 和 busy_timeout。单个异步后端同时服务多用户、多标签页和不同会话；同一会话的运行中回合由上游协调器控制。
- 当前固定一个进程、一个服务副本。启动时以数据卷文件锁拒绝第二个内存协调器；Dokploy 更新必须 stop-first。横向扩容需要先改成 Redis 协调，不能直接把副本数调到 2。
- 账号切换时重新加载页面，避免旧账号的客户端缓存和 WebSocket 留在当前页。恢复草稿继续使用上游按 principal 隔离的机制。

## 网页边界

关闭 CLI、exec、MCP、子 agent、机器人/Partners、设备配对和厂商 OAuth。后端路由、工具权限、子 agent 注册表以及 TurnRequest 均独立限制，不能只靠隐藏菜单。普通用户无法访问管理接口；统一账号和 one-api 控制台负责身份与额度管理。

## Dokploy 首次填写

| 配置项 | 值 |
|---|---|
| GitHub | `momorain-tech/DeepTutor`，分支 `main` |
| Build Type | Dockerfile |
| Docker File | `Dockerfile.momorain` |
| Docker Context Path | `.` |
| Container Port | `3000` |
| Domain Host / Path | `momorain.com` / `/deeptutor` |
| HTTPS | 开启，复用 Let's Encrypt |
| Strip Path | **关闭** |
| Auto Deploy | **关闭**，先发布前端产物，再手动 Deploy |
| Replicas | **1** |
| Update order | **stop-first** |
| Volume | 命名卷 `deeptutor-data` → `/app/data` |

环境变量只需按需覆盖（默认已经可用，无任何厂商 key）：

```text
MOMORAIN_ENABLED=true
MOMORAIN_PUBLIC_ORIGIN=https://momorain.com
MOMORAIN_SESSION_URL=https://momorain.com/api/auth/session
MOMORAIN_PROVISION_URL=https://momorain.com/one-api/api/momorain/provision
```

服务器内网可替换后两个 URL，但务必包含对应原始路径。构建期的 `/deeptutor` 前缀和网页模式已入库于 `web/.env.production`，不依赖手填 build args。

容器内 nginx 监听 3000，Next 监听 127.0.0.1:3001，FastAPI 监听 127.0.0.1:8001。nginx 保留页面前缀，只对内部 API / WS 转发剥一次 `/deeptutor`，关闭上传和流式响应缓冲。前端在 `.github/workflows/momorain-frontend.yml` 中由 GitHub Actions 进行类型检查、路径测试和构建，再发布 Linux x64 压缩包及 SHA256。服务器 Dockerfile 只下载 `deploy/momorain/frontend-version.txt` 指定的版本并验校验值，完全不在生产机器编译 Next。Python 使用 `requirements/momorain-linux.lock`，不在服务器重复解析整棵依赖图。阶段之间串行，运行内存限制不等于 Docker 构建限制，仍不要同时启动多个首次构建。

每次修改 web，先修改前端版本文件为新值、推送，再运行 `momorain 网页构建` workflow；成功后才在 Dokploy 部署。版本产物不可覆盖，方便回滚到一致的前端。

Dockerfile 使用国内 npm、pip、apt 源；前端使用本机字体，不访问 Google Fonts。

健康检查：API `/health/ready`。未登录请求 `/deeptutor/api/sessions` 应为 401，`/deeptutor/api/auth/status` 应为 enabled=true/authenticated=false，`/deeptutor/api/modules` 应为 200。禁止功能应为 404。无痕窗口打开 `/deeptutor/chat` 应停在页面上（不跳登录），点发送才弹登录说明。聊天和知识库操作还需要一个真实已登录且 provision ready 的账号。

## 备份与恢复

只备份数据库主文件会漏掉 WAL 中的最新消息。`/app/backup.py` 使用 SQLite online backup，包含各用户数据库和非数据库文件：

```sh
python /app/backup.py /backups/deeptutor-20261102
```

`/backups` 应另挂卷；备份目录必须在 `/app/data` 之外。需要同一时刻一致的附件与数据库快照时，先停服务，再备份整个数据卷。恢复时停服务、恢复整个 data 树、保持目录权限，再启动一个副本。

## 本地验收

```sh
.venv-web/bin/python -m pytest tests/momorain tests/api/test_unified_ws_protocol.py tests/api/test_session_search.py -q
cd web && npx vitest run tests/momorain-base-path.spec.ts tests/momorain-guest.spec.ts
npm run build
```

完整本机协议验收（先运行本机 API、前端和模拟服务）：

```sh
.venv-web/bin/python tests/momorain/smoke_check.py
```

覆盖两个账号、三个并发会话、重复提交去重、历史保存、跨账号 404 和 after_seq 增量回放；模型调用使用本机模拟 key。

`tests/momorain/smoke_server.py` 是仅监听 127.0.0.1 的模拟账号/模型网关，用于全流程检查，不连接真实付费服务、不用于部署。正式镜像不包含 tests 目录。

固定依赖更新命令（在开发机执行）：

```sh
uv pip compile requirements/full.txt --python-version 3.12 --python-platform x86_64-unknown-linux-gnu --index-url https://pypi.tuna.tsinghua.edu.cn/simple -o requirements/momorain-linux.lock
```
