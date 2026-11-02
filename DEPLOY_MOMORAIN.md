# momorain 网页多用户部署

读者：维护本 fork、Dokploy 部署或用户会话的开发者。基线是上游 v1.7.0 后的 `1ea7df155`；`upstream` 保留 HKUDS/DeepTutor，`origin` 指向 momorain-tech/DeepTutor。

## 接入与费用

- `MOMORAIN_ENABLED=true` 开启网页模式。所有 API、文件下载和 WebSocket 在 ASGI 边界检查 momorain `session` Cookie；通过 `/api/auth/session` 查身份，不持有 JWT 签名密钥。
- 浏览器访问站点根 `/api/auth/session` 续期；登录跳到 `https://momorain.com/login?next=...`，昵称链到全站 `/account`。
- 后端转发当前用户 Cookie 获取 one-api provision。模型选择只存 `{profile_id: "momorain", model_id: ...}`；模型 key 不写入数据库、目录、会话、环境变量或前端。
- 每个新回合在线程取一次 provision，后台回合只继承自己的操作上下文。不会跨回合、跨用户复用凭证；模型列表等读取只返回去除 key 的元数据。
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

容器内 nginx 监听 3000，Next 监听 127.0.0.1:3001，FastAPI 监听 127.0.0.1:8001。nginx 保留页面前缀，只对内部 API / WS 转发剥一次 `/deeptutor`，关闭上传和流式响应缓冲。Dockerfile 使用国内 npm、pip、apt 源；前端使用本机字体，不访问 Google Fonts。

健康检查：API `/health/ready`。未登录请求 `/deeptutor/api/sessions` 应为 401，`/deeptutor/api/auth/status` 应为 enabled=true/authenticated=false。禁止功能应为 404。聊天和知识库操作还需要一个真实已登录且 provision ready 的账号。

## 备份与恢复

只备份数据库主文件会漏掉 WAL 中的最新消息。`/app/backup.py` 使用 SQLite online backup，包含各用户数据库和非数据库文件：

```sh
python /app/backup.py /backups/deeptutor-20261102
```

`/backups` 应另挂卷；备份目录必须在 `/app/data` 之外。需要同一时刻一致的附件与数据库快照时，先停服务，再备份整个数据卷。恢复时停服务、恢复整个 data 树、保持目录权限，再启动一个副本。

## 本地验收

```sh
.venv-web/bin/python -m pytest tests/momorain tests/api/test_unified_ws_protocol.py tests/api/test_session_search.py -q
cd web && npx vitest run tests/momorain-base-path.spec.ts
npm run build
```

`tests/momorain/smoke_server.py` 是仅监听 127.0.0.1 的模拟账号/模型网关，用于全流程检查，不连接真实付费服务、不用于部署。正式镜像不包含 tests 目录。
