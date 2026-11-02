"""仅用于本机冒烟：模拟账号/网关并提供带 WebSocket 的同域反代，不连接真实付费服务。"""
import asyncio
import json
from aiohttp import web, ClientSession, WSMsgType


async def auth(request):
    user = request.cookies.get("session")
    if user not in {"alice", "bob"}:
        return web.json_response({"status": "error"}, status=401)
    return web.json_response({"status": "ok", "data": {"userId": "u_" + ("a" if user == "alice" else "b") * 32, "nickname": user}})


async def provision(request):
    user = request.cookies.get("session")
    if user not in {"alice", "bob"}:
        return web.json_response({"success": False, "need_login": True})
    return web.json_response({"success": True, "data": {"ready": True, "key": "sk-smoke-" + user,
        "baseUrl": "http://127.0.0.1:8766/one-api", "quota": {"display": "¥1.00"},
        "topupUrl": "https://momorain.com/account", "modelInfos": [
            {"id": "smoke-chat", "name": "本机测试模型", "capabilities": ["text"]},
            {"id": "smoke-embedding", "name": "本机向量模型", "capabilities": ["embedding"]}]}})


async def completion(request):
    body = await request.json()
    key = request.headers.get("Authorization", "")
    who = "alice" if key == "Bearer sk-smoke-alice" else "bob" if key == "Bearer sk-smoke-bob" else "invalid"
    if who == "invalid":
        return web.json_response({"error": {"message": "invalid key"}}, status=401)
    text = f"这是 {who} 的本机测试回答。会话已保存。"
    print(json.dumps({"charged_to": who, "endpoint": request.path, "model": body.get("model")}), flush=True)
    if not body.get("stream"):
        return web.json_response({"id": "smoke", "object": "chat.completion", "model": body["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}})
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await response.prepare(request)
    for part in [text[:10], text[10:]]:
        event = {"id": "smoke", "object": "chat.completion.chunk", "model": body["model"], "choices": [{"index": 0, "delta": {"content": part}, "finish_reason": None}]}
        await response.write(("data: " + json.dumps(event) + "\n\n").encode())
    await response.write(("data: " + json.dumps({"id": "smoke", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}) + "\n\ndata: [DONE]\n\n").encode())
    await response.write_eof()
    return response


async def embeddings(request):
    body = await request.json()
    values = body["input"] if isinstance(body["input"], list) else [body["input"]]
    return web.json_response({"object": "list", "model": body["model"], "data": [{"object": "embedding", "index": i, "embedding": [0.1, 0.2, 0.3, 0.4]} for i, _ in enumerate(values)], "usage": {"prompt_tokens": len(values), "total_tokens": len(values)}})


async def mock_login(request):
    user = request.match_info["who"]
    if user not in {"alice", "bob"}:
        raise web.HTTPNotFound()
    response = web.HTTPFound("/deeptutor/chat")
    response.set_cookie("session", user, httponly=True, samesite="Lax")
    return response


async def proxy(request):
    backend = request.path.startswith(("/deeptutor/api/", "/deeptutor/ws", "/deeptutor/files/"))
    path = request.path_qs[len("/deeptutor"):] if backend else request.path_qs
    target = f"http://127.0.0.1:{8007 if backend else 3007}" + path
    headers = {k: v for k, v in request.headers.items() if not k.lower().startswith("sec-websocket-") and k.lower() not in {"connection", "upgrade", "content-length", "transfer-encoding"}}
    async with ClientSession() as client:
        if request.headers.get("Upgrade", "").lower() == "websocket":
            downstream = web.WebSocketResponse()
            await downstream.prepare(request)
            async with client.ws_connect(target, headers=headers) as upstream:
                async def forward(src, dst):
                    async for message in src:
                        if message.type == WSMsgType.TEXT:
                            await dst.send_str(message.data)
                        elif message.type == WSMsgType.BINARY:
                            await dst.send_bytes(message.data)
                        else:
                            break
                tasks = [asyncio.create_task(forward(downstream, upstream)), asyncio.create_task(forward(upstream, downstream))]
                _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
            return downstream
        async with client.request(request.method, target, headers=headers, data=await request.read(), allow_redirects=False) as upstream:
            return web.Response(status=upstream.status, body=await upstream.read(), headers={k: v for k, v in upstream.headers.items() if k.lower() not in {"transfer-encoding", "content-encoding", "content-length", "connection"}})


async def main():
    mock = web.Application()
    mock.router.add_get("/api/auth/session", auth)
    mock.router.add_get("/one-api/api/momorain/provision", provision)
    mock.router.add_post("/one-api/v1/chat/completions", completion)
    mock.router.add_post("/one-api/v1/embeddings", embeddings)
    public = web.Application()
    public.router.add_get("/api/auth/session", auth)
    public.router.add_get("/mock/{who}", mock_login)
    public.router.add_route("*", "/{tail:.*}", proxy)
    for app, port in [(mock, 8766), (public, 8087)]:
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
    print("本机冒烟服务就绪 http://127.0.0.1:8087/mock/alice", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
